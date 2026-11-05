"""Private Terraform provider/work caches for independent concurrent calls."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .contracts.execution import CommandRequest


def private_terraform_environment(argv, cwd: Path) -> dict[str, str]:
    root = next((arg.partition("=")[2] for arg in argv if arg.startswith("-chdir=")), None)
    if root is None:
        return {}
    key = Path(root).name + "-" + hashlib.sha256(root.encode()).hexdigest()[:8]
    data = cwd.absolute() / ("terraform-data-" + key)
    if data.is_symlink():
        raise ValueError("Terraform data directory cannot be a symlink")
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {**os.environ, "TF_DATA_DIR": str(data)}


def initialize_terraform(runner, request: CommandRequest, initialized: set[str]):
    """Init local backend/provider cache once per private attempt/root, with no apply."""
    data = request.environment.get("TF_DATA_DIR")
    if not data or data in initialized or "init" in request.argv:
        return
    chdir = next(arg for arg in request.argv if arg.startswith("-chdir="))
    init = request.model_copy(update={"action_id": "terraform-init-" + request.action_id,
        "argv": [request.argv[0], chdir, "init", "-backend=false", "-input=false", "-lockfile=readonly"]})
    result = runner.run(init)
    if result.outcome != "succeeded" or result.exit_code != 0:
        raise RuntimeError(f"private Terraform initialization failed: {request.action_id}")
    initialized.add(data)
