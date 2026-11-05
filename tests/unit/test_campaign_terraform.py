"""Provider-boundary tests for independent local Terraform work caches."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cloud_network_benchmark.contracts.execution import CommandResult


class Runner:
    def __init__(self, init_outcome="succeeded"):
        self.requests = []
        self.init_outcome = init_outcome

    def run(self, request):
        self.requests.append(request)
        outcome = self.init_outcome if request.action_id.startswith("terraform-init-") else "succeeded"
        now = datetime.now(timezone.utc)
        return CommandResult(action_id=request.action_id, outcome=outcome,
            started_at=now, finished_at=now, duration_seconds=0,
            exit_code=0 if outcome == "succeeded" else 1 if outcome == "failed" else None,
            stdout="", stderr="synthetic initialization failure" if outcome != "succeeded" else "")


def provider_driver(provider, tmp_path, runner):
    if provider == "aws":
        from tests.integration.test_aws_provider_sequences import make_inter_region_attempt
        from cloud_network_benchmark.providers.aws import AwsProviderDriver
        _, settings = make_inter_region_attempt(tmp_path)
        return AwsProviderDriver(runner, settings)
    from tests.unit.test_gcp_provider import settings
    from cloud_network_benchmark.providers.gcp import GcpProviderDriver
    return GcpProviderDriver(runner, settings(tmp_path))


def invoke(provider, driver, root, attempt, *, action="apply"):
    attempt.mkdir(parents=True, exist_ok=True)
    argv = ["terraform", f"-chdir={root}", action, "-input=false"]
    if provider == "aws":
        return driver._run(f"aws-pair-{action}", argv, 30, cwd=attempt)
    return driver._run(f"gcp-pair-{action}", argv, attempt, 30)


@pytest.mark.parametrize("provider", ["aws", "gcp"])
@pytest.mark.parametrize("outcome", ["failed", "timed_out", "interrupted"])
def test_init_failure_prevents_apply_and_retry_reinitializes(provider, outcome, tmp_path):
    runner = Runner(outcome)
    driver = provider_driver(provider, tmp_path, runner)
    with pytest.raises(RuntimeError, match="initialization failed"):
        invoke(provider, driver, tmp_path / "source" / "same_zone", tmp_path / "attempt")
    assert [r.action_id for r in runner.requests] == [f"terraform-init-{provider}-pair-apply"]
    assert not driver._initialized_terraform
    runner.init_outcome = "succeeded"
    invoke(provider, driver, tmp_path / "source" / "same_zone", tmp_path / "attempt")
    assert [r.action_id for r in runner.requests] == [f"terraform-init-{provider}-pair-apply",
        f"terraform-init-{provider}-pair-apply", f"{provider}-pair-apply"]


@pytest.mark.parametrize("provider", ["aws", "gcp"])
def test_two_attempts_and_distinct_terraform_roots_never_share_cache(provider, tmp_path):
    runner = Runner()
    driver = provider_driver(provider, tmp_path, runner)
    roots = [tmp_path / "sources" / name / "same_zone" for name in ("pair", "safety")]
    for attempt in (tmp_path / "P0-private", tmp_path / "P1-private"):
        for root in roots:
            invoke(provider, driver, root, attempt)
            invoke(provider, driver, root, attempt, action="output")
    inits = [request for request in runner.requests if request.action_id.startswith("terraform-init-")]
    assert len(inits) == 4
    directories = {request.environment["TF_DATA_DIR"] for request in inits}
    assert len(directories) == 4
    assert all(Path(path).is_dir() and Path(path).stat().st_mode & 0o777 == 0o700 for path in directories)
    assert all(request.argv[2:] == ["init", "-backend=false", "-input=false", "-lockfile=readonly"] for request in inits)
    for request in runner.requests:
        assert Path(request.environment["TF_DATA_DIR"]).parent == Path(request.cwd)


@pytest.mark.parametrize("provider", ["aws", "gcp"])
def test_slow_init_cannot_start_apply_after_original_deadline(provider, tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module(f"cloud_network_benchmark.providers.{provider}")
    monotonic = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: monotonic[0])

    class SlowInitialization(Runner):
        def run(self, request):
            result = super().run(request)
            if request.action_id.startswith("terraform-init-"):
                monotonic[0] = 20.0
            return result

    runner = SlowInitialization()
    driver = provider_driver(provider, tmp_path, runner)
    if provider == "aws":
        driver._precreate_deadline = 15.0
    else:
        driver._deadline = 15.0
    with pytest.raises(Exception, match="deadline|expired|elapsed|budget"):
        invoke(provider, driver, tmp_path / "source/same_zone", tmp_path / "attempt")
    assert [request.action_id for request in runner.requests] == [f"terraform-init-{provider}-pair-apply"]
