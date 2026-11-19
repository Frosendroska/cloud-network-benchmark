"""Private, deadline-bound saved-plan review for the existing provider roots.

The operator receipt is technical inspection evidence, never paid authorization.
Only apply enters this boundary; original scoped destroy remains independent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable

from .contracts.execution import CommandRequest, CommandResult


class PlanReviewError(ValueError):
    category = "terraform_plan_review"


def _json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise PlanReviewError("plan evidence must be a regular non-symlink file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _private(path: Path, content: bytes) -> None:
    # All artifacts are new, under an exclusively created 0700 directory.
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def source_hash(root: Path) -> str:
    files = sorted(p for p in root.rglob("*") if p.is_file()
                   and (p.suffix in {".tf", ".py", ".ps1"} or p.name == ".terraform.lock.hcl")
                   and ".terraform" not in p.parts)
    return hashlib.sha256(_json({str(p.relative_to(root)): _hash(p) for p in files})).hexdigest()


def _same(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise PlanReviewError(f"saved plan differs from approved {label}")


def _fields(actual: dict, expected: dict, label: str) -> None:
    for key, value in expected.items():
        _same(actual.get(key), value, f"{label}.{key}")


def expected_addresses(provider: str, root: str, v: dict) -> set[str]:
    """Exact managed addresses, including count/for_each instances and guards."""
    if provider == "azure":
        from .azure_plan_scope import addresses
        return addresses(root, v)
    if provider == "aws" and root == "safety":
        return {"aws_iam_role.expiry", "aws_iam_role_policy.expiry", "aws_ssm_document.expiry"}
    if provider == "gcp" and root == "pair":
        return {"terraform_data.scope_guard", "google_compute_instance_from_template.vm_a", "google_compute_instance_from_template.vm_b"}
    scenario = v["scenario"]
    if provider == "gcp" and root == "protection":
        result = {"terraform_data.scope_guard", "google_compute_network.attempt", "google_compute_subnetwork.a",
                  "google_compute_firewall.ssh", "google_compute_firewall.listeners", "google_compute_firewall.pair_icmp",
                  'google_compute_instance_template.role["vm_a"]', 'google_compute_instance_template.role["vm_b"]'}
        if scenario == "inter_region": result.add("google_compute_subnetwork.b[0]")
        if scenario == "placement_optimization": result.add("google_compute_resource_policy.compact[0]")
        return result
    if provider != "aws" or root != "pair":
        raise PlanReviewError("unrecognized provider Terraform root")
    result = {"terraform_data.account_guard", "aws_vpc.frankfurt", "aws_internet_gateway.frankfurt",
              "aws_route_table.frankfurt", "aws_subnet.a", "aws_route_table_association.a", "aws_security_group.vm_a",
              "aws_instance.vm_a", "aws_ec2_tag.eni_a", "aws_ec2_tag.eni_a_deployment"}
    if scenario == "inter_region":
        result |= {f"{kind}.london[0]" for kind in ("aws_vpc", "aws_internet_gateway", "aws_route_table", "aws_subnet",
                   "aws_route_table_association", "aws_vpc_peering_connection", "aws_vpc_peering_connection_accepter")}
        result |= {"aws_security_group.vm_b_london[0]", "aws_instance.vm_b_london[0]", "aws_ec2_tag.eni_b_london[0]",
                   "aws_ec2_tag.eni_b_london_deployment[0]", "aws_route.frankfurt_to_london[0]", "aws_route.london_to_frankfurt[0]"}
    else:
        result |= {"aws_security_group.vm_b_frankfurt[0]", "aws_instance.vm_b_frankfurt[0]",
                   "aws_ec2_tag.eni_b_frankfurt[0]", "aws_ec2_tag.eni_b_frankfurt_deployment[0]"}
    if scenario == "cross_zone": result |= {"aws_subnet.b_frankfurt[0]", "aws_route_table_association.b_frankfurt[0]"}
    if scenario == "placement_optimization": result.add("aws_placement_group.cluster[0]")
    return result


def validate_scope(plan: dict, provider: str, root: Path, v: dict) -> list[dict]:
    """Require fresh exact scope; unknown critical inputs fail via missing values."""
    if not str(plan.get("format_version", "")).startswith("1.") or plan.get("errored") or plan.get("deferred_changes") or plan.get("resource_drift"):
        raise PlanReviewError("unsupported, incomplete or drifted Terraform plan")
    _same({k: item["value"] for k, item in plan.get("variables", {}).items()}, v, "input variables")
    for check in plan.get("checks", []):
        if check.get("status") in {"fail", "error"}: raise PlanReviewError("Terraform plan checks failed")
    resources = {}
    data_allowed = {"data.aws_caller_identity.current", "data.aws_availability_zones.primary", "data.aws_availability_zones.london[0]"} if provider == "aws" else ({"data.azurerm_resource_group.pair"} if provider == "azure" and root.name == "pair" else set())
    for item in plan.get("resource_changes", []):
        address, change = item["address"], item["change"]
        if item.get("mode") == "data":
            if address not in data_allowed or change["actions"] not in (["read"], ["no-op"]):
                raise PlanReviewError("unexpected Terraform data action")
            if provider == "azure":
                _same(change.get("after", {}).get("name"), v["resource_group_name"], "Azure existing resource group")
                _same(item.get("provider_name"), "registry.terraform.io/hashicorp/azurerm", "Azure data provider")
            continue
        if address in resources or change["actions"] != ["create"] or change.get("before") is not None or item.get("previous_address") or item.get("importing") or change.get("importing"):
            raise PlanReviewError("only fresh creation is reviewable; update/delete/replacement/import rejected")
        expected_provider = ("terraform.io/builtin/terraform" if item["type"] == "terraform_data" else f"registry.terraform.io/hashicorp/{ {'gcp': 'google', 'azure': 'azurerm', 'aws': 'aws'}[provider]}")
        _same(item.get("provider_name"), expected_provider, "provider identity")
        resources[address] = change["after"]
    _same(set(resources), expected_addresses(provider, root.name, v), "resource address scope")
    if provider == "aws": _aws_scope(resources, root, v)
    elif provider == "gcp": _gcp_scope(resources, root.name, v)
    else:
        from .azure_plan_scope import validate
        validate(resources, root.name, v, plan)
    return [{"address": key, "actions": ["create"]} for key in sorted(resources)]


def _aws_scope(r: dict, root: Path, v: dict) -> None:
    if root.name == "safety":
        digest = hashlib.sha256(f"{v['run_id']}:{v['deployment_id']}:{v['region']}".encode()).hexdigest()[:24]
        name = f"cnb-expiry-{v['resource_namespace'].replace('.', '-')[:24]}-{digest}"
        role = r["aws_iam_role.expiry"]
        _same(role.get("name"), name, "expiry role name")
        _same(json.loads(role["assume_role_policy"]), {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ssm.amazonaws.com"}, "Action": "sts:AssumeRole", "Condition": {"StringEquals": {"aws:SourceAccount": v["account_id"]}, "ArnLike": {"aws:SourceArn": f"arn:aws:ssm:{v['region']}:{v['account_id']}:automation-execution/*"}}}]}, "expiry trust")
        policy = r["aws_iam_role_policy.expiry"]
        _same(policy.get("name"), "cnb-expiry-stop-owned-instances", "expiry policy name")
        _same(json.loads(policy["policy"]), {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["ec2:DescribeInstances"], "Resource": "*"}, {"Effect": "Allow", "Action": ["ec2:StopInstances"], "Resource": f"arn:aws:ec2:{v['region']}:{v['account_id']}:instance/*", "Condition": {"StringEquals": {"ec2:ResourceTag/cnb_run_id": v["run_id"], "ec2:ResourceTag/cnb_deployment_id": v["deployment_id"]}}}]}, "expiry stop policy")
        document = r["aws_ssm_document.expiry"]
        _fields(document, {"name": name, "document_type": "Automation", "document_format": "JSON"}, "expiry document")
        body = json.loads(document["content"])
        _same(body.get("assumeRole"), "{{ AutomationAssumeRole }}", "expiry document role")
        _same(body.get("parameters"), {k: {"type": "String"} for k in ("AutomationAssumeRole", "ExpiryTriggerUtc", "RunId", "DeploymentId", "Region")}, "expiry parameters")
        _same(body.get("mainSteps"), [{"name": "waitUntilExpiry", "action": "aws:sleep", "inputs": {"Timestamp": "{{ ExpiryTriggerUtc }}"}}, {"name": "discoverAndStopOwnedInstances", "action": "aws:executeScript", "inputs": {"Runtime": "python3.11", "Handler": "handler", "Script": (root / "expiry.py").read_text(), "InputPayload": {"run_id": "{{ RunId }}", "deployment_id": "{{ DeploymentId }}", "region": "{{ Region }}"}}, "timeoutSeconds": 330}], "expiry steps")
        return
    _same(r["terraform_data.account_guard"].get("input"), v["account_id"], "account guard")
    for address, a in r.items():
        if address.startswith("aws_instance."):
            is_a, london = address == "aws_instance.vm_a", "london" in address
            ip = "10.60.1.10" if is_a else "10.61.1.10" if london else "10.60.2.10" if v["scenario"] == "cross_zone" else "10.60.1.11"
            _fields(a, {"ami": v["image_id_london" if london else "image_id_primary"], "instance_type": "m7i.xlarge", "private_ip": ip, "associate_public_ip_address": True, "user_data_replace_on_change": True}, address)
            disks = a.get("root_block_device", [])
            if len(disks) != 1: raise PlanReviewError("expected one AWS root disk")
            _fields(disks[0], {"volume_size": 32, "volume_type": "gp3", "delete_on_termination": True}, address)
            if a.get("ebs_block_device") or a.get("ephemeral_block_device") or a.get("launch_template"):
                raise PlanReviewError("unexpected AWS additional disk or launch template")
            _fields(a.get("tags", {}), {"cnb_run_id": v["run_id"], "cnb_deployment_id": v["deployment_id"], "cnb_role": "vm_a" if is_a else "vm_b"}, address)
        if address.startswith("aws_vpc."):
            _same(a.get("cidr_block"), "10.61.0.0/16" if "london" in address else "10.60.0.0/16", address)
        if address.startswith("aws_subnet."):
            london, cross = "london" in address, "b_frankfurt" in address
            _fields(a, {"cidr_block": "10.61.1.0/24" if london else "10.60.2.0/24" if cross else "10.60.1.0/24", "availability_zone": "eu-west-2a" if london else "eu-central-1b" if cross else v["primary_zone"], "map_public_ip_on_launch": True}, address)
        if address.startswith("aws_security_group."):
            is_a = address.endswith("vm_a")
            peer = "10.61.1.10/32" if v["scenario"] == "inter_region" else "10.60.2.10/32" if v["scenario"] == "cross_zone" else "10.60.1.11/32"
            rules = {(x["protocol"], x["from_port"], x["to_port"], tuple(x.get("cidr_blocks", []))) for x in a.get("ingress", [])}
            wanted = {("tcp", 22, 22, (v["controller_cidr"],)), ("icmp", 8, 0, (peer if is_a else "10.60.1.10/32",))}
            if not is_a: wanted |= {("udp", 11111, 11111, ("10.60.1.10/32",)), ("tcp", 5201, 5201, ("10.60.1.10/32",))}
            _same(rules, wanted, address + " ingress")
            if any(x.get("ipv6_cidr_blocks") or x.get("security_groups") or x.get("self") or x.get("prefix_list_ids") for x in a["ingress"]):
                raise PlanReviewError("unexpected AWS ingress source")
        if address.startswith("aws_placement_group."):
            _fields(a, {"name": v["placement_group_name"], "strategy": "cluster"}, address)


def _gcp_scope(r: dict, root: str, v: dict) -> None:
    _same(r["terraform_data.scope_guard"].get("input"), v["project_id"], "project guard")
    if root == "pair":
        if v.get("scenario") not in {"same_zone", "cross_zone", "placement_optimization", "inter_region"}:
            raise PlanReviewError("GCP pair scenario is missing or unsupported")
        namespace = v["name_a"][:-2] if v["name_a"].endswith("-a") else ""
        if not namespace:
            raise PlanReviewError("GCP pair A name does not provide the selected resource namespace")
        region_a = "-".join(v["zone_a"].split("-")[:-1])
        region_b = "-".join(v["zone_b"].split("-")[:-1])
        ips = ("10.80.1.10", "10.81.1.11") if v["scenario"] == "inter_region" else ("10.80.1.10", "10.80.1.11")
        network = f"projects/{v['project_id']}/global/networks/{namespace}-net"
        for role in ("a", "b"):
            resource = r[f"google_compute_instance_from_template.vm_{role}"]
            _fields(resource, {"project": v["project_id"], "name": v[f"name_{role}"], "zone": v[f"zone_{role}"], "source_instance_template": v[f"template_{role}"]}, f"GCP VM {role}")
            expected_region = region_a if role == "a" else region_b
            subnet_suffix = "a" if role == "a" or v["scenario"] != "inter_region" else "b"
            nic = resource.get("network_interface", [])
            if not isinstance(nic, list) or len(nic) != 1 or not isinstance(nic[0], dict):
                raise PlanReviewError(f"GCP VM {role} must have exactly one reviewed network interface")
            _fields(nic[0], {"network": network,
                             "subnetwork": f"projects/{v['project_id']}/regions/{expected_region}/subnetworks/{namespace}-{subnet_suffix}",
                             "network_ip": ips[0 if role == "a" else 1], "nic_type": "GVNIC"}, f"GCP VM {role} network interface")
            access = nic[0].get("access_config", [])
            if (not isinstance(access, list) or len(access) != 1 or not isinstance(access[0], dict) or
                    access[0].get("nat_ip") not in (None, "") or
                    access[0].get("network_tier") not in (None, "PREMIUM") or
                    set(access[0]) - {"nat_ip", "network_tier", "public_ptr_domain_name"}):
                raise PlanReviewError(f"GCP VM {role} must request exactly one ephemeral external IPv4 access config")
        return
    for address, a in r.items():
        if address.startswith("google_compute_"):
            _same(a.get("project"), v["project_id"], address + " project")
    _fields(r["google_compute_network.attempt"], {"name": v["namespace"] + "-net", "auto_create_subnetworks": False, "mtu": 1460}, "GCP network")
    for suffix in ("a", "b"):
        address = "google_compute_subnetwork." + suffix + ("[0]" if suffix == "b" else "")
        if address in r:
            _fields(r[address], {"name": v["namespace"] + "-" + suffix, "region": v["zone_" + suffix].rsplit("-", 1)[0], "ip_cidr_range": "10.80.1.0/24" if suffix == "a" else "10.81.1.0/24"}, address)
    ips = ["10.80.1.10", "10.81.1.11" if v["scenario"] == "inter_region" else "10.80.1.11"]
    for name, sources, allows, tags in (("ssh", [v["controller_cidr"]], {("tcp", ("22",))}, []), ("listeners", [ips[0] + "/32"], {("tcp", ("5201",)), ("udp", ("11111",))}, [v["namespace"] + "-vm-b"]), ("pair_icmp", [ip + "/32" for ip in ips], {("icmp", ())}, [])):
        a = r["google_compute_firewall." + name]
        _same(a.get("source_ranges"), sources, name + " sources")
        _same({(x["protocol"], tuple(x.get("ports") or [])) for x in a.get("allow", [])}, allows, name + " allow")
        _same(a.get("target_tags") or [], tags, name + " target")
        if a.get("source_tags") or a.get("source_service_accounts") or a.get("deny") or a.get("disabled"):
            raise PlanReviewError("unexpected GCP firewall scope")
        _same(a.get("direction"), "INGRESS", name + " direction")
    for index, role in enumerate(("a", "b")):
        a = r[f'google_compute_instance_template.role["vm_{role}"]']
        _fields(a, {"name": v["namespace"] + "-" + role, "machine_type": "n2-standard-4", "can_ip_forward": False, "labels": v["labels"]}, "GCP template")
        scheduling = a.get("scheduling", [])
        if len(scheduling) != 1: raise PlanReviewError("missing GCP expiry scheduling")
        _fields(scheduling[0], {"provisioning_model": "STANDARD", "automatic_restart": False, "instance_termination_action": "STOP", "termination_time": v["expiry_time"]}, "GCP expiry")
        if scheduling[0].get("max_run_duration") or scheduling[0].get("preemptible"):
            raise PlanReviewError("unexpected GCP expiry/purchase scope")
        disks, nics = a.get("disk", []), a.get("network_interface", [])
        if len(disks) != 1 or len(nics) != 1 or len(nics[0].get("access_config", [])) != 1:
            raise PlanReviewError("GCP template requires one disk, NIC and public IPv4")
        _fields(disks[0], {"source_image": v["image_" + role], "disk_size_gb": 32, "disk_type": "pd-balanced", "boot": True, "auto_delete": True}, "GCP disk")
        _fields(nics[0], {"network_ip": ips[index], "nic_type": "GVNIC"}, "GCP NIC")
        if nics[0].get("ipv6_access_config") or nics[0].get("queue_count") or a.get("guest_accelerator") or a.get("network_performance_config"):
            raise PlanReviewError("unexpected GCP accelerator/network scope")
        affinity = a.get("reservation_affinity", [])
        if len(affinity) != 1: raise PlanReviewError("missing GCP reservation affinity")
        _fields(affinity[0], {"type": "NO_RESERVATION"}, "GCP reservation affinity")
    if "google_compute_resource_policy.compact[0]" in r:
        policy = r["google_compute_resource_policy.compact[0]"]
        _fields(policy, {"name": v["namespace"] + "-compact", "region": v["zone_a"].rsplit("-", 1)[0]}, "GCP compact policy")
        groups = policy.get("group_placement_policy", [])
        if len(groups) != 1 or groups[0].get("max_distance"): raise PlanReviewError("unexpected GCP placement scope")
        _fields(groups[0], {"collocation": "COLLOCATED"}, "GCP placement")


def await_review(directory: Path, binding: dict, deadline: float) -> dict:
    """Operator writes receipt atomically from a separate controller session."""
    receipt_path = directory / "operator-review.json"
    while True:
        if receipt_path.exists() or receipt_path.is_symlink():
            _hash(receipt_path)
            receipt = json.loads(receipt_path.read_bytes())
            if (receipt.get("decision") != "approved" or receipt.get("binding") != binding
                    or not isinstance(receipt.get("reviewer"), str) or not receipt["reviewer"].strip()
                    or not isinstance(receipt.get("inspection"), str) or not receipt["inspection"].strip()):
                raise PlanReviewError("operator review rejected or bound to different plan/source/input/state bytes")
            os.chmod(receipt_path, 0o600)
            return receipt
        if time.monotonic() >= deadline:
            raise PlanReviewError(f"operator saved-plan inspection required at {directory}")
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))


def reviewed_apply(request: CommandRequest, run: Callable[[CommandRequest], CommandResult], *, review_seconds: int = 0) -> CommandResult:
    """Plan -> JSON/scope -> bounded operator inspection -> same saved-plan apply."""
    deadline = time.monotonic() + request.timeout_seconds
    argv = request.argv
    root = Path(next(x.split("=", 1)[1] for x in argv if x.startswith("-chdir=")))
    provider = root.parent.name
    if provider not in {"aws", "azure", "gcp"} or "apply" not in argv:
        raise PlanReviewError("saved-plan boundary accepts only provider apply")
    variables = Path(next(x.split("=", 1)[1] for x in argv if x.startswith("-var-file=")))
    state = Path(next(x.split("=", 1)[1] for x in argv if x.startswith("-state=")))
    directory = Path(tempfile.mkdtemp(prefix=request.action_id + "-review-", dir=request.cwd))
    os.chmod(directory, 0o700)
    binary, json_path = directory / "saved.tfplan", directory / "saved-plan.json"
    _private(binary, b"")
    inputs_hash, sources_hash = _hash(variables), source_hash(root)
    state_hash = _hash(state) if state.exists() or state.is_symlink() else None

    def execute(action: str, command: list[str]) -> CommandResult:
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise PlanReviewError("original apply deadline elapsed during plan review")
        return run(request.model_copy(update={"action_id": action, "argv": command, "timeout_seconds": remaining}))

    try:
        prefix = argv[:2]
        plan = execute(request.action_id + "-plan", [*prefix, "plan", "-input=false", "-no-color", f"-state={state}", f"-var-file={variables}", f"-out={binary}"])
        _private(directory / "plan-result.json", _json(plan.durable_view()))
        if plan.outcome != "succeeded" or plan.exit_code != 0:
            raise PlanReviewError("Terraform saved-plan creation failed")
        if not binary.stat().st_size: raise PlanReviewError("Terraform did not materialize the saved plan")
        os.chmod(binary, 0o600)
        binary_hash = _hash(binary)
        shown = execute(request.action_id + "-show", [*prefix, "show", "-json", str(binary)])
        _private(json_path, shown.stdout.encode())
        if shown.outcome != "succeeded" or shown.exit_code != 0:
            raise PlanReviewError("Terraform saved-plan JSON inspection failed")
        scope = validate_scope(json.loads(shown.stdout), provider, root, json.loads(variables.read_bytes()))
        binding = {"action_id": request.action_id, "root": str(root), "state": str(state), "state_sha256": state_hash,
                   "plan_sha256": binary_hash, "json_sha256": _hash(json_path), "variables_sha256": inputs_hash,
                   "source_sha256": sources_hash, "scope": scope}
        _private(directory / "review-request.json", _json(binding))
        receipt = await_review(directory, binding, min(deadline, time.monotonic() + review_seconds))
        _private(directory / "accepted-review.json", _json(receipt))
        _same((_hash(binary), _hash(json_path), _hash(variables), source_hash(root), _hash(state) if state.exists() or state.is_symlink() else None),
              (binary_hash, binding["json_sha256"], inputs_hash, sources_hash, state_hash), "reviewed artifact hashes")
        # The legacy local -state override is not carried into a saved plan's
        # backend configuration. Keep apply on the original attempt state too.
        result = execute(request.action_id, [*prefix, "apply", "-input=false", f"-state={state}", str(binary)])
        apply_evidence = {"action_id": result.action_id, "outcome": str(result.outcome), "exit_code": result.exit_code}
        apply_failed = result.outcome != "succeeded" or result.exit_code != 0
        aws_apply_failed = provider == "aws" and apply_failed
        if aws_apply_failed:
            diagnostic = {}
            for field in ("stdout", "stderr"):
                raw = getattr(result, field)
                text = _redact_apply_diagnostic(raw, result.redactions)
                diagnostic[field] = text[:8192]
                diagnostic[field + "_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
                diagnostic[field + "_truncated"] = len(text) > 8192
            apply_evidence["diagnostic"] = diagnostic
        try:
            _private(directory / "apply-result.json", _json(apply_evidence))
        except OSError:
            # Evidence I/O must not replace the provider's original failed result;
            # its caller retains the original category and proceeds to cleanup.
            if not aws_apply_failed:
                raise
        return result
    except BaseException as exc:
        _private(directory / "failure.json", _json({"category": "terraform_plan_review", "error_type": type(exc).__name__}))
        raise


def _redact_apply_diagnostic(value: str, secrets: list[str]) -> str:
    """Bounded redaction for private AWS saved-apply failure diagnostics."""
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?:AKIA|ASIA)[A-Z0-9]{16}", "[REDACTED_ACCESS_KEY_ID]", value)
    value = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [REDACTED]", value)
    sensitive = r"(?:aws_access_key_id|aws_secret_access_key|aws_session_token|authorization|bearer|password|token|secret|credential)"
    value = re.sub(rf'''(?i)(["']?{sensitive}["']?\s*:\s*)(["'])([^"'\r\n]*)(\2)''',
                   r"\1\2[REDACTED]\4", value)
    value = re.sub(rf"(?i)((?:{sensitive})\s*[=:]\s*)[^\s,;}}\"']+", r"\1[REDACTED]", value)
    value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", value, flags=re.S)
    return value


def record_operator_review(directory: Path, request_sha256: str, reviewer: str, inspection: str) -> Path:
    """Record an operator's completed inspection; never execute Terraform."""
    directory = directory.absolute()
    if directory.is_symlink() or any(p.is_symlink() for p in directory.parents):
        raise PlanReviewError("review directory must not traverse symlinks")
    _same(_hash(directory / "review-request.json"), request_sha256, "inspection request hash")
    binding = json.loads((directory / "review-request.json").read_bytes())
    _same(_hash(directory / "saved.tfplan"), binding["plan_sha256"], "plan hash")
    _same(_hash(directory / "saved-plan.json"), binding["json_sha256"], "plan JSON hash")
    _same(source_hash(Path(binding["root"])), binding["source_sha256"], "source hash")
    if not reviewer.strip() or not inspection.strip():
        raise PlanReviewError("reviewer and completed inspection description are required")
    receipt = {"decision": "approved", "binding": binding, "reviewer": reviewer, "inspection": inspection}
    # Atomic publication prevents a waiting controller reading partial JSON.
    pending = directory / "operator-review.pending"
    _private(pending, _json(receipt))
    target = directory / "operator-review.json"
    if target.exists() or target.is_symlink():
        raise PlanReviewError("an operator receipt already exists; reviews are not overwritten")
    os.link(pending, target)
    pending.unlink()
    return target


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Record completed technical inspection of one saved Terraform plan; does not launch a run")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--inspection", required=True)
    args = parser.parse_args(argv)
    record_operator_review(args.directory, args.request_sha256, args.reviewer, args.inspection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
