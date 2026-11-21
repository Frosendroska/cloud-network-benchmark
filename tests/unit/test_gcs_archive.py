from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

from cloud_network_benchmark.closed_archive import create_closed_archive
from cloud_network_benchmark.gcs_archive import upload_sealed_archive


class Deadline:
    def remaining(self, clock, *, cleanup=False):
        return 300.0


def _sink(source):
    return {"source_directory": source, "maximum_archive_bytes": 1_000_000,
        "minimum_controller_free_bytes": 0, "staging_directory": source,
        "deadline": Deadline(), "clock": lambda: 0.0}


class FakeGCS:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.interrupt_after = None
        self.download_corrupt = set()
        self.download_fail = set()

    def stat(self, key, *, timeout_seconds):
        assert timeout_seconds > 0
        row = self.objects.get(key)
        return None if row is None else {"generation": row[1], "size_bytes": len(row[0])}

    def upload(self, key, source, *, size_bytes, sha256, if_generation_match, chunk_bytes, timeout_seconds):
        assert if_generation_match == 0 and chunk_bytes > 0
        assert timeout_seconds > 0
        if key in self.objects:
            raise FileExistsError(key)
        content = Path(source).read_bytes()
        assert len(content) == size_bytes and hashlib.sha256(content).hexdigest() == sha256
        self.objects[key] = (content, str(len(self.objects) + 1))
        if self.interrupt_after == len(self.objects):
            raise InterruptedError("lost response after server stored object")
        return {"generation": self.objects[key][1], "size_bytes": len(content)}

    def download(self, key, destination, *, maximum_bytes, timeout_seconds, generation):
        if key in self.download_fail:
            raise TimeoutError("synthetic remote read timeout")
        if key not in self.objects:
            raise FileNotFoundError(key)
        assert timeout_seconds > 0
        if self.objects[key][1] != generation:
            raise RuntimeError("generation precondition failed")
        content = self.objects[key][0]
        if len(content) > maximum_bytes:
            raise ValueError("staging cap")
        if key in self.download_corrupt:
            content += b"changed"
        Path(destination).write_bytes(content)

    def delete(self, key, *, generation, timeout_seconds):
        assert timeout_seconds > 0
        if self.objects.get(key, (None, None))[1] == generation:
            self.deleted.append(key)
            del self.objects[key]


def _archive(tmp_path, *, max_part_bytes=4096):
    source = tmp_path / "native"
    source.mkdir()
    (source / "sockperf.csv").write_bytes(b"rtt\n" + b"1\n" * 5000)
    (source / "iperf.jsonl").write_bytes(b'{"bytes":1}\n' * 1000)
    archive = tmp_path / "sealed"
    create_closed_archive(source, archive, max_part_bytes=max_part_bytes)
    return source, archive


def _upload(archive, backend, source, **kwargs):
    return upload_sealed_archive(archive, backend=backend, bucket="approved-later",
        provider="aws", experiment_id="exp-001", run_id="run-001",
        maximum_staging_bytes=8192, closure_verified=True, **_sink(source), **kwargs)


def test_upload_is_immutable_verified_idempotent_and_keeps_native_sources(tmp_path):
    source, archive = _archive(tmp_path)
    original = {p.name: p.read_bytes() for p in source.iterdir()}
    backend = FakeGCS()
    first = _upload(archive, backend, source)
    second = _upload(archive, backend, source)
    assert first["verified"] is True and first["status"] == "verified"
    assert first["prefix"] == "cnb/v1/aws/exp-001/run-001/"
    assert {key: value for key, value in first.items() if key != "staging_filesystem_free_bytes"} == {
        key: value for key, value in second.items() if key != "staging_filesystem_free_bytes"}
    assert first["staging_peak_bytes"] <= 8192
    assert {p.name: p.read_bytes() for p in source.iterdir()} == original


def test_requires_sealed_archive_and_rejects_changed_part_after_seal(tmp_path):
    source, archive = _archive(tmp_path)
    backend = FakeGCS()
    (archive / "manifest.json").unlink()
    with pytest.raises(ValueError, match="not sealed"):
        _upload(archive, backend, source)
    with pytest.raises(ValueError, match="closed"):
        upload_sealed_archive(archive, backend=backend, bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="run-001", maximum_staging_bytes=8192,
            closure_verified=False, **_sink(source))
    again = tmp_path / "again"
    again.mkdir()
    source, archive = _archive(again)
    part = json.loads((archive / "manifest.json").read_text())["parts"][0]["path"]
    part_bytes = (archive / part).read_bytes()
    (archive / part).write_bytes(b"mutated after seal")
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure
    with pytest.raises(GCSArchiveFailure):
        _upload(archive, backend, source)
    (archive / part).write_bytes(part_bytes)
    (source / "sockperf.csv").write_bytes(b"changed after seal")
    with pytest.raises(ValueError, match="source changed"):
        _upload(archive, backend, source)


def test_collision_and_remote_missing_or_changed_parts_fail_closed(tmp_path):
    source, archive = _archive(tmp_path)
    backend = FakeGCS()
    done = _upload(archive, backend, source)
    key = done["objects"][0]["key"]
    content, generation = backend.objects[key]
    backend.objects[key] = (content + b"collision", generation)
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure
    with pytest.raises(GCSArchiveFailure):
        _upload(archive, backend, source)
    assert all((archive / item["path"]).is_file() for item in json.loads((archive / "manifest.json").read_text())["parts"])
    backend2 = FakeGCS()
    done2 = _upload(archive, backend2, source)
    backend2.download_corrupt.add(done2["objects"][0]["key"])
    with pytest.raises(GCSArchiveFailure):
        _upload(archive, backend2, source)


def test_interrupted_upload_reconciles_exact_object_and_resumes_idempotently(tmp_path):
    source, archive = _archive(tmp_path)
    backend = FakeGCS()
    backend.interrupt_after = 1
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure
    with pytest.raises(GCSArchiveFailure):
        _upload(archive, backend, source)
    # Simulate process death: a remotely committed first part remains. A retry
    # verifies it byte-for-byte, then conditionally creates the missing objects.
    backend.interrupt_after = None
    result = _upload(archive, backend, source)
    assert result["verified"] is True


def test_missing_remote_part_fails_closed_without_touching_guest_files(tmp_path):
    source, archive = _archive(tmp_path)
    backend = FakeGCS()
    result = _upload(archive, backend, source)
    del backend.objects[result["objects"][0]["key"]]
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure
    with pytest.raises(GCSArchiveFailure):
        _upload(archive, backend, source)
    assert (source / "sockperf.csv").is_file()


def test_provider_experiment_and_run_are_separate_stable_prefixes(tmp_path):
    source, archive = _archive(tmp_path)
    first = upload_sealed_archive(archive, backend=FakeGCS(), bucket="valid-bucket", provider="aws",
        experiment_id="exp-001", run_id="run-001", maximum_staging_bytes=8192,
        closure_verified=True, **_sink(source))
    second = upload_sealed_archive(archive, backend=FakeGCS(), bucket="valid-bucket", provider="azure",
        experiment_id="exp-002", run_id="run-002", maximum_staging_bytes=8192,
        closure_verified=True, **_sink(source))
    assert first["prefix"] == "cnb/v1/aws/exp-001/run-001/"
    assert second["prefix"] == "cnb/v1/azure/exp-002/run-002/"
    assert first["prefix"] != second["prefix"]


def test_second_archive_for_same_logical_run_fails_before_writing_parts(tmp_path):
    source, archive = _archive(tmp_path)
    backend = FakeGCS()
    _upload(archive, backend, source)
    prior_keys = set(backend.objects)
    changed = tmp_path / "other-native"
    changed.mkdir()
    (changed / "sockperf.csv").write_bytes(b"different bytes\n")
    (changed / "iperf.jsonl").write_bytes(b"also different\n")
    other_archive = tmp_path / "other-sealed"
    create_closed_archive(changed, other_archive, max_part_bytes=4096)
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure
    with pytest.raises(GCSArchiveFailure, match="logical run prefix"):
        upload_sealed_archive(other_archive, backend=backend, bucket="approved-later",
            provider="aws", experiment_id="exp-001", run_id="run-001",
            maximum_staging_bytes=8192, closure_verified=True, **_sink(changed))
    assert set(backend.objects) == prior_keys


def test_preflight_timeout_preserves_observed_manifest_key_and_generation(tmp_path):
    source, archive = _archive(tmp_path)
    backend = FakeGCS()
    first = _upload(archive, backend, source)
    manifest = next(row for row in first["objects"] if row["key"].endswith("manifest.json"))
    backend.download_fail.add(manifest["key"])
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure
    with pytest.raises(GCSArchiveFailure) as error:
        _upload(archive, backend, source)
    assert error.value.partial_objects == [{"key": manifest["key"], "generation": manifest["generation"]}]


def test_generation_interleaving_does_not_delete_replacement_object(tmp_path):
    source, archive = _archive(tmp_path)

    class ReplaceBeforeReadback(FakeGCS):
        replaced = False
        def download(self, key, destination, **kwargs):
            if not self.replaced and key in self.objects:
                content, generation = self.objects[key]
                self.objects[key] = (b"replacement", "foreign-generation")
                self.replaced = True
            return super().download(key, destination, **kwargs)

    backend = ReplaceBeforeReadback()
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure
    with pytest.raises(GCSArchiveFailure):
        _upload(archive, backend, source)
    assert "foreign-generation" in {row[1] for row in backend.objects.values()}


def test_deadline_expiring_during_source_hash_fails_before_remote_calls(tmp_path):
    source, archive = _archive(tmp_path)

    class ShortDeadline:
        calls = 0
        def remaining(self, _clock, *, cleanup=False):
            self.calls += 1
            return 1 if self.calls == 1 else 0

    backend = FakeGCS()
    with pytest.raises(TimeoutError):
        upload_sealed_archive(archive, backend=backend, bucket="approved-later", provider="aws",
            experiment_id="exp-001", run_id="run-001", maximum_staging_bytes=8192,
            closure_verified=True, source_directory=source, maximum_archive_bytes=1_000_000,
            minimum_controller_free_bytes=0, staging_directory=source, deadline=ShortDeadline())
    assert backend.objects == {}


def test_controller_staging_headroom_fails_closed_and_removes_only_our_partial_objects(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import cloud_network_benchmark.gcs_archive as archive_sink
    source, archive = _archive(tmp_path)
    backend = FakeGCS()
    monkeypatch.setattr(archive_sink.shutil, "disk_usage", lambda path: SimpleNamespace(
        free=100_000 if Path(path) == archive.parent else 0))
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure
    with pytest.raises(GCSArchiveFailure):
        _upload(archive, backend, source)
    assert backend.objects == {}
    assert (source / "sockperf.csv").is_file()


def test_local_archive_limit_is_checked_before_remote_writes(tmp_path):
    source, archive = _archive(tmp_path)
    backend = FakeGCS()
    from cloud_network_benchmark.gcs_archive import upload_sealed_archive
    with pytest.raises(ValueError, match="local archive byte bound"):
        upload_sealed_archive(archive, backend=backend, bucket="approved-later", provider="aws",
            experiment_id="exp-001", run_id="run-001", maximum_staging_bytes=8192,
            closure_verified=True, source_directory=source, maximum_archive_bytes=1,
            minimum_controller_free_bytes=0, staging_directory=source, deadline=Deadline())
    assert backend.objects == {}


@pytest.mark.parametrize("provider,experiment,run,limit", [
    ("other", "exp-001", "run-001", 8192),
    ("aws", "../exp", "run-001", 8192),
    ("aws", "exp-001", "../run", 8192),
    ("aws", "exp-001", "run-001", 100),
])
def test_identity_prefix_and_controller_quota_are_strict(tmp_path, provider, experiment, run, limit):
    source, archive = _archive(tmp_path)
    with pytest.raises(ValueError):
        upload_sealed_archive(archive, backend=FakeGCS(), bucket="valid-bucket", provider=provider,
            experiment_id=experiment, run_id=run, maximum_staging_bytes=limit,
            closure_verified=True, **_sink(source))


def test_verification_failure_never_deletes_guest_originals_or_reports_success(tmp_path):
    source, archive = _archive(tmp_path)
    original_files = set(source.iterdir())
    backend = FakeGCS()
    # Fail the remote read after upload; local guest/native originals stay intact.
    class BadRead(FakeGCS):
        def download(self, key, destination, **kwargs):
            super().download(key, destination, **kwargs)
            Path(destination).write_bytes(b"wrong")
    bad = BadRead()
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure
    with pytest.raises(GCSArchiveFailure):
        _upload(archive, bad, source)
    assert bad.objects == {}
    assert bad.deleted
    assert set(source.iterdir()) == original_files
    assert not (source / "sockperf.csv").is_symlink()


def test_google_backend_pins_generation_and_disables_sdk_retries(monkeypatch, tmp_path):
    calls = []

    class Blob:
        generation, size = 7, 4
        def reload(self, **kwargs): calls.append(("reload", kwargs))
        def download_to_filename(self, path, **kwargs):
            calls.append(("download", kwargs))
            Path(path).write_bytes(b"data")
        def upload_from_filename(self, _path, **kwargs): calls.append(("upload", kwargs))
        def delete(self, **kwargs): calls.append(("delete", kwargs))

    class Bucket:
        def get_blob(self, key, **kwargs): calls.append(("stat", key, kwargs)); return Blob()
        def blob(self, key, **kwargs): calls.append(("blob", key, kwargs)); return Blob()

    class Client:
        def bucket(self, _name): return Bucket()

    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    cloud.storage = types.SimpleNamespace(Client=Client)
    google.cloud = cloud
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    from cloud_network_benchmark.gcs_archive import GoogleCloudStorageBackend
    backend = GoogleCloudStorageBackend("valid-bucket")
    assert backend.stat("key", timeout_seconds=9)["generation"] == "7"
    target = tmp_path / "remote"
    backend.download("key", target, maximum_bytes=10, timeout_seconds=8, generation="7")
    assert target.read_bytes() == b"data"
    assert calls[0] == ("stat", "key", {"timeout": 9, "retry": None})
    assert calls[1] == ("blob", "key", {"generation": 7})
    assert calls[2][1]["if_generation_match"] == 7 and calls[2][1]["retry"] is None
    assert calls[3][1]["if_generation_match"] == 7 and calls[3][1]["retry"] is None

