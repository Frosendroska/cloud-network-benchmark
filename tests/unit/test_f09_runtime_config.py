import json

import pytest

from cloud_network_benchmark.lifecycle_runtime import load_runtime_config


def test_runtime_config_rejects_duplicate_keys_nonfinite_and_unknown_fields(tmp_path):
    for content, message in (
        ('{"schema_version":1,"schema_version":1}', "duplicate"),
        ('{"schema_version":NaN}', "non-finite"),
        (json.dumps({"schema_version": 1, "surprise": True}), "exactly"),
    ):
        path = tmp_path / "runtime.json"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_runtime_config(path)


def test_runtime_config_requires_exact_provider_selection(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly"):
        load_runtime_config(path, selected_providers={"gcp"})
