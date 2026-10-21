"""Deterministic export/check for the public V2 JSON schemas."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Type

from pydantic import BaseModel

from . import (
    CampaignConfiguration, ResolvedCampaign, ScheduleEntry, ReservationRequest,
    ReservationEvidence, ExpiryRequest, ExpiryEvidence, DiagnosticSample, VmMetadata,
    ProviderDeploymentInput, ProviderDeploymentOutput, ValidatorInput, ValidationResult,
)
from ..manifests import CampaignManifest, ChildRunManifest

SCHEMAS: dict[str, Type[BaseModel]] = {
    "campaign-config.schema.json": CampaignConfiguration,
    "resolved-campaign.schema.json": ResolvedCampaign,
    "campaign-manifest.schema.json": CampaignManifest,
    "child-run-manifest.schema.json": ChildRunManifest,
    "provider-deployment-input.schema.json": ProviderDeploymentInput,
    "provider-deployment-output.schema.json": ProviderDeploymentOutput,
    "validator-input.schema.json": ValidatorInput,
    "validation-result.schema.json": ValidationResult,
    "schedule-entry.schema.json": ScheduleEntry,
    "reservation-request.schema.json": ReservationRequest,
    "reservation-evidence.schema.json": ReservationEvidence,
    "expiry-request.schema.json": ExpiryRequest,
    "expiry-evidence.schema.json": ExpiryEvidence,
    "diagnostic-sample.schema.json": DiagnosticSample,
    "vm-metadata.schema.json": VmMetadata,
}


def schema_bytes(model: Type[BaseModel]) -> bytes:
    schema = model.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return (json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def export(output: Path, check: bool = False) -> bool:
    output.mkdir(parents=True, exist_ok=True)
    success = True
    for filename, model in SCHEMAS.items():
        path, expected = output / filename, schema_bytes(model)
        if check:
            if not path.is_file() or path.read_bytes() != expected:
                print(f"schema drift: {path}")
                success = False
        else:
            path.write_bytes(expected)
    return success


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return 0 if export(args.output, args.check) else 1


if __name__ == "__main__":
    raise SystemExit(main())
