from __future__ import annotations

from typing import Dict

from pydantic import Field

from .common import StrictModel


class ArtifactLayout(StrictModel):
    config_snapshot: str = "config.yaml"
    provider_outputs: str = "terraform-outputs.json"
    idle_flent: str = "flent/idle.flent.gz"
    single_flow_flent: str = "flent/single_flow.flent.gz"
    multi_flow_flent: str = "flent/multi_flow.flent.gz"
    single_flow_vm_a_diagnostics: str = "diagnostics/single_flow/vm_a.json"
    single_flow_vm_b_diagnostics: str = "diagnostics/single_flow/vm_b.json"
    multi_flow_vm_a_diagnostics: str = "diagnostics/multi_flow/vm_a.json"
    multi_flow_vm_b_diagnostics: str = "diagnostics/multi_flow/vm_b.json"
    vm_a_metadata: str = "metadata/vm_a.json"
    vm_b_metadata: str = "metadata/vm_b.json"
    validation_result: str = "validation/result.json"
    validation_summary: str = "validation/summary.txt"

    def as_mapping(self) -> Dict[str, str]:
        return self.model_dump()


ARTIFACTS = ArtifactLayout()
