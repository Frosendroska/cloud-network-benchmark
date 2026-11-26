"""Offline AWS CommandRunner leaf for production lifecycle composition.

Only provider command responses are synthesized here. The AWS driver, reviewed
apply boundary, validators, hooks, and lifecycle remain production code.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult


@dataclass(frozen=True)
class AwsRunContext:
    provider: str
    request: object
    driver: object
    deployment: object
    run_id: str
    deployment_id: str
    clock: Callable[[], datetime]
    runtime_roots: Mapping[str, Path]
    source_roots: Mapping[str, Path]
    control_root: Path | None = None


class AwsLeafProtocol:
    """Bind source-shaped AWS replies to one actual driver and deployment."""

    def __init__(self, context: AwsRunContext):
        if context.provider != "aws" or not context.runtime_roots or not context.source_roots:
            raise ValueError("AWS context, private runtime roots, and trusted source roots are required")
        if (getattr(context.deployment, "run_id", None) != context.run_id
                or getattr(context.deployment, "deployment_id", None) != context.deployment_id):
            raise ValueError("AWS run identity does not match deployment")
        # Import the response producer only. Its test module's pytest marks are
        # not inherited by this module or applied to the imported class.
        from tests.integration.test_f09_aws_runtime_factory import FakePreparedAwsRunner

        self.context = context
        self._source = FakePreparedAwsRunner()
        self._source.run_id = context.run_id
        self._source.deployment_id = context.deployment_id
        self._source.expiry_request = context.request
        self._source.complete_cleanup = True
        self._source.driver = context.driver
        self._source.deployment = context.deployment
        self._source.clock = type("ClockView", (), {"utc_now": staticmethod(context.clock)})()
        # The existing low-level responder defaults to Frankfurt. Bind its
        # provider readbacks to this actual observed deployment instead.
        self._source.region = str(context.deployment.vm_a.region)
        self._source.zone = str(context.deployment.vm_a.zone)
        self._source.image_id = str(context.driver.settings.ami_ids[self._source.region])
        self._plans: dict[str, tuple[Path, str, dict]] = {}
        self._pair_variables_binding: tuple[Path, str] | None = None
        self._expiry_starts: dict[str, dict] = {}

    @staticmethod
    def _plan_for(root: Path, variables: dict) -> dict:
        from tests.unit.test_terraform_plan_review import plan_document, scope

        root_name = root.name
        scenario = str(variables.get("scenario", "same_zone")) if root_name == "pair" else "same_zone"
        original, resources = scope("aws", root_name, scenario)

        def replace(value):
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            for key, old in original.items():
                if value == old and key in variables:
                    return variables[key]
            return value

        resources = replace(resources)
        if root_name == "safety":
            # The legacy source fixture embeds these policy documents as JSON
            # strings, so scalar replacement cannot bind their account, run,
            # deployment and region predicates to the actual reviewed vars.
            trust = {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "ssm.amazonaws.com"},
                "Action": "sts:AssumeRole", "Condition": {"StringEquals": {
                    "aws:SourceAccount": variables["account_id"]}, "ArnLike": {
                    "aws:SourceArn": f"arn:aws:ssm:{variables['region']}:{variables['account_id']}:automation-execution/*"}}}]}
            stop_policy = {"Version": "2012-10-17", "Statement": [
                {"Effect": "Allow", "Action": ["ec2:DescribeInstances"], "Resource": "*"},
                {"Effect": "Allow", "Action": ["ec2:StopInstances"],
                 "Resource": f"arn:aws:ec2:{variables['region']}:{variables['account_id']}:instance/*",
                 "Condition": {"StringEquals": {
                     "ec2:ResourceTag/cnb_run_id": variables["run_id"],
                     "ec2:ResourceTag/cnb_deployment_id": variables["deployment_id"]}}}]}
            resources["aws_iam_role.expiry"]["assume_role_policy"] = json.dumps(trust)
            resources["aws_iam_role_policy.expiry"]["policy"] = json.dumps(stop_policy)
            digest = hashlib.sha256(
                f"{variables['run_id']}:{variables['deployment_id']}:{variables['region']}".encode()
            ).hexdigest()[:24]
            bound_name = ("cnb-expiry-" + variables["resource_namespace"].replace(".", "-")[:24]
                + "-" + digest)
            resources["aws_iam_role.expiry"]["name"] = bound_name
            resources["aws_ssm_document.expiry"]["name"] = bound_name
        elif scenario == "placement_optimization":
            # scope() predates the S3 AWS resource. Mirror the source-created
            # cluster group so the real scope validator receives the exact
            # placement resource set for the actual scenario.
            resources["aws_placement_group.cluster[0]"] = {
                "name": variables["placement_group_name"], "strategy": "cluster"}
            for address in ("aws_instance.vm_a", "aws_instance.vm_b_frankfurt[0]"):
                resources[address]["placement_group"] = variables["placement_group_name"]
        return plan_document(variables, resources, "aws")

    def _check(self, request: CommandRequest, *, allow_repository_control: bool = False) -> None:
        cwd = Path(request.cwd).resolve()
        roots = tuple(Path(root).resolve() for root in self.context.runtime_roots.values())
        repository_control = (allow_repository_control and self.context.control_root is not None
            and cwd == Path(self.context.control_root).resolve())
        if not repository_control and not any(cwd == root or root in cwd.parents for root in roots):
            raise ValueError("command cwd is outside this run's runtime roots")

    def run(self, request: CommandRequest) -> CommandResult:
        executable = Path(request.argv[0]).name if request.argv else ""
        aws_control = (request.action_id.startswith("aws-") and (
            (executable == "aws" and not any(Path(token).name == "terraform" for token in request.argv))
            or (request.action_id.startswith("aws-host-keyscan-") and executable == "ssh-keyscan")
            or (request.action_id.startswith("aws-host-key-fingerprint-") and executable == "ssh-keygen")))
        self._check(request, allow_repository_control=aws_control)
        if not request.action_id.startswith(("aws-", "f02r-")):
            raise ValueError("non-AWS/F02R command rejected")
        if request.action_id.startswith("aws-expiry-start-"):
            region = request.action_id.removeprefix("aws-expiry-start-")
            argv = request.argv
            if (len(argv) < 2 or argv[1:3] != ["ssm", "start-automation-execution"]
                    or "--document-name" not in argv or "--document-version" not in argv
                    or "--parameters" not in argv):
                raise ValueError("actual AWS expiry start request is incomplete")
            def argument(name):
                index = argv.index(name)
                if index + 1 >= len(argv):
                    raise ValueError(f"actual AWS expiry start lacks {name} value")
                return argv[index + 1]
            parameters = json.loads(argument("--parameters"))
            if (set(parameters) != {"AutomationAssumeRole", "ExpiryTriggerUtc", "RunId",
                    "DeploymentId", "Region"}
                    or parameters["RunId"] != [self.context.run_id]
                    or parameters["DeploymentId"] != [self.context.deployment_id]
                    or parameters["Region"] != [region]):
                raise ValueError("actual AWS expiry start parameters differ from its bound run")
            result = self._source.run(request)
            execution_id = json.loads(result.stdout).get("AutomationExecutionId")
            if not isinstance(execution_id, str) or not execution_id:
                raise ValueError("AWS expiry start fixture returned no execution identity")
            self._expiry_starts[region] = {"execution_id": execution_id,
                "document_name": argument("--document-name"),
                "document_version": argument("--document-version"),
                "parameters": parameters}
            return result
        if (request.action_id.startswith("aws-expiry-confirm-")
                and not request.action_id.startswith("aws-expiry-confirm-cancel-")):
            region = request.action_id.removeprefix("aws-expiry-confirm-")
            started = self._expiry_starts.get(region)
            argv = request.argv
            if (started is None or len(argv) < 2 or argv[1:3] != ["ssm", "get-automation-execution"]
                    or "--automation-execution-id" not in argv):
                raise ValueError("AWS expiry readback has no matching actual start request")
            execution_id = argv[argv.index("--automation-execution-id") + 1]
            if execution_id != started["execution_id"]:
                raise ValueError("AWS expiry readback identity differs from actual start response")
            timestamp = started["parameters"]["ExpiryTriggerUtc"][0]
            output = {"AutomationExecutionId": execution_id,
                "DocumentName": started["document_name"],
                "DocumentVersion": started["document_version"],
                "AutomationExecutionStatus": "InProgress", "CurrentAction": "aws:sleep",
                "CurrentStepName": "waitUntilExpiry", "Parameters": started["parameters"],
                "StepExecutionsTruncated": False,
                "StepExecutions": [{"StepName": "waitUntilExpiry", "Action": "aws:sleep",
                    "StepStatus": "InProgress", "Inputs": {"Timestamp": timestamp}}]}
            now = self.context.clock()
            return CommandResult(action_id=request.action_id, outcome="succeeded",
                started_at=now, finished_at=now, duration_seconds=0.0, exit_code=0,
                stdout=json.dumps({"AutomationExecution": output}, sort_keys=True), stderr="")
        if request.action_id.endswith("-plan") and "plan" in request.argv:
            output = next((arg.removeprefix("-out=") for arg in request.argv if arg.startswith("-out=")), None)
            varfile = next((arg.removeprefix("-var-file=") for arg in request.argv if arg.startswith("-var-file=")), None)
            chdir = next((arg.removeprefix("-chdir=") for arg in request.argv if arg.startswith("-chdir=")), None)
            if output is None or varfile is None or chdir is None:
                raise ValueError("saved-plan request has no output path")
            root, target, variables_path = Path(chdir).resolve(), Path(output), Path(varfile)
            if not target.is_absolute() or not target.resolve().is_relative_to(Path(request.cwd).resolve()):
                raise ValueError("saved-plan output must be absolute and inside bound runtime root")
            if not any(target.resolve().is_relative_to(bound.resolve())
                       for bound in self.context.runtime_roots.values()):
                raise ValueError("saved-plan output is outside bound runtime roots")
            source_root = self.context.source_roots.get(request.action_id.removesuffix("-plan"))
            if source_root is None or root != Path(source_root).resolve():
                raise ValueError("Terraform root does not match action-specific trusted source root")
            if not any(variables_path.resolve().is_relative_to(bound.resolve())
                       for bound in self.context.runtime_roots.values()):
                raise ValueError("Terraform variables file is outside bound runtime roots")
            variables_bytes = variables_path.read_bytes()
            variables = json.loads(variables_bytes)
            plan = self._plan_for(root, variables)
            if root.name == "pair" and request.action_id == "aws-pair-apply-plan":
                self._pair_variables_binding = (variables_path.resolve(), hashlib.sha256(variables_bytes).hexdigest())
            source_hashes = []
            for source in sorted(root.rglob("*.tf")):
                source_hashes.append((str(source.relative_to(root)), hashlib.sha256(source.read_bytes()).hexdigest()))
            binding = json.dumps([request.action_id, str(root), hashlib.sha256(variables_bytes).hexdigest(), source_hashes], sort_keys=True).encode()
            payload = b"CNB-OFFLINE-SAVED-PLAN\0" + hashlib.sha256(binding).digest()
            if target.is_symlink() or not target.is_file() or target.stat().st_size != 0:
                raise ValueError("reviewed_apply did not precreate an empty regular saved-plan destination")
            with target.open("wb") as stream:
                stream.write(payload)
            self._plans[request.action_id.removesuffix("-plan")] = (target.resolve(), hashlib.sha256(payload).hexdigest(), plan)
            return self._result(request, "")
        if request.action_id.endswith("-show") and "show" in request.argv:
            key = request.action_id.removesuffix("-show")
            entry = self._plans.get(key)
            if entry is None or len(request.argv) < 4:
                raise ValueError("show request has no matching plan response")
            path = Path(request.argv[-1]).resolve()
            if path != entry[0] or hashlib.sha256(path.read_bytes()).hexdigest() != entry[1]:
                raise ValueError("show request plan path or bytes differ from generated plan")
            return self._result(request, json.dumps(entry[2], sort_keys=True))
        if request.action_id == "aws-pair-apply":
            entry = self._plans.get("aws-pair-apply")
            variables = self._pair_variables_binding
            if entry is None or variables is None or not request.argv:
                raise ValueError("AWS apply has no matching saved plan and source variables")
            binary_path = Path(request.argv[-1]).resolve(strict=True)
            variables_path, variables_sha256 = variables
            if (binary_path != entry[0]
                    or hashlib.sha256(binary_path.read_bytes()).hexdigest() != entry[1]
                    or variables_path.is_symlink() or not variables_path.is_file()
                    or hashlib.sha256(variables_path.read_bytes()).hexdigest() != variables_sha256):
                raise ValueError("AWS apply binary or source variables differ from the exact reviewed plan")
            # The established low-level fake responder expects a varfile on
            # apply. Add it only to its internal response-adapter copy after
            # verifying the actual request still applies the exact saved bytes.
            adapted = request.model_copy(update={"argv": [*request.argv,
                f"-var-file={variables_path}"]})
            return self._source_result(self._source.run(adapted))
        if request.action_id.startswith(("f02r-", "aws-")):
            return self._source_result(self._source.run(request))

    def _result(self, request: CommandRequest, stdout: str) -> CommandResult:
        now = self.context.clock()
        return CommandResult(action_id=request.action_id, outcome="succeeded", started_at=now,
            finished_at=now, duration_seconds=0, exit_code=0, stdout=stdout, stderr="")

    def _source_result(self, result: CommandResult) -> CommandResult:
        # The inherited responder's dated baseline predates this live-window
        # fixture. Model command timing at the actual bounded test clock.
        now = self.context.clock()
        return result.model_copy(update={"started_at": now, "finished_at": now,
            "duration_seconds": 0.0})

    def run_bounded_copy(self, request: CommandRequest, destination: Path,
                         maximum_payload_bytes: int) -> CommandResult:
        self._check(request)
        # This fixture does not manufacture native archive evidence. Delegate
        # only when the source fixture has verified the production EOF proof.
        return self._source.run_bounded_copy(request, destination, maximum_payload_bytes)
