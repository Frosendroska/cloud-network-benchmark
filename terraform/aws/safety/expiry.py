"""Inline payload for the AWS SSM Automation expiry step.

This file is also imported directly by credential-free tests.  Automation's
``aws:executeScript`` runtime calls ``handler(event, context)``; the injectable
arguments below exist only for local tests and are never passed by AWS.
"""
from __future__ import annotations

import time
from typing import Any, Callable


HORIZON_SECONDS = 300
POLL_SECONDS = 5
TRANSIENT_CODES = {
    "RequestLimitExceeded", "Throttling", "ThrottlingException",
    "ServiceUnavailable", "InternalError", "InternalServerError",
}


def stop_owned_instances(
    event: dict[str, Any],
    *,
    ec2: Any | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Discover exactly tagged instances for 300 seconds and request stop.

    The complete discovery horizon is retained even after an empty query or
    an apparently successful stop: an in-flight RunInstances call can become
    visible late.  This requests stop only; it neither forces stop nor deletes
    disks and other resources.
    """
    if ec2 is None:
        import boto3  # supplied by the AWS Automation Python runtime
        from botocore.config import Config

        ec2 = boto3.client(
            "ec2",
            region_name=event["region"],
            config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}),
        )

    run_id = event["run_id"]
    deployment_id = event["deployment_id"]
    selector = [
        {"Name": "tag:cnb_run_id", "Values": [run_id]},
        {"Name": "tag:cnb_deployment_id", "Values": [deployment_id]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
    ]
    deadline = clock() + HORIZON_SECONDS
    seen: dict[str, str] = {}
    stop_requests: list[str] = []
    errors: list[dict[str, str]] = []

    while clock() < deadline:
        try:
            paginator = ec2.get_paginator("describe_instances")
            for page in paginator.paginate(Filters=selector):
                for reservation in page.get("Reservations", []):
                    for instance in reservation.get("Instances", []):
                        tags = {tag.get("Key"): tag.get("Value") for tag in instance.get("Tags", [])}
                        if tags.get("cnb_run_id") != run_id or tags.get("cnb_deployment_id") != deployment_id:
                            continue
                        instance_id = str(instance["InstanceId"])
                        state = str(instance.get("State", {}).get("Name", "unknown"))
                        seen[instance_id] = state
                        if state == "running" and instance_id not in stop_requests:
                            try:
                                ec2.stop_instances(InstanceIds=[instance_id])
                                stop_requests.append(instance_id)
                            except Exception as exc:  # AWS API errors are evidence, not control flow
                                code = _error_code(exc)
                                errors.append({"instance_id": instance_id, "code": code})
                                if code not in TRANSIENT_CODES | {"IncorrectInstanceState"}:
                                    # Continue discovery to limit leakage, but retain the denial.
                                    pass
        except Exception as exc:
            errors.append({"instance_id": "*", "code": _error_code(exc)})
        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(POLL_SECONDS, remaining))

    unresolved = sorted(
        instance_id for instance_id, state in seen.items()
        if state in {"pending", "running", "stopping"}
    )
    return {
        "run_id": run_id,
        "deployment_id": deployment_id,
        "discovery_seconds": HORIZON_SECONDS,
        "instances_seen": dict(sorted(seen.items())),
        "stop_requests": sorted(stop_requests),
        "errors": errors,
        "owned_instance_count_anomaly": len(seen) > 2,
        "unresolved_instance_ids": unresolved,
        "complete_deletion": False,
    }


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", type(exc).__name__))
    return type(exc).__name__


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    return stop_owned_instances(event)
