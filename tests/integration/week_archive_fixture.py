"""Build a real, offline F09-native WEEK archive replay fixture.

The fixture runs the existing AWS fake-boundary lifecycle/archive scenario and
captures the actual capsule, receipt, backend, native roots and real restore call.
It is deliberately AWS-only; it does not claim a complete three-provider WEEK
entry. Call ``cleanup`` after any campaign reconciliation assertions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class WeekArchiveFixture:
    capsule_path: Path
    capsule_sha256: str
    capsule: dict[str, Any]
    receipt: dict[str, Any]
    backend: Any
    bucket: str
    archive_staging_root: Path
    materialization_root: Path
    repository_root: Path
    restore: Any
    restore_kwargs: dict[str, Any]
    cleanup: Any


def build_single_provider_week_archive_fixture(*, tmp_path: Path, monkeypatch: Any,
                                              repository_root: Path) -> WeekArchiveFixture:
    """Return generated native/archive evidence from production F09 code paths.

    This reuses the existing lifecycle integration scenario rather than
    fabricating a receipt or validator result. It stops at the exact archive
    pending boundary, before the genuine restore call. Its request finalizers
    are returned as an explicit cleanup callback so callers can keep source
    manifests and artifacts available through campaign reconciliation.
    """
    from cloud_network_benchmark import archive_replay
    from tests.integration import test_f09_aws_runtime_factory as f09

    captured: dict[str, Any] = {}
    restore = archive_replay.restore_and_finalize_batch_capsule

    class CapturedBeforeReplay(Exception):
        pass

    def capture_restore(**kwargs):
        captured["kwargs"] = kwargs
        raise CapturedBeforeReplay

    monkeypatch.setattr(archive_replay, "restore_and_finalize_batch_capsule", capture_restore)

    class Request:
        def __init__(self) -> None:
            self.finalizers: list[Any] = []

        def addfinalizer(self, callback: Any) -> None:
            self.finalizers.append(callback)

    request = Request()

    def cleanup() -> None:
        for finalizer in reversed(request.finalizers):
            finalizer()

    try:
        f09.test_actual_lifecycle_archive_sink_bounded_receive_and_pending_capsule_fake_only(
            tmp_path=tmp_path,
            repository_root=repository_root,
            fake_git_commits={"implementation": "a" * 40, "design": "b" * 40},
            request=request,
            receive_fault=None,
        )
    except CapturedBeforeReplay:
        pass
    except BaseException:
        cleanup()
        raise
    if not captured:
        cleanup()
        raise AssertionError("F09 archive scenario did not produce a replay capsule")

    kwargs = captured["kwargs"]
    capsule_path = Path(kwargs["capsule_path"])
    capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
    receipt = capsule["archives"]["aws"]

    return WeekArchiveFixture(
        capsule_path=capsule_path,
        capsule_sha256=kwargs["expected_capsule_sha256"],
        capsule=capsule,
        receipt=receipt,
        backend=kwargs["backend"],
        bucket=kwargs["bucket"],
        archive_staging_root=Path(kwargs["archive_staging_root"]),
        materialization_root=Path(kwargs["materialization_root"]),
        repository_root=Path(kwargs["repository_root"]),
        restore=restore,
        restore_kwargs=kwargs,
        cleanup=cleanup,
    )
