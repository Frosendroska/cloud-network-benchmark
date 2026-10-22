from pathlib import Path
import hashlib
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
    assert len(_slug(type("D", (), {"campaign_id":"x"*190, "deployment_id":"y"*190})())) <= 50
