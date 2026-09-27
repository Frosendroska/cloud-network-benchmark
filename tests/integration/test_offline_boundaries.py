import json
from pathlib import Path

from cloud_network_benchmark.cli import main


def test_all_commands_remain_offline(repository_root: Path, tmp_path: Path, capsys: object, deny_external_io: None) -> None:
    assert main(["validate", "--config", "configs/tests/exp-900-single-provider.yaml", "--format", "json"]) == 0
    capsys.readouterr()
    assert main(["resolve", "--config", "configs/tests/exp-900-single-provider.yaml", "--format", "json"]) == 0
    capsys.readouterr()
    assert main(["dry-run", "--config", "configs/tests/exp-900-single-provider.yaml", "--format", "json"]) == 0
    capsys.readouterr()
    assert main(["init", "--config", "configs/tests/exp-900-single-provider.yaml", "--results-root", str(tmp_path / "results"), "--format", "json"]) == 0
    output = capsys.readouterr().out.lower()
    for forbidden in ("private key", "secret", "credential"):
        assert forbidden not in output


def test_json_errors_have_stable_shape(repository_root: Path, capsys: object) -> None:
    assert main(["validate", "--config", "configs/tests/does-not-exist.yaml", "--format", "json"]) == 3
    payload = json.loads(capsys.readouterr().err)
    assert set(payload["error"]) >= {"code", "message", "field"}
