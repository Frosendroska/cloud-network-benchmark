"""Focused offline qualification of exact-request disclosure and pre-positioned watch."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
from threading import Event
import time

import pytest

from cloud_network_benchmark import plan_disclosure
from cloud_network_benchmark.terraform_plan import PlanReviewError

from tests.unit.test_terraform_plan_review import ROOT, plan_document, scope


def _request(attempt: Path, name: str, variables: dict | None = None,
             *, provider: str = "gcp", root: str = "protection",
             publish: bool = True) -> Path:
    if variables is None:
        variables = scope(provider, root)[0]
    vars_path = attempt / "inputs" / "safety-eu-north-1.tfvars.json"
    vars_path.parent.mkdir(parents=True, exist_ok=True)
    vars_path.write_text(json.dumps(variables, sort_keys=True))
    plan_root = ROOT / "terraform" / provider / root
    state = attempt / "original.tfstate"
    state_bytes = b'{"lineage":"offline-original-state"}\n'
    state.write_bytes(state_bytes)
    _, resources = scope(provider, root)
    plan = plan_document(variables, resources, provider)
    plan_bytes = (json.dumps(plan, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    if provider == "gcp":
        directory = attempt / "hash" / "run" / "deployment" / root / name
    else:
        directory = attempt / "namespace" / "run" / "deployment" / name
    directory.mkdir(mode=0o700, parents=True)
    binary = b"offline-original-plan:" + name.encode()
    (directory / "saved.tfplan").write_bytes(binary)
    (directory / "saved-plan.json").write_bytes(plan_bytes)
    binding = {
        "action_id": f"{provider}-{root}-apply", "root": str(plan_root), "state": str(state),
        "state_sha256": hashlib.sha256(state_bytes).hexdigest(), "plan_sha256": hashlib.sha256(binary).hexdigest(),
        "json_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "variables_sha256": hashlib.sha256(vars_path.read_bytes()).hexdigest(),
        "source_sha256": plan_disclosure.tp.source_hash(plan_root),
        "scope": plan_disclosure.tp.validate_scope(plan, provider, plan_root, variables),
    }
    request_path = directory / "review-request.json"
    request_bytes = (json.dumps(binding, sort_keys=True, indent=2) + "\n").encode()
    if publish:
        request_path.write_bytes(request_bytes)
    return request_path


def _variables():
    return scope("gcp", "protection")[0]


def _six_roots(base: Path, provider_root: Path) -> list[Path]:
    return [provider_root, *(base / f"allowlisted-provider-root-{index}" for index in range(1, 6))]


def test_watcher_selects_only_second_original_request_and_prints_original_receipt(
        tmp_path, capsys):
    attempt = tmp_path / "provider-root" / "gcp"
    attempt.mkdir(parents=True, mode=0o700)
    old = _request(attempt, "gcp-protection-apply-review-one")
    (old.parent / "accepted-review.json").write_text("{}")
    checkpoint = tmp_path / "checkpoint"
    cutoff = time.time() + 3

    def publish_second():
        time.sleep(0.08)
        return _request(attempt, "gcp-protection-apply-review-two")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(publish_second)
        from scripts import watch_plan_review
        published = watch_plan_review.watch(_six_roots(tmp_path, attempt), checkpoint,
                                            cutoff + 10, poll_seconds=0.005)
        current = pending.result()
    assert published == checkpoint / "gcp-protection-apply"
    output = capsys.readouterr().out
    assert str(current.parent) in output
    assert str(old.parent) not in output
    assert "human_review_required=true" in output
    assert "terraform_plan" in output
    assert str(hashlib.sha256(current.read_bytes()).hexdigest()) in output
    assert not (current.parent / "operator-review.json").exists()
    manifest = json.loads((checkpoint / "manifest.json").read_text())
    assert manifest["entries"][0]["review_directory"] == str(current.parent)


def test_exact_request_rejects_ambiguous_variable_hash_match(tmp_path):
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    request = _request(attempt, "new-review", _variables())
    vars_path = attempt / "inputs" / "safety-eu-north-1.tfvars.json"
    (attempt / "duplicate.tfvars.json").write_bytes(vars_path.read_bytes())
    with pytest.raises(PlanReviewError, match="uniquely bind"):
        plan_disclosure.disclose([attempt], tmp_path / "disclosure", time.time() + 10,
                                 request_path=request)
    assert not (tmp_path / "disclosure").exists()


def test_exact_request_rejects_foreign_path_and_changed_original(tmp_path):
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    request = _request(attempt, "new-review", _variables())
    foreign = tmp_path / "foreign"
    foreign.mkdir(mode=0o700)
    outside = _request(foreign, "foreign-review", _variables())
    with pytest.raises(PlanReviewError, match="outside the owned roots"):
        plan_disclosure.disclose([attempt], tmp_path / "foreign-disclosure", time.time() + 10,
                                 request_path=outside)
    (request.parent / "saved.tfplan").write_bytes(b"changed")
    with pytest.raises(PlanReviewError, match="do not match request binding"):
        plan_disclosure.disclose([attempt], tmp_path / "changed-disclosure", time.time() + 10,
                                 request_path=request)
    assert not (tmp_path / "changed-disclosure").exists()


@pytest.mark.parametrize("marker", ["operator-review.json", "accepted-review.json",
                                    "review-cancelled.json"])
def test_exact_request_rejects_nonpending_original(tmp_path, marker):
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    request = _request(attempt, "new-review", _variables())
    (request.parent / marker).write_text("{}")
    with pytest.raises(PlanReviewError, match="already has a receipt"):
        plan_disclosure.disclose([attempt], tmp_path / "disclosure", time.time() + 10,
                                 request_path=request)
    assert not (tmp_path / "disclosure").exists()


def test_exact_request_rejects_symlink_and_expired_cutoff(tmp_path):
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    request = _request(attempt, "new-review", _variables())
    alias = attempt / "alias-review"
    alias.symlink_to(request.parent, target_is_directory=True)
    with pytest.raises(PlanReviewError, match="symlink"):
        plan_disclosure.disclose([attempt], tmp_path / "symlink-disclosure", time.time() + 10,
                                 request_path=alias / "review-request.json")
    alias.unlink()
    with pytest.raises(PlanReviewError, match="deadline has elapsed"):
        plan_disclosure.disclose([attempt], tmp_path / "stale-disclosure", time.time() - 1,
                                 request_path=request)


def test_exact_request_rejects_sensitive_and_unrenderably_small_disclosure(tmp_path):
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    request = _request(attempt, "new-review", _variables())
    plan_path = request.parent / "saved-plan.json"
    plan = json.loads(plan_path.read_bytes())
    plan["private_key"] = "-----BEGIN PRIVATE KEY----- secret"
    plan_bytes = (json.dumps(plan, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    plan_path.write_bytes(plan_bytes)
    binding = json.loads(request.read_bytes())
    binding["json_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
    request.write_text(json.dumps(binding, sort_keys=True, indent=2) + "\n")
    with pytest.raises(PlanReviewError, match="sensitive-marked"):
        plan_disclosure.disclose([attempt], tmp_path / "sensitive-disclosure", time.time() + 10,
                                 request_path=request)
    assert not (tmp_path / "sensitive-disclosure").exists()
    (request.parent / "review-cancelled.json").write_text("{}")

    request = _request(attempt, "another-review", _variables())
    with pytest.raises(PlanReviewError, match="display limit"):
        plan_disclosure.disclose([attempt], tmp_path / "truncated-disclosure", time.time() + 10,
                                 display_limit=100, request_path=request)
    assert not (tmp_path / "truncated-disclosure").exists()


def test_watcher_rejects_multiple_new_requests(tmp_path):
    from scripts import watch_plan_review

    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    cutoff = time.time() + 3

    def publish_multiple():
        time.sleep(0.08)
        _request(attempt, "gcp-protection-apply-review-one")
        _request(attempt, "gcp-protection-apply-review-two")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(publish_multiple)
        with pytest.raises(PlanReviewError, match="multiple|ambiguous|changed"):
            watch_plan_review.watch(_six_roots(tmp_path, attempt), tmp_path / "checkpoint",
                                    cutoff + 10, poll_seconds=0.005)
        pending.result()
    assert not (tmp_path / "checkpoint").exists()


def test_watcher_rejects_request_with_stale_file_timestamp(tmp_path, monkeypatch):
    from scripts import watch_plan_review

    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    request = _request(attempt, "new-review-current", _variables())
    os.utime(request, (time.time() - 20, time.time() - 20))
    real_requests = watch_plan_review._requests
    calls = [0]

    def appear_after_start(roots, *args, **kwargs):
        calls[0] += 1
        return {} if calls[0] == 1 else real_requests(roots, *args, **kwargs)

    monkeypatch.setattr(watch_plan_review, "_requests", appear_after_start)
    with pytest.raises(PlanReviewError, match="predates this watcher"):
        watch_plan_review.watch(_six_roots(tmp_path, attempt), tmp_path / "checkpoint",
                                time.time() + 10, poll_seconds=0.001)
    assert not (tmp_path / "checkpoint").exists()


def test_prepositioned_watcher_handles_original_open_write_publication(tmp_path, capsys):
    from scripts import watch_plan_review

    attempt = tmp_path / "not-yet-created" / "gcp"
    roots = _six_roots(tmp_path, attempt)
    checkpoint = tmp_path / "checkpoint"
    watch_end = time.time() + 5
    baseline_ready = Event()

    def publish_in_place():
        assert baseline_ready.wait(timeout=2), "watcher did not validate its baseline"
        path = _request(attempt, "gcp-protection-apply-review-delayed", publish=False)
        payload = (json.dumps({
            "action_id": "gcp-protection-apply", "root": str(ROOT / "terraform/gcp/protection"),
            "state": str(attempt / "original.tfstate"),
            "state_sha256": hashlib.sha256((attempt / "original.tfstate").read_bytes()).hexdigest(),
            "plan_sha256": hashlib.sha256(b"offline-original-plan:gcp-protection-apply-review-delayed").hexdigest(),
            "json_sha256": hashlib.sha256((path.parent / "saved-plan.json").read_bytes()).hexdigest(),
            "variables_sha256": hashlib.sha256((attempt / "inputs/safety-eu-north-1.tfvars.json").read_bytes()).hexdigest(),
            "source_sha256": plan_disclosure.tp.source_hash(ROOT / "terraform/gcp/protection"),
            "scope": plan_disclosure.tp.validate_scope(
                json.loads((path.parent / "saved-plan.json").read_bytes()), "gcp",
                ROOT / "terraform/gcp/protection", _variables()),
        }, sort_keys=True, indent=2) + "\n").encode()
        with path.open("xb") as stream:
            stream.flush()
            time.sleep(0.05)
            stream.write(payload[: len(payload) // 2])
            stream.flush()
            time.sleep(0.05)
            stream.write(payload[len(payload) // 2 :])
            stream.flush()
            os.fsync(stream.fileno())

    with ThreadPoolExecutor(max_workers=1) as pool:
        producer = pool.submit(publish_in_place)
        assert not attempt.exists()
        published = watch_plan_review.watch(
            roots, checkpoint, watch_end, poll_seconds=0.005,
            on_ready=baseline_ready.set)
        producer.result()
    assert published == checkpoint / "gcp-protection-apply"
    output = capsys.readouterr().out
    assert "human_review_required=true" in output
    assert (checkpoint / "manifest.json").is_file()


def test_watcher_discovers_nested_aws_safety_request_under_late_provider_root(tmp_path, capsys):
    from scripts import watch_plan_review

    attempt = tmp_path / "another-provider-root" / "aws"
    roots = _six_roots(tmp_path, attempt)
    checkpoint = tmp_path / "aws-checkpoint"
    watch_end = time.time() + 5

    def publish_nested_aws():
        time.sleep(0.05)
        return _request(attempt, "aws-safety-eu-north-1-review-current",
                        provider="aws", root="safety")

    with ThreadPoolExecutor(max_workers=1) as pool:
        producer = pool.submit(publish_nested_aws)
        published = watch_plan_review.watch(roots, checkpoint, watch_end,
                                            poll_seconds=0.005)
        request = producer.result()
    assert published == checkpoint / "aws-safety-apply"
    assert request.parent == attempt / "namespace" / "run" / "deployment" / "aws-safety-eu-north-1-review-current"
    output = capsys.readouterr().out
    assert str(request.parent) in output
    assert "human_review_required=true" in output
    assert not (request.parent / "operator-review.json").exists()


@pytest.mark.parametrize(("provider", "root_name"), [("aws", "safety"), ("gcp", "protection")])
def test_mirror_provider_link_does_not_block_complete_original_disclosure(
        tmp_path, capsys, monkeypatch, provider, root_name):
    from scripts import watch_plan_review

    attempt = tmp_path / "acceptance" / "V-S3" / provider
    if provider == "aws":
        data_path = (attempt / "cnb-20261123T130643Z-exp-003-e83f53cd-aws-fixture"
                     / "20261123T130643Z-exp-003-e83f53cd-aws"
                     / "20261123T130643Z-exp-003-e83f53cd-aws-deployment"
                     / "terraform-data-safety-365dda5a")
        version = "6.70.0"
    else:
        data_path = (attempt / "08eed51c16ef"
                     / "20261123T130643Z-exp-003-e83f53cd-gcp"
                     / "20261123T130643Z-exp-003-e83f53cd-gcp-deployment"
                     / "protection" / "terraform-data-protection-e37f184c")
        version = "7.46.1"
    cache_link = (data_path / "providers" / "registry.terraform.io" / "hashicorp" /
                  ("google" if provider == "gcp" else provider) / version / "linux_amd64")
    mirror = tmp_path / "mirror"
    namespace_provider = "google" if provider == "gcp" else provider
    package = (mirror / "registry.terraform.io" / "hashicorp" / namespace_provider /
               version / "linux_amd64")
    package.mkdir(parents=True)
    (package / f"terraform-provider-{namespace_provider}_v{version}").write_text("offline fixture")
    config = tmp_path / "terraform.rc"
    config.write_text(f'provider_installation {{\n filesystem_mirror {{\n  path = "{mirror}"\n }}\n}}\n')
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", str(config))
    (package / "review-request.json").write_text('{"action_id":"hidden"}')
    cache_link.parent.mkdir(parents=True)
    cache_link.symlink_to(package, target_is_directory=True)
    roots = _six_roots(tmp_path, attempt)
    checkpoint = tmp_path / f"{provider}-checkpoint"
    ready = Event()
    watch_end = time.time() + 6

    def publish_after_ready():
        assert ready.wait(2)
        return _request(attempt, f"{provider}-{root_name}-apply-review-mirror",
                        provider=provider, root=root_name)

    with ThreadPoolExecutor(max_workers=1) as pool:
        producer = pool.submit(publish_after_ready)
        published = watch_plan_review.watch(roots, checkpoint, watch_end,
                                            poll_seconds=0.005, on_ready=ready.set)
        request = producer.result()

    assert published == checkpoint / f"{provider}-{root_name}-apply"
    manifest = json.loads((checkpoint / "manifest.json").read_text())
    entry = manifest["entries"][0]
    assert entry["review_directory"] == str(request.parent)
    assert entry["lossless"] is True
    assert (published / "review-request.json").read_bytes() == request.read_bytes()
    assert (published / "saved-plan.json").is_file()
    assert (published / "saved.tfplan").is_file()
    assert list(published.glob("variables*"))
    output = capsys.readouterr().out
    request_mtime = request.stat().st_mtime
    original_cutoff = min(request_mtime + 40.0, watch_end)
    assert time.time() < original_cutoff
    assert f"deadline_utc_epoch={original_cutoff}" in output
    assert "python -m cloud_network_benchmark.terraform_plan" in output
    assert entry["state_sha256"] == hashlib.sha256((attempt / "original.tfstate").read_bytes()).hexdigest()
    assert "human_review_required=true" in output
    assert not (request.parent / "operator-review.json").exists()


def test_nested_provider_cache_link_to_wrong_target_is_rejected(tmp_path, monkeypatch):
    from scripts import watch_plan_review

    attempt = tmp_path / "acceptance" / "V-S3" / "aws"
    link = (attempt / "cnb-fixture" / "fixture-aws" / "fixture-aws-deployment" /
            "terraform-data-safety-deadbeef" /
            "providers" / "registry.terraform.io" / "hashicorp" / "aws" / "6.70.0" / "linux_amd64")
    link.parent.mkdir(parents=True)
    wrong = tmp_path / "unapproved-provider-target"
    wrong.mkdir()
    link.symlink_to(wrong, target_is_directory=True)
    mirror = tmp_path / "approved-mirror"
    expected = mirror / "registry.terraform.io/hashicorp/aws/6.70.0/linux_amd64"
    expected.mkdir(parents=True)
    config = tmp_path / "terraform.rc"
    config.write_text(f'provider_installation {{\n filesystem_mirror {{\n  path = "{mirror}"\n }}\n}}\n')
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", str(config))
    with pytest.raises(PlanReviewError, match="symlink"):
        watch_plan_review.watch([attempt], tmp_path / "checkpoint", time.time() + 2)


@pytest.mark.parametrize(("provider", "root_name", "relative", "version"), [
    ("aws", "safety", "cnb-fixture/fixture-aws/fixture-aws-deployment/terraform-data-safety-deadbeef", "6.70.0"),
    ("aws", "pair", "cnb-fixture/fixture-aws/fixture-aws-deployment/terraform-data-pair-deadbeef", "6.70.0"),
    ("azurerm", "expiry", "20261123T130643Z-exp-003/20261123T130643Z-exp-003-deployment/terraform-data-expiry", "4.81.0"),
    ("azurerm", "pair", "20261123T130643Z-exp-003/20261123T130643Z-exp-003-deployment/terraform-data-pair", "4.81.0"),
    ("google", "protection", "08eed51c16ef/20261123T130643Z-exp-003-gcp/20261123T130643Z-exp-003-gcp-deployment/protection/terraform-data-protection-e37f184c", "7.46.1"),
    ("google", "pair", "08eed51c16ef/20261123T130643Z-exp-003-gcp/20261123T130643Z-exp-003-gcp-deployment/pair/terraform-data-pair-e37f184c", "7.46.1"),
])
def test_all_six_provider_cache_root_layouts_are_recognized(
        tmp_path, monkeypatch, provider, root_name, relative, version):
    root = tmp_path / "owned"
    mirror = tmp_path / "mirror"
    link = (root / relative / "providers" / "registry.terraform.io" /
            "hashicorp" / provider / version / "linux_amd64")
    target = mirror / "registry.terraform.io" / "hashicorp" / provider / version / "linux_amd64"
    target.mkdir(parents=True)
    link.parent.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=True)
    config = tmp_path / "terraform.rc"
    config.write_text(f'provider_installation {{\n filesystem_mirror {{\n  path = "{mirror}"\n }}\n}}\n')
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", str(config))
    assert plan_disclosure._terraform_provider_cache_link(
        link, root, plan_disclosure._provider_mirror_snapshot()) is True, (provider, root_name)


@pytest.mark.parametrize(("provider", "relative", "version"), [
    ("aws", "cnb-fixture/fixture-aws/fixture-aws-deployment/terraform-data-safety-deadbeef", "6.70.0"),
    ("aws", "cnb-fixture/fixture-aws/fixture-aws-deployment/terraform-data-pair-deadbeef", "6.70.0"),
    ("azurerm", "20261123T130643Z-exp-003/20261123T130643Z-exp-003-deployment/terraform-data-expiry", "4.81.0"),
    ("azurerm", "20261123T130643Z-exp-003/20261123T130643Z-exp-003-deployment/terraform-data-pair", "4.81.0"),
    ("google", "08eed51c16ef/20261123T130643Z-exp-003-gcp/20261123T130643Z-exp-003-gcp-deployment/protection/terraform-data-protection-e37f184c", "7.46.1"),
    ("google", "08eed51c16ef/20261123T130643Z-exp-003-gcp/20261123T130643Z-exp-003-gcp-deployment/pair/terraform-data-pair-e37f184c", "7.46.1"),
])
def test_all_six_provider_cache_gates_reject_wrong_target_and_config_mutation(
        tmp_path, monkeypatch, provider, relative, version):
    root = tmp_path / "owned"
    mirror = tmp_path / "mirror"
    link = (root / relative / "providers" / "registry.terraform.io" /
            "hashicorp" / provider / version / "linux_amd64")
    target = mirror / "registry.terraform.io" / "hashicorp" / provider / version / "linux_amd64"
    wrong_target = tmp_path / "wrong-target"
    target.mkdir(parents=True)
    wrong_target.mkdir()
    link.parent.mkdir(parents=True)
    link.symlink_to(wrong_target, target_is_directory=True)
    config = tmp_path / "terraform.rc"
    original_config = f'provider_installation {{\n filesystem_mirror {{\n  path = "{mirror}"\n }}\n}}\n'
    config.write_text(original_config)
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", str(config))
    snapshot = plan_disclosure._provider_mirror_snapshot()
    assert not plan_disclosure._terraform_provider_cache_link(link, root, snapshot)
    link.unlink()
    link.symlink_to(target, target_is_directory=True)
    assert plan_disclosure._terraform_provider_cache_link(link, root, snapshot)
    config.write_text(original_config + "\n")
    assert not plan_disclosure._terraform_provider_cache_link(
        link, root, snapshot), "mirror configuration mutation must invalidate the snapshot"


def test_exact_disclosure_allows_only_append_only_valid_peer_cache_link(
        tmp_path, monkeypatch):
    attempt = tmp_path / "acceptance" / "V-S1" / "aws"
    request = _request(attempt, "aws-safety-apply-review-selected",
                       provider="aws", root="safety")
    mirror = tmp_path / "mirror"
    config = tmp_path / "terraform.rc"
    config.write_text(f'provider_installation {{\n filesystem_mirror {{\n  path = "{mirror}"\n }}\n}}\n')
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", str(config))
    safety_target = mirror / "registry.terraform.io/hashicorp/aws/6.70.0/linux_amd64"
    safety_target.mkdir(parents=True)
    safety_link = (attempt / "cnb-fixture/fixture-aws/fixture-aws-deployment/"
                   "terraform-data-safety-deadbeef/providers/registry.terraform.io/"
                   "hashicorp/aws/6.70.0/linux_amd64")
    safety_link.parent.mkdir(parents=True)
    safety_link.symlink_to(safety_target, target_is_directory=True)
    mirror_snapshot = plan_disclosure._provider_mirror_snapshot()
    initial_links = {}
    plan_disclosure._request_inventory([attempt], mirror_snapshot, initial_links)
    original_inventory = plan_disclosure._request_inventory
    calls = 0

    def add_peer_cache_after_initial_inventory(roots, snapshot=None, cache_links=None):
        nonlocal calls
        inventory = original_inventory(roots, snapshot, cache_links)
        calls += 1
        if calls == 1:
            pair_target = mirror / "registry.terraform.io/hashicorp/aws/6.70.0/linux_amd64"
            pair_link = (attempt / "cnb-fixture/fixture-aws/fixture-aws-deployment/"
                         "terraform-data-pair-afd3ac4c/providers/registry.terraform.io/"
                         "hashicorp/aws/6.70.0/linux_amd64")
            pair_link.parent.mkdir(parents=True)
            pair_link.symlink_to(pair_target, target_is_directory=True)
        return inventory

    monkeypatch.setattr(plan_disclosure, "_request_inventory", add_peer_cache_after_initial_inventory)
    checkpoint = tmp_path / "checkpoint"
    plan_disclosure.disclose([attempt], checkpoint, time.time() + 10,
                             request_path=request, mirror_snapshot=mirror_snapshot,
                             cache_links=initial_links)
    assert (checkpoint / "manifest.json").is_file()
    assert not (request.parent / "operator-review.json").exists()


def test_accepted_peer_cache_links_remain_bound_against_mutation_and_deletion(tmp_path):
    safety = tmp_path / "safety-cache-link"
    pair = tmp_path / "pair-cache-link"
    original = {safety: ("target-a", "signature-a")}
    appended = {**original, pair: ("target-b", "signature-b")}
    assert plan_disclosure._cache_links_are_append_only(original, appended)
    assert not plan_disclosure._cache_links_are_append_only(
        appended, {**appended, pair: ("target-c", "signature-c")})
    assert not plan_disclosure._cache_links_are_append_only(appended, original)


def test_watcher_allows_peer_cache_initialization_during_selected_request_publication(
        tmp_path, capsys, monkeypatch):
    from scripts import watch_plan_review

    attempt = tmp_path / "acceptance" / "V-S1" / "aws"
    mirror = tmp_path / "mirror"
    config = tmp_path / "terraform.rc"
    config.write_text(f'provider_installation {{\n filesystem_mirror {{\n  path = "{mirror}"\n }}\n}}\n')
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", str(config))
    target = mirror / "registry.terraform.io/hashicorp/aws/6.70.0/linux_amd64"
    target.mkdir(parents=True)
    safety_link = (attempt / "cnb-fixture/fixture-aws/fixture-aws-deployment/"
                   "terraform-data-safety-deadbeef/providers/registry.terraform.io/"
                   "hashicorp/aws/6.70.0/linux_amd64")
    safety_link.parent.mkdir(parents=True)
    safety_link.symlink_to(target, target_is_directory=True)
    roots = _six_roots(tmp_path, attempt)
    ready = Event()
    checkpoint = tmp_path / "watcher-checkpoint"
    original_complete = watch_plan_review._complete_binding

    def initialize_pair_after_selection(path, owned, baseline, cutoff, watch_end, poll,
                                        mirror_snapshot, expected_cache_links):
        pair_link = (attempt / "cnb-fixture/fixture-aws/fixture-aws-deployment/"
                     "terraform-data-pair-afd3ac4c/providers/registry.terraform.io/"
                     "hashicorp/aws/6.70.0/linux_amd64")
        pair_link.parent.mkdir(parents=True)
        pair_link.symlink_to(target, target_is_directory=True)
        return original_complete(path, owned, baseline, cutoff, watch_end, poll,
                                 mirror_snapshot, expected_cache_links)

    monkeypatch.setattr(watch_plan_review, "_complete_binding", initialize_pair_after_selection)

    def publish_selected_request():
        assert ready.wait(2)
        return _request(attempt, "aws-safety-apply-review-concurrent",
                        provider="aws", root="safety")

    with ThreadPoolExecutor(max_workers=1) as pool:
        producer = pool.submit(publish_selected_request)
        published = watch_plan_review.watch(roots, checkpoint, time.time() + 5,
                                            poll_seconds=0.005, on_ready=ready.set)
        request = producer.result()
    assert published == checkpoint / "aws-safety-apply"
    assert (checkpoint / "manifest.json").is_file()
    assert not (request.parent / "operator-review.json").exists()
    assert "human_review_required=true" in capsys.readouterr().out


@pytest.mark.parametrize(("provider_root", "relative", "namespace", "provider", "version"), [
    ("aws", "cnb-20261123T130643Z-exp-903-b45cdaa1-aws-20261123T1306-a356363d50c461a781edbf16/20261123T130643Z-exp-903-b45cdaa1-aws/20261123T130643Z-exp-903-b45cdaa1-aws-deployment/terraform-data-safety-365dda5a", "hashicorp", "aws", "6.70.0"),
    ("aws", "cnb-20261123T130643Z-exp-003-e83f53cd-aws-20261123T1306-721bfafb573b50f9cf8ea865/20261123T130643Z-exp-003-e83f53cd-aws/20261123T130643Z-exp-003-e83f53cd-aws-deployment/terraform-data-safety-365dda5a", "hashicorp", "aws", "6.70.0"),
    ("azure", "20261123T130643Z-exp-903-b45cdaa1-azure/20261123T130643Z-exp-903-b45cdaa1-azure-deployment/terraform-data-expiry", "hashicorp", "azurerm", "4.81.0"),
    ("azure", "20261123T130643Z-exp-903-b45cdaa1-azure/20261123T130643Z-exp-903-b45cdaa1-azure-deployment/terraform-data-pair", "hashicorp", "azurerm", "4.81.0"),
    ("gcp", "4da92a874f43/20261123T130643Z-exp-903-b45cdaa1-gcp/20261123T130643Z-exp-903-b45cdaa1-gcp-deployment/protection/terraform-data-protection-e37f184c", "hashicorp", "google", "7.46.1"),
    ("gcp", "08eed51c16ef/20261123T130643Z-exp-003-e83f53cd-gcp/20261123T130643Z-exp-003-e83f53cd-gcp-deployment/protection/terraform-data-protection-e37f184c", "hashicorp", "google", "7.46.1"),
])
def test_all_recorded_v4_provider_cache_layouts_are_bound_to_configured_mirror(
        tmp_path, monkeypatch, provider_root, relative, namespace, provider, version):
    from scripts import watch_plan_review

    root = tmp_path / "acceptance" / "V-S1" / provider_root
    mirror = tmp_path / "mirror"
    target = mirror / "registry.terraform.io" / namespace / provider / version / "linux_amd64"
    target.mkdir(parents=True)
    (target / f"terraform-provider-{provider}_v{version}").write_text("fixture package")
    config = tmp_path / "terraform.rc"
    config.write_text(f'provider_installation {{\n filesystem_mirror {{\n  path = "{mirror}"\n }}\n}}\n')
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", str(config))
    link = root / relative / "providers" / "registry.terraform.io" / namespace / provider / version / "linux_amd64"
    link.parent.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=True)

    assert watch_plan_review._requests([root]) == {}


def test_cache_link_retarget_during_disclosure_withholds_manual_command(tmp_path, monkeypatch, capsys):
    attempt = tmp_path / "acceptance" / "V-S3" / "aws"
    link = (attempt / "cnb-fixture" / "fixture-aws" / "fixture-aws-deployment" /
            "terraform-data-safety-deadbeef" /
            "providers" / "registry.terraform.io" / "hashicorp" / "aws" / "6.70.0" / "linux_amd64")
    mirror = tmp_path / "mirror"
    target = mirror / "registry.terraform.io/hashicorp/aws/6.70.0/linux_amd64"
    target.mkdir(parents=True)
    (target / "provider-binary").write_text("fixture")
    link.parent.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=True)
    config = tmp_path / "terraform.rc"
    config.write_text(f'provider_installation {{\n filesystem_mirror {{\n  path = "{mirror}"\n }}\n}}\n')
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", str(config))
    request = _request(attempt, "aws-safety-eu-north-1-review-retarget", provider="aws", root="safety")
    original_pages = plan_disclosure._bounded_pages
    changed = [False]

    def retarget_after_page(*args, **kwargs):
        pages = original_pages(*args, **kwargs)
        if not changed[0]:
            link.unlink()
            alternative_mirror = tmp_path / "retargeted-mirror"
            alternative = alternative_mirror / "registry.terraform.io/hashicorp/aws/6.70.0/linux_amd64"
            alternative.mkdir(parents=True)
            config.write_text(f'provider_installation {{\n filesystem_mirror {{\n  path = "{alternative_mirror}"\n }}\n}}\n')
            link.symlink_to(alternative, target_is_directory=True)
            changed[0] = True
        return pages

    monkeypatch.setattr(plan_disclosure, "_bounded_pages", retarget_after_page)
    with pytest.raises(PlanReviewError, match="symlink"):
        plan_disclosure.disclose([attempt], tmp_path / "checkpoint", time.time() + 5,
                                 request_path=request)
    output = capsys.readouterr().out
    assert "human_review_required=true" not in output
    assert "python -m cloud_network_benchmark.terraform_plan" not in output
    assert not (request.parent / "operator-review.json").exists()


@pytest.mark.parametrize("link_name", ["alias", "linux_amd64"])
def test_unrecognized_or_malformed_mirror_symlink_still_fails_closed(tmp_path, link_name):
    from scripts import watch_plan_review

    attempt = tmp_path / "attempt"
    target = tmp_path / "outside"
    target.mkdir()
    if link_name == "alias":
        link = attempt / "alias"
    else:
        link = (attempt / "terraform-data-safety-test" / "providers" /
                "registry.terraform.io" / "hashicorp" / "aws" / "not-a-version" / link_name)
    link.parent.mkdir(parents=True)
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(PlanReviewError, match="symlink"):
        watch_plan_review.watch([attempt], tmp_path / "checkpoint", time.time() + 2)
    assert not (tmp_path / "checkpoint").exists()


def test_provider_cache_link_replacement_with_file_fails_closed(tmp_path, monkeypatch):
    from scripts import watch_plan_review

    attempt = tmp_path / "acceptance" / "V-S3" / "aws"
    link = (attempt / "cnb-fixture" / "fixture-aws" / "fixture-aws-deployment" /
            "terraform-data-safety-deadbeef" /
            "providers" / "registry.terraform.io" / "hashicorp" / "aws" / "6.70.0" / "linux_amd64")
    mirror = tmp_path / "mirror"
    package = mirror / "registry.terraform.io/hashicorp/aws/6.70.0/linux_amd64"
    package.mkdir(parents=True)
    config = tmp_path / "terraform.rc"
    config.write_text(f'provider_installation {{\n filesystem_mirror {{\n  path = "{mirror}"\n }}\n}}\n')
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", str(config))
    link.parent.mkdir(parents=True)
    link.symlink_to(package, target_is_directory=True)
    assert watch_plan_review._requests([attempt]) == {}
    link.unlink()
    file_target = tmp_path / "provider-file"
    file_target.write_text("changed cache target")
    link.symlink_to(file_target)
    with pytest.raises(PlanReviewError, match="symlink"):
        watch_plan_review._requests([attempt])


def test_incomplete_open_request_fails_at_fixed_watch_end(tmp_path):
    from scripts import watch_plan_review

    attempt = tmp_path / "provider-root" / "gcp"
    roots = _six_roots(tmp_path, attempt)
    watch_end = time.time() + 0.2

    def publish_incomplete():
        time.sleep(0.03)
        request = _request(attempt, "gcp-protection-apply-review-incomplete", publish=False)
        request.open("xb").close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        producer = pool.submit(publish_incomplete)
        with pytest.raises(PlanReviewError, match="did not become complete and stable"):
            watch_plan_review.watch(roots, tmp_path / "checkpoint", watch_end,
                                    poll_seconds=0.005)
        producer.result()
    assert not (tmp_path / "checkpoint").exists()


@pytest.mark.parametrize("watch_end,poll", [
    (float("nan"), 0.01), (float("inf"), 0.01), (time.time() + 1, float("nan")),
    (time.time() + 1, float("inf")),
])
def test_watcher_rejects_nonfinite_time_inputs(tmp_path, watch_end, poll):
    from scripts import watch_plan_review

    root = tmp_path / "provider-root" / "gcp"
    with pytest.raises(PlanReviewError, match="finite positive"):
        watch_plan_review.watch(_six_roots(tmp_path, root), tmp_path / "checkpoint",
                                watch_end, poll_seconds=poll)


def test_cancellation_during_disclosure_withholds_receipt_command(tmp_path, monkeypatch, capsys):
    attempt = tmp_path / "provider-root" / "gcp"
    attempt.mkdir(parents=True, mode=0o700)
    request = _request(attempt, "gcp-protection-apply-review-cancel-during-output")
    checkpoint = tmp_path / "checkpoint"
    original_pages = plan_disclosure._bounded_pages

    def cancel_after_page(*args, **kwargs):
        result = original_pages(*args, **kwargs)
        (request.parent / "review-cancelled.json").write_text("{}")
        return result

    monkeypatch.setattr(plan_disclosure, "_bounded_pages", cancel_after_page)
    with pytest.raises(PlanReviewError, match="no longer pending"):
        plan_disclosure.disclose([attempt], checkpoint, time.time() + 5,
                                 request_path=request)
    output = capsys.readouterr().out
    assert "human_review_required=true" not in output
    assert "python -m cloud_network_benchmark.terraform_plan" not in output
    assert (checkpoint / "manifest.json").is_file()


def test_second_request_appearing_during_disclosure_withholds_receipt_command(
        tmp_path, monkeypatch, capsys):
    attempt = tmp_path / "provider-root" / "gcp"
    attempt.mkdir(parents=True, mode=0o700)
    request = _request(attempt, "gcp-protection-apply-review-current")
    original_pages = plan_disclosure._bounded_pages
    published = [False]

    def publish_second_during_output(*args, **kwargs):
        result = original_pages(*args, **kwargs)
        if not published[0]:
            _request(attempt, "gcp-protection-apply-review-raced")
            published[0] = True
        return result

    monkeypatch.setattr(plan_disclosure, "_bounded_pages", publish_second_during_output)
    with pytest.raises(PlanReviewError, match="request set changed"):
        plan_disclosure.disclose([attempt], tmp_path / "checkpoint", time.time() + 5,
                                 request_path=request)
    output = capsys.readouterr().out
    assert "human_review_required=true" not in output
    assert "python -m cloud_network_benchmark.terraform_plan" not in output


@pytest.mark.parametrize("count", [1, 3, 6])
def test_watcher_accepts_bounded_packet_allowlist_sizes(tmp_path, count):
    from scripts import watch_plan_review

    roots = [tmp_path / f"root-{index}" for index in range(count)]
    with pytest.raises(PlanReviewError, match="finite overall watch end"):
        watch_plan_review.watch(roots, tmp_path / "checkpoint", time.time() + 0.01,
                                poll_seconds=0.002)


@pytest.mark.parametrize("count", [0, 7])
def test_watcher_rejects_unbounded_allowlist_sizes(tmp_path, count):
    from scripts import watch_plan_review

    roots = [tmp_path / f"root-{index}" for index in range(count)]
    with pytest.raises(PlanReviewError, match="between one and six"):
        watch_plan_review.watch(roots, tmp_path / "checkpoint", time.time() + 1)


def test_cli_emits_watch_ready_only_after_watch_baseline_validation(tmp_path, monkeypatch, capsys):
    from scripts import watch_plan_review

    seen = []

    def validated_watch(roots, checkpoint, watch_end, **kwargs):
        kwargs["on_ready"]()
        seen.append((roots, checkpoint, watch_end))
        return checkpoint

    monkeypatch.setattr(watch_plan_review, "watch", validated_watch)
    roots = [tmp_path / f"provider-root-{index}" for index in range(1, 4)]
    args = []
    for root in roots:
        args.extend(["--owned-root", str(root)])
    args.extend(["--checkpoint", str(tmp_path / "checkpoint"),
                 "--watch-end-epoch", str(time.time() + 10)])
    assert watch_plan_review.main(args) == 0
    assert len(seen) == 1
    assert capsys.readouterr().out == "watch_ready=true\n"
