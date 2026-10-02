from __future__ import annotations

from typing import Dict

from typing import Literal

from .common import StrictModel


class ArtifactLayout(StrictModel):
    config_snapshot: Literal["config.yaml"] = "config.yaml"
    provider_outputs: Literal["terraform-outputs.json"] = "terraform-outputs.json"
    idle_flent: Literal["flent/idle.flent.gz"] = "flent/idle.flent.gz"
    single_flow_flent: Literal["flent/single_flow.flent.gz"] = "flent/single_flow.flent.gz"
    multi_flow_flent: Literal["flent/multi_flow.flent.gz"] = "flent/multi_flow.flent.gz"
    single_flow_vm_a_diagnostics: Literal["diagnostics/single_flow/vm_a.json"] = "diagnostics/single_flow/vm_a.json"
    single_flow_vm_b_diagnostics: Literal["diagnostics/single_flow/vm_b.json"] = "diagnostics/single_flow/vm_b.json"
    multi_flow_vm_a_diagnostics: Literal["diagnostics/multi_flow/vm_a.json"] = "diagnostics/multi_flow/vm_a.json"
    multi_flow_vm_b_diagnostics: Literal["diagnostics/multi_flow/vm_b.json"] = "diagnostics/multi_flow/vm_b.json"
    vm_a_metadata: Literal["metadata/vm_a.json"] = "metadata/vm_a.json"
    vm_b_metadata: Literal["metadata/vm_b.json"] = "metadata/vm_b.json"
    validation_result: Literal["validation/result.json"] = "validation/result.json"
    validation_summary: Literal["validation/summary.txt"] = "validation/summary.txt"

    def as_mapping(self) -> Dict[str, str]:
        return self.model_dump()


ARTIFACTS = ArtifactLayout()
