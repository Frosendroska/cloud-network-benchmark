"""The provider model and campaign preflight must agree without provider calls."""
from __future__ import annotations

import subprocess

import pytest

from cloud_network_benchmark.campaign_inputs import validate_stage_inputs
from tests.integration.test_campaign_admission import operator_settings
from tests.unit.test_gcp_provider import settings as runtime_settings


PREFIX = "projects/ubuntu-os-cloud/global/images/"
OBSERVED = PREFIX + "ubuntu-2404-noble-amd64-v20261105"
LEGACY = PREFIX + "ubuntu-2404-lts-amd64-v20261001"
REGIONS = ("europe-west3", "europe-west2", "europe-north1")


@pytest.fixture(params=["provider", "campaign"])
def validate_images(request, repository_root, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("image validation must not run an external command")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    if request.param == "provider":
        def validate(images):
            try:
                model = runtime_settings(tmp_path, images=images)
            except ValueError as exc:
                return [str(exc)]
            assert model.images == images
            return []
    else:
        operator = operator_settings(tmp_path, repository_root, monkeypatch)

        def validate(images):
            operator["providers"]["gcp"]["images"] = images
            errors = validate_stage_inputs({"stage": "acceptance"}, operator, repository_root)
            assert not (tmp_path / "private").exists()
            assert not (tmp_path / "results").exists()
            return errors
    return validate


@pytest.mark.parametrize("image", [OBSERVED, LEGACY, PREFIX + "ubuntu-2404-lts-amd64-20261001"],
                         ids=["observed-noble", "legacy-versioned", "legacy-dated"])
def test_concrete_ubuntu_images_are_accepted_by_both_entrypoints(validate_images, image):
    assert validate_images(dict.fromkeys(REGIONS, image)) == []


def test_regions_can_pin_distinct_concrete_image_names(validate_images):
    assert validate_images({"europe-west3": OBSERVED, "europe-west2": LEGACY,
                            "europe-north1": OBSERVED}) == []


def test_explicit_finland_image_can_join_baseline_map(validate_images):
    assert validate_images({"europe-west3": LEGACY, "europe-west2": LEGACY,
                            "europe-north1": OBSERVED}) == []


def test_provider_accepts_its_baseline_region_map(tmp_path):
    model = runtime_settings(tmp_path, images={"europe-west3": OBSERVED, "europe-west2": LEGACY})
    assert model.images == {"europe-west3": OBSERVED, "europe-west2": LEGACY}


def test_finland_only_image_map_requires_finland_selected_region():
    from cloud_network_benchmark.providers.gcp import validate_ubuntu_image_identities
    validate_ubuntu_image_identities({"europe-north1": OBSERVED}, selected_regions={"europe-north1"})
    with pytest.raises(ValueError, match="every selected GCP region"):
        validate_ubuntu_image_identities({"europe-north1": OBSERVED}, selected_regions={"europe-west3"})


@pytest.mark.parametrize("image", [
    OBSERVED.replace("ubuntu-os-cloud", "another-project"),
    OBSERVED.replace("ubuntu-os-cloud", "ubuntu-os-cloud-devel"),
    OBSERVED.replace("ubuntu-2404-noble", "debian-2404-noble"),
    OBSERVED.replace("2404-noble", "2204-jammy"),
    OBSERVED.replace("amd64", "arm64"),
    "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64",
    PREFIX + "ubuntu-2404-lts-amd64",
    PREFIX + "ubuntu-2404-noble-amd64-latest",
    PREFIX + "ubuntu-2404-lts-amd64-latest",
    PREFIX + "ubuntu-2404-lts-amd64-current",
    PREFIX + "ubuntu-2404-lts-amd64-v2026110",
    PREFIX + "ubuntu-2404-noble-amd64-v20261105-latest",
    PREFIX + "../images/ubuntu-2404-noble-amd64-v20261105",
    OBSERVED + "/../latest",
    OBSERVED + "%2f..%2flatest",
    OBSERVED + "?family=latest",
    OBSERVED + "#latest",
    "https://www.googleapis.com/compute/v1/" + OBSERVED,
    OBSERVED + "\n",
    OBSERVED + " ",
], ids=["wrong-project", "development-project", "wrong-os", "wrong-release", "wrong-arch",
        "family-path", "family-name", "noble-latest", "legacy-latest", "current", "short-version",
        "latest-suffix", "traversal", "trailing-path", "encoded-path", "query", "fragment",
        "url", "newline", "space"])
def test_aliases_wrong_images_and_unsafe_references_are_rejected(validate_images, image):
    errors = validate_images({"europe-west3": LEGACY, "europe-west2": LEGACY, "europe-north1": image})
    assert errors and "image" in errors[0]


@pytest.mark.parametrize("images", [{}, {"europe-west3": OBSERVED},
    {**dict.fromkeys(REGIONS, OBSERVED), "us-central1": OBSERVED},
    {"europe-west3": OBSERVED, "europe-west2": None}],
    ids=["empty", "missing-region", "extra-region", "non-string"])
def test_image_mapping_must_cover_exactly_the_selected_regions(validate_images, images):
    errors = validate_images(images)
    assert errors and "image" in errors[0]
