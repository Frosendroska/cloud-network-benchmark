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
    with pytest.raises(ValueError, match="native source changed after archive sealing"):
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
    class InterruptReadback(FakeGCS):
        interrupted = False
        def download(self, key, destination, **kwargs):
            if "/parts/" in key and not self.interrupted:
                self.interrupted = True
                raise TimeoutError("synthetic interrupted readback")
            return super().download(key, destination, **kwargs)
    backend = InterruptReadback()
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


def test_bounded_relay_reconstructs_archive_and_never_stages_full_bundle(tmp_path):
    import gzip
    import tarfile
    import io
    from cloud_network_benchmark.gcs_archive import relay_closed_native_tree
    source, _unused = _archive(tmp_path)
    backend = FakeGCS()
    statuses = []
    result = relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
        experiment_id="exp-001", run_id="relay-001", maximum_staging_bytes=4096,
        closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
        staging_directory=tmp_path, deadline=Deadline(), status_callback=statuses.append)
    assert result["verified"] and result["staging_peak_bytes"] <= 4096
    manifest_object = backend.objects[result["prefix"] + "manifest.json"]
    assert list(backend.objects)[-2:] == [result["prefix"] + "manifest.json",
                                         result["prefix"] + "complete.json"]
    manifest = json.loads(manifest_object[0])
    compressed = b"".join(backend.objects[row["key"]][0] for row in result["objects"]
        if "/parts/" in row["key"])
    assert hashlib.sha256(compressed).hexdigest() == manifest["archive_sha256"]
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(compressed)), mode="r:") as archive:
        assert {member.name: archive.extractfile(member).read() for member in archive.getmembers()} == {
            path.name: path.read_bytes() for path in source.iterdir()}
    assert all(path.is_file() for path in source.iterdir())
    assert not list(tmp_path.glob("cnb-gcs-relay-*"))
    assert statuses[0]["status"] == "progress" and statuses[-1]["status"] == "verified"
    assert statuses[-1]["verified_parts"] == len(manifest["parts"])
    backend.objects.pop(result["objects"][0]["key"])
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure
    with pytest.raises(GCSArchiveFailure, match="completed archive is missing a remote part"):
        relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="relay-001", maximum_staging_bytes=4096,
            closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=Deadline())
    assert all(path.is_file() for path in source.iterdir())


def test_bounded_relay_rechecks_parts_after_completion_upload(tmp_path):
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure, relay_closed_native_tree
    source, _unused = _archive(tmp_path)

    class DeletePartAtCompletion(FakeGCS):
        def upload(self, key, source, **kwargs):
            result = super().upload(key, source, **kwargs)
            if key.endswith("complete.json"):
                part_key = next(key for key in self.objects if "/parts/" in key)
                del self.objects[part_key]
            return result

    backend = DeletePartAtCompletion()
    with pytest.raises(GCSArchiveFailure, match="before completion acceptance"):
        relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="completion-race-001", maximum_staging_bytes=4096,
            closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=Deadline())
    assert all(path.is_file() for path in source.iterdir())


def test_bounded_relay_rescans_sources_after_sink_finishes(tmp_path, monkeypatch):
    import cloud_network_benchmark.closed_archive as closed_archive
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure, relay_closed_native_tree
    source, _unused = _archive(tmp_path)
    original = closed_archive.create_closed_archive

    def finish_then_mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        (source / "sockperf.csv").write_bytes(b"mutated after sink finish")
        return result

    monkeypatch.setattr(closed_archive, "create_closed_archive", finish_then_mutate)
    backend = FakeGCS()
    with pytest.raises(GCSArchiveFailure, match="source changed while"):
        relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="source-race-001", maximum_staging_bytes=4096,
            closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=Deadline())
    assert not any(key.endswith(("manifest.json", "complete.json")) for key in backend.objects)
    assert (source / "sockperf.csv").read_bytes() == b"mutated after sink finish"


def test_bounded_relay_rejects_changed_source_before_mutating_occupied_prefix(tmp_path):
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure, relay_closed_native_tree
    source, _unused = _archive(tmp_path)

    class InterruptAfterManifest(FakeGCS):
        def upload(self, key, source, **kwargs):
            result = super().upload(key, source, **kwargs)
            if key.endswith("manifest.json"):
                raise InterruptedError("synthetic interruption after manifest creation")
            return result

    backend = InterruptAfterManifest()
    with pytest.raises(GCSArchiveFailure):
        relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="occupied-prefix-001", maximum_staging_bytes=4096,
            closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=Deadline())
    assert f"cnb/v1/aws/exp-001/occupied-prefix-001/manifest.json" in backend.objects
    assert f"cnb/v1/aws/exp-001/occupied-prefix-001/complete.json" not in backend.objects
    existing_keys = set(backend.objects)
    (source / "sockperf.csv").write_bytes(b"different source reuses this run identity")
    with pytest.raises(GCSArchiveFailure, match="different native source inventory"):
        relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="occupied-prefix-001", maximum_staging_bytes=4096,
            closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=Deadline())
    assert set(backend.objects) == existing_keys


def test_bounded_relay_manifest_metadata_collision_with_missing_part_creates_nothing(tmp_path):
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure, relay_closed_native_tree
    source, _unused = _archive(tmp_path)

    class InterruptAfterManifest(FakeGCS):
        uploads = 0
        def upload(self, key, source, **kwargs):
            result = super().upload(key, source, **kwargs)
            self.uploads += 1
            if key.endswith("manifest.json"):
                raise InterruptedError("synthetic interruption after manifest creation")
            return result

    backend = InterruptAfterManifest()
    options = dict(bucket="valid-bucket", provider="aws", experiment_id="exp-001",
        run_id="manifest-metadata-collision-001", maximum_staging_bytes=4096,
        closure_verified=True, maximum_archive_bytes=2_000_000,
        minimum_controller_free_bytes=0, staging_directory=tmp_path, deadline=Deadline())
    with pytest.raises(GCSArchiveFailure):
        relay_closed_native_tree(source, backend=backend, **options)

    prefix = "cnb/v1/aws/exp-001/manifest-metadata-collision-001/"
    manifest_key = prefix + "manifest.json"
    completion_key = prefix + "complete.json"
    prior_manifest = json.loads(backend.objects[manifest_key][0])
    backend.objects.pop(completion_key, None)
    missing_part = prefix + "parts/00000001-" + prior_manifest["parts"][0]["sha256"]
    backend.objects.pop(missing_part)
    prior_manifest["archive_sha256"] = "0" * 64
    altered_bytes = (json.dumps(prior_manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    backend.objects[manifest_key] = (altered_bytes, backend.objects[manifest_key][1])
    before = dict(backend.objects)
    upload_count = backend.uploads

    with pytest.raises(GCSArchiveFailure, match="missing a declared remote part"):
        relay_closed_native_tree(source, backend=backend, **options)

    assert backend.objects == before
    assert backend.uploads == upload_count


def test_bounded_relay_resume_preserves_partial_generations_and_rejects_source_change(tmp_path):
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure, relay_closed_native_tree
    source, _unused = _archive(tmp_path)
    class InterruptReadback(FakeGCS):
        interrupted = False
        def download(self, key, destination, **kwargs):
            if "/parts/" in key and not self.interrupted:
                self.interrupted = True
                raise TimeoutError("synthetic interrupted readback")
            return super().download(key, destination, **kwargs)
    backend = InterruptReadback()
    with pytest.raises(GCSArchiveFailure) as failed:
        relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="resume-001", maximum_staging_bytes=4096,
            closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=Deadline())
    assert failed.value.partial_objects and all(row["generation"] != "unknown" for row in failed.value.partial_objects)
    resumed = relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
        experiment_id="exp-001", run_id="resume-001", maximum_staging_bytes=4096,
        closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
        staging_directory=tmp_path, deadline=Deadline())
    assert resumed["verified"]
    (source / "sockperf.csv").write_bytes(b"changed")
    with pytest.raises(GCSArchiveFailure, match="different native source inventory"):
        relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="resume-001", maximum_staging_bytes=4096,
            closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=Deadline())


def test_bounded_relay_rejects_one_part_headroom_and_keeps_originals(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import cloud_network_benchmark.gcs_archive as archive_sink
    from cloud_network_benchmark.gcs_archive import relay_closed_native_tree
    source, _unused = _archive(tmp_path)
    monkeypatch.setattr(archive_sink.shutil, "disk_usage", lambda _path: SimpleNamespace(free=4095))
    with pytest.raises(OSError, match="one bounded part, manifest/state overhead"):
        relay_closed_native_tree(source, backend=FakeGCS(), bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="space-001", maximum_staging_bytes=4096,
            closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=Deadline())
    assert all(path.is_file() for path in source.iterdir())


def test_bounded_relay_requires_closed_sources_and_uses_one_deadline(tmp_path):
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure, relay_closed_native_tree
    source, _unused = _archive(tmp_path)
    with pytest.raises(ValueError, match="closed"):
        relay_closed_native_tree(source, backend=FakeGCS(), bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="open-001", maximum_staging_bytes=4096,
            closure_verified=False, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=Deadline())

    class ExpiringDeadline:
        calls = 0
        def remaining(self, _clock, *, cleanup=False):
            self.calls += 1
            return 1 if self.calls < 4 else 0
    backend = FakeGCS()
    with pytest.raises(TimeoutError):
        relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="deadline-001", maximum_staging_bytes=4096,
            closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=ExpiringDeadline())
    assert all(path.is_file() for path in source.iterdir())


def _published_archive(tmp_path, *, run_id="replay-001"):
    from cloud_network_benchmark.gcs_archive import relay_closed_native_tree
    source, _unused = _archive(tmp_path)
    backend = FakeGCS()
    status = relay_closed_native_tree(source, backend=backend, bucket="valid-bucket",
        provider="aws", experiment_id="exp-001", run_id=run_id,
        maximum_staging_bytes=4096, closure_verified=True, maximum_archive_bytes=2_000_000,
        minimum_controller_free_bytes=0, staging_directory=tmp_path, deadline=Deadline())
    raw_bytes = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
    assert raw_bytes > status["staging_peak_bytes"]
    assert status["staging_peak_bytes"] <= 4096
    completion = json.loads(backend.objects[f"{status['prefix']}complete.json"][0])
    assert completion["schema_version"] == 2
    assert all(row.get("generation") for row in completion["objects"])
    return source, backend, status


def test_materialize_verified_archive_stays_pending_until_explicit_replay(tmp_path):
    from cloud_network_benchmark.gcs_archive import materialize_verified_archive
    source, backend, uploaded = _published_archive(tmp_path)
    archive_destination = tmp_path / "analysis-archive"
    archive_destination.mkdir()
    native_destination = tmp_path / "analysis-native"
    native_destination.mkdir()
    receipt = materialize_verified_archive(backend=backend, bucket="valid-bucket",
        provider="aws", experiment_id="exp-001", run_id="replay-001",
        expected_manifest_sha256=uploaded["manifest_sha256"],
        archive_directory=archive_destination, materialization_root=native_destination,
        maximum_part_bytes=4096)
    assert receipt["status"] == "materialized_pending_replay"
    assert receipt["validation_status"] == "not_validated"
    assert receipt["replay_status"] == "not_run"
    assert (Path(receipt["native_root"]) / "sockperf.csv").read_bytes() == (source / "sockperf.csv").read_bytes()

    second_archive = tmp_path / "analysis-archive-2"
    second_archive.mkdir()
    second_native = tmp_path / "analysis-native-2"
    second_native.mkdir()
    replayed = materialize_verified_archive(backend=backend, bucket="valid-bucket",
        provider="aws", experiment_id="exp-001", run_id="replay-001",
        expected_manifest_sha256=uploaded["manifest_sha256"],
        archive_directory=second_archive, materialization_root=second_native,
        maximum_part_bytes=4096,
        replay=lambda _root: {"validation_status": "valid", "result_id": "offline-replay-1"})
    assert replayed["status"] == "replayed"
    assert replayed["validation_status"] == "valid"
    assert replayed["replay_result"]["result_id"] == "offline-replay-1"


def test_guest_archive_is_deterministic_bounded_and_preserves_native_sources(tmp_path):
    from cloud_network_benchmark.guest_archive import create_guest_archive
    source, _unused = _archive(tmp_path)
    original = {path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()}
    from cloud_network_benchmark.closed_archive import projected_archive_upper_bound
    upper_bound = projected_archive_upper_bound(sum(map(len, original.values())), len(original))
    first = create_guest_archive(source, tmp_path / "guest-archive-a",
        maximum_part_bytes=4096, maximum_archive_bytes=upper_bound, minimum_free_bytes=0)
    second = create_guest_archive(source, tmp_path / "guest-archive-b",
        maximum_part_bytes=4096, maximum_archive_bytes=upper_bound, minimum_free_bytes=0)
    assert first == second
    assert all(row["size_bytes"] <= 4096 for row in first["parts"])
    assert {path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()} == original


def test_guest_archive_rejects_output_inside_source_and_projection_over_limit(tmp_path):
    from cloud_network_benchmark.guest_archive import create_guest_archive
    source, _unused = _archive(tmp_path)
    with pytest.raises(ValueError, match="inside"):
        create_guest_archive(source, source / ".archive", maximum_part_bytes=4096,
            maximum_archive_bytes=2_000_000, minimum_free_bytes=0)
    with pytest.raises(ValueError, match="upper bound"):
        create_guest_archive(source, tmp_path / "too-large", maximum_part_bytes=4096,
            maximum_archive_bytes=1, minimum_free_bytes=0)


@pytest.mark.parametrize("corruption", ["missing_part", "generation", "content"])
def test_materialization_rejects_missing_or_changed_remote_part(tmp_path, corruption):
    from cloud_network_benchmark.gcs_archive import materialize_verified_archive
    _source, backend, uploaded = _published_archive(tmp_path)
    prefix = "cnb/v1/aws/exp-001/replay-001/"
    manifest = json.loads(backend.objects[prefix + "manifest.json"][0])
    key = prefix + "parts/00000001-" + manifest["parts"][0]["sha256"]
    if corruption == "missing_part":
        backend.objects.pop(key)
    elif corruption == "generation":
        content, _generation = backend.objects[key]
        backend.objects[key] = (content, "altered-generation")
    else:
        content, generation = backend.objects[key]
        backend.objects[key] = (content + b"corrupt", generation)
    archive_destination = tmp_path / "analysis-archive"
    archive_destination.mkdir()
    native_destination = tmp_path / "analysis-native"
    native_destination.mkdir()
    with pytest.raises((ValueError, FileNotFoundError, RuntimeError)):
        materialize_verified_archive(backend=backend, bucket="valid-bucket",
            provider="aws", experiment_id="exp-001", run_id="replay-001",
            expected_manifest_sha256=uploaded["manifest_sha256"],
            archive_directory=archive_destination, materialization_root=native_destination,
            maximum_part_bytes=4096)
    assert not list(native_destination.iterdir())


def test_materialization_rejects_unsafe_or_occupied_destinations_and_fabricated_replay(tmp_path):
    from cloud_network_benchmark.gcs_archive import materialize_verified_archive
    _source, backend, uploaded = _published_archive(tmp_path)
    archive_destination = tmp_path / "analysis-archive"
    archive_destination.mkdir()
    native_destination = tmp_path / "analysis-native"
    native_destination.mkdir()
    occupied = native_destination / "aws-exp-001-replay-001-native"
    occupied.mkdir()
    with pytest.raises(FileExistsError):
        materialize_verified_archive(backend=backend, bucket="valid-bucket",
            provider="aws", experiment_id="exp-001", run_id="replay-001",
            expected_manifest_sha256=uploaded["manifest_sha256"],
            archive_directory=archive_destination, materialization_root=native_destination,
            maximum_part_bytes=4096)
    occupied.rmdir()
    unsafe_root = tmp_path / "unsafe-link"
    unsafe_root.symlink_to(native_destination, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        materialize_verified_archive(backend=backend, bucket="valid-bucket",
            provider="aws", experiment_id="exp-001", run_id="replay-001",
            expected_manifest_sha256=uploaded["manifest_sha256"],
            archive_directory=archive_destination, materialization_root=unsafe_root,
            maximum_part_bytes=4096)
    with pytest.raises(ValueError, match="explicit validation_status"):
        materialize_verified_archive(backend=backend, bucket="valid-bucket",
            provider="aws", experiment_id="exp-001", run_id="replay-001",
            expected_manifest_sha256=uploaded["manifest_sha256"],
            archive_directory=archive_destination, materialization_root=native_destination,
            maximum_part_bytes=4096, replay=lambda _root: {"valid": True})


def test_bounded_relay_part_collision_is_generation_bound_and_preserves_originals(tmp_path):
    from cloud_network_benchmark.closed_archive import create_closed_archive
    from cloud_network_benchmark.gcs_archive import GCSArchiveFailure, relay_closed_native_tree
    source, _unused = _archive(tmp_path)
    sealed = tmp_path / "sealed-for-collision"
    manifest = create_closed_archive(source, sealed, max_part_bytes=4096)
    first = manifest["parts"][0]
    backend = FakeGCS()
    key = f"cnb/v1/aws/exp-001/collision-001/parts/00000001-{first['sha256']}"
    backend.objects[key] = (b"x" * first["size_bytes"], "foreign-generation")
    with pytest.raises(GCSArchiveFailure):
        relay_closed_native_tree(source, backend=backend, bucket="valid-bucket", provider="aws",
            experiment_id="exp-001", run_id="collision-001", maximum_staging_bytes=4096,
            closure_verified=True, maximum_archive_bytes=2_000_000, minimum_controller_free_bytes=0,
            staging_directory=tmp_path, deadline=Deadline())
    assert backend.objects[key][1] == "foreign-generation"
    assert all(path.is_file() for path in source.iterdir())

