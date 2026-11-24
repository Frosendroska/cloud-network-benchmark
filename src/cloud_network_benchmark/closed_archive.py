"""Deterministic lossless archives for closed native result trees.

Archives are emitted as bounded parts (each below FAT32's 4 GiB limit), with
the manifest written last. An interrupted build has no publishable manifest.
Verification streams, checks every part and payload digest, and atomically
publishes extracted files only after the complete archive validates.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from typing import Callable, Iterable

FAT32_FILE_LIMIT = (1 << 32) - 1
_CHUNK = 1024 * 1024


def _sha(path: Path, guard: Callable[[], None] | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(_CHUNK):
            if guard is not None:
                guard()
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _safe_files(source: Path, guard: Callable[[], None] | None = None) -> list[tuple[str, Path, int, str]]:
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("archive source must be a real directory")
    source = source.resolve(strict=True)
    result = []
    for path in sorted(source.rglob("*")):
        if guard is not None:
            guard()
        if path.is_symlink():
            raise ValueError(f"archive source contains a symlink: {path.relative_to(source)}")
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            if not relative or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
                raise ValueError("archive member path is unsafe")
            size, digest = _sha(path, guard)
            result.append((relative, path, size, digest))
    if not result:
        raise ValueError("cannot archive an empty result tree")
    return result


class _PartSink:
    def __init__(self, root: Path, max_part_bytes: int, guard: Callable[[], None] | None = None,
                 on_part: Callable[[Path, dict], None] | None = None):
        self.root, self.limit = root, max_part_bytes
        self.guard = guard
        self.on_part = on_part
        self.parts: list[dict] = []
        self.archive_hash = hashlib.sha256()
        self.archive_bytes = 0
        self.stream = None
        self.part_bytes = 0
        self.part_hash = hashlib.sha256()

    def _open(self):
        name = f"archive.tar.gz.part-{len(self.parts) + 1:05d}.partial"
        self.stream = (self.root / name).open("xb")
        self.part_bytes = 0
        self.part_hash = hashlib.sha256()

    def write(self, data: bytes) -> int:
        offset = 0
        while offset < len(data):
            if self.guard is not None:
                self.guard()
            if self.stream is None or self.part_bytes == self.limit:
                self._close_part()
                self._open()
            count = min(len(data) - offset, self.limit - self.part_bytes)
            block = data[offset:offset + count]
            self.stream.write(block)
            self.part_hash.update(block)
            self.archive_hash.update(block)
            self.part_bytes += count
            self.archive_bytes += count
            offset += count
        return len(data)

    def _close_part(self):
        if self.stream is None:
            return
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        number = len(self.parts) + 1
        partial = self.root / f"archive.tar.gz.part-{number:05d}.partial"
        final = self.root / f"archive.tar.gz.part-{number:05d}"
        os.replace(partial, final)
        self.parts.append({"path": final.name, "size_bytes": self.part_bytes,
                           "sha256": self.part_hash.hexdigest()})
        if self.on_part is not None:
            self.on_part(final, self.parts[-1])
            final.unlink()
        self.stream = None

    def finish(self):
        self._close_part()


def projected_archive_upper_bound(raw_bytes: int, file_count: int) -> int:
    """Conservative bound for gzip's worst case plus tar headers/padding."""
    if type(raw_bytes) is not int or raw_bytes < 0 or type(file_count) is not int or file_count < 1:
        raise ValueError("archive projection requires nonnegative bytes and positive file count")
    return raw_bytes + raw_bytes // 100 + file_count * 10240 + 1024 * 1024


def copy_archive_parts(source: Path, destination: Path, *, expected_manifest_sha256: str,
                       free_bytes: Callable[[Path], int] | None = None,
                       minimum_headroom_bytes: int = 0,
                       before_part: Callable[[int], None] | None = None) -> Path:
    """Copy only compressed parts, then publish the bound manifest last.

    Retry is idempotent only when an existing complete destination has the
    expected manifest digest and all parts still verify. Partial destinations
    are never treated as successful; callers may retain them for diagnosis.
    """
    source = Path(source).resolve(strict=True)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("archive source must be a real directory")
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("archive source has no complete manifest")
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if digest != expected_manifest_sha256:
        raise ValueError("source manifest does not match its bound SHA-256")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = sum(item["size_bytes"] for item in manifest["parts"]) + manifest_path.stat().st_size
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            existing = destination / "manifest.json"
            if existing.is_file() and hashlib.sha256(existing.read_bytes()).hexdigest() == digest:
                for part in manifest["parts"]:
                    if _sha(destination / part["path"]) != (part["size_bytes"], part["sha256"]):
                        raise OSError("existing archive copy is corrupt")
                return destination
        raise FileExistsError("archive destination exists without matching verified content")
    parent = destination.parent.resolve(strict=True)
    if free_bytes is not None and free_bytes(parent) < required + minimum_headroom_bytes:
        raise OSError("destination free space does not cover compressed archive and required headroom")
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".partial", dir=parent))
    try:
        for index, part in enumerate(manifest["parts"], 1):
            source_part, target_part = source / part["path"], temp / part["path"]
            if source_part.is_symlink() or not source_part.is_file():
                raise ValueError("archive source part is missing or unsafe")
            with source_part.open("rb") as src, target_part.open("xb") as dst:
                shutil.copyfileobj(src, dst, _CHUNK)
                dst.flush()
                os.fsync(dst.fileno())
            if _sha(target_part) != (part["size_bytes"], part["sha256"]):
                raise OSError("copied archive part failed size or SHA-256 verification")
            if before_part is not None:
                before_part(index)
        copied_manifest = temp / "manifest.json.partial"
        shutil.copyfile(manifest_path, copied_manifest)
        if hashlib.sha256(copied_manifest.read_bytes()).hexdigest() != expected_manifest_sha256:
            raise OSError("copied archive manifest failed SHA-256 verification")
        os.replace(copied_manifest, temp / "manifest.json")
        os.replace(temp, destination)
        return destination
    except BaseException:
        # Preserve the partial directory for a later diagnostic/retry; it has
        # no manifest and therefore cannot pass the verification gate.
        raise


def create_closed_archive(source: Path, destination: Path, *, max_part_bytes: int = FAT32_FILE_LIMIT,
                          free_bytes: Callable[[Path], int] | None = None,
                          minimum_headroom_bytes: int = 0,
                          minimum_source_headroom_bytes: int = 0,
                          before_write: Callable[[int], None] | None = None,
                          guard: Callable[[], None] | None = None,
                          on_part: Callable[[Path, dict], None] | None = None,
                          include_files: Iterable[str] | None = None) -> dict:
    """Create and publish a deterministic archive directory, preserving source.

    Parts use a conservative 4 GiB-minus-one bound by default, so each part is
    directly copyable to FAT32. The caller may choose smaller bounds for tests
    or transport limits.
    """
    if type(max_part_bytes) is not int or not 1 <= max_part_bytes <= FAT32_FILE_LIMIT:
        raise ValueError("max_part_bytes must fit a FAT32 file")
    if type(minimum_headroom_bytes) is not int or minimum_headroom_bytes < 0:
        raise ValueError("minimum_headroom_bytes must be a nonnegative integer")
    if type(minimum_source_headroom_bytes) is not int or minimum_source_headroom_bytes < 0:
        raise ValueError("minimum_source_headroom_bytes must be a nonnegative integer")
    source = Path(source).resolve(strict=True)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("archive destination must be new")
    rows = _safe_files(source, guard)
    if include_files is not None:
        selected = list(include_files)
        if (not selected or len(selected) != len(set(selected))
                or any(not isinstance(name, str) or not name or PurePosixPath(name).is_absolute()
                    or PurePosixPath(name).as_posix() != name or ".." in PurePosixPath(name).parts
                    for name in selected)):
            raise ValueError("archive file selection must contain unique normalized relative paths")
        by_name = {row[0]: row for row in rows}
        if set(selected) - set(by_name):
            raise ValueError("archive file selection references a missing source file")
        rows = [by_name[name] for name in sorted(selected)]
    raw_bytes = sum(row[2] for row in rows)
    upper = projected_archive_upper_bound(raw_bytes, len(rows))
    if free_bytes is not None and free_bytes(source) < minimum_source_headroom_bytes:
        raise OSError("source free space is below required headroom")
    parent = destination.parent.resolve(strict=True)
    if free_bytes is not None:
        required = upper + minimum_headroom_bytes if on_part is None else max_part_bytes + minimum_headroom_bytes
        if free_bytes(parent) < required:
            raise OSError("destination free space does not cover bounded archive part and required headroom")
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".partial", dir=parent))
    try:
        sink = _PartSink(temp, max_part_bytes, guard, on_part)
        with gzip.GzipFile(filename="", mode="wb", fileobj=sink, mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                for name, path, size, _digest in rows:
                    info = tarfile.TarInfo(name)
                    info.size, info.mtime, info.uid, info.gid = size, 0, 0, 0
                    info.uname = info.gname = ""
                    info.mode = 0o644
                    with path.open("rb") as payload:
                        try:
                            archive.addfile(info, payload)
                        except Exception as exc:
                            if _sha(path, guard) != (size, _digest):
                                raise ValueError(f"archive source changed while streaming: {name}") from exc
                            raise
                    if _sha(path, guard) != (size, _digest):
                        raise ValueError(f"archive source changed while streaming: {name}")
                    if before_write is not None:
                        before_write(size)
        sink.finish()
        manifest = {"schema_version": 1, "format": "deterministic-tar-gzip",
                    "raw_bytes": raw_bytes, "archive_bytes": sink.archive_bytes,
                    "archive_sha256": sink.archive_hash.hexdigest(), "parts": sink.parts,
                    "files": [{"path": name, "size_bytes": size, "sha256": digest}
                              for name, _path, size, digest in rows]}
        marker = temp / "manifest.json.partial"
        marker.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        with marker.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(marker, temp / "manifest.json")
        dir_fd = os.open(temp, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        os.replace(temp, destination)
        return manifest
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise


class _PartReader:
    def __init__(self, root: Path, parts: list[dict]):
        self.root, self.parts = root, parts
        self.index = 0
        self.stream = None
        self.archive_hash = hashlib.sha256()
        self.archive_bytes = 0

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        chunks = []
        left = size
        while size < 0 or left > 0:
            if self.stream is None:
                if self.index >= len(self.parts):
                    break
                self.stream = (self.root / self.parts[self.index]["path"]).open("rb")
            block = self.stream.read(_CHUNK if size < 0 else min(_CHUNK, left))
            if not block:
                self.stream.close()
                self.stream = None
                self.index += 1
                continue
            chunks.append(block)
            self.archive_hash.update(block)
            self.archive_bytes += len(block)
            if size > 0:
                left -= len(block)
        return b"".join(chunks)


def verify_and_extract_archive(archive_dir: Path, destination: Path, *,
                               expected_manifest_sha256: str,
                               free_bytes: Callable[[Path], int] | None = None,
                               minimum_headroom_bytes: int = 0) -> dict:
    """Verify archive parts, manifest and payloads, then atomically publish."""
    archive_dir = Path(archive_dir).resolve(strict=True)
    if archive_dir.is_symlink() or not archive_dir.is_dir():
        raise ValueError("archive source must be a real directory")
    manifest_path = archive_dir / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("archive manifest is absent; incomplete archive is not publishable")
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if (not isinstance(expected_manifest_sha256, str) or len(expected_manifest_sha256) != 64
            or manifest_digest != expected_manifest_sha256):
        raise ValueError("archive manifest SHA-256 differs from the separately bound expected digest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("format") != "deterministic-tar-gzip":
        raise ValueError("unsupported archive manifest")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("archive manifest must list parts")
    for part in parts:
        path = archive_dir / part["path"]
        size, digest = _sha(path)
        if size != part["size_bytes"] or digest != part["sha256"] or size > FAT32_FILE_LIMIT:
            raise ValueError("archive part size or SHA-256 mismatch")
    if sum(item["size_bytes"] for item in parts) != manifest.get("archive_bytes"):
        raise ValueError("archive total size differs from manifest")
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("extraction destination must be new")
    parent = destination.parent.resolve(strict=True)
    required = int(manifest["raw_bytes"]) + minimum_headroom_bytes
    if free_bytes is not None and free_bytes(parent) < required:
        raise OSError("destination free space does not cover raw output and required headroom")
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".partial", dir=parent))
    reader = _PartReader(archive_dir, parts)
    expected = {item["path"]: item for item in manifest["files"]}
    seen = set()
    try:
        with gzip.GzipFile(fileobj=reader, mode="rb") as compressed:
            with tarfile.open(fileobj=compressed, mode="r|") as archive:
                for member in archive:
                    name = member.name
                    PurePosixPath(name)
                    if (name not in expected or name in seen or not member.isfile()
                            or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts):
                        raise ValueError("archive contains an unlisted, duplicate or unsafe member")
                    row = expected[name]
                    if member.size != row["size_bytes"]:
                        raise ValueError("archive payload size differs from manifest")
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError("archive member could not be read")
                    target = temp / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest, size = hashlib.sha256(), 0
                    with target.open("xb") as output:
                        while block := source.read(_CHUNK):
                            output.write(block)
                            digest.update(block)
                            size += len(block)
                        output.flush()
                        os.fsync(output.fileno())
                    if size != row["size_bytes"] or digest.hexdigest() != row["sha256"]:
                        raise ValueError(f"payload verification failed: {name}")
                    seen.add(name)
        while reader.read(_CHUNK):
            pass
        if seen != set(expected):
            raise ValueError("archive is missing manifest payloads")
        if reader.archive_bytes != manifest["archive_bytes"] or reader.archive_hash.hexdigest() != manifest["archive_sha256"]:
            raise ValueError("archive SHA-256 or byte count differs from manifest")
        os.replace(temp, destination)
        return manifest
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
