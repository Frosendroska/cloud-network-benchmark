from pathlib import Path
import json
import re

ROOT = Path(__file__).parents[2]


def blocks(source, resource_type):
    return re.findall(rf'resource\s+"{resource_type}"\s+"[^"]+"\s*\{{', source)


def test_protection_is_templates_and_network_only():
    source = (ROOT / "terraform/gcp/protection/main.tf").read_text()
    assert len(blocks(source, "google_compute_instance_template")) == 1
    assert len(blocks(source, "google_compute_network")) == 1
    assert "termination_time            = var.expiry_time" in source
    assert 'instance_termination_action = "STOP"' in source
    assert "max_distance" not in source
    assert not re.search(r'resource\s+"google_compute_instance"\s', source)
    assert "disk_size_gb = 32" in source
    assert 'nic_type   = "GVNIC"' in source


def test_pair_has_exactly_two_template_inherited_instances_without_overrides():
    source = (ROOT / "terraform/gcp/pair/main.tf").read_text()
    assert len(blocks(source, "google_compute_instance_from_template")) == 2
    assert source.count("source_instance_template =") == 2
    assert not re.search(r'(?m)^\s*(scheduling|network_interface|boot_disk|metadata|machine_type)\s*[={]', source)
    assert "self_link_unique" not in source


def test_scenarios_and_security_inputs_are_exact_and_owned():
    fixture = json.loads((ROOT / "tests/fixtures/gcp/scenarios.json").read_text())
    scenarios = {item["id"]: item for item in fixture["scenarios"]}
    assert set(scenarios) == {"S1", "S2", "S3", "S4"}
    assert scenarios["S1"]["vm_a"]["zone"] == scenarios["S1"]["vm_b"]["zone"]
    assert scenarios["S2"]["vm_a"]["zone"] != scenarios["S2"]["vm_b"]["zone"]
    assert scenarios["S3"]["placement"] == {"collocation": "COLLOCATED"}
    assert scenarios["S4"]["vm_a"]["region"] != scenarios["S4"]["vm_b"]["region"]
    assert fixture["network_interface"] == {"count": 1, "type": "GVNIC", "tier_1": False}
    hcl = (ROOT / "terraform/gcp/protection/main.tf").read_text()
    assert 'source_ranges = [var.controller_cidr]' in hcl
    assert 'source_ranges = ["${local.ips[0]}/32"]' in hcl
    assert 'source_ranges = ["${local.ips[0]}/32", "${local.ips[1]}/32"]' in hcl
    assert 'collocation = "COLLOCATED"' in hcl
    assert "max_distance" not in hcl


def test_native_absolute_stop_is_controller_independent_and_templates_do_not_replace_cleanup():
    protection = (ROOT / "terraform/gcp/protection/main.tf").read_text()
    pair = (ROOT / "terraform/gcp/pair/main.tf").read_text()
    traces = json.loads((ROOT / "tests/fixtures/gcp/operation-traces.json").read_text())
    assert "termination_time            = var.expiry_time" in protection
    assert 'instance_termination_action = "STOP"' in protection
    assert "max_run_duration" not in protection
    assert "time_sleep" not in protection and "local-exec" not in protection
    assert traces["controller_loss"] == {"policy_survives_controller": True, "local_due_time_means_triggered": False, "template_deletion_cancels_live_policy": False}
    assert pair.index('resource "google_compute_instance_from_template" "vm_a"') < pair.index('resource "google_compute_instance_from_template" "vm_b"')
