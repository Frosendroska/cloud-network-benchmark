"""Synthetic, non-authorizing workflow-envelope sources for offline tests."""
from __future__ import annotations

import hashlib
import json
import tempfile
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Mapping


UNITS = {
    "compute": ("vm-hour", "two comparable VM compute allowance"),
    "disks": ("GB-month", "boot and result disk allowance"),
    "addresses": ("address-hour", "temporary public address allowance"),
    "traffic_export": ("GB-egress", "measurement and result traffic/export allowance"),
    "tax": ("USD", "tax included in this per-run ceiling"),
    "uncertainty": ("USD", "safety margin included in this per-run ceiling"),
    "expiry_cleanup_support": ("region-session", "provider expiry registration and cleanup support included"),
}


def make_synthetic_envelopes(campaign, child_identities: Mapping[str, str], funding: Mapping[str, Decimal]):
    total = sum((Decimal(str(v)) for v in funding.values()), Decimal("0")).quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
    providers = sorted(child_identities)
    base = (total / len(providers)).quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
    rows = {}
    observations = {str(getattr(item.provider, "value", item.provider)): item for item in campaign.observations}
    for index, provider in enumerate(providers):
        provider_total = base if index < len(providers)-1 else total - base * (len(providers)-1)
        weights = {"compute": Decimal("0.40"), "disks": Decimal("0.10"), "addresses": Decimal("0.05"),
            "traffic_export": Decimal("0.10"), "tax": Decimal("0.05"), "uncertainty": Decimal("0.05")}
        components = {}
        used = Decimal("0")
        bucket = sorted(funding)[0]
        for name, weight in weights.items():
            amount = (provider_total * weight).quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
            used += amount
            unit, inclusion = UNITS[name]
            components[name] = {"amount_usd": str(amount), "quantity": "1", "unit": unit,
                "inclusion_rule": inclusion, "funding_bucket": bucket}
        support = provider_total - used
        unit, inclusion = UNITS["expiry_cleanup_support"]
        components["expiry_cleanup_support"] = {"amount_usd": str(support), "quantity": "1", "unit": unit,
            "inclusion_rule": inclusion, "funding_bucket": bucket}
        observation = observations[provider]
        regions = sorted({observation.provider_config.regions.vm_a, observation.provider_config.regions.vm_b})
        rows[provider] = {"envelope_id": f"synthetic:{campaign.config_id}:{provider}", "provider": provider,
            "run_id": child_identities[provider], "priced_on": "2026-10-14", "source_kind": "synthetic-test-only",
            "regions": regions, "unit_basis": "one-two-vm-provider-run", "components": components}
    payload = {"schema_version": 1, "fixture_scope": "synthetic-test-only", "provider_run_envelopes": rows}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = Path(tempfile.gettempdir()) / "cnb-f11-envelope-sources" / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    return {provider: {**row, "source_path": path.as_posix(), "source_sha256": digest} for provider, row in rows.items()}
