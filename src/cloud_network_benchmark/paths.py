from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable

from pydantic import BaseModel, ConfigDict

from .contracts.common import Provider
from .errors import CollisionError, PersistenceError


FaultInjector = Callable[[str], None]


def no_fault(_point: str) -> None:
    return None


class AllocatedPaths(BaseModel):
    model_config = ConfigDict(frozen=True)
    campaign_manifest: Path
    child_manifests: Dict[Provider, Path]
    child_results: Dict[Provider, Path]


def allocate_paths(results_root: Path, campaign_id: str, run_ids: Dict[Provider, str]) -> AllocatedPaths:
    return AllocatedPaths(
        campaign_manifest=results_root / "manifests" / f"{campaign_id}.json",
        child_manifests={p: results_root / "manifests" / f"{rid}.json" for p, rid in run_ids.items()},
        child_results={p: results_root / "raw" / rid for p, rid in run_ids.items()},
    )


def preflight(paths: AllocatedPaths) -> None:
    destinations: Iterable[Path] = [
        paths.campaign_manifest,
        *paths.child_manifests.values(),
        *paths.child_results.values(),
    ]
    collisions = [str(path) for path in destinations if path.exists()]
    if collisions:
        raise CollisionError("occupied destination(s): " + ", ".join(collisions))
    paths.campaign_manifest.parent.mkdir(parents=True, exist_ok=True)
    next(iter(paths.child_results.values())).parent.mkdir(parents=True, exist_ok=True)


def reserve_json(path: Path, payload: Dict[str, Any], fault: FaultInjector = no_fault) -> None:
    fault("before_manifest_reservation")
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise CollisionError(f"occupied destination: {path}") from exc
    except OSError as exc:
        raise PersistenceError(f"cannot reserve manifest {path}: {exc}") from exc
    fault("after_manifest_reservation")


def atomic_write_json(path: Path, payload: Dict[str, Any], fault: FaultInjector = no_fault) -> None:
    fault("before_atomic_manifest_update")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_name = handle.name
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        raise PersistenceError(f"cannot update manifest {path}: {exc}") from exc
    fault("after_atomic_manifest_update")


def create_directory(path: Path, fault: FaultInjector = no_fault) -> None:
    fault("before_result_directory")
    try:
        path.mkdir()
    except FileExistsError as exc:
        raise CollisionError(f"occupied destination: {path}") from exc
    except OSError as exc:
        raise PersistenceError(f"cannot create result directory {path}: {exc}") from exc
    fault("after_result_directory")


def write_exclusive(path: Path, content: bytes, fault: FaultInjector = no_fault) -> None:
    fault("before_config_snapshot")
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise CollisionError(f"occupied destination: {path}") from exc
    except OSError as exc:
        raise PersistenceError(f"cannot create {path}: {exc}") from exc
    fault("after_config_snapshot")
