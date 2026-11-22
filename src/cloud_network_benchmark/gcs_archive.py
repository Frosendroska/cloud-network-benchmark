"""Optional controller-only GCS sink for sealed, bounded native archives.

The backend is injected so ordinary runs need neither Google credentials nor a
Google client dependency. A production backend must use generation preconditions
and stream downloads to the supplied bounded staging file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol

from .closed_archive import FAT32_FILE_LIMIT


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ARCHIVE_PROJECT_ID = "project-a70f8497-3463-4ef2-9cd"
_ARCHIVE_SERVICE_ACCOUNT = "cnb-archive-halle@project-a70f8497-3463-4ef2-9cd.iam.gserviceaccount.com"
_ARCHIVE_SOURCE_PRINCIPAL = "katyabraun0@gmail.com"
_ARCHIVE_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"


def _safe_source_adc(path: str | Path, expected_principal: str) -> dict:
    """Read a private human ADC file without refreshing it or exposing errors."""
    candidate = Path(path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("archive source ADC path must be absolute and normalized")
    if any(parent.is_symlink() for parent in (candidate, *candidate.parents)):
        raise ValueError("archive source ADC file is unsafe")
    try:
        info = candidate.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ValueError
        if os.name == "posix" and (info.st_mode & 0o077 or not info.st_mode & 0o400
                or info.st_uid != os.geteuid()):
            raise ValueError
        document = json.loads(candidate.read_text(encoding="utf-8"))
        if (not isinstance(document, dict) or document.get("type") != "authorized_user"
                or not isinstance(document.get("client_id"), str)
                or not isinstance(document.get("client_secret"), str)
                or not document["client_secret"]
                or not isinstance(document.get("refresh_token"), str)
                or not document["refresh_token"]
                or document.get("quota_project_id") != _ARCHIVE_PROJECT_ID
                or document.get("universe_domain", "googleapis.com") != "googleapis.com"):
            raise ValueError
        if not valid_archive_source(expected_principal):
            raise ValueError
        return document
    except Exception:
        raise ValueError("archive source ADC is missing, unsafe, or not the configured human ADC type") from None


class GCSObjectBackend(Protocol):
    def stat(self, key: str, *, timeout_seconds: float) -> dict | None: ...
    def upload(self, key: str, source: Path, *, size_bytes: int, sha256: str,
               if_generation_match: int, chunk_bytes: int, timeout_seconds: float) -> dict: ...
    def download(self, key: str, destination: Path, *, maximum_bytes: int,
                 timeout_seconds: float, generation: str) -> None: ...
    def delete(self, key: str, *, generation: str, timeout_seconds: float) -> None: ...


class GCSArchiveFailure(RuntimeError):
    def __init__(self, message: str, *, partial_objects: list[dict[str, Any]]):
        super().__init__(message)
        self.partial_objects = partial_objects


def valid_bucket_name(value: Any) -> bool:
    return (isinstance(value, str)
        and bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", value))
        and ".." not in value
        and re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", value) is None)


def valid_archive_identity(project_id: Any, service_account_email: Any) -> bool:
    return project_id == _ARCHIVE_PROJECT_ID and service_account_email == _ARCHIVE_SERVICE_ACCOUNT


def valid_archive_source(principal: Any) -> bool:
    return principal == _ARCHIVE_SOURCE_PRINCIPAL


class GoogleCloudStorageBackend:
    """Production backend impersonating the fixed archive SA from explicit human ADC."""

    def __init__(self, bucket: str, *, source_credentials_file: str | Path,
                 expected_source_principal: str, expected_project_id: str,
                 expected_service_account_email: str, lifetime: int = 900):
        if not isinstance(source_credentials_file, (str, Path)) or not str(source_credentials_file).strip():
            raise ValueError("archive source ADC path is required")
        if (not isinstance(expected_project_id, str) or not expected_project_id.strip()
                or not isinstance(expected_service_account_email, str)
                or not expected_service_account_email.strip()):
            raise ValueError("expected archive project and service-account identity are required")
        if not valid_archive_identity(expected_project_id, expected_service_account_email):
            raise ValueError("configured archive identity does not match the approved archive account")
        if not isinstance(expected_source_principal, str) or not expected_source_principal.strip():
            raise ValueError("expected archive source principal is required")
        if not valid_archive_source(expected_source_principal):
            raise ValueError("configured archive source principal is not the approved human identity")
        if type(lifetime) is not int or not 600 <= lifetime <= 3600:
            raise ValueError("archive impersonation lifetime must be between 600 and 3600 seconds")
        _safe_source_adc(source_credentials_file, expected_source_principal)
        try:
            from google.cloud import storage
            from google.auth import load_credentials_from_file
            from google.auth import impersonated_credentials
        except ImportError as exc:  # pragma: no cover - optional production extra
            raise RuntimeError("install the gcs optional dependency on the controller") from exc
        try:
            source_credentials, _ = load_credentials_from_file(str(source_credentials_file))
            credentials = impersonated_credentials.Credentials(
                source_credentials=source_credentials,
                target_principal=expected_service_account_email,
                target_scopes=[_ARCHIVE_SCOPE],
                lifetime=lifetime)
            archive_client = storage.Client(project=expected_project_id, credentials=credentials)
        except Exception:
            raise ValueError("archive impersonated credentials could not be constructed") from None
        self._bucket = archive_client.bucket(bucket)


    def stat(self, key: str, *, timeout_seconds: float) -> dict | None:
        blob = self._bucket.get_blob(key, timeout=timeout_seconds, retry=None)
        if blob is None:
            return None
        return {"generation": str(blob.generation), "size_bytes": int(blob.size or 0)}

    def upload(self, key: str, source: Path, *, size_bytes: int, sha256: str,
               if_generation_match: int, chunk_bytes: int, timeout_seconds: float) -> dict:
        started = time.monotonic()
        def remaining_timeout() -> float:
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("generation-bound object upload exhausted its shared timeout")
            return remaining
        blob = self._bucket.blob(key, chunk_size=chunk_bytes)
        blob.metadata = {"cnb-sha256": sha256}
        # Drive the SDK's resumable state machine directly. Blob.upload_from_file
        # reuses one timeout for every chunk and deletes the object implicitly on
        # DataCorruption; both behaviors violate this archive's source-bound budget.
        with Path(source).open("rb") as stream:
            upload, transport = blob._initiate_resumable_upload(
                blob.bucket.client, stream, "application/octet-stream", size_bytes,
                if_generation_match=if_generation_match, chunk_size=chunk_bytes,
                timeout=remaining_timeout(), checksum="crc32c", retry=None)
            response = None
            while not upload.finished:
                response = upload.transmit_next_chunk(transport, timeout=remaining_timeout())
        if response is None:
            raise RuntimeError("GCS resumable create returned no final response")
        properties = response.json()
        blob._properties.update(properties)
        # The response generation is the exact create-only version; never
        # re-read an unbound latest generation before verification.
        if blob.generation is None or blob.size is None:
            raise RuntimeError("GCS create response omitted immutable object identity")
        return {"generation": str(blob.generation), "size_bytes": int(blob.size or 0)}

    def download(self, key: str, destination: Path, *, maximum_bytes: int,
                 timeout_seconds: float, generation: str) -> None:
        started = time.monotonic()
        def remaining_timeout() -> float:
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("generation-bound object download exhausted its shared timeout")
            return remaining
        blob = self._bucket.blob(key, generation=int(generation))
        blob.reload(if_generation_match=int(generation), timeout=remaining_timeout(), retry=None)
        if blob is None or blob.size is None or blob.size > maximum_bytes:
            raise ValueError("remote part is missing or exceeds staging bound")
        from google.cloud.storage._media.requests import ChunkedDownload
        client = blob.bucket.client
        download_url = blob._get_download_url(client, if_generation_match=int(generation))
        try:
            with Path(destination).open("wb") as stream:
                download = ChunkedDownload(download_url, 8 * 1024 * 1024, stream,
                    headers={"accept-encoding": "identity"}, start=0, end=int(blob.size) - 1,
                    retry=None)
                while not download.finished:
                    download.consume_next_chunk(client._http, timeout=remaining_timeout())
            if Path(destination).stat().st_size != int(blob.size):
                raise ValueError("generation-bound download returned an incomplete object")
        except Exception:
            Path(destination).unlink(missing_ok=True)
            raise

    def delete(self, key: str, *, generation: str, timeout_seconds: float) -> None:
        self._bucket.blob(key, generation=int(generation)).delete(
            if_generation_match=int(generation), timeout=timeout_seconds, retry=None)


def google_cloud_storage_backend(settings: dict[str, Any]) -> GoogleCloudStorageBackend:
    """Build the production archive backend from explicit, reviewed ADC identity."""
    required = {"bucket", "source_credentials_file", "expected_source_principal",
        "expected_project_id", "expected_service_account_email"}
    if not isinstance(settings, dict) or not required <= settings.keys():
        raise ValueError("archive backend requires explicit source ADC and approved archive identity")
    return GoogleCloudStorageBackend(settings["bucket"],
        source_credentials_file=settings["source_credentials_file"],
        expected_source_principal=settings["expected_source_principal"],
        expected_project_id=settings["expected_project_id"],
        expected_service_account_email=settings["expected_service_account_email"])


def _hash(path: Path, limit: int, guard: Callable[[], None]) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            guard()
            size += len(block)
            if size > limit:
                raise ValueError("object exceeds configured bounded staging size")
            digest.update(block)
    return size, digest.hexdigest()


def _remaining(deadline: Any, clock: Callable[[], float]) -> float:
    amount = deadline.remaining(clock, cleanup=True)
    if amount <= 0:
        raise TimeoutError("archive transfer reached the original deadline or cleanup reserve")
    return amount


def upload_sealed_archive(archive_dir: Path, *, backend: GCSObjectBackend,
                          bucket: str, provider: str, experiment_id: str, run_id: str,
                          maximum_staging_bytes: int, closure_verified: bool,
                          source_directory: Path, maximum_archive_bytes: int,
                          minimum_controller_free_bytes: int,
                          staging_directory: Path,
                          deadline: Any, clock: Callable[[], float] = time.monotonic,
                          chunk_bytes: int = 8 * 1024 * 1024) -> dict:
    """Upload one part at a time; return success only after byte-for-byte readback.

    The returned object is suitable for durable retrieval status. Failure removes
    only objects created by this invocation using their observed generations;
    leftovers from an interrupted process are reconciled by exact key and hash
    on the next call.
    """
    if not valid_bucket_name(bucket):
        raise ValueError("bucket name must use valid lowercase GCS bucket syntax")
    if closure_verified is not True:
        raise ValueError("native producers must be closed before archive upload")
    if provider not in {"aws", "azure", "gcp"}:
        raise ValueError("provider identity is not supported")
    if not all(_ID.fullmatch(value) for value in (experiment_id, run_id)):
        raise ValueError("experiment and run identities must be stable safe identifiers")
    if type(maximum_staging_bytes) is not int or maximum_staging_bytes <= 0:
        raise ValueError("maximum controller staging bytes must be positive")
    if type(maximum_archive_bytes) is not int or maximum_archive_bytes <= 0:
        raise ValueError("maximum local archive bytes must be positive")
    if type(minimum_controller_free_bytes) is not int or minimum_controller_free_bytes < 0:
        raise ValueError("minimum controller free bytes must be nonnegative")
    if type(chunk_bytes) is not int or chunk_bytes <= 0:
        raise ValueError("resumable upload chunk size must be positive")
    root = Path(archive_dir)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("sealed archive must be a real directory")
    staging_directory = Path(staging_directory)
    if staging_directory.is_symlink() or not staging_directory.is_dir():
        raise ValueError("controller verification staging directory must be an existing real directory")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("archive is not sealed: manifest is missing")
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    def remaining() -> float:
        return _remaining(deadline, clock)

    def guard() -> None:
        remaining()
    guard()
    def require_staging_space(size: int) -> None:
        if size > maximum_staging_bytes:
            raise ValueError("remote readback exceeds configured one-part staging bound")
        if shutil.disk_usage(staging_directory).free < size + minimum_controller_free_bytes:
            raise OSError("verification staging filesystem lacks part plus required headroom")

    def tempdir(prefix: str):
        return tempfile.TemporaryDirectory(prefix=prefix, dir=staging_directory)
    from .closed_archive import _safe_files
    source_rows = _safe_files(Path(source_directory), guard)
    sealed_files = [{"path": name, "size_bytes": size, "sha256": digest}
                    for name, _path, size, digest in source_rows]
    if manifest.get("files") != sealed_files:
        raise ValueError("native source changed after archive sealing")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("sealed archive has no bounded parts")
    for part in parts:
        name = part.get("path", "")
        if (not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}
                or type(part.get("size_bytes")) is not int or not 0 < part["size_bytes"] <= maximum_staging_bytes
                or not re.fullmatch(r"[a-f0-9]{64}", str(part.get("sha256", "")))):
            raise ValueError("archive part identity or configured staging bound is invalid")
    expected_members = {"manifest.json", *(part["path"] for part in parts)}
    actual_members = {path.name for path in root.iterdir() if path.is_file() and not path.is_symlink()}
    if actual_members != expected_members or any(path.is_symlink() for path in root.iterdir()):
        raise ValueError("sealed archive contains missing, extra or unsafe local members")
    prefix = f"cnb/v1/{provider}/{experiment_id}/{run_id}/"
    # Fixed content identities make retries idempotent and collisions explicit.
    entries = [(f"{prefix}parts/{index:05d}-of-{len(parts):05d}-{part['sha256']}",
                root / part["path"], part["size_bytes"], part["sha256"])
               for index, part in enumerate(parts, 1)]
    manifest_key = f"{prefix}manifest.json"
    # A logical run has exactly one immutable manifest. Check it before adding
    # any part so a second archive cannot partially occupy the same run prefix.
    try:
        prior_manifest = backend.stat(manifest_key, timeout_seconds=remaining())
    except Exception as exc:
        raise GCSArchiveFailure("manifest identity readback failed before upload",
            partial_objects=[{"key": manifest_key, "generation": "unknown"}]) from exc
    if prior_manifest is not None:
        if prior_manifest.get("size_bytes") != len(manifest_bytes):
            raise GCSArchiveFailure("logical run prefix is already bound to a different archive manifest",
                partial_objects=[{"key": manifest_key, "generation": str(prior_manifest.get("generation", "unknown"))}])
        try:
            require_staging_space(len(manifest_bytes))
            with tempdir("cnb-gcs-manifest-preflight-") as temp:
                staged = Path(temp) / "manifest.json"
                backend.download(manifest_key, staged, maximum_bytes=maximum_staging_bytes,
                    timeout_seconds=remaining(), generation=str(prior_manifest["generation"]))
                if _hash(staged, maximum_staging_bytes, guard) != (len(manifest_bytes), manifest_sha):
                    raise ValueError("manifest bytes differ from sealed archive")
        except Exception as exc:
            raise GCSArchiveFailure("logical run prefix manifest preflight did not verify",
                partial_objects=[{"key": manifest_key, "generation": str(prior_manifest.get("generation", "unknown"))}]) from exc
    entries.append((manifest_key, None, len(manifest_bytes), manifest_sha))
    local_archive_bytes = len(manifest_bytes) + sum(item[2] for item in entries[:-1])
    if local_archive_bytes > maximum_archive_bytes:
        raise ValueError("sealed archive exceeds configured local archive byte bound")
    if not root.exists():
        raise ValueError("sealed archive disappeared before upload")
    if shutil.disk_usage(root.parent).free < minimum_controller_free_bytes:
        raise OSError("controller free space is below required post-archive headroom")
    completion_key = f"{prefix}complete.json"
    expected_completion = {"schema_version": 1, "manifest_sha256": manifest_sha,
        "objects": [{"key": key, "size_bytes": size, "sha256": digest}
                    for key, _source, size, digest in entries]}
    completion_bytes = (json.dumps(expected_completion, sort_keys=True, separators=(",", ":")) + "\n").encode()
    completion_sha = hashlib.sha256(completion_bytes).hexdigest()
    try:
        prior_completion = backend.stat(completion_key, timeout_seconds=remaining())
    except Exception as exc:
        raise GCSArchiveFailure("completion marker identity readback failed",
            partial_objects=[{"key": completion_key, "generation": "unknown"}]) from exc
    if prior_completion is not None:
        try:
            require_staging_space(len(completion_bytes))
            with tempdir("cnb-gcs-complete-preflight-") as temp:
                staged = Path(temp) / "complete.json"
                backend.download(completion_key, staged, maximum_bytes=maximum_staging_bytes,
                    timeout_seconds=remaining(), generation=str(prior_completion["generation"]))
                if _hash(staged, maximum_staging_bytes, guard) != (len(completion_bytes), completion_sha):
                    raise ValueError("completion marker bytes differ from sealed archive")
        except Exception as exc:
            raise GCSArchiveFailure("logical run prefix completion preflight did not verify",
                partial_objects=[{"key": completion_key,
                    "generation": str(prior_completion.get("generation", "unknown"))}]) from exc
    created: list[tuple[str, str]] = []
    verified = []
    active_key: str | None = None
    try:
        for key, source, expected_size, expected_sha in entries:
            active_key = key
            guard()
            if source is not None:
                if source.is_symlink() or not source.is_file() or _hash(source, maximum_staging_bytes, guard) != (expected_size, expected_sha):
                    raise ValueError("sealed archive part is missing or changed")
            else:
                # Manifest is staged only if it fits the same one-object bound.
                if expected_size > maximum_staging_bytes:
                    raise ValueError("manifest exceeds configured staging bound")
            current = backend.stat(key, timeout_seconds=remaining())
            if current is None:
                if prior_completion is not None:
                    raise ValueError("a completed archive is missing a remote object")
                if source is None:
                    require_staging_space(expected_size)
                    with tempdir("cnb-gcs-manifest-") as temp:
                        staged = Path(temp) / "manifest.json"
                        staged.write_bytes(manifest_bytes)
                        current = backend.upload(key, staged, size_bytes=expected_size,
                            sha256=expected_sha, if_generation_match=0, chunk_bytes=chunk_bytes,
                            timeout_seconds=remaining())
                else:
                    current = backend.upload(key, source, size_bytes=expected_size,
                        sha256=expected_sha, if_generation_match=0, chunk_bytes=chunk_bytes,
                        timeout_seconds=remaining())
                created.append((key, str(current.get("generation", ""))))
            if current.get("size_bytes") != expected_size or not current.get("generation"):
                raise ValueError("remote object collision or incomplete upload")
            require_staging_space(expected_size)
            with tempdir("cnb-gcs-verify-") as temp:
                staged = Path(temp) / "remote.part"
                backend.download(key, staged, maximum_bytes=maximum_staging_bytes,
                                 timeout_seconds=remaining(), generation=str(current["generation"]))
                if _hash(staged, maximum_staging_bytes, guard) != (expected_size, expected_sha):
                    raise ValueError("remote object byte count or SHA-256 verification failed")
            verified.append({"key": key, "size_bytes": expected_size, "sha256": expected_sha,
                             "generation": str(current["generation"])})
        active_key = completion_key
        current = prior_completion or backend.stat(completion_key, timeout_seconds=remaining())
        if current is None:
            require_staging_space(len(completion_bytes))
            with tempdir("cnb-gcs-complete-") as temp:
                staged = Path(temp) / "complete.json"
                staged.write_bytes(completion_bytes)
                current = backend.upload(completion_key, staged, size_bytes=len(completion_bytes),
                    sha256=completion_sha, if_generation_match=0, chunk_bytes=chunk_bytes,
                    timeout_seconds=remaining())
                created.append((completion_key, str(current.get("generation", ""))))
        if current.get("size_bytes") != len(completion_bytes) or not current.get("generation"):
            raise ValueError("remote completion marker collision or incomplete upload")
        require_staging_space(len(completion_bytes))
        with tempdir("cnb-gcs-complete-verify-") as temp:
            staged = Path(temp) / "complete.json"
            backend.download(completion_key, staged, maximum_bytes=maximum_staging_bytes,
                timeout_seconds=remaining(), generation=str(current["generation"]))
            if _hash(staged, maximum_staging_bytes, guard) != (len(completion_bytes), completion_sha):
                raise ValueError("remote completion marker failed byte-count or SHA-256 verification")
        for row in verified:
            latest = backend.stat(row["key"], timeout_seconds=remaining())
            if latest is None or str(latest.get("generation")) != row["generation"]:
                raise ValueError("verified remote object disappeared before completion")
        latest_completion = backend.stat(completion_key, timeout_seconds=remaining())
        if latest_completion is None or str(latest_completion.get("generation")) != str(current["generation"]):
            raise ValueError("remote completion marker disappeared before acceptance")
        return {"status": "verified", "verified": True, "bucket": bucket,
                "prefix": prefix, "manifest_sha256": manifest_sha,
                "archive_bytes": sum(item["size_bytes"] for item in verified),
                "controller_archive_disk_bytes": local_archive_bytes,
                "staging_path": str(staging_directory),
                "staging_filesystem_free_bytes": shutil.disk_usage(staging_directory).free,
                "objects": verified, "completion_generation": str(current["generation"]),
                "staging_peak_bytes": max(max(item["size_bytes"] for item in verified), len(completion_bytes))}
    except BaseException as exc:
        unresolved: dict[str, str] = {}
        for key, generation in reversed(created):
            if generation:
                try:
                    backend.delete(key, generation=generation, timeout_seconds=remaining())
                except Exception:
                    unresolved[key] = generation
        if prior_completion is not None and completion_key not in {key for key, _ in created}:
            unresolved[completion_key] = str(prior_completion.get("generation", "unknown"))
        if active_key and active_key not in {key for key, _ in created}:
            try:
                current = backend.stat(active_key, timeout_seconds=remaining())
                if current is not None:
                    unresolved[active_key] = str(current.get("generation", "unknown"))
            except Exception:
                unresolved[active_key] = "unknown"
        raise GCSArchiveFailure(f"archive upload or verification failed ({type(exc).__name__})",
            partial_objects=[{"key": key, "generation": value}
                for key, value in sorted(unresolved.items())]) from exc


def relay_closed_native_tree(source_directory: Path, *, backend: GCSObjectBackend,
                             bucket: str, provider: str, experiment_id: str, run_id: str,
                             maximum_staging_bytes: int, closure_verified: bool,
                             maximum_archive_bytes: int, minimum_controller_free_bytes: int,
                             staging_directory: Path, deadline: Any,
                             clock: Callable[[], float] = time.monotonic,
                             chunk_bytes: int = 8 * 1024 * 1024,
                             status_callback: Callable[[dict], None] | None = None) -> dict:
    """Compress and relay one bounded part at a time, retaining only its verified manifest."""
    from .closed_archive import FAT32_FILE_LIMIT, create_closed_archive, projected_archive_upper_bound, _safe_files
    if closure_verified is not True:
        raise ValueError("native producers must be closed before archive upload")
    if not valid_bucket_name(bucket) or provider not in {"aws", "azure", "gcp"}:
        raise ValueError("archive destination identity is invalid")
    if not all(_ID.fullmatch(value) for value in (experiment_id, run_id)):
        raise ValueError("experiment and run identities must be stable safe identifiers")
    if type(maximum_staging_bytes) is not int or maximum_staging_bytes <= 0:
        raise ValueError("maximum controller staging bytes must be positive")
    if type(minimum_controller_free_bytes) is not int or minimum_controller_free_bytes < 0:
        raise ValueError("minimum controller free bytes must be nonnegative")
    root, source = Path(staging_directory), Path(source_directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("controller staging directory must be an existing real directory")
    if source.is_symlink() or not source.is_dir():
        raise ValueError("native source must be a real directory")
    max_part = min(FAT32_FILE_LIMIT, maximum_staging_bytes)

    def remaining():
        value = deadline.remaining(clock, cleanup=True)
        if value <= 0:
            raise TimeoutError("archive transfer reached the original deadline or cleanup reserve")
        return value

    def guard():
        remaining()

    guard()
    rows = _safe_files(source, guard)
    projection = projected_archive_upper_bound(sum(row[2] for row in rows), len(rows))
    if projection > maximum_archive_bytes:
        raise ValueError("measured archive upper bound exceeds configured archive limit")
    projected_parts = (projection + max_part - 1) // max_part
    manifest_state_bytes = (2048 + sum(len(row[0].encode("utf-8")) * 12 + 192 for row in rows)
        + projected_parts * 192 + 2048 + projected_parts * 256 + 2048)
    required = max_part + manifest_state_bytes + minimum_controller_free_bytes
    if shutil.disk_usage(root).free < required:
        raise OSError("controller quota lacks one bounded part, manifest/state overhead and required headroom")
    prefix = f"cnb/v1/{provider}/{experiment_id}/{run_id}/"
    manifest_key, completion_key = prefix + "manifest.json", prefix + "complete.json"
    partial: dict[str, str] = {}
    uploaded: list[dict] = []
    archive_dir = None
    verified_bytes = 0
    def report(state: str, **extra):
        if status_callback is not None:
            try:
                status_callback({"status": state, "verified": state == "verified",
                    "verified_parts": len(uploaded), "verified_bytes": verified_bytes, **extra})
            except Exception:
                pass
    try:
        report("progress")
        prior_manifest = backend.stat(manifest_key, timeout_seconds=remaining())
        prior_completion = backend.stat(completion_key, timeout_seconds=remaining())
        if prior_manifest is not None:
            partial[manifest_key] = str(prior_manifest.get("generation", "unknown"))
        if prior_completion is not None:
            partial[completion_key] = str(prior_completion.get("generation", "unknown"))
        if prior_completion is not None and prior_manifest is None:
            raise ValueError("completion marker exists without its immutable manifest")
        prior_manifest_payload = None
        if prior_manifest is not None:
            if prior_manifest.get("size_bytes", max_part + 1) > max_part:
                raise ValueError("existing manifest exceeds configured one-part bound")
            with tempfile.TemporaryDirectory(prefix="cnb-gcs-prior-manifest-", dir=root) as temp:
                manifest_readback = Path(temp) / "manifest.json"
                backend.download(manifest_key, manifest_readback, maximum_bytes=max_part,
                    timeout_seconds=remaining(), generation=str(prior_manifest["generation"]))
                prior_manifest_payload = json.loads(manifest_readback.read_bytes())
            current_files = [{"path": name, "size_bytes": size, "sha256": digest}
                for name, _path, size, digest in rows]
            if not isinstance(prior_manifest_payload, dict) or prior_manifest_payload.get("files") != current_files:
                raise ValueError("logical run prefix is bound to a different native source inventory")
            prior_parts = prior_manifest_payload.get("parts")
            if (not isinstance(prior_parts, list) or not prior_parts
                    or any(not isinstance(part, dict)
                        or not isinstance(part.get("path"), str)
                        or type(part.get("size_bytes")) is not int
                        or not 0 < part["size_bytes"] <= max_part
                        or not re.fullmatch(r"[a-f0-9]{64}", str(part.get("sha256", "")))
                        for part in prior_parts)):
                raise ValueError("existing manifest has no valid ordered part sequence")
            # A published manifest closes the remote archive. Bind the entire
            # ordered part set before rebuilding locally, and never resume by
            # creating a missing part under an occupied manifest prefix.
            with tempfile.TemporaryDirectory(prefix="cnb-gcs-prior-part-", dir=root) as temp:
                readback = Path(temp) / "part"
                for number, part in enumerate(prior_parts, start=1):
                    expected_name = f"archive.tar.gz.part-{number:05d}"
                    if part["path"] != expected_name:
                        raise ValueError("existing manifest has an invalid ordered part path")
                    key = f"{prefix}parts/{number:08d}-{part['sha256']}"
                    current = backend.stat(key, timeout_seconds=remaining())
                    if current is None:
                        if prior_completion is not None:
                            raise ValueError("completed archive is missing a remote part")
                        raise ValueError("existing manifest is missing a declared remote part")
                    generation = str(current.get("generation", ""))
                    if (not generation or current.get("size_bytes") != part["size_bytes"]):
                        raise ValueError("existing manifest part has an invalid generation or size")
                    backend.download(key, readback, maximum_bytes=max_part,
                        timeout_seconds=remaining(), generation=generation)
                    if _hash(readback, max_part, guard) != (part["size_bytes"], part["sha256"]):
                        raise ValueError("existing manifest part failed generation-bound content verification")
        part_number = 0

        def relay(part_path: Path, part: dict):
            nonlocal part_number, verified_bytes
            guard()
            part_number += 1
            if part["size_bytes"] > max_part:
                raise ValueError("compressed part exceeds configured one-part bound")
            if prior_manifest_payload is not None:
                prior_parts = prior_manifest_payload["parts"]
                if part_number > len(prior_parts) or any(
                        prior_parts[part_number - 1].get(field) != part.get(field)
                        for field in ("path", "size_bytes", "sha256")):
                    raise ValueError("generated part sequence differs from the occupied run manifest")
            key = f"{prefix}parts/{part_number:08d}-{part['sha256']}"
            current = backend.stat(key, timeout_seconds=remaining())
            if current is None:
                if prior_manifest_payload is not None:
                    raise ValueError("existing manifest is missing a declared remote part")
                if prior_completion is not None:
                    raise ValueError("completed archive is missing a remote part")
                partial[key] = "unknown"
                try:
                    current = backend.upload(key, part_path, size_bytes=part["size_bytes"],
                        sha256=part["sha256"], if_generation_match=0, chunk_bytes=chunk_bytes,
                        timeout_seconds=remaining())
                except Exception:
                    try:
                        observed = backend.stat(key, timeout_seconds=remaining())
                    except Exception:
                        raise
                    if observed is None:
                        partial.pop(key, None)
                        raise
                    current = observed
            generation = str(current.get("generation", ""))
            partial[key] = generation or "unknown"
            if current.get("size_bytes") != part["size_bytes"] or not generation:
                raise ValueError("remote part collision or incomplete upload")
            if shutil.disk_usage(root).free + part_path.stat().st_size < required:
                raise OSError("controller quota lacks part verification headroom")
            backend.download(key, part_path, maximum_bytes=max_part,
                timeout_seconds=remaining(), generation=generation)
            if _hash(part_path, max_part, guard) != (part["size_bytes"], part["sha256"]):
                raise ValueError("remote part byte count or SHA-256 verification failed")
            uploaded.append({"key": key, "size_bytes": part["size_bytes"],
                "sha256": part["sha256"], "generation": generation})
            verified_bytes += part["size_bytes"]
            partial.pop(key, None)
            report("progress")

        archive_dir = tempfile.TemporaryDirectory(prefix="cnb-gcs-relay-", dir=root)
        archive_path = Path(archive_dir.name) / "sealed"
        manifest = create_closed_archive(source, archive_path, max_part_bytes=max_part,
            free_bytes=lambda _path: shutil.disk_usage(root).free,
            minimum_headroom_bytes=minimum_controller_free_bytes, guard=guard, on_part=relay)
        current_rows = _safe_files(source, guard)
        current_files = [(name, size, digest) for name, _path, size, digest in current_rows]
        original_files = [(name, size, digest) for name, _path, size, digest in rows]
        if current_files != original_files:
            raise ValueError("native source changed while the archive was being relayed")
        if prior_manifest_payload is not None and part_number != len(prior_manifest_payload["parts"]):
            raise ValueError("generated part sequence is incomplete for the occupied run manifest")
        if prior_manifest_payload is not None and prior_manifest_payload != manifest:
            raise ValueError("logical run prefix is already bound to a different archive manifest")
        manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        if len(manifest_bytes) > max_part:
            raise ValueError("archive manifest exceeds configured one-part bound")
        if prior_manifest is not None:
            temp_path = Path(archive_dir.name) / "manifest.readback"
            backend.download(manifest_key, temp_path, maximum_bytes=max_part,
                timeout_seconds=remaining(), generation=str(prior_manifest["generation"]))
            if _hash(temp_path, max_part, guard) != (len(manifest_bytes), manifest_sha):
                raise ValueError("logical run prefix is already bound to a different archive manifest")
            manifest_generation = str(prior_manifest["generation"])
        else:
            manifest_path = Path(archive_dir.name) / "manifest.json"
            manifest_path.write_bytes(manifest_bytes)
            partial[manifest_key] = "unknown"
            result = backend.upload(manifest_key, manifest_path, size_bytes=len(manifest_bytes),
                sha256=manifest_sha, if_generation_match=0, chunk_bytes=chunk_bytes,
                timeout_seconds=remaining())
            manifest_generation = str(result["generation"])
            partial[manifest_key] = manifest_generation
            backend.download(manifest_key, manifest_path, maximum_bytes=max_part,
                timeout_seconds=remaining(), generation=manifest_generation)
            if _hash(manifest_path, max_part, guard) != (len(manifest_bytes), manifest_sha):
                raise ValueError("remote manifest failed byte-count or SHA-256 verification")
            partial.pop(manifest_key, None)
        objects = [*uploaded, {"key": manifest_key, "size_bytes": len(manifest_bytes),
            "sha256": manifest_sha, "generation": manifest_generation}]
        # Completion v2 binds every object generation as well as bytes. This
        # lets a later roomy-destination retrieval reject same-key replacement.
        completion = {"schema_version": 2, "manifest_sha256": manifest_sha,
            "objects": [{"key": item["key"], "size_bytes": item["size_bytes"],
                         "sha256": item["sha256"], "generation": item["generation"]}
                        for item in objects]}
        completion_bytes = (json.dumps(completion, sort_keys=True, separators=(",", ":")) + "\n").encode()
        completion_sha = hashlib.sha256(completion_bytes).hexdigest()
        if prior_completion is not None:
            marker = Path(archive_dir.name) / "complete.readback"
            backend.download(completion_key, marker, maximum_bytes=max_part,
                timeout_seconds=remaining(), generation=str(prior_completion["generation"]))
            if _hash(marker, max_part, guard) != (len(completion_bytes), completion_sha):
                raise ValueError("completion marker differs from this verified archive")
            completion_generation = str(prior_completion["generation"])
        else:
            marker = Path(archive_dir.name) / "complete.json"
            marker.write_bytes(completion_bytes)
            partial[completion_key] = "unknown"
            result = backend.upload(completion_key, marker, size_bytes=len(completion_bytes),
                sha256=completion_sha, if_generation_match=0, chunk_bytes=chunk_bytes,
                timeout_seconds=remaining())
            completion_generation = str(result["generation"])
            partial[completion_key] = completion_generation
            backend.download(completion_key, marker, maximum_bytes=max_part,
                timeout_seconds=remaining(), generation=completion_generation)
            if _hash(marker, max_part, guard) != (len(completion_bytes), completion_sha):
                raise ValueError("remote completion marker failed byte-count or SHA-256 verification")
            partial.pop(completion_key, None)
        # Retain exact late-stage identities if the completion acceptance readback
        # discovers that a manifest-bound object changed after marker upload.
        partial[manifest_key] = manifest_generation
        partial[completion_key] = completion_generation
        for row in objects:
            latest = backend.stat(row["key"], timeout_seconds=remaining())
            if (latest is None or str(latest.get("generation")) != row["generation"]
                    or latest.get("size_bytes") != row["size_bytes"]):
                raise ValueError("manifest-recorded remote object disappeared or changed before completion acceptance")
        latest_completion = backend.stat(completion_key, timeout_seconds=remaining())
        if (latest_completion is None
                or str(latest_completion.get("generation")) != completion_generation
                or latest_completion.get("size_bytes") != len(completion_bytes)):
            raise ValueError("completion marker disappeared or changed before acceptance")
        partial.pop(manifest_key, None)
        partial.pop(completion_key, None)
        result = {"status": "verified", "verified": True, "bucket": bucket, "prefix": prefix,
            "manifest_sha256": manifest_sha, "archive_bytes": manifest["archive_bytes"],
            "objects": objects, "completion_generation": completion_generation,
            "staging_peak_bytes": max([item["size_bytes"] for item in uploaded] + [len(manifest_bytes), len(completion_bytes)])}
        report("verified", manifest_sha256=manifest_sha,
               completion_generation=completion_generation, archive_bytes=manifest["archive_bytes"])
        return result
    except BaseException as exc:
        # Keep remote generations as recovery evidence; never remove guest originals.
        for key, generation in list(partial.items()):
            try:
                observed = backend.stat(key, timeout_seconds=remaining())
                if observed is not None:
                    partial[key] = str(observed.get("generation", generation))
            except Exception:
                partial[key] = generation or "unknown"
        report("failed", error=type(exc).__name__, partial_object_count=len(uploaded) + len(partial))
        raise GCSArchiveFailure(f"bounded archive relay failed ({type(exc).__name__}: {exc})",
            partial_objects=([{"key": row["key"], "generation": row["generation"]}
                              for row in uploaded] + [{"key": key, "generation": generation}
                             for key, generation in sorted(partial.items())]) ) from exc
    finally:
        if archive_dir is not None:
            archive_dir.cleanup()


def publish_guest_archive_parts(*, guest_archives: list[dict], fetch_part: Callable[[str, dict], Path],
        staging_directory: Path,
        backend: GCSObjectBackend, bucket: str, provider: str, experiment_id: str, run_id: str,
        closed_inventory: dict,
        maximum_part_bytes: int, maximum_archive_bytes: int, minimum_controller_free_bytes: int,
        controller_quota_free_bytes: int,
        deadline: Any, clock: Callable[[], float] = time.monotonic,
        chunk_bytes: int = 8 * 1024 * 1024,
        object_progress: Callable[[dict], None] | None = None) -> dict:
    """Publish already guest-compressed role parts and immutable receipts.

    Only one controller part exists at a time. The logical manifest is
    preflighted before the first create, then parts, manifest and completion
    are uploaded create-only and read back by generation.
    """
    if not valid_bucket_name(bucket) or provider not in {"aws", "azure", "gcp"}:
        raise ValueError("archive destination identity is invalid")
    if not all(_ID.fullmatch(value) for value in (experiment_id, run_id)):
        raise ValueError("experiment and run identities must be stable safe identifiers")
    if (type(maximum_part_bytes) is not int or maximum_part_bytes <= 0
            or type(maximum_archive_bytes) is not int or maximum_archive_bytes <= 0
            or type(minimum_controller_free_bytes) is not int or minimum_controller_free_bytes < 0
            or type(controller_quota_free_bytes) is not int or controller_quota_free_bytes < 0):
        raise ValueError("guest archive size and reserve limits must be nonnegative bounded integers")
    if {row.get("role") for row in guest_archives} != {"vm_a", "vm_b"} or len(guest_archives) != 2:
        raise ValueError("guest archive role inventory must contain VM A and VM B exactly once")
    def remaining():
        value = deadline.remaining(clock, cleanup=True)
        if value <= 0:
            raise TimeoutError("archive transfer reached the original deadline or cleanup reserve")
        return value
    roles = []
    expected_parts = []
    for archive in sorted(guest_archives, key=lambda value: value["role"]):
        role, manifest = archive["role"], archive["manifest"]
        if role not in {"vm_a", "vm_b"} or not isinstance(manifest, dict):
            raise ValueError("guest archive role manifest is invalid")
        files, parts = manifest.get("files"), manifest.get("parts")
        if not isinstance(files, list) or not files or not isinstance(parts, list) or not parts:
            raise ValueError("guest archive inventory or parts are incomplete")
        roles.append({"role": role, "files": files, "raw_bytes": manifest.get("raw_bytes"),
            "archive_bytes": manifest.get("archive_bytes"), "archive_sha256": manifest.get("archive_sha256"),
            "parts": parts})
        for index, part in enumerate(parts, 1):
            if (part.get("path") != f"archive.tar.gz.part-{index:05d}"
                    or type(part.get("size_bytes")) is not int
                    or not 0 < part["size_bytes"] <= min(maximum_part_bytes, FAT32_FILE_LIMIT)
                    or not re.fullmatch(r"[a-f0-9]{64}", str(part.get("sha256", "")))):
                raise ValueError("guest part sequence or byte bound is invalid")
            expected_parts.append((role, part["size_bytes"], part))
    staging_directory = Path(staging_directory)
    if staging_directory.is_symlink() or not staging_directory.is_dir():
        raise ValueError("one-part staging directory must be an existing real directory")
    prefix = f"cnb/v1/{provider}/{experiment_id}/{run_id}/"
    manifest = {"schema_version": 1, "provider": provider, "experiment_id": experiment_id,
        "run_id": run_id, "closed_inventory": closed_inventory, "roles": roles}
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    expected_archive_total = sum(size for _role, size, _part in expected_parts)
    if expected_archive_total > maximum_archive_bytes or len(manifest_bytes) > maximum_part_bytes:
        raise ValueError("guest archive aggregate or immutable manifest exceeds configured bounds")
    peak_staged = max(max(size for _role, size, _part in expected_parts), len(manifest_bytes))
    if peak_staged + minimum_controller_free_bytes > controller_quota_free_bytes:
        raise OSError("measured controller user quota cannot hold one bounded archive object plus reserve")
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_key, completion_key = prefix + "manifest.json", prefix + "complete.json"
    # A preoccupied prefix is closed; do not mutate it, even if incomplete.
    if backend.stat(manifest_key, timeout_seconds=remaining()) is not None or backend.stat(
            completion_key, timeout_seconds=remaining()) is not None:
        raise ValueError("logical run prefix is already occupied")
    part_keys = [f"{prefix}parts/{role}/{part['path']}-{part['sha256']}"
        for role, _size, part in expected_parts]
    for key in part_keys:
        if backend.stat(key, timeout_seconds=remaining()) is not None:
            raise ValueError("logical run prefix contains an occupied part before upload")
    objects = []
    attempted_objects: list[dict[str, Any]] = []

    def progress(status: str) -> None:
        if object_progress is not None:
            object_progress({"status": status, "verified": False, "bucket": bucket,
                "prefix": prefix, "manifest_sha256": manifest_sha,
                "partial_objects": [dict(row) for row in attempted_objects]})

    def upload_and_verify(*, key: str, source: Path, size_bytes: int, sha256: str,
            maximum_read_bytes: int, label: str) -> dict:
        attempt = {"key": key, "size_bytes": size_bytes, "sha256": sha256,
            "generation": "unknown", "create_outcome": "unknown",
            "verification_state": "not_started"}
        attempted_objects.append(attempt)
        try:
            progress("uploading")
            created = backend.upload(key, source, size_bytes=size_bytes, sha256=sha256,
                if_generation_match=0, chunk_bytes=chunk_bytes, timeout_seconds=remaining())
            generation = str(created.get("generation", ""))
            if generation:
                attempt.update(generation=generation, create_outcome="created",
                    verification_state="pending")
                progress("uploading")
            if not generation or created.get("size_bytes") != size_bytes:
                raise ValueError(f"{label} create response omitted the exact immutable object identity")
            backend.download(key, source, maximum_bytes=maximum_read_bytes,
                timeout_seconds=remaining(), generation=generation)
            if _hash(source, maximum_read_bytes, lambda: remaining()) != (size_bytes, sha256):
                raise ValueError(f"{label} generation-bound readback failed")
            attempt["verification_state"] = "verified"
            progress("uploading")
            return {"key": key, "size_bytes": size_bytes, "sha256": sha256,
                "generation": generation}
        except Exception as exc:
            attempt["failure_stage"] = ("readback" if attempt["create_outcome"] == "created"
                else "create_or_response")
            attempt["verification_state"] = "failed_or_unknown"
            try:
                progress("failed")
            except Exception:
                pass
            raise GCSArchiveFailure(f"{label} publication failed ({type(exc).__name__})",
                partial_objects=[dict(row) for row in attempted_objects]) from exc

    for role, size, part in expected_parts:
        path = None
        try:
            path = Path(fetch_part(role, part))
            if path.parent.resolve() != staging_directory.resolve() or path.is_symlink() or not path.is_file():
                raise ValueError("guest part fetch did not return a file in the bounded staging directory")
            if _hash(path, maximum_part_bytes, lambda: remaining()) != (size, part["sha256"]):
                raise ValueError("guest part failed controller size/hash verification")
            key = f"{prefix}parts/{role}/{part['path']}-{part['sha256']}"
            objects.append(upload_and_verify(key=key, source=path, size_bytes=size,
                sha256=part["sha256"], maximum_read_bytes=maximum_part_bytes,
                label="guest part"))
        except GCSArchiveFailure:
            raise
        except Exception as exc:
            if attempted_objects:
                try:
                    progress("failed")
                except Exception:
                    pass
                raise GCSArchiveFailure(f"guest archive staging failed ({type(exc).__name__})",
                    partial_objects=[dict(row) for row in attempted_objects]) from exc
            raise
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
    for key, content, digest in ((manifest_key, manifest_bytes, manifest_sha),):
        if len(content) + minimum_controller_free_bytes > controller_quota_free_bytes:
            exc = OSError("measured controller user quota cannot hold the immutable archive manifest")
            if attempted_objects:
                progress("failed")
                raise GCSArchiveFailure(str(exc), partial_objects=[dict(row) for row in attempted_objects]) from exc
            raise exc
        with tempfile.TemporaryDirectory(prefix="cnb-guest-meta-", dir=staging_directory) as temp:
            source = Path(temp) / "metadata.json"
            source.write_bytes(content)
            objects.append(upload_and_verify(key=key, source=source, size_bytes=len(content),
                sha256=digest, maximum_read_bytes=max(maximum_part_bytes, len(content)),
                label="GCS manifest"))
    completion = {"schema_version": 2, "manifest_sha256": manifest_sha,
        "objects": objects}
    complete_bytes = (json.dumps(completion, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if (len(complete_bytes) > maximum_part_bytes
            or len(complete_bytes) + minimum_controller_free_bytes > controller_quota_free_bytes):
        exc = OSError("measured controller user quota cannot hold the immutable completion receipt")
        progress("failed")
        raise GCSArchiveFailure(str(exc), partial_objects=[dict(row) for row in attempted_objects]) from exc
    complete_sha = hashlib.sha256(complete_bytes).hexdigest()
    with tempfile.TemporaryDirectory(prefix="cnb-guest-complete-", dir=staging_directory) as temp:
        source = Path(temp) / "complete.json"
        source.write_bytes(complete_bytes)
        completion_object = upload_and_verify(key=completion_key, source=source,
            size_bytes=len(complete_bytes), sha256=complete_sha,
            maximum_read_bytes=max(maximum_part_bytes, len(complete_bytes)), label="GCS completion")
        completion_generation = completion_object["generation"]
    try:
        for row in [*objects, {"key": completion_key, "generation": completion_generation,
                               "size_bytes": len(complete_bytes)}]:
            current = backend.stat(row["key"], timeout_seconds=remaining())
            if current is None or str(current.get("generation")) != row["generation"] or current.get("size_bytes") != row["size_bytes"]:
                raise ValueError("a verified archive generation changed before acceptance")
    except Exception as exc:
        progress("failed")
        raise GCSArchiveFailure(f"archive acceptance check failed ({type(exc).__name__})",
            partial_objects=[dict(row) for row in attempted_objects]) from exc
    return {"status": "verified", "verified": True, "validation_state": "archive_pending_validation",
        "bucket": bucket, "prefix": prefix, "manifest_sha256": manifest_sha,
        "manifest": manifest, "objects": objects, "completion_generation": completion_generation,
        "archive_bytes": expected_archive_total, "run_id": run_id}


def materialize_verified_archive(*, backend: GCSObjectBackend, bucket: str, provider: str,
        experiment_id: str, run_id: str, expected_manifest_sha256: str,
        archive_directory: Path, materialization_root: Path,
        maximum_part_bytes: int, minimum_free_bytes: int = 0) -> dict:
    """Download a completed archive by generation, verify, then extract safely.

    Without a replay callback the returned receipt explicitly remains pending;
    archive integrity alone never upgrades data validation.
    """
    from .closed_archive import verify_and_extract_archive

    if not valid_bucket_name(bucket) or provider not in {"aws", "azure", "gcp"}:
        raise ValueError("archive destination identity is invalid")
    if not all(_ID.fullmatch(value) for value in (experiment_id, run_id)):
        raise ValueError("experiment and run identities must be stable safe identifiers")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_manifest_sha256):
        raise ValueError("expected manifest digest must be a SHA-256 identity")
    if type(maximum_part_bytes) is not int or maximum_part_bytes <= 0:
        raise ValueError("maximum materialized part bytes must be positive")
    if type(minimum_free_bytes) is not int or minimum_free_bytes < 0:
        raise ValueError("minimum materialization headroom must be nonnegative")
    archive_directory, materialization_root = Path(archive_directory), Path(materialization_root)
    for path, label in ((archive_directory, "archive directory"),
                        (materialization_root, "materialization root")):
        if path.is_symlink() or not path.is_dir() or path.resolve(strict=True) != path.absolute():
            raise ValueError(f"{label} must be an existing real directory without symlink components")
    destination = materialization_root / f"{provider}-{experiment_id}-{run_id}-native"
    local_archive = archive_directory / f"{provider}-{experiment_id}-{run_id}.archive"
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("native materialization destination must be new")
    if local_archive.exists() or local_archive.is_symlink():
        raise FileExistsError("archive staging destination must be new")

    prefix = f"cnb/v1/{provider}/{experiment_id}/{run_id}/"
    complete_key, manifest_key = prefix + "complete.json", prefix + "manifest.json"
    complete_ref = backend.stat(complete_key, timeout_seconds=30)
    if not complete_ref or not complete_ref.get("generation"):
        raise ValueError("archive completion marker is absent")
    complete_path = archive_directory / f".{provider}-{experiment_id}-{run_id}.complete.partial"
    if complete_path.exists() or complete_path.is_symlink():
        raise FileExistsError("completion staging path is occupied")
    try:
        backend.download(complete_key, complete_path, maximum_bytes=maximum_part_bytes,
            timeout_seconds=30, generation=str(complete_ref["generation"]))
        complete = json.loads(complete_path.read_bytes())
        if complete.get("schema_version") == 1:
            raise ValueError("legacy archive completion schema v1 is unsupported for generation-bound restoration")
        if (complete.get("schema_version") != 2
                or complete.get("manifest_sha256") != expected_manifest_sha256
                or not isinstance(complete.get("objects"), list)):
            raise ValueError("completion marker does not bind the requested archive")
        objects = complete["objects"]
        keys = [row.get("key") for row in objects if isinstance(row, dict)]
        if len(keys) != len(objects) or len(set(keys)) != len(keys) or manifest_key not in keys:
            raise ValueError("completion object inventory is malformed")
        rows_by_key = {row["key"]: row for row in objects}
        manifest_ref = backend.stat(manifest_key, timeout_seconds=30)
        manifest_row = rows_by_key[manifest_key]
        if (manifest_ref is None or not manifest_ref.get("generation")
                or manifest_ref.get("size_bytes") != manifest_row.get("size_bytes")
                or type(manifest_row.get("size_bytes")) is not int
                or not 0 < manifest_row["size_bytes"] <= maximum_part_bytes):
            raise ValueError("manifest generation or size differs from completion")
        local_archive.mkdir(mode=0o700)
        manifest_path = local_archive / "manifest.json"
        backend.download(manifest_key, manifest_path, maximum_bytes=maximum_part_bytes,
            timeout_seconds=30, generation=str(manifest_ref["generation"]))
        manifest_bytes = manifest_path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
            raise ValueError("manifest bytes do not match the bound digest")
        manifest = json.loads(manifest_bytes)
        parts = manifest.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError("archive manifest has no part inventory")
        expected = {manifest_key: (len(manifest_bytes), expected_manifest_sha256)}
        part_key_by_name = {}
        for number, part in enumerate(parts, 1):
            if (not isinstance(part, dict) or part.get("path") != f"archive.tar.gz.part-{number:05d}"
                    or type(part.get("size_bytes")) is not int
                    or not 0 < part["size_bytes"] <= maximum_part_bytes
                    or not re.fullmatch(r"[a-f0-9]{64}", str(part.get("sha256", "")))):
                raise ValueError("archive manifest part identity is invalid")
            key = f"{prefix}parts/{number:08d}-{part['sha256']}"
            expected[key] = (part["size_bytes"], part["sha256"])
            part_key_by_name[key] = part["path"]
        if set(keys) != set(expected):
            raise ValueError("completion and manifest inventories differ")
        required = sum(size for size, _digest in expected.values()) + minimum_free_bytes
        if shutil.disk_usage(archive_directory).free < required:
            raise OSError("analysis destination lacks archive bytes plus required headroom")
        for key, (size, digest) in expected.items():
            row = rows_by_key[key]
            current = backend.stat(key, timeout_seconds=30)
            if (current is None or not current.get("generation")
                    or current.get("generation") != row.get("generation")
                    or current.get("size_bytes") != size or row.get("size_bytes") != size):
                raise ValueError("archive object generation or size differs from completion")
            if key == manifest_key:
                continue
            target = local_archive / part_key_by_name[key]
            backend.download(key, target, maximum_bytes=maximum_part_bytes,
                timeout_seconds=30, generation=str(current["generation"]))
            if _hash(target, maximum_part_bytes, lambda: None) != (size, digest):
                raise ValueError("downloaded archive part failed byte-count or SHA-256 verification")
        final_ref = backend.stat(complete_key, timeout_seconds=30)
        if final_ref is None or final_ref.get("generation") != complete_ref["generation"]:
            raise ValueError("completion marker changed during materialization")
        native_manifest = verify_and_extract_archive(local_archive, destination,
            expected_manifest_sha256=expected_manifest_sha256,
            free_bytes=lambda path: shutil.disk_usage(path).free,
            minimum_headroom_bytes=minimum_free_bytes)
        receipt = {"status": "materialized_pending_replay", "verified": True,
            "validation_status": "not_validated", "replay_status": "not_run",
            "bucket": bucket, "prefix": prefix, "manifest_sha256": expected_manifest_sha256,
            "archive_path": str(local_archive), "native_root": str(destination),
            "raw_bytes": native_manifest["raw_bytes"], "archive_bytes": native_manifest["archive_bytes"],
            "completion_generation": str(complete_ref["generation"])}
        return receipt
    except BaseException:
        shutil.rmtree(local_archive, ignore_errors=True)
        if destination.exists() and not destination.is_symlink():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        complete_path.unlink(missing_ok=True)


def materialize_verified_guest_archives(*, backend: GCSObjectBackend, bucket: str,
        provider: str, experiment_id: str, run_id: str, expected_manifest_sha256: str,
        archive_directory: Path, materialization_root: Path, maximum_part_bytes: int,
        minimum_free_bytes: int = 0) -> dict:
    """Restore a role-separated guest archive into one replay-native tree.

    Every part is generation-bound through the immutable completion receipt,
    verified against the guest manifest, and safely extracted before any file
    is published into the native result root.
    """
    from .closed_archive import verify_and_extract_archive
    from .retrieval import RetrievalManifest
    if not valid_bucket_name(bucket) or provider not in {"aws", "azure", "gcp"}:
        raise ValueError("archive destination identity is invalid")
    if not all(_ID.fullmatch(value) for value in (experiment_id, run_id)):
        raise ValueError("experiment and run identities must be stable safe identifiers")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_manifest_sha256):
        raise ValueError("expected manifest digest must be a SHA-256 identity")
    if type(maximum_part_bytes) is not int or maximum_part_bytes <= 0 or type(minimum_free_bytes) is not int or minimum_free_bytes < 0:
        raise ValueError("restore part bound and free-space reserve must be valid integers")
    archive_directory, materialization_root = Path(archive_directory), Path(materialization_root)
    for path, label in ((archive_directory, "archive staging directory"), (materialization_root, "native restore directory")):
        if path.is_symlink() or not path.is_dir() or path.resolve(strict=True) != path.absolute():
            raise ValueError(f"{label} must be an existing real directory without symlink components")
    if archive_directory.stat().st_dev != materialization_root.stat().st_dev:
        raise ValueError("archive and restore staging must share a filesystem to keep the measured peak bounded")
    destination = materialization_root / f"{provider}-{experiment_id}-{run_id}-native"
    local_archive = archive_directory / f"{provider}-{experiment_id}-{run_id}.guest-archive"
    if destination.exists() or destination.is_symlink() or local_archive.exists() or local_archive.is_symlink():
        raise FileExistsError("guest archive restore destinations must be new")
    prefix = f"cnb/v1/{provider}/{experiment_id}/{run_id}/"
    complete_key, manifest_key = prefix + "complete.json", prefix + "manifest.json"
    complete_ref = backend.stat(complete_key, timeout_seconds=30)
    if not complete_ref or not complete_ref.get("generation"):
        raise ValueError("archive completion marker is absent")
    complete_path = archive_directory / f".{run_id}.guest-complete.partial"
    if complete_path.exists() or complete_path.is_symlink():
        raise FileExistsError("completion staging path is occupied")
    created_native_dirs = []
    try:
        backend.download(complete_key, complete_path, maximum_bytes=maximum_part_bytes,
            timeout_seconds=30, generation=str(complete_ref["generation"]))
        completion = json.loads(complete_path.read_bytes())
        if (completion.get("schema_version") != 2 or completion.get("manifest_sha256") != expected_manifest_sha256
                or not isinstance(completion.get("objects"), list)):
            raise ValueError("completion receipt does not bind the requested role archive")
        refs = {row.get("key"): row for row in completion["objects"] if isinstance(row, dict)}
        if len(refs) != len(completion["objects"]) or len(refs) != len(completion["objects"]):
            raise ValueError("completion object generation inventory is malformed")
        manifest_ref = refs.get(manifest_key)
        if manifest_ref is None or type(manifest_ref.get("size_bytes")) is not int or not 0 < manifest_ref["size_bytes"] <= maximum_part_bytes:
            raise ValueError("completion receipt has no bounded immutable manifest entry")
        current_manifest = backend.stat(manifest_key, timeout_seconds=30)
        if (current_manifest is None or str(current_manifest.get("generation")) != str(manifest_ref.get("generation"))
                or current_manifest.get("size_bytes") != manifest_ref["size_bytes"]
                or manifest_ref.get("sha256") != expected_manifest_sha256):
            raise ValueError("archive manifest object generation differs from completion")
        local_archive.mkdir(mode=0o700)
        manifest_path = local_archive / "manifest.json"
        backend.download(manifest_key, manifest_path, maximum_bytes=maximum_part_bytes,
            timeout_seconds=30, generation=str(current_manifest["generation"]))
        manifest_bytes = manifest_path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
            raise ValueError("archive manifest bytes differ from its pinned digest")
        manifest = json.loads(manifest_bytes)
        if any(manifest.get(field) != value for field, value in (("provider", provider),
                ("experiment_id", experiment_id), ("run_id", run_id))):
            raise ValueError("archive manifest identity differs from the requested run")
        closed = RetrievalManifest.model_validate(manifest.get("closed_inventory"))
        if closed.identity.get("run_id") != run_id or closed.identity.get("provider") != provider:
            raise ValueError("closed retrieval inventory identity differs from the archive path")
        role_rows = manifest.get("roles")
        if not isinstance(role_rows, list) or {row.get("role") for row in role_rows if isinstance(row, dict)} != {"vm_a", "vm_b"}:
            raise ValueError("archive must contain exactly both VM role manifests")
        expected_by_role = {}
        for role in ("vm_a", "vm_b"):
            expected_by_role[role] = []
            for item in closed.items:
                if item.source_role == role:
                    relative = str(PurePosixPath(item.source_path).relative_to(PurePosixPath(closed.remote_workspace)))
                    expected_by_role[role].append({"path": relative,
                        "size_bytes": item.expected_size_bytes, "sha256": item.expected_sha256})
        expected_keys = {manifest_key}
        total_archive_bytes = 0
        total_raw_bytes = sum(item.expected_size_bytes for item in closed.items)
        if total_raw_bytes + minimum_free_bytes > shutil.disk_usage(materialization_root).free:
            raise OSError("native restore destination lacks raw payload plus measured headroom")
        projected_archive_bytes = 0
        for role_row in role_rows:
            parts = role_row.get("parts")
            if not isinstance(parts, list) or not parts:
                raise ValueError("role archive part list is empty")
            size_sum = 0
            for number, part in enumerate(parts, 1):
                if (part.get("path") != f"archive.tar.gz.part-{number:05d}"
                        or type(part.get("size_bytes")) is not int
                        or not 0 < part["size_bytes"] <= maximum_part_bytes
                        or not re.fullmatch(r"[a-f0-9]{64}", str(part.get("sha256", "")))):
                    raise ValueError("role archive part declaration is malformed")
                size_sum += part["size_bytes"]
            if size_sum != role_row.get("archive_bytes"):
                raise ValueError("role archive total differs from its ordered part sizes")
            projected_archive_bytes += size_sum
        required_peak = total_raw_bytes + projected_archive_bytes + minimum_free_bytes
        if shutil.disk_usage(archive_directory).free < required_peak:
            raise OSError("restore filesystem lacks compressed archive, native output and reserve peak")
        role_native = []
        for role_row in role_rows:
            role = role_row["role"]
            expected_files = sorted(expected_by_role[role], key=lambda row: row["path"])
            files = role_row.get("files")
            if not expected_files or files != expected_files:
                raise ValueError("role archive files differ from the frozen retrieval inventory")
            parts = role_row.get("parts")
            if not isinstance(parts, list) or not parts:
                raise ValueError("role archive part list is empty")
            standard = {"schema_version": 1, "format": "deterministic-tar-gzip",
                "raw_bytes": sum(row["size_bytes"] for row in files),
                "archive_bytes": role_row.get("archive_bytes"),
                "archive_sha256": role_row.get("archive_sha256"), "files": files, "parts": parts}
            # Guest role receipts bind the exact deterministic ClosedArchive manifest.
            if (type(standard["archive_bytes"]) is not int or standard["archive_bytes"] <= 0
                    or not re.fullmatch(r"[a-f0-9]{64}", str(standard["archive_sha256"]))):
                raise ValueError("role archive total identity is malformed")
            role_dir = local_archive / role
            role_dir.mkdir(mode=0o700)
            (role_dir / "manifest.json").write_text(json.dumps(standard, sort_keys=True,
                separators=(",", ":")) + "\n", encoding="utf-8")
            for number, part in enumerate(parts, 1):
                if (part.get("path") != f"archive.tar.gz.part-{number:05d}"
                        or type(part.get("size_bytes")) is not int
                        or not 0 < part["size_bytes"] <= maximum_part_bytes
                        or not re.fullmatch(r"[a-f0-9]{64}", str(part.get("sha256", "")))):
                    raise ValueError("role archive part declaration is malformed")
                key = f"{prefix}parts/{role}/{part['path']}-{part['sha256']}"
                expected_keys.add(key)
                ref = refs.get(key)
                current = backend.stat(key, timeout_seconds=30)
                if (ref is None or current is None or str(ref.get("generation")) != str(current.get("generation"))
                        or current.get("size_bytes") != part["size_bytes"] or ref.get("size_bytes") != part["size_bytes"]
                        or ref.get("sha256") != part["sha256"]):
                    raise ValueError("role part generation or size differs from completion receipt")
                target = role_dir / part["path"]
                backend.download(key, target, maximum_bytes=maximum_part_bytes,
                    timeout_seconds=30, generation=str(current["generation"]))
                if _hash(target, maximum_part_bytes, lambda: None) != (part["size_bytes"], part["sha256"]):
                    raise ValueError("restored role part failed size or SHA-256 verification")
                total_archive_bytes += part["size_bytes"]
            native = local_archive / f"{role}-native"
            verify_and_extract_archive(role_dir, native,
                expected_manifest_sha256=hashlib.sha256((role_dir / "manifest.json").read_bytes()).hexdigest())
            role_native.append((role, native))
        if set(refs) != expected_keys:
            raise ValueError("completion object inventory differs from the role manifests")
        current_complete = backend.stat(complete_key, timeout_seconds=30)
        if current_complete is None or str(current_complete.get("generation")) != str(complete_ref["generation"]):
            raise ValueError("completion marker changed during restore")
        for key, row in refs.items():
            current = backend.stat(key, timeout_seconds=30)
            if (current is None or str(current.get("generation")) != str(row.get("generation"))
                    or current.get("size_bytes") != row.get("size_bytes")):
                raise ValueError("a manifest-recorded object changed before restored native publication")
        destination.mkdir(mode=0o700)
        for role, root in role_native:
            by_source = {item.source_path: item for item in closed.items if item.source_role == role}
            for item in by_source.values():
                relative = str(PurePosixPath(item.source_path).relative_to(PurePosixPath(closed.remote_workspace)))
                source = root / relative
                size, digest = _hash(source, total_raw_bytes, lambda: None)
                if (size, digest) != (item.expected_size_bytes, item.expected_sha256):
                    raise ValueError("restored native file differs from its lifecycle artifact record")
                target = destination / item.destination_path
                if target.exists() or target.is_symlink():
                    raise FileExistsError("native restore artifact path is occupied")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
        expected_inventory = sorted(({"path": item.destination_path,
            "size_bytes": item.expected_size_bytes, "sha256": item.expected_sha256} for item in closed.items),
            key=lambda row: row["path"])
        actual_inventory = []
        for path in sorted(destination.rglob("*")):
            if path.is_symlink():
                raise ValueError("restored native tree contains a symlink")
            if path.is_file():
                size, digest = _hash(path, total_raw_bytes, lambda: None)
                actual_inventory.append({"path": path.relative_to(destination).as_posix(),
                    "size_bytes": size, "sha256": digest})
        if actual_inventory != expected_inventory:
            raise ValueError("published native inventory differs from the immutable closed inventory")
        return {"status": "materialized_pending_replay", "verified": True,
            "validation_status": "not_validated", "replay_status": "not_run", "bucket": bucket,
            "prefix": prefix, "manifest_sha256": expected_manifest_sha256, "manifest": manifest,
            "archive_path": str(local_archive), "native_root": str(destination),
            "raw_bytes": total_raw_bytes, "archive_bytes": total_archive_bytes,
            "completion_generation": str(complete_ref["generation"])}
    except BaseException:
        shutil.rmtree(local_archive, ignore_errors=True)
        shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        complete_path.unlink(missing_ok=True)
