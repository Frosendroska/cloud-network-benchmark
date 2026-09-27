from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address
from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator, model_validator

from .campaign import Placement, VmIntent
from .common import FailureEvidence, Provider, Scenario, StrictModel, StructuredEvidence, VmRole


class RemoteConnectionData(StrictModel):
    role: VmRole
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(min_length=1)
    authentication_reference: str = Field(min_length=1)
    host_key_reference: Optional[str] = None
    readiness_expectations: List[str] = Field(default_factory=list)
    failure: Optional[FailureEvidence] = None

    @field_validator("host")
    @classmethod
    def private_ipv4(cls, value: str) -> str:
        address = IPv4Address(value)
        if not address.is_private:
            raise ValueError("host must be a private IPv4 address")
        return value


class ProviderDeploymentInput(StrictModel):
    campaign_id: str
    run_id: str
    experiment_id: str
    provider: Provider
    scenario: Scenario
    scheduled_start: datetime
    vm_a: VmIntent
    vm_b: VmIntent
    placement: Placement
    bootstrap_template: str
    bootstrap_inputs: Dict[str, Any] = Field(default_factory=dict)
    terraform_directory: str
    child_result_path: str
    provisioning_timeout_seconds: int = Field(gt=0)
    cleanup_timeout_seconds: int = Field(gt=0)
    config_sha256: str
    implementation_git_commit: str
    design_git_commit: Optional[str] = None

    @model_validator(mode="after")
    def exactly_two_roles(self) -> "ProviderDeploymentInput":
        if self.vm_a.role != VmRole.VM_A.value or self.vm_b.role != VmRole.VM_B.value:
            raise ValueError("deployment requires VM A and VM B roles")
        return self


class ProviderVmOutput(StrictModel):
    role: VmRole
    resource_id: Optional[str]
    private_ipv4: Optional[str]
    image_identity: Optional[str]
    actual_shape: Optional[str]
    region: Optional[str]
    zone: Optional[str]
    placement_metadata: StructuredEvidence
    connection: Optional[RemoteConnectionData]
    unavailable: Optional[StructuredEvidence] = None

    @field_validator("private_ipv4")
    @classmethod
    def private_output_ipv4(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not IPv4Address(value).is_private:
            raise ValueError("private_ipv4 must be private")
        return value


class ProviderDeploymentOutput(StrictModel):
    run_id: str
    provider: Provider
    apply_action_id: str
    started_at: datetime
    finished_at: datetime
    vm_a: ProviderVmOutput
    vm_b: ProviderVmOutput
    provider_metadata: StructuredEvidence
    documented_network_limit: StructuredEvidence
    output_artifact_path: str = "terraform-outputs.json"

    @model_validator(mode="after")
    def roles_and_time(self) -> "ProviderDeploymentOutput":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        if self.vm_a.role != VmRole.VM_A.value or self.vm_b.role != VmRole.VM_B.value:
            raise ValueError("deployment output requires VM A and VM B")
        return self
