"""Optional controller-only GCS sink for sealed, bounded native archives.

The backend is injected so ordinary runs need neither Google credentials nor a
Google client dependency. A production backend must use generation preconditions
and stream downloads to the supplied bounded staging file.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import tempfile
from pathlib import Path
from typing import Any, Callable, Protocol


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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


class GoogleCloudStorageBackend:
    """Production backend. Construct only on the configured controller identity."""

    def __init__(self, bucket: str):
        try:
            from google.cloud import storage
        except ImportError as exc:  # pragma: no cover - optional production extra
            raise RuntimeError("install the gcs optional dependency on the controller") from exc
        self._bucket = storage.Client().bucket(bucket)

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
