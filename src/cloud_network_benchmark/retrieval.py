from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional
from pydantic import Field

from .access import AccessFailureEvidence, ConnectionOutput, RemoteActionEvidence, _failure
from .contracts.common import CommandOutcome, StrictModel
from .contracts.execution import CommandRequest, CommandRunner


class RetrievalItem(StrictModel):
    artifact_name: str = Field(min_length=1)
    source_vm: str = Field(min_length=1)
    source_role: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    destination_path: str = Field(min_length=1)
    expected_sha256: Optional[str] = None
    timeout_seconds: float = Field(gt=0)


class RetrievalManifest(StrictModel):
    items: List[RetrievalItem] = Field(min_length=1)


class RetrievalResult(StrictModel):
    complete: bool
    actions: List[RemoteActionEvidence] = Field(default_factory=list)
    retrieved: List[str] = Field(default_factory=list)
    failure: AccessFailureEvidence | None = None
    integrity: dict[str, dict[str, int | str]] = Field(default_factory=dict)


def run_retrieval(connection: ConnectionOutput, manifest: RetrievalManifest, runner: CommandRunner, filesystem: Path) -> RetrievalResult:
    actions: List[RemoteActionEvidence] = []
    retrieved: List[str] = []
    integrity: dict[str, dict[str, int | str]] = {}
    for item in manifest.items:
        vm = connection.vm_a if item.source_role == "vm_a" else connection.vm_b if item.source_role == "vm_b" else None
        if vm is None or vm.host != item.source_vm:
            return RetrievalResult(complete=False, actions=actions, retrieved=retrieved, integrity=integrity, failure=AccessFailureEvidence(stage="retrieval", role=item.source_role, category="invalid_connection", message="retrieval source does not match VM role", source_path=item.source_path, occurred_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
        destination = filesystem / item.destination_path
        if destination.exists():
            return RetrievalResult(complete=False, actions=actions, retrieved=retrieved, integrity=integrity, failure=AccessFailureEvidence(stage="retrieval", vm_id=item.source_vm, role=item.source_role, category="destination_collision", message="destination already contains evidence", source_path=item.source_path, occurred_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
        destination.parent.mkdir(parents=True, exist_ok=True)
        scp_args = ["scp", "-P", str(vm.port), "-i", vm.authentication_reference]
        if vm.host_key_reference:
            scp_args.extend(["-o", f"UserKnownHostsFile={vm.host_key_reference}"])
        scp_args.extend([f"{vm.user}@{vm.host}:{item.source_path}", str(destination)])
        request = CommandRequest(action_id=f"scp-{item.artifact_name}", action_type="scp", argv=scp_args, cwd=".", environment_classification="contains_secret_references", timeout_seconds=item.timeout_seconds)
        result = runner.run(request)
        actions.append(RemoteActionEvidence(action_id=request.action_id, action_type="scp", vm_id=vm.host, role=item.source_role, result=result))
        if result.outcome != CommandOutcome.SUCCEEDED:
            category = "retrieval_timeout" if result.outcome == CommandOutcome.TIMED_OUT else "interrupted" if result.outcome == CommandOutcome.INTERRUPTED else "scp_failed"
            return RetrievalResult(complete=False, actions=actions, retrieved=retrieved, integrity=integrity, failure=_failure("retrieval", vm, category, result, f"failed to retrieve {item.artifact_name}"))
        if result.stdout and not destination.exists():
            destination.write_text(result.stdout)
        if not destination.exists() or destination.stat().st_size == 0:
            return RetrievalResult(complete=False, actions=actions, retrieved=retrieved, integrity=integrity, failure=AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category="artifact_missing", message=f"transfer completed without materializing {item.artifact_name}", source_path=item.source_path, occurred_at=result.finished_at))
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        integrity[item.artifact_name] = {"bytes": destination.stat().st_size, "sha256": digest}
        if item.expected_sha256:
            if digest != item.expected_sha256:
                return RetrievalResult(complete=False, actions=actions, retrieved=retrieved, integrity=integrity, failure=AccessFailureEvidence(stage="retrieval", vm_id=vm.host, role=item.source_role, category="integrity_failed", message=f"integrity check failed for {item.artifact_name}", source_path=item.source_path, occurred_at=result.finished_at, details={"expected_sha256": item.expected_sha256, "actual_sha256": digest}))
        retrieved.append(item.artifact_name)
    return RetrievalResult(complete=True, actions=actions, retrieved=retrieved, integrity=integrity)
