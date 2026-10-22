from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "terraform/aws/safety/expiry.py"
spec = importlib.util.spec_from_file_location("aws_expiry", SCRIPT)
aws_expiry = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(aws_expiry)


class FakePaginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, *, Filters):
        self.client.calls += 1
        self.client.filters.append(Filters)
        if self.client.denied:
            raise RuntimeError("access denied")
        instances = self.client.pages_for_call()
        midpoint = len(instances) // 2
        yield {"Reservations": [{"Instances": instances[:midpoint]}]}
        yield {"Reservations": [{"Instances": instances[midpoint:]}]}


class FakeEc2:
    def __init__(self, instances=(), *, denied=False):
        self.instances = list(instances)
        self.denied = denied
        self.filters = []
        self.stop_calls = []
        self.calls = 0

    def get_paginator(self, operation):
        assert operation == "describe_instances"
        return FakePaginator(self)

    def pages_for_call(self):
        return list(self.instances)

    def stop_instances(self, *, InstanceIds):
        self.stop_calls.extend(InstanceIds)


def instance(instance_id, state="running", run_id="run-aws", deployment_id="run-aws-deployment"):
    return {
        "InstanceId": instance_id,
        "State": {"Name": state},
        "Tags": [
            {"Key": "cnb_run_id", "Value": run_id},
            {"Key": "cnb_deployment_id", "Value": deployment_id},
        ],
    }


def execute(client):
    now = [0.0]
    result = aws_expiry.stop_owned_instances(
        {"run_id": "run-aws", "deployment_id": "run-aws-deployment", "region": "eu-central-1"},
        ec2=client,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert now[0] == 300
    assert client.calls == 60
    return result


def test_expiry_requeries_empty_discovery_for_full_horizon_and_uses_both_tags():
    client = FakeEc2()
    result = execute(client)
    assert result["instances_seen"] == {}
    assert result["stop_requests"] == []
    assert result["complete_deletion"] is False
    assert all(
        {item["Name"] for item in filters} >= {"tag:cnb_run_id", "tag:cnb_deployment_id"}
        for filters in client.filters
    )


def test_expiry_stops_only_exact_owned_running_instances_and_reports_anomaly():
    client = FakeEc2([
        instance("i-owned-a"),
        instance("i-owned-b", "pending"),
        instance("i-owned-extra"),
        instance("i-foreign", run_id="other-aws"),
    ])
    result = execute(client)
    assert client.stop_calls == ["i-owned-a", "i-owned-extra"]
    assert result["owned_instance_count_anomaly"] is True
    assert "i-owned-b" in result["unresolved_instance_ids"]
    assert "i-foreign" not in result["instances_seen"]


def test_expiry_preserves_stop_request_separately_from_observed_state():
    client = FakeEc2([instance("i-running")])
    result = execute(client)
    assert result["stop_requests"] == ["i-running"]
    assert result["instances_seen"]["i-running"] == "running"
    assert result["unresolved_instance_ids"] == ["i-running"]


def test_expiry_records_denied_discovery_for_full_horizon():
    result = execute(FakeEc2(denied=True))
    assert len(result["errors"]) == 60
    assert result["unresolved_instance_ids"] == []


def test_expiry_finds_late_instance_and_observes_stopped_after_request():
    class DelayedEc2(FakeEc2):
        def pages_for_call(self):
            if self.calls < 4:
                return []
            if self.calls < 6:
                return [instance("i-late", "running")]
            if self.calls < 10:
                return [instance("i-late", "stopping")]
            return [instance("i-late", "stopped")]

    client = DelayedEc2()
    result = execute(client)
    assert client.stop_calls == ["i-late"]
    assert result["instances_seen"] == {"i-late": "stopped"}
    assert result["unresolved_instance_ids"] == []


def test_expiry_retries_throttled_discovery_and_incorrect_state_stop():
    class TransientError(RuntimeError):
        response = {"Error": {"Code": "ThrottlingException"}}

    class IncorrectState(RuntimeError):
        response = {"Error": {"Code": "IncorrectInstanceState"}}

    class RetryingEc2(FakeEc2):
        def pages_for_call(self):
            if self.calls == 1:
                raise TransientError("throttled")
            state = "stopped" if self.calls >= 4 else "running"
            return [instance("i-race", state)]

        def stop_instances(self, *, InstanceIds):
            self.stop_calls.extend(InstanceIds)
            if len(self.stop_calls) == 1:
                raise IncorrectState("state changed during stop")

    client = RetryingEc2()
    result = execute(client)
    assert client.calls == 60
    assert any(error["code"] == "ThrottlingException" for error in result["errors"])
    assert any(error["code"] == "IncorrectInstanceState" for error in result["errors"])
    assert result["instances_seen"]["i-race"] == "stopped"
    assert result["unresolved_instance_ids"] == []


def test_expiry_observes_pending_to_running_to_stopped_and_ignores_terminated():
    class TransitionEc2(FakeEc2):
        def pages_for_call(self):
            if self.calls == 4:
                return [instance("i-transition", "pending")]
            if self.calls == 5:
                return [instance("i-transition", "running")]
            if self.calls >= 6:
                return [instance("i-transition", "stopped")]
            return []

    client = TransitionEc2()
    result = execute(client)
    assert client.stop_calls == ["i-transition"]
    assert result["instances_seen"]["i-transition"] == "stopped"
    assert result["unresolved_instance_ids"] == []

    terminated = FakeEc2([instance("i-terminated", "terminated")])
    result = execute(terminated)
    assert terminated.stop_calls == []
    assert result["unresolved_instance_ids"] == []
