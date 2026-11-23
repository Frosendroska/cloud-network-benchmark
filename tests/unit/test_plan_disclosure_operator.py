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
        "state_sha256": None, "plan_sha256": hashlib.sha256(binary).hexdigest(),
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

    def appear_after_start(roots):
        calls[0] += 1
        return {} if calls[0] == 1 else real_requests(roots)

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
            "state": str(attempt / "original.tfstate"), "state_sha256": None,
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
