from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud_network_benchmark import replay_quota


USER = "brek"
UID = 25357
DEVICE = "nashalle.in.tum.de:/srv/halle/drec"
MOUNT = "/u/halle/drec"
SAMPLE = (
    "Disk quotas for user brek (uid 25357): \n"
    "     Filesystem  blocks   quota   limit   grace   files   quota   limit   grace\n"
    "nashalle.in.tum.de:/srv/halle/drec /u/halle/drec 20495392  52428800 52428800       0  477143       0       0       0\n"
)
POLICY = {"metadata_headroom_bytes": 4096,
    "projected_other_growth_bytes": 8192, "reserve_bytes": 16384}


def _scope(root: Path) -> dict:
    return {"user": USER, "uid": UID, "device": DEVICE, "mountpoint": str(root),
        "filesystem_paths": [str(root)], "minimum_available_bytes": 1_000_000}


def _sample(tmp_path, monkeypatch, *, stdout=SAMPLE, returncode=0, identity=(UID, UID, USER),
            command_error=None, now=None):
    monkeypatch.setattr(replay_quota, "QUOTA_BINARY", Path("/usr/bin/true"))
    captured = {}

    def runner(argv, *, timeout, env):
        captured.update(argv=argv, timeout=timeout, env=env)
        if command_error is not None:
            raise command_error
        local_stdout = stdout.replace(MOUNT, str(tmp_path))
        return SimpleNamespace(returncode=returncode, stdout=local_stdout.encode("utf-8"))

    fixed_now = now or datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    result = replay_quota.sample_replay_quota(scope_value=_scope(tmp_path),
        entry_id="P0-01", runtime_config_sha256="a" * 64, frozen_policy=POLICY,
        filesystem_paths=[str(tmp_path)], receipt_directory=tmp_path,
        now=lambda: fixed_now, identity_reader=lambda: identity, command_runner=runner)
    return result, captured, fixed_now


def test_observed_halle_row_yields_hash_bound_receipt_and_trusted_filtered_argv(tmp_path, monkeypatch):
    receipt, captured, now = _sample(tmp_path, monkeypatch)
    payload = replay_quota.load_replay_quota_receipt(path=Path(receipt["path"]),
        expected_sha256=receipt["sha256"], scope_value=_scope(tmp_path), entry_id="P0-01",
        runtime_config_sha256="a" * 64, frozen_policy=POLICY,
        filesystem_paths=[str(tmp_path)], now=now)

    assert captured["argv"] == ["/usr/bin/true", "--user", "--verbose", "--no-wrap",
        "--raw-grace", "--show-mntpoint", f"--filesystem={tmp_path}", USER]
    assert captured["timeout"] == 10
    assert captured["env"] == {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}
    assert payload["parsed_quota"]["blocks_used_kib"] == 20_495_392
    assert payload["parsed_quota"]["blocks_soft_limit_kib"] == 52_428_800
    assert payload["parsed_quota"]["blocks_hard_limit_kib"] == 52_428_800
    assert payload["parsed_quota"]["inodes_used"] == 477_143
    assert payload["parsed_quota"]["inodes_soft_limit"] == payload["parsed_quota"]["inodes_hard_limit"] == 0
    assert payload["measured_free_bytes"] == (52_428_800 - 20_495_392) * 1024
    assert payload["metadata_headroom_bytes"] == POLICY["metadata_headroom_bytes"]
    assert payload["minimum_available_bytes"] == 1_000_000
    assert hashlib.sha256(payload["stdout"].encode()).hexdigest() == payload["stdout_sha256"]
    assert Path(receipt["path"]).stat().st_mode & 0o777 == 0o600


def test_parser_accepts_the_exact_read_only_halle_sample():
    parsed = replay_quota.parse_quota_output(SAMPLE, user=USER, uid=UID,
        device=DEVICE, mountpoint=MOUNT)
    assert parsed["available_bytes"] == (52_428_800 - 20_495_392) * 1024


@pytest.mark.parametrize("change", [
    lambda text: text.replace("uid 25357", "uid 1"),
    lambda text: text.replace("nashalle.in.tum.de:/srv/halle/drec", "other-device:/drec"),
    lambda text: text.replace("/u/halle/drec 20495392", "/other/mount 20495392"),
    lambda text: text.replace("52428800 52428800", "0 0"),
    lambda text: text.replace("20495392", "52428800"),
    lambda text: text.replace("52428800 52428800       0", "52428800 52428800       1"),
    lambda text: text.replace("477143       0       0       0", "477143 1 2 0"),
    lambda text: text + "extra row\n",
    lambda text: text.replace("20495392  52428800", "NaN  52428800"),
])
def test_quota_parser_rejects_wrong_scope_unlimited_exhausted_and_ambiguous_rows(change):
    with pytest.raises(replay_quota.ReplayQuotaError):
        replay_quota.parse_quota_output(change(SAMPLE), user=USER, uid=UID,
            device=DEVICE, mountpoint=MOUNT)


def test_quota_sampler_rejects_wrong_controller_identity_before_command(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_quota, "QUOTA_BINARY", Path("/usr/bin/true"))
    calls = []
    with pytest.raises(replay_quota.ReplayQuotaError, match="controller uid/euid/user"):
        replay_quota.sample_replay_quota(scope_value=_scope(tmp_path), entry_id="P0-01",
            runtime_config_sha256="a" * 64, frozen_policy=POLICY,
            filesystem_paths=[str(tmp_path)], receipt_directory=tmp_path,
            identity_reader=lambda: (UID + 1, UID + 1, USER),
            command_runner=lambda *a, **k: calls.append(a))
    assert calls == []
    assert not list(tmp_path.glob("week-quota-*.json"))


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("quota", 10), OSError("missing")])
def test_quota_sampler_fails_closed_on_timeout_or_command_error(tmp_path, monkeypatch, error):
    monkeypatch.setattr(replay_quota, "QUOTA_BINARY", Path("/usr/bin/true"))
    with pytest.raises(replay_quota.ReplayQuotaError):
        replay_quota.sample_replay_quota(scope_value=_scope(tmp_path), entry_id="P0-01",
            runtime_config_sha256="a" * 64, frozen_policy=POLICY,
            filesystem_paths=[str(tmp_path)], receipt_directory=tmp_path,
            identity_reader=lambda: (UID, UID, USER),
            command_runner=lambda *a, **k: (_ for _ in ()).throw(error))
    assert not list(tmp_path.glob("week-quota-*.json"))


def test_quota_sampler_rejects_nonzero_exit_without_receipt(tmp_path, monkeypatch):
    with pytest.raises(replay_quota.ReplayQuotaError, match="exited with status 2"):
        _sample(tmp_path, monkeypatch, returncode=2)
    assert not list(tmp_path.glob("week-quota-*.json"))


@pytest.mark.parametrize("seconds", [-1, 901])
def test_quota_receipt_rejects_future_and_stale_observations(tmp_path, monkeypatch, seconds):
    receipt, _, now = _sample(tmp_path, monkeypatch)
    payload = json.loads(Path(receipt["path"]).read_text())
    payload["measured_at"] = (now - timedelta(seconds=seconds)).isoformat()
    encoded = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    Path(receipt["path"]).write_bytes(encoded)
    with pytest.raises(replay_quota.ReplayQuotaError, match="future-dated or more than 15 minutes old"):
        replay_quota.load_replay_quota_receipt(path=Path(receipt["path"]),
            expected_sha256=hashlib.sha256(encoded).hexdigest(), scope_value=_scope(tmp_path),
            entry_id="P0-01", runtime_config_sha256="a" * 64,
            frozen_policy=POLICY, filesystem_paths=[str(tmp_path)], now=now)


def test_quota_receipt_tampering_and_frozen_policy_change_are_rejected(tmp_path, monkeypatch):
    receipt, _, now = _sample(tmp_path, monkeypatch)
    path = Path(receipt["path"])
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(replay_quota.ReplayQuotaError, match="missing or changed"):
        replay_quota.load_replay_quota_receipt(path=path, expected_sha256=receipt["sha256"],
            scope_value=_scope(tmp_path), entry_id="P0-01", runtime_config_sha256="a" * 64,
            frozen_policy=POLICY, filesystem_paths=[str(tmp_path)], now=now)

