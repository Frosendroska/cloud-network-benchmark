"""Real review boundary; only external commands and operator are simulated."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from cloud_network_benchmark.contracts.execution import CommandRequest, CommandResult
from cloud_network_benchmark import terraform_plan as tp

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def result(req, stdout="", failed=False):
    return CommandResult(action_id=req.action_id, outcome="failed" if failed else "succeeded", started_at=NOW,
                         finished_at=NOW, duration_seconds=0, exit_code=1 if failed else 0, stdout=stdout, stderr="")


def scope(provider, root, scenario="same_zone"):
    if provider == "gcp":
        v = dict(project_id="review-project", zone_a="europe-north1-a", zone_b="europe-north1-a")
        if root == "pair":
            if scenario == "inter_region":
                v.update(zone_a="europe-west3-a", zone_b="europe-west2-a")
            v.update(name_a="scope-a", name_b="scope-b", template_a="projects/review-project/global/instanceTemplates/a?uniqueId=101", template_b="projects/review-project/global/instanceTemplates/b?uniqueId=102", scenario=scenario)
            r = {"terraform_data.scope_guard": {"input": v["project_id"]}}
            namespace = "scope"
            ips = ("10.80.1.10", "10.81.1.11") if scenario == "inter_region" else ("10.80.1.10", "10.80.1.11")
            for i, role in enumerate(("a", "b")):
                region = "-".join(v["zone_" + role].split("-")[:-1])
                suffix = "b" if role == "b" and scenario == "inter_region" else "a"
                nic = {"network": f"projects/{v['project_id']}/global/networks/{namespace}-net",
                       "subnetwork": f"projects/{v['project_id']}/regions/{region}/subnetworks/{namespace}-{suffix}",
                       "network_ip": ips[i], "nic_type": "GVNIC", "access_config": [{"nat_ip": None, "network_tier": "PREMIUM", "public_ptr_domain_name": None}]}
                r["google_compute_instance_from_template.vm_" + role] = {"project": v["project_id"], "zone": v["zone_" + role], "name": v["name_" + role], "source_instance_template": v["template_" + role], "network_interface": [nic]}
            return v, r
        v.update(namespace="scope", scenario=scenario, controller_cidr="203.0.113.4/32", public_key="fixture-public-key", labels={"cnb-run": "run", "cnb-deploy": "deploy"}, expiry_time="2026-11-14T00:20:00Z", image_a="projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20261105", image_b="projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20261105")
        if scenario == "inter_region": v["zone_b"] = "europe-west2-a"
        ips = ["10.80.1.10", "10.81.1.11" if scenario == "inter_region" else "10.80.1.11"]
        r = {"terraform_data.scope_guard": {"input": v["project_id"]}, "google_compute_network.attempt": {"project": v["project_id"], "name": "scope-net", "auto_create_subnetworks": False, "mtu": 1460}, "google_compute_subnetwork.a": {"project": v["project_id"], "name": "scope-a", "region": "europe-north1", "ip_cidr_range": "10.80.1.0/24"}}
        for name, sources, allow, tags in (("ssh", [v["controller_cidr"]], [{"protocol": "tcp", "ports": ["22"]}], []), ("listeners", [ips[0]+"/32"], [{"protocol": "tcp", "ports": ["5201"]}, {"protocol": "udp", "ports": ["11111"]}], ["scope-vm-b"]), ("pair_icmp", [ip+"/32" for ip in ips], [{"protocol": "icmp", "ports": []}], [])):
            r["google_compute_firewall."+name] = {"project": v["project_id"], "direction": "INGRESS", "source_ranges": sources, "allow": allow, "target_tags": tags}
        for i, role in enumerate(("a", "b")):
            r[f'google_compute_instance_template.role["vm_{role}"]'] = {"project": v["project_id"], "name": "scope-"+role, "machine_type": "n2-standard-4", "can_ip_forward": False, "labels": v["labels"], "scheduling": [{"provisioning_model": "STANDARD", "automatic_restart": False, "instance_termination_action": "STOP", "termination_time": v["expiry_time"]}], "disk": [{"source_image": v["image_"+role], "disk_size_gb": 32, "disk_type": "pd-balanced", "boot": True, "auto_delete": True}], "network_interface": [{"network_ip": ips[i], "nic_type": "GVNIC", "access_config": [{}]}], "reservation_affinity": [{"type": "NO_RESERVATION"}]}
        if scenario == "inter_region": r["google_compute_subnetwork.b[0]"] = {"project": v["project_id"], "name": "scope-b", "region": "europe-west2", "ip_cidr_range": "10.81.1.0/24"}
        if scenario == "placement_optimization": r["google_compute_resource_policy.compact[0]"] = {"project": v["project_id"], "name": "scope-compact", "region": "europe-north1", "group_placement_policy": [{"collocation": "COLLOCATED"}]}
        return v, r
    v = dict(account_id="238360820461", resource_namespace="scope", run_id="run-aws", deployment_id="run-aws-deployment")
    if root == "safety":
        v["region"] = "eu-north-1"
        name = "cnb-expiry-scope-"+hashlib.sha256(b"run-aws:run-aws-deployment:eu-north-1").hexdigest()[:24]
        trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ssm.amazonaws.com"}, "Action": "sts:AssumeRole", "Condition": {"StringEquals": {"aws:SourceAccount": v["account_id"]}, "ArnLike": {"aws:SourceArn": "arn:aws:ssm:eu-north-1:238360820461:automation-execution/*"}}}]}
        policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["ec2:DescribeInstances"], "Resource": "*"}, {"Effect": "Allow", "Action": ["ec2:StopInstances"], "Resource": "arn:aws:ec2:eu-north-1:238360820461:instance/*", "Condition": {"StringEquals": {"ec2:ResourceTag/cnb_run_id": v["run_id"], "ec2:ResourceTag/cnb_deployment_id": v["deployment_id"]}}}]}
        body = {"assumeRole": "{{ AutomationAssumeRole }}", "parameters": {k: {"type": "String"} for k in ("AutomationAssumeRole", "ExpiryTriggerUtc", "RunId", "DeploymentId", "Region")}, "mainSteps": [{"name": "waitUntilExpiry", "action": "aws:sleep", "inputs": {"Timestamp": "{{ ExpiryTriggerUtc }}"}}, {"name": "discoverAndStopOwnedInstances", "action": "aws:executeScript", "inputs": {"Runtime": "python3.11", "Handler": "handler", "Script": (ROOT/"terraform/aws/safety/expiry.py").read_text(), "InputPayload": {"run_id": "{{ RunId }}", "deployment_id": "{{ DeploymentId }}", "region": "{{ Region }}"}}, "timeoutSeconds": 330}]}
        return v, {"aws_iam_role.expiry": {"name": name, "assume_role_policy": json.dumps(trust)}, "aws_iam_role_policy.expiry": {"name": "cnb-expiry-stop-owned-instances", "policy": json.dumps(policy)}, "aws_ssm_document.expiry": {"name": name, "document_type": "Automation", "document_format": "JSON", "content": json.dumps(body)}}
    v.update(scenario=scenario, primary_region="eu-north-1", primary_zone="eu-north-1a", image_id_primary="ami-084df9e6ad4622caf", image_id_london="ami-fixture-london", controller_cidr="203.0.113.4/32", placement_group_name="scope-cluster")
    # Independently enumerated same-zone layout (no EIP, placement or London resources).
    names = ["terraform_data.account_guard", "aws_vpc.frankfurt", "aws_internet_gateway.frankfurt", "aws_route_table.frankfurt", "aws_subnet.a", "aws_route_table_association.a", "aws_security_group.vm_a", "aws_security_group.vm_b_frankfurt[0]", "aws_instance.vm_a", "aws_instance.vm_b_frankfurt[0]", "aws_ec2_tag.eni_a", "aws_ec2_tag.eni_a_deployment", "aws_ec2_tag.eni_b_frankfurt[0]", "aws_ec2_tag.eni_b_frankfurt_deployment[0]"]
    r = {name: {} for name in names}
    r["terraform_data.account_guard"] = {"input": v["account_id"]}
    r["aws_vpc.frankfurt"] = {"cidr_block": "10.60.0.0/16"}
    r["aws_subnet.a"] = {"cidr_block": "10.60.1.0/24", "availability_zone": "eu-north-1a", "map_public_ip_on_launch": True}
    for role, address in (("vm_a", "aws_instance.vm_a"), ("vm_b", "aws_instance.vm_b_frankfurt[0]")):
        r[address] = {"ami": v["image_id_primary"], "instance_type": "m7i.xlarge", "private_ip": "10.60.1.10" if role=="vm_a" else "10.60.1.11", "associate_public_ip_address": True, "user_data_replace_on_change": True, "root_block_device": [{"volume_size": 32, "volume_type": "gp3", "delete_on_termination": True}], "tags": {"cnb_run_id": v["run_id"], "cnb_deployment_id": v["deployment_id"], "cnb_role": role}}
    for role, address in (("vm_a", "aws_security_group.vm_a"), ("vm_b", "aws_security_group.vm_b_frankfurt[0]")):
        ingress = [{"protocol": "tcp", "from_port": 22, "to_port": 22, "cidr_blocks": [v["controller_cidr"]]}, {"protocol": "icmp", "from_port": 8, "to_port": 0, "cidr_blocks": ["10.60.1.11/32" if role=="vm_a" else "10.60.1.10/32"]}]
        if role=="vm_b": ingress += [{"protocol": protocol, "from_port": port, "to_port": port, "cidr_blocks": ["10.60.1.10/32"]} for protocol, port in (("tcp",5201),("udp",11111))]
        r[address] = {"ingress": ingress}
    return v, r


def plan_document(v, resources, provider):
    return {"format_version": "1.2", "variables": {k: {"value": value} for k, value in v.items()}, "resource_changes": [{"mode": "managed", "address": address, "type": address.split(".")[0], "provider_name": "terraform.io/builtin/terraform" if address.startswith("terraform_data.") else "registry.terraform.io/hashicorp/"+("aws" if provider=="aws" else "google"), "change": {"actions": ["create"], "before": None, "after": attributes}} for address, attributes in resources.items()]}


@pytest.mark.parametrize("provider,root", [("aws","safety"),("aws","pair"),("gcp","protection"),("gcp","pair")])
def test_all_four_roots_have_exact_fresh_scope(provider, root):
    v, resources = scope(provider,root)
    checked = tp.validate_scope(plan_document(v,resources,provider), provider, ROOT/"terraform"/provider/root, v)
    assert len(checked) == len(resources)


@pytest.mark.parametrize("scenario", ["same_zone", "cross_zone", "placement_optimization", "inter_region"])
def test_gcp_pair_review_pins_role_specific_template_addresses_and_networks(scenario):
    v, resources = scope("gcp", "pair", scenario)
    checked = tp.validate_scope(plan_document(v, resources, "gcp"), "gcp", ROOT/"terraform/gcp/pair", v)
    assert len(checked) == len(resources)


@pytest.mark.parametrize("scenario,count", [("same_zone",8),("cross_zone",8),("placement_optimization",9),("inter_region",9)])
def test_gcp_protection_scenario_scope(scenario,count):
    v,r = scope("gcp","protection",scenario)
    assert len(tp.validate_scope(plan_document(v,r,"gcp"),"gcp",ROOT/"terraform/gcp/protection",v)) == count


@pytest.mark.parametrize("mutation", ["extra","missing","update","delete","replace","noop","drift","variables","provider","import","deferred"])
def test_rejects_unreviewable_resource_or_input_changes(mutation):
    v,r = scope("gcp","pair")
    p = plan_document(v,r,"gcp")
    item = p["resource_changes"][1]
    if mutation == "extra": p["resource_changes"].append({**deepcopy(item),"address":"google_compute_disk.unapproved"})
    elif mutation == "missing": p["resource_changes"].pop()
    elif mutation in {"update","delete","replace","noop"}: item["change"]["actions"] = {"update":["update"],"delete":["delete"],"replace":["delete","create"],"noop":["no-op"]}[mutation]
    elif mutation == "drift": p["resource_drift"] = [item]
    elif mutation == "variables": p["variables"]["project_id"]["value"] = "foreign"
    elif mutation == "provider": item["provider_name"] = "unapproved/provider"
    elif mutation == "import": item["change"]["importing"] = {"id":"foreign"}
    elif mutation == "deferred": p["deferred_changes"] = [item]
    with pytest.raises(tp.PlanReviewError): tp.validate_scope(p,"gcp",ROOT/"terraform/gcp/pair",v)


@pytest.mark.parametrize("provider,root,address,path,value", [
    ("aws","pair","aws_instance.vm_a",("instance_type",),"m7i.2xlarge"),
    ("aws","pair","aws_instance.vm_a",("ami",),"ami-unapproved"),
    ("aws","pair","aws_instance.vm_a",("root_block_device",0,"volume_size"),64),
    ("aws","pair","aws_instance.vm_a",("associate_public_ip_address",),False),
    ("aws","pair","aws_subnet.a",("availability_zone",),"eu-west-2a"),
    ("aws","pair","aws_security_group.vm_a",("ingress",0,"cidr_blocks"),["0.0.0.0/0"]),
    ("aws","safety","aws_iam_role_policy.expiry",("policy",),'{"Statement":[]}'),
    ("gcp","pair","google_compute_instance_from_template.vm_b",("source_instance_template",),"foreign"),
    ("gcp","pair","google_compute_instance_from_template.vm_a",("project",),"foreign"),
    ("gcp","pair","google_compute_instance_from_template.vm_a",("network_interface",0,"network_ip"),"10.80.1.3"),
    ("gcp","pair","google_compute_instance_from_template.vm_b",("network_interface",0,"subnetwork"),"projects/review-project/regions/europe-north1/subnetworks/scope-b"),
    ("gcp","pair","google_compute_instance_from_template.vm_a",("network_interface",0,"access_config"),[{},{}]),
    ("gcp","protection",'google_compute_instance_template.role["vm_a"]',("disk",0,"disk_size_gb"),64),
    ("gcp","protection",'google_compute_instance_template.role["vm_a"]',("scheduling",0,"instance_termination_action"),"DELETE"),
    ("gcp","protection",'google_compute_instance_template.role["vm_a"]',("scheduling",0,"termination_time"),"later"),
    ("gcp","protection",'google_compute_instance_template.role["vm_b"]',("network_interface",0,"access_config"),[{},{}]),
    ("gcp","protection","google_compute_firewall.ssh",("source_ranges",),["0.0.0.0/0"]),
])
def test_critical_provider_scope_drift(provider,root,address,path,value):
    v,r = scope(provider,root)
    target = r[address]
    for key in path[:-1]: target = target[key]
    target[path[-1]] = value
    with pytest.raises(tp.PlanReviewError): tp.validate_scope(plan_document(v,r,provider),provider,ROOT/"terraform"/provider/root,v)


class SavedPlanRunner:
    def __init__(self, document): self.document, self.requests = document, []
    def run(self, req):
        self.requests.append(req)
        if "plan" in req.argv:
            Path(next(a.split("=",1)[1] for a in req.argv if a.startswith("-out="))).write_bytes(b"actual-saved-plan-fixture")
        return result(req, json.dumps(self.document) if "show" in req.argv else "")


def request(tmp_path, provider="gcp", root="pair"):
    v,r = scope(provider,root)
    variables = tmp_path/"vars.json"
    variables.write_text(json.dumps(v))
    state = tmp_path/"terraform.tfstate"
    return CommandRequest(action_id=f"{provider}-{root}-apply",action_type="provider_control",argv=["terraform",f"-chdir={ROOT/'terraform'/provider/root}","apply","-input=false","-auto-approve",f"-state={state}",f"-var-file={variables}"],cwd=str(tmp_path),timeout_seconds=60), SavedPlanRunner(plan_document(v,r,provider))


def approve(directory,binding,_deadline):
    receipt = {"decision":"approved", "binding":binding, "reviewer":"offline fixture", "inspection":"checked complete JSON and approved source scope"}
    (directory/"operator-review.json").write_text(json.dumps(receipt))
    return tp.await_review_original(directory,binding,_deadline)


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.setattr(tp,"await_review_original",tp.await_review,raising=False)
    monkeypatch.setattr(tp,"await_review",approve)


@pytest.mark.parametrize("provider,root",[("aws","safety"),("aws","pair"),("gcp","protection"),("gcp","pair")])
def test_applies_exact_reviewed_binary_without_vars_or_replanning(tmp_path,operator,provider,root):
    req,runner = request(tmp_path,provider,root)
    assert tp.reviewed_apply(req,runner.run).outcome == "succeeded"
    assert [r.argv[2] for r in runner.requests] == ["plan","show","apply"]
    planned = next(a.split("=",1)[1] for a in runner.requests[0].argv if a.startswith("-out="))
    state_arg = next(a for a in req.argv if a.startswith("-state="))
    assert runner.requests[-1].argv == [*req.argv[:2],"apply","-input=false",state_arg,planned]
    directory=Path(planned).parent
    evidence=json.loads((directory/"review-request.json").read_text())
    assert evidence["plan_sha256"] == hashlib.sha256(Path(planned).read_bytes()).hexdigest()
    assert json.loads((directory/"accepted-review.json").read_text())["binding"] == evidence
    assert all(p.stat().st_mode & 0o077 == 0 for p in directory.iterdir())
    assert directory.stat().st_mode & 0o077 == 0
    assert runner.requests[-1].timeout_seconds <= runner.requests[0].timeout_seconds


def test_aws_failed_saved_apply_retains_bounded_redacted_diagnostics(tmp_path, operator):
    req, runner = request(tmp_path, "aws", "pair")
    secret = "private-ssh-key:attempt-only"
    public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA fixture-public"
    req = req.model_copy(update={"redactions": [secret, public_key]})
    original_run = runner.run

    def fail_apply(command):
        if "apply" in command.argv:
            runner.requests.append(command)
            return result(command, failed=True).model_copy(update={
                "stderr": (f"apply denied; token=abc123 {secret} {public_key} AKIAABCDEFGHIJKLMNOP "
                           "Authorization: Bearer bearer-secret\n-----BEGIN RSA PRIVATE KEY-----\nprivate-bytes\n"
                           "-----END RSA PRIVATE KEY-----"),
                "stdout": "resource error",
                "redactions": command.redactions,
            })
        return original_run(command)

    returned = tp.reviewed_apply(req, fail_apply)
    assert returned.outcome == "failed"
    review_dir = next(tmp_path.glob("*-review-*"))
    evidence_path = review_dir / "apply-result.json"
    evidence = json.loads(evidence_path.read_text())
    assert review_dir.stat().st_mode & 0o777 == 0o700
    assert evidence_path.stat().st_mode & 0o777 == 0o600
    diagnostic = evidence["diagnostic"]
    assert "apply denied" in diagnostic["stderr"]
    assert "[REDACTED]" in diagnostic["stderr"]
    assert "abc123" not in diagnostic["stderr"]
    assert secret not in diagnostic["stderr"] and public_key not in diagnostic["stderr"]
    assert "AKIAABCDEFGHIJKLMNOP" not in diagnostic["stderr"]
    raw_stderr = (f"apply denied; token=abc123 {secret} {public_key} AKIAABCDEFGHIJKLMNOP "
                  "Authorization: Bearer bearer-secret\n-----BEGIN RSA PRIVATE KEY-----\nprivate-bytes\n"
                  "-----END RSA PRIVATE KEY-----")
    assert diagnostic["stderr_sha256"] == hashlib.sha256(raw_stderr.encode()).hexdigest()
    assert "bearer-secret" not in diagnostic["stderr"] and "private-bytes" not in diagnostic["stderr"]
    assert diagnostic["stderr_truncated"] is False


def test_missing_operator_receipt_never_applies_and_preserves_files(tmp_path):
    req,runner=request(tmp_path)
    with pytest.raises(tp.PlanReviewError,match="inspection required"): tp.reviewed_apply(req,runner.run)
    assert [r.argv[2] for r in runner.requests] == ["plan","show"]
    directory=next(tmp_path.glob("*-review-*"))
    assert {p.name for p in directory.iterdir()} >= {"saved.tfplan","saved-plan.json","review-request.json","failure.json"}


@pytest.mark.parametrize("field",["plan_sha256","json_sha256","variables_sha256","source_sha256","state_sha256","action_id","scope"])
def test_receipt_rejects_mismatched_binding(tmp_path,monkeypatch,field):
    original=tp.await_review
    def mismatched(directory,binding,deadline):
        bad={**binding,field:"different"}
        (directory/"operator-review.json").write_text(json.dumps({"decision":"approved","binding":bad,"reviewer":"fixture","inspection":"checked"}))
        return original(directory,binding,deadline)
    monkeypatch.setattr(tp,"await_review",mismatched)
    req,runner=request(tmp_path)
    with pytest.raises(tp.PlanReviewError,match="bound to different"): tp.reviewed_apply(req,runner.run)
    assert not any("apply" in r.argv for r in runner.requests)


@pytest.mark.parametrize("target",["binary","json","vars","state"])
def test_tampering_after_review_blocks_apply(tmp_path,monkeypatch,target):
    def tamper(directory,binding,deadline):
        path={"binary":directory/"saved.tfplan","json":directory/"saved-plan.json","vars":tmp_path/"vars.json","state":tmp_path/"terraform.tfstate"}[target]
        path.write_bytes(b"changed")
        return {"decision":"approved","binding":binding,"reviewer":"fixture","inspection":"checked"}
    monkeypatch.setattr(tp,"await_review",tamper)
    req,runner=request(tmp_path)
    with pytest.raises(tp.PlanReviewError,match="artifact hashes"): tp.reviewed_apply(req,runner.run)
    assert not any("apply" in r.argv for r in runner.requests)


def test_operator_wait_cannot_extend_command_deadline(tmp_path,monkeypatch):
    elapsed=[0.0]
    monkeypatch.setattr(tp.time,"monotonic",lambda:elapsed[0])
    def late_review(directory,binding,deadline):
        elapsed[0]=61
        return {"decision":"approved","binding":binding,"reviewer":"fixture","inspection":"checked"}
    monkeypatch.setattr(tp,"await_review",late_review)
    req,runner=request(tmp_path)
    with pytest.raises(tp.PlanReviewError,match="deadline elapsed"): tp.reviewed_apply(req,runner.run,review_seconds=600)
    assert not any("apply" in r.argv for r in runner.requests)


@pytest.mark.parametrize("provider,root",[("aws","safety"),("aws","pair"),("gcp","protection"),("gcp","pair")])
def test_provider_dispatch_requires_review_but_destroy_remains_accessible(tmp_path,provider,root):
    req,runner=request(tmp_path,provider,root)
    if provider == "aws":
        from tests.unit.test_aws_provider import make_settings
        from cloud_network_benchmark.providers.aws import AwsProviderDriver
        driver=AwsProviderDriver(runner,make_settings(tmp_path))
        run=lambda argv:driver._run(req.action_id,argv,60,cwd=tmp_path)
    else:
        from tests.unit.test_gcp_provider import settings
        from cloud_network_benchmark.providers.gcp import GcpProviderDriver
        driver=GcpProviderDriver(runner,settings(tmp_path))
        run=lambda argv:driver._run(req.action_id,argv,tmp_path,timeout=60)
    with pytest.raises(tp.PlanReviewError,match="inspection required"): run(req.argv)
    assert not any("apply" in r.argv for r in runner.requests)
    # Original owned recovery stays available without a review/financial receipt.
    destroy=["destroy" if a=="apply" else a for a in req.argv]
    assert run(destroy).outcome=="succeeded"
    assert "destroy" in runner.requests[-1].argv


@pytest.mark.parametrize("stage",["plan","show"])
def test_failed_external_plan_or_show_never_applies(tmp_path,stage):
    req,runner=request(tmp_path)
    def fail(command):
        response=runner.run(command)
        return result(command,response.stdout,failed=True) if stage in command.argv else response
    with pytest.raises(tp.PlanReviewError):tp.reviewed_apply(req,fail)
    assert not any("apply" in r.argv for r in runner.requests)


def test_source_hash_binds_child_source_and_lock(tmp_path):
    root=tmp_path/"terraform"
    root.mkdir()
    for name in ("main.tf","expiry.py",".terraform.lock.hcl"):
        p=root/name
        p.write_text("original")
        original=tp.source_hash(root)
        p.write_text("modified")
        assert tp.source_hash(root)!=original


def test_operator_recipe_publishes_receipt_for_exact_inspected_request(tmp_path,monkeypatch):
    original=tp.await_review
    def inspect(directory,binding,deadline):
        request_hash=hashlib.sha256((directory/"review-request.json").read_bytes()).hexdigest()
        assert tp.main([str(directory),"--request-sha256",request_hash,"--reviewer","offline operator","--inspection","checked scope, unknowns and support resources against approved source"])==0
        return original(directory,binding,deadline)
    monkeypatch.setattr(tp,"await_review",inspect)
    req,runner=request(tmp_path)
    assert tp.reviewed_apply(req,runner.run).outcome=="succeeded"


def test_recursive_financial_binding_includes_actual_child_roots(tmp_path):
    from types import SimpleNamespace
    from cloud_network_benchmark.lifecycle_runtime import _financial_runtime_binding
    from cloud_network_benchmark.contracts.common import StringEvidence
    tf=tmp_path/"terraform/aws/safety"
    tf.mkdir(parents=True)
    for name in ("main.tf","expiry.py",".terraform.lock.hcl"):(tf/name).write_text("fixture")
    bootstrap=tmp_path/"cloud-init.yaml"
    bootstrap.write_text("fixture")
    runtime={"providers":{"aws":{"bootstrap_template":bootstrap,"terraform_directory":tf.parent,"sources":{}}}}
    child=SimpleNamespace(config_provenance=SimpleNamespace(implementation_git_commit=StringEvidence(value="a"*40)))
    binding=_financial_runtime_binding(runtime,{"aws":child})
    paths={item["path"] for item in binding["sources"]}
    assert all(str(tf/name) in paths for name in ("main.tf","expiry.py",".terraform.lock.hcl"))
    assert any(path.endswith("/terraform_plan.py") for path in paths)


def test_gcp_fractional_deadline_is_not_rounded_up(tmp_path,monkeypatch):
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver
    from tests.unit.test_gcp_provider import settings
    runner=SavedPlanRunner({})
    driver=GcpProviderDriver(runner,settings(tmp_path))
    monkeypatch.setattr(tp.time,"monotonic",lambda:10.0)
    driver._deadline=10.25
    driver._run("gcp-read-only-fixture",["gcloud","fixture"],tmp_path)
    assert runner.requests[-1].timeout_seconds==0.25


def test_operator_rejection_never_applies(tmp_path,monkeypatch):
    original=tp.await_review
    def rejected(directory,binding,deadline):
        (directory/"operator-review.json").write_text(json.dumps({"decision":"rejected","binding":binding,"reviewer":"fixture","inspection":"unexpected scope"}))
        return original(directory,binding,deadline)
    monkeypatch.setattr(tp,"await_review",rejected)
    req,runner=request(tmp_path)
    with pytest.raises(tp.PlanReviewError,match="rejected"):tp.reviewed_apply(req,runner.run)
    assert not any("apply" in r.argv for r in runner.requests)
