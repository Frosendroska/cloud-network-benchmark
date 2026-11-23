"""Private, deadline-bound saved-plan review for the existing provider roots.

The operator receipt is technical inspection evidence, never paid authorization.
Only apply enters this boundary; original scoped destroy remains independent.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import fcntl
import re
from pathlib import Path
import tempfile
import time
from typing import Any, Callable

from .contracts.execution import CommandRequest, CommandResult


class PlanReviewError(ValueError):
    category = "terraform_plan_review"


LEGACY_PUBLICATION_REVIEW_SECONDS = 40
MAX_SOURCE_BOUND_REVIEW_SECONDS = 600


def workflow_review_stage_deadline(*, cap_started_monotonic: float,
        registration_allowance_seconds: float, provision_allowance_seconds: float,
        stage: str, overall_deadline: float, stop_deadline: float) -> float:
    """Return the immutable original stage cutoff clipped by overall cap and STOP."""
    values = (cap_started_monotonic, registration_allowance_seconds,
        provision_allowance_seconds, overall_deadline, stop_deadline)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) for value in values):
        raise PlanReviewError("workflow review stage timing inputs must be finite")
    if cap_started_monotonic < 0 or registration_allowance_seconds <= 0 or provision_allowance_seconds < 0:
        raise PlanReviewError("workflow review stage allowances are invalid")
    if stage == "registration":
        stage_deadline = cap_started_monotonic + registration_allowance_seconds
    elif stage == "provision":
        stage_deadline = (cap_started_monotonic + registration_allowance_seconds
            + provision_allowance_seconds)
    else:
        raise PlanReviewError("workflow review stage is unsupported")
    return min(stage_deadline, float(overall_deadline), float(stop_deadline))


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


def original_review_cutoff(directory: Path, binding: dict,
                           approved_horizon_epoch: float | None = None) -> float:
    """Bound review by original publication, producer deadline, and stage horizon.

    Old requests without timing metadata retain the publication-plus-40-second
    cutoff. New requests carry their finite setting and original deadline.
    """
    horizon = approved_horizon_epoch
    if (horizon is not None and (isinstance(horizon, bool)
            or not isinstance(horizon, (int, float)) or not math.isfinite(horizon))):
        raise PlanReviewError("approved review horizon must be a finite epoch")
    horizon = float("inf") if horizon is None else float(horizon)
    request_path = Path(directory) / "review-request.json"
    _hash(request_path)
    publication_mtime = request_path.stat().st_mtime
    has_window = "review_window_seconds" in binding
    has_deadline = "review_deadline_utc_epoch" in binding
    has_publication = "review_published_at_utc_epoch" in binding
    if not (has_window == has_deadline == has_publication):
        raise PlanReviewError("original review timing metadata is incomplete")
    if not has_window:
        return min(publication_mtime + LEGACY_PUBLICATION_REVIEW_SECONDS, float(horizon))
    window = binding["review_window_seconds"]
    producer_deadline = binding["review_deadline_utc_epoch"]
    producer_publication = binding["review_published_at_utc_epoch"]
    if (type(window) is not int or not 1 <= window <= MAX_SOURCE_BOUND_REVIEW_SECONDS
            or isinstance(producer_deadline, bool)
            or not isinstance(producer_deadline, (int, float))
            or not math.isfinite(producer_deadline) or producer_deadline <= 0
            or isinstance(producer_publication, bool)
            or not isinstance(producer_publication, (int, float))
            or not math.isfinite(producer_publication) or producer_publication <= 0):
        raise PlanReviewError("original review timing metadata is invalid")
    return min(float(producer_publication) + window, publication_mtime + window,
               float(producer_deadline), float(horizon))


def _deadline_epoch(deadline_monotonic: float) -> float:
    # Sampling wall time before monotonic time makes the conversion conservative.
    wall_now = time.time()
    monotonic_now = time.monotonic()
    epoch = wall_now + deadline_monotonic - monotonic_now
    if not math.isfinite(epoch) or epoch <= 0:
        raise PlanReviewError("original review deadline cannot be represented as an epoch")
    return epoch


def _review_request_matches(directory: Path, binding: dict) -> None:
    request_path = directory / "review-request.json"
    _same(_hash(request_path), hashlib.sha256(_json(binding)).hexdigest(),
          "immutable review request bytes")


def _review_is_closed(directory: Path) -> bool:
    return any((directory / name).exists() or (directory / name).is_symlink()
               for name in ("review-cancelled.json", "failure.json", "accepted-review.json"))


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
        boundary = v.get("expiry_role_permissions_boundary_arn")
        if boundary is not None:
            if (not isinstance(boundary, str)
                    or not re.fullmatch(rf"arn:aws:iam::{re.escape(v['account_id'])}:policy/(?:[A-Za-z0-9+=,.@_-]+/)*[A-Za-z0-9+=,.@_-]+", boundary)):
                raise PlanReviewError("AWS expiry permissions boundary must be a customer-managed policy in the configured account")
        _same(role.get("permissions_boundary"), boundary, "expiry role permissions boundary")
        expected_role_tags = ({} if boundary is None else {
            "cnb_run_id": v["run_id"], "cnb_deployment_id": v["deployment_id"]})
        _same(role.get("tags") or {}, expected_role_tags, "expiry role identity tags")
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
        for role in ("a", "b"):
            _fields(r[f"google_compute_instance_from_template.vm_{role}"], {"project": v["project_id"], "name": v[f"name_{role}"], "zone": v[f"zone_{role}"], "source_instance_template": v[f"template_{role}"]}, f"GCP VM {role}")
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
        if time.monotonic() >= deadline:
            raise PlanReviewError(f"operator inspection required before saved-plan apply; deadline expired at {directory}")
        if _review_is_closed(directory):
            raise PlanReviewError("operator review was cancelled or closed before acceptance")
        _review_request_matches(directory, binding)
        if receipt_path.exists() or receipt_path.is_symlink():
            _hash(receipt_path)
            receipt = json.loads(receipt_path.read_bytes())
            if (receipt.get("decision") != "approved" or receipt.get("binding") != binding
                    or not isinstance(receipt.get("reviewer"), str) or not receipt["reviewer"].strip()
                    or not isinstance(receipt.get("inspection"), str) or not receipt["inspection"].strip()):
                raise PlanReviewError("operator review rejected or bound to different plan/source/input/state bytes")
            if time.monotonic() >= deadline:
                raise PlanReviewError("operator receipt arrived after the original review cutoff")
            if _review_is_closed(directory):
                raise PlanReviewError("operator review was cancelled or closed before acceptance")
            _review_request_matches(directory, binding)
            os.chmod(receipt_path, 0o600)
            return receipt
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))


def _review_admission_lock(path: Path, deadline: float):
    """Acquire the campaign-wide review slot before publishing a review request."""
    path = Path(path)
    if not path.is_absolute() or any(parent.is_symlink() for parent in (path, *path.parents)):
        raise PlanReviewError("shared review admission path must be absolute and symlink-free")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise PlanReviewError("shared review admission lock is unavailable") from exc
    os.fchmod(fd, 0o600)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise PlanReviewError("shared review admission window expired before publication")
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def reviewed_apply(request: CommandRequest, run: Callable[[CommandRequest], CommandResult], *, review_seconds: int = 60,
                   command_timeout_seconds: int | None = None,
                   apply_timeout_seconds: float | None = None,
                   before_apply: Callable[[], float] | None = None,
                   review_admission_path: Path | None = None) -> CommandResult:
    """Plan -> JSON/scope -> bounded operator inspection -> same saved-plan apply."""
    if (apply_timeout_seconds is None) != (before_apply is None):
        raise PlanReviewError("split saved-apply deadlines require both a stage ceiling and a fresh-fit callback")
    if type(review_seconds) is not int or not 0 <= review_seconds <= MAX_SOURCE_BOUND_REVIEW_SECONDS:
        raise PlanReviewError("saved-plan review timeout must be an integer in 0..600")
    if (command_timeout_seconds is not None
            and (type(command_timeout_seconds) is not int or command_timeout_seconds <= 0)):
        raise PlanReviewError("per-command saved-plan timeout must be a positive integer")
    if (apply_timeout_seconds is not None and (isinstance(apply_timeout_seconds, bool)
            or not isinstance(apply_timeout_seconds, (int, float))
            or not math.isfinite(apply_timeout_seconds) or apply_timeout_seconds <= 0)):
        raise PlanReviewError("saved-apply stage ceiling must be finite and positive")
    started = time.monotonic()
    deadline = started + request.timeout_seconds
    # The shared queue consumes only the original command/stage budget. A
    # fresh operator window begins after this request actually owns the slot.
    review_runtime_deadline = deadline if review_admission_path is not None else None
    review_deadline = None
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
        budget = min(remaining, command_timeout_seconds) if command_timeout_seconds is not None else remaining
        return run(request.model_copy(update={"action_id": action, "argv": command, "timeout_seconds": budget}))

    lock_fd = None
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
        if review_admission_path is not None:
            lock_fd = _review_admission_lock(review_admission_path, review_runtime_deadline)
            review_deadline = min(review_runtime_deadline, time.monotonic() + (review_seconds if review_seconds > 0 else request.timeout_seconds))
        else:
            # A zero review_seconds is the legacy mode: operator review is
            # bounded by the original request deadline. Positive values set
            # a shorter manual-review window, still capped by that deadline.
            review_window = review_seconds if review_seconds > 0 else request.timeout_seconds
            review_deadline = min(deadline, time.monotonic() + review_window)
        if time.monotonic() >= review_deadline:
            raise PlanReviewError("saved-plan review window expired before request publication")
        if review_seconds > 0:
            binding.update(review_window_seconds=review_seconds,
                review_deadline_utc_epoch=_deadline_epoch(review_deadline))
            binding["review_published_at_utc_epoch"] = time.time()
        _private(directory / "review-request.json", _json(binding))
        if review_seconds > 0 and time.time() >= original_review_cutoff(directory, binding):
            raise PlanReviewError("original review cutoff elapsed before request publication completed")
        receipt = await_review(directory, binding, review_deadline)
        if time.monotonic() >= review_deadline or time.monotonic() >= deadline or _review_is_closed(directory):
            raise PlanReviewError("original apply deadline elapsed or review cutoff/cancellation before acceptance")
        _review_request_matches(directory, binding)
        _same((_hash(binary), _hash(json_path), _hash(variables), source_hash(root), _hash(state) if state.exists() or state.is_symlink() else None),
              (binary_hash, binding["json_sha256"], inputs_hash, sources_hash, state_hash), "reviewed artifact hashes")
        if time.monotonic() >= review_deadline or time.monotonic() >= deadline or _review_is_closed(directory):
            raise PlanReviewError("original apply deadline elapsed or review cutoff/cancellation before acceptance")
        _private(directory / "accepted-review.json", _json(receipt))
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            lock_fd = None
        # The legacy local -state override is not carried into a saved plan's
        # backend configuration. Keep apply on the original attempt state too.
        if time.monotonic() >= deadline:
            raise PlanReviewError("original apply deadline elapsed during saved-plan review")
        apply_budget = deadline - time.monotonic()
        if command_timeout_seconds is not None:
            apply_budget = min(apply_budget, command_timeout_seconds)
        if apply_timeout_seconds is not None:
            apply_budget = min(apply_budget, apply_timeout_seconds)
            if before_apply is not None:
                fresh_budget = before_apply()
                if (isinstance(fresh_budget, bool) or not isinstance(fresh_budget, (int, float))
                        or not math.isfinite(fresh_budget) or fresh_budget <= 0):
                    raise PlanReviewError("saved apply has no positive remaining provider budget")
                apply_budget = min(apply_budget, fresh_budget)
        if apply_budget <= 0:
            raise PlanReviewError("saved apply no longer fits its original stage deadline")
        apply_request = request.model_copy(update={"action_id": request.action_id,
            "argv": [*prefix, "apply", "-input=false", f"-state={state}", str(binary)],
            "timeout_seconds": apply_budget})
        result = run(apply_request)
        _private(directory / "apply-result.json", _json({"action_id": result.action_id, "outcome": str(result.outcome), "exit_code": result.exit_code}))
        return result
    except BaseException as exc:
        _private(directory / "failure.json", _json({"category": "terraform_plan_review", "error_type": type(exc).__name__}))
        cancelled_path = directory / "review-cancelled.json"
        if (lock_fd is not None and (directory / "review-request.json").exists()
                and not cancelled_path.exists() and not cancelled_path.is_symlink()):
            _private(directory / "review-cancelled.json", _json({"reason": "review deadline expired or operation failed",
                "action_id": request.action_id, "cancelled_at_monotonic": time.monotonic()}))
        raise
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


def record_operator_review(directory: Path, request_sha256: str, reviewer: str, inspection: str,
                           deadline_utc_epoch: float | None = None) -> Path:
    """Record an operator's completed inspection; never execute Terraform."""
    directory = directory.absolute()
    if directory.is_symlink() or any(p.is_symlink() for p in directory.parents):
        raise PlanReviewError("review directory must not traverse symlinks")
    request_path = directory / "review-request.json"
    _same(_hash(request_path), request_sha256, "inspection request hash")
    binding = json.loads(request_path.read_bytes())
    cutoff = original_review_cutoff(directory, binding, deadline_utc_epoch)
    if time.time() >= cutoff:
        raise PlanReviewError("operator receipt arrived after the original review cutoff")
    if (_review_is_closed(directory) or (directory / "operator-review.json").exists()
            or (directory / "operator-review.json").is_symlink()):
        raise PlanReviewError("review is cancelled, closed, or already has a receipt")
    _same(_hash(directory / "saved.tfplan"), binding["plan_sha256"], "plan hash")
    _same(_hash(directory / "saved-plan.json"), binding["json_sha256"], "plan JSON hash")
    _same(source_hash(Path(binding["root"])), binding["source_sha256"], "source hash")
    state_path = Path(binding["state"])
    state_hash = _hash(state_path) if state_path.exists() or state_path.is_symlink() else None
    _same(state_hash, binding["state_sha256"], "state hash")
    variable_candidates = sorted({candidate for base in (directory, directory.parent)
        for pattern in ("*.auto.tfvars.json", "*.tfvars.json", "*.tfvars", "vars.json")
        for candidate in base.glob(pattern) if not candidate.is_symlink() and candidate.is_file()
        and _hash(candidate) == binding["variables_sha256"]})
    if len(variable_candidates) != 1:
        raise PlanReviewError("original variable file does not uniquely match the review request")
    if (not isinstance(reviewer, str) or not reviewer.strip()
            or not isinstance(inspection, str) or not inspection.strip()):
        raise PlanReviewError("reviewer and completed inspection description are required")
    receipt = {"decision": "approved", "binding": binding, "reviewer": reviewer, "inspection": inspection}
    # Atomic publication prevents a waiting controller reading partial JSON.
    pending = directory / "operator-review.pending"
    _private(pending, _json(receipt))
    target = directory / "operator-review.json"
    if (time.time() >= cutoff or _review_is_closed(directory)
            or _hash(request_path) != request_sha256
            or _hash(directory / "saved.tfplan") != binding["plan_sha256"]
            or _hash(directory / "saved-plan.json") != binding["json_sha256"]
            or source_hash(Path(binding["root"])) != binding["source_sha256"]
            or (_hash(state_path) if state_path.exists() or state_path.is_symlink() else None) != binding["state_sha256"]
            or _hash(variable_candidates[0]) != binding["variables_sha256"]):
        raise PlanReviewError("review cutoff, cancellation, or bound plan inputs changed before receipt publication")
    # Hash verification can be expensive. Recheck the original cutoff and
    # request identity at the final publication boundary too.
    if time.time() >= cutoff or _review_is_closed(directory) or _hash(request_path) != request_sha256:
        raise PlanReviewError("review cutoff or request changed at receipt publication")
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
    parser.add_argument("--deadline-utc-epoch", type=float,
                        help="approved review horizon from the original disclosure command")
    args = parser.parse_args(argv)
    record_operator_review(args.directory, args.request_sha256, args.reviewer, args.inspection,
                           args.deadline_utc_epoch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
