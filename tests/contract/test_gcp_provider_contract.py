from pathlib import Path
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
