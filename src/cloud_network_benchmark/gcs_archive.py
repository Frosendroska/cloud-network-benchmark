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
from pathlib import Path
from typing import Any, Callable, Protocol


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
    def __init__(self, message: str, *, partial_objects: list[dict[str, str]]):
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
        blob = self._bucket.blob(key, chunk_size=chunk_bytes)
        blob.metadata = {"cnb-sha256": sha256}
        blob.upload_from_filename(str(source), if_generation_match=if_generation_match,
                                  checksum="crc32c", timeout=timeout_seconds, retry=None)
        # upload response generation is the exact create-only version; never
        # re-read an unbound latest generation before cleanup or verification.
        if blob.generation is None or blob.size is None:
            raise RuntimeError("GCS create response omitted immutable object identity")
        return {"generation": str(blob.generation), "size_bytes": int(blob.size or 0)}

    def download(self, key: str, destination: Path, *, maximum_bytes: int,
                 timeout_seconds: float, generation: str) -> None:
        blob = self._bucket.blob(key, generation=int(generation))
        blob.reload(if_generation_match=int(generation), timeout=timeout_seconds, retry=None)
        if blob is None or blob.size is None or blob.size > maximum_bytes:
            raise ValueError("remote part is missing or exceeds staging bound")
        blob.download_to_filename(str(destination), checksum="crc32c", timeout=timeout_seconds,
                                  if_generation_match=int(generation), retry=None)

    def delete(self, key: str, *, generation: str, timeout_seconds: float) -> None:
        self._bucket.blob(key, generation=int(generation)).delete(
            if_generation_match=int(generation), timeout=timeout_seconds, retry=None)


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
        completion = {"schema_version": 1, "manifest_sha256": manifest_sha,
            "objects": [{"key": item["key"], "size_bytes": item["size_bytes"],
                         "sha256": item["sha256"]} for item in objects]}
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
