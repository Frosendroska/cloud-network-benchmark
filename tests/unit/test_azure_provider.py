from pathlib import Path
import hashlib
import re
import pytest
from cloud_network_benchmark.azure_provider import _key_material, _slug


def test_authenticated_host_key_parser_rejects_untrusted_or_malformed_material():
    assert _key_material("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc vm\n") == "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE9F4huSqss0wx3n6f5+WJiEpePM3oXTbuOzBLRR5NCc"
    for text in ("", "not-a-key", "ssh-ed25519 !!!", "-----BEGIN OPENSSH PRIVATE KEY-----"):
        with pytest.raises(ValueError, match="malformed"):
            _key_material(text)


def test_workspace_slug_is_collision_safe_and_bounded():
    first = _slug(type("D", (), {"campaign_id":"f06", "deployment_id":"f06-azure-deployment"})())
    second = _slug(type("D", (), {"campaign_id":"f06", "deployment_id":"f06-azure-deployment-other"})())
    assert first.endswith(hashlib.sha256(b"f06\0f06-azure-deployment").hexdigest()[:8])
    assert first != second
    name=_slug(type("D", (), {"campaign_id":"x"*190, "deployment_id":"y"*190})())
    assert len(name) <= 45 and len(name+"-auto") <= 50


def test_expiry_root_declares_only_group_identity_and_narrow_role_prerequisites():
    root=Path(__file__).resolve().parents[2]/"terraform/azure/expiry"
    source=(root/"main.tf").read_text()
    declared=set(re.findall(r'resource\s+"([^"]+)"',source))
    assert declared=={"azurerm_resource_group","azurerm_automation_account","azurerm_role_definition","azurerm_role_assignment"}
    for forbidden in ("azurerm_linux_virtual_machine","azurerm_network_interface","azurerm_virtual_network","Contributor","Microsoft.Compute/virtualMachines/start/action","Microsoft.Compute/virtualMachines/delete"):
        assert forbidden not in source
    runbook=(root/"deallocate.ps1").read_text()
    assert "Connect-AzAccount -Identity" in runbook
    assert "/deallocate?api-version=2025-04-01" in runbook
    for forbidden in ("/start?","/delete?","Start-AzVM","Remove-AzVM"):
        assert forbidden not in runbook
