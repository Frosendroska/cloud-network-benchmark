from __future__ import annotations

import json
import os

from cloud_network_benchmark.orchestration import cleanup_lifecycle


def _write_recovery(path, providers, session="old-session"):
    path.write_text(json.dumps({"schema_version": 1, "attempt_id": "attempt-1",
        "controller_session_id": session, "providers": providers}))
    path.chmod(0o600)


def _provider(name, *, cleanup_open=True, expiry=True, creation=True):
    return {"provider": name, "deployment_id": f"attempt-1-{name}-deployment",
        "journal_reference": f"/private/{name}/recovery.json", "expiry_registered": expiry,
        "creation_may_have_started": creation, "cleanup_open": cleanup_open}


def test_changed_session_performs_cleanup_only_and_keeps_unknown_liability_blocked(tmp_path):
    recovery = tmp_path / "recovery.json"
    _write_recovery(recovery, [_provider("aws"), _provider("gcp")])
    called = []
    def cleanup(record):
        called.append((record.provider, "cleanup"))
        return {"requested_delete": True}
    actions = {name: cleanup for name in ("aws", "gcp")}
    outcome = cleanup_lifecycle(recovery, current_session_id="new-session", cleanup_actions=actions,
        reconcile=lambda record, result: {"absence_proved": record.provider == "aws"})
    assert called == [("aws", "cleanup"), ("gcp", "cleanup")]
    assert outcome.original_session_id == "old-session"
    assert outcome.current_session_id == "new-session"
    assert outcome.providers["aws"]["cleanup"] == "succeeded"
    assert outcome.providers["gcp"]["launch_blocked"] is True
    assert not outcome.complete


def test_no_resource_side_effect_evidence_skips_provider_cleanup(tmp_path):
    recovery = tmp_path / "recovery.json"
    _write_recovery(recovery, [_provider("azure", cleanup_open=False, expiry=False, creation=False)])
    called = []
    outcome = cleanup_lifecycle(recovery, current_session_id="new-session",
        cleanup_actions={"azure": lambda record: called.append(record.provider)},
        reconcile=lambda record, result: {"absence_proved": True})
    assert called == []
    assert outcome.complete
    assert outcome.providers["azure"]["cleanup"] == "not_required"


def test_missing_cleanup_adapter_still_reconciles_and_keeps_launch_blocked(tmp_path):
    recovery = tmp_path / "recovery.json"
    _write_recovery(recovery, [_provider("gcp")])
    outcome = cleanup_lifecycle(recovery, current_session_id="new-session", cleanup_actions={},
        reconcile=lambda record, result: {"absence_proved": False})
    assert not outcome.complete
    assert outcome.providers["gcp"]["errors"] == ["provider cleanup adapter unavailable"]
    assert outcome.providers["gcp"]["launch_blocked"]
