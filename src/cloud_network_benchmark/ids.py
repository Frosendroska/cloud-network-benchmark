from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Callable

from .contracts.common import Provider


Clock = Callable[[], datetime]
TokenSource = Callable[[], str]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def random_token() -> str:
    return secrets.token_hex(4)


def normalize_experiment_id(experiment_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", experiment_id.lower()).strip("-")


def candidate_campaign_id(experiment_id: str, clock: Clock = utc_now, token_source: TokenSource = random_token) -> str:
    stamp = clock().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = re.sub(r"[^a-zA-Z0-9]", "", token_source()).lower()
    if not token:
        raise ValueError("token source returned no usable characters")
    return f"{stamp}-{normalize_experiment_id(experiment_id)}-{token}"


def candidate_run_id(campaign_id: str, provider: Provider) -> str:
    return f"{campaign_id}-{Provider(provider).value}"


def candidate_attempt_id(campaign_id: str, provider: Provider) -> str:
    """Documentation alias: a run ID names one provider attempt."""
    return candidate_run_id(campaign_id, provider)


def candidate_observation_id(run_id: str) -> str:
    """Stable first-attempt observation identity; replacements carry it forward."""
    if not run_id or any(character.isspace() for character in run_id):
        raise ValueError("run_id must be a non-empty identifier")
    return f"{run_id}-observation"


def candidate_deployment_id(run_id: str) -> str:
    """Give each provider attempt its own deployment identity."""
    if not run_id or any(character.isspace() for character in run_id):
        raise ValueError("run_id must be a non-empty identifier")
    return f"{run_id}-deployment"
