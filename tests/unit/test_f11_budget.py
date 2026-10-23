import hashlib
import json
from pathlib import Path

import pytest

from cloud_network_benchmark.admission import load_cost_inputs, _decimal


ROOT = Path(__file__).parents[2]


def test_scoped_cost_input_hashes_saved_payload_and_keeps_forecast_dated():
    source = ROOT / "tests/fixtures/f11/cost-inputs.json"
    costs = load_cost_inputs(source)
    assert costs.payload_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert costs.payload["funding"]["primary_forecast"] == "147.82"
    assert costs.prices_checked_on == "2026-10-14"
    assert costs.refresh_required
    assert costs.payload["live_quote"] is False


@pytest.mark.parametrize("value", [True, -1, "NaN", "Infinity"])
def test_money_rejects_boolean_negative_and_nonfinite(value):
    with pytest.raises(ValueError):
        _decimal(value)
