"""Copy closed result bundles into a relocatable native-evidence selection."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _copy_verified(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise ValueError(f"result bundle is missing or unsafe: {source}")
    for item in source.rglob("*"):
        if item.is_symlink():
            raise ValueError(f"result bundle contains a symlink: {item}")
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    for item in source.rglob("*"):
        if item.is_file() and _digest(item) != _digest(destination / item.relative_to(source)):
            raise OSError(f"copied result checksum differs: {item.relative_to(source)}")


def export_selection(stage: dict[str, Any], destination: Path) -> Path | None:
    """Export completed runs, native files, and manifests; return selection.json."""
    destination = Path(destination).resolve()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"campaign export destination already exists: {destination}")
    results_root = Path(stage["results_root"]).resolve(strict=True)
    destination.mkdir(parents=True)
    run_rows: list[dict[str, str]] = []
    copied_sources: list[dict[str, str]] = []
    try:
        runs_dir, manifests_dir = destination / "runs", destination / "manifests"
        runs_dir.mkdir(); manifests_dir.mkdir()
        for entry in stage["entries"]:
            parent_source = Path(entry["parent_manifest_path"]).resolve(strict=True)
            parent_doc = json.loads(parent_source.read_text(encoding="utf-8"))
            parent_rel = f"manifests/{entry['entry_id']}-{parent_source.name}"
            parent_target = destination / parent_rel
            shutil.copy2(parent_source, parent_target)
            copied_sources.append({"source": str(parent_source), "sha256": _digest(parent_source), "export": parent_rel})
            for run_id in entry["run_ids"].values():
                run_id = str(run_id)
                raw_source = results_root / "raw" / run_id
                raw_target = runs_dir / run_id
                _copy_verified(raw_source, raw_target)
                children = [item for item in parent_doc.get("child_runs", []) if item.get("run_id") == run_id]
                if len(children) != 1:
                    raise ValueError(f"parent manifest does not uniquely identify run {run_id}")
                manifest_ref = Path(children[0]["manifest_path"])
                child_source = (Path(stage["repository_root"]) / manifest_ref).resolve(strict=True)
                child_rel = f"manifests/{run_id}-{child_source.name}"
                shutil.copy2(child_source, destination / child_rel)
                copied_sources.append({"source": str(child_source), "sha256": _digest(child_source), "export": child_rel})
                input_path = raw_target / "validation" / "input.json"
                result_path = raw_target / "validation" / "result.json"
                if not input_path.is_file() or not result_path.is_file():
                    copied_sources.append({"source": str(input_path), "sha256": "unavailable",
                                           "export": f"runs/{run_id}/partial-validation"})
                    continue
                original_input_sha, original_result_sha = _digest(input_path), _digest(result_path)
                input_doc = json.loads(input_path.read_text(encoding="utf-8"))
                # checksums.sha256 covers the original native validator input. Keep
                # that byte-for-byte file intact and put relocatable references in
                # a separate derived descriptor used only by selection.json.
                descriptor_dir = destination / "descriptors"
                descriptor_dir.mkdir(exist_ok=True)
                descriptor_path = descriptor_dir / f"{run_id}-input.json"
                input_doc["campaign_manifest_path"] = parent_rel
                input_doc["child_manifest_path"] = child_rel
                input_doc["child_result_root"] = f"runs/{run_id}"
                input_doc["config_snapshot_path"] = "config.yaml"
                input_doc["resolved_snapshot_path"] = "config.resolved.json"
                schedule_snapshot = input_doc.get("schedule_snapshot_path")
                if isinstance(schedule_snapshot, dict) and schedule_snapshot.get("value"):
                    schedule_snapshot["value"] = "schedule.csv"
                descriptor_path.write_text(json.dumps(input_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                copied_sources.extend((
                    {"source": str(input_path), "sha256": original_input_sha,
                     "export": f"runs/{run_id}/validation/input.json", "export_sha256": _digest(input_path)},
                    {"source": str(input_path), "sha256": original_input_sha,
                     "export": f"descriptors/{run_id}-input.json", "derived_sha256": _digest(descriptor_path)},
                    {"source": str(result_path), "sha256": original_result_sha,
                     "export": f"runs/{run_id}/validation/result.json", "export_sha256": original_result_sha},
                ))
                run_rows.append({"validator_input": f"descriptors/{run_id}-input.json",
                                 "validation_result": f"runs/{run_id}/validation/result.json"})
        selection_path = destination / "selection.json" if run_rows else None
        if selection_path:
            selection_path.write_text(json.dumps({"format_version": 1, "runs": run_rows},
                                                 indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (destination / "export-provenance.json").write_text(json.dumps({"schema_version": 1,
            "stage_id": stage["stage_id"], "source_manifests": copied_sources,
            "selection_sha256": _digest(selection_path) if selection_path else None}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return selection_path
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def export_entry(stage: dict[str, Any], entry: dict[str, Any], destination: Path) -> Path | None:
    """Export one terminal entry so its evidence is durable before later slots run."""
    return export_selection({**stage, "entries": [entry]}, destination)


def verify_export(destination: Path) -> bool:
    """Recheck every file recorded by an export provenance document."""
    destination = Path(destination)
    if destination.is_symlink() or not destination.is_dir():
        return False
    if any(item.is_symlink() for item in destination.rglob("*")):
        return False
    provenance_path = destination / "export-provenance.json"
    if not provenance_path.is_file() or provenance_path.is_symlink():
        return False
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        for row in provenance.get("closed_exports", []):
            nested = destination.parent / row["export"]
            nested_provenance = nested / "export-provenance.json"
            if (_digest(nested_provenance) != row.get("sha256") or not verify_export(nested)):
                return False
        for row in provenance.get("source_manifests", []):
            relative = row.get("export")
            expected = row.get("export_sha256") or row.get("derived_sha256") or row.get("sha256")
            if expected == "unavailable":
                continue
            if not relative or not expected:
                continue
            item = destination / relative
            if not item.is_file() or item.is_symlink() or _digest(item) != expected:
                return False
        selection = provenance.get("selection_sha256")
        selection_path = destination / "selection.json"
        if selection is not None and (not selection_path.is_file() or _digest(selection_path) != selection):
            return False
        checksum_indexes = list(destination.rglob("checksums.sha256"))
        if (destination / "runs").is_dir() and not checksum_indexes:
            return False
        for index in checksum_indexes:
            bundle_root = index.parent.resolve(strict=True)
            for line in index.read_text(encoding="utf-8").splitlines():
                parts = line.split("  ", 1)
                if len(parts) != 2 or len(parts[0]) != 64:
                    return False
                expected, relative = parts
                relative_path = Path(relative)
                if relative_path.is_absolute():
                    return False
                item = (bundle_root / relative_path).resolve(strict=True)
                try:
                    item.relative_to(bundle_root)
                except ValueError:
                    return False
                if not item.is_file() or item.is_symlink() or _digest(item) != expected:
                    return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def aggregate_closed_exports(stage: dict[str, Any], journal: dict[str, Any], destination: Path) -> Path | None:
    """Create a small selection over per-entry exports without duplicating raw data."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"campaign export destination already exists: {destination}")
    destination.mkdir(parents=True)
    rows: list[dict[str, str]] = []
    provenance_rows: list[dict[str, str]] = []
    descriptor_rows: list[dict[str, str]] = []
    try:
        for entry in stage["entries"]:
            record = journal["entries"][entry["entry_id"]]
            if record["state"] not in {"complete", "failed"} or (record.get("result") or {}).get("cleanup_open"):
                continue
            bundle = Path(stage["results_root"]) / "closed" / entry["entry_id"] / "export"
            if not verify_export(bundle):
                continue
            selected = bundle / "selection.json"
            if not selected.is_file():
                continue
            document = json.loads(selected.read_text(encoding="utf-8"))
            prefix = f"../closed/{entry['entry_id']}/export/"
            for row in document.get("runs", []):
                source_descriptor = bundle / row["validator_input"]
                descriptor = json.loads(source_descriptor.read_text(encoding="utf-8"))
                for field in ("campaign_manifest_path", "child_manifest_path", "child_result_root"):
                    value = descriptor.get(field)
                    if isinstance(value, str) and value and not Path(value).is_absolute():
                        descriptor[field] = prefix + value
                descriptor_dir = destination / "descriptors"
                descriptor_dir.mkdir(exist_ok=True)
                descriptor_target = descriptor_dir / f"{entry['entry_id']}-{source_descriptor.name}"
                descriptor_target.write_text(json.dumps(descriptor, indent=2, sort_keys=True) + "\n",
                                             encoding="utf-8")
                descriptor_rel = str(descriptor_target.relative_to(destination))
                descriptor_rows.append({"source": str(source_descriptor.relative_to(destination.parent)),
                                        "export": descriptor_rel, "derived_sha256": _digest(descriptor_target)})
                rows.append({"validator_input": descriptor_rel,
                             "validation_result": prefix + row["validation_result"]})
            provenance_rows.append({"export": str(bundle.relative_to(Path(stage["results_root"]))),
                                    "sha256": _digest(bundle / "export-provenance.json") or ""})
        selection_path = destination / "selection.json" if rows else None
        if selection_path:
            selection_path.write_text(json.dumps({"format_version": 1, "runs": rows},
                                                 indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (destination / "export-provenance.json").write_text(json.dumps({
            "schema_version": 1, "stage_id": stage["stage_id"], "closed_exports": provenance_rows,
            "source_manifests": descriptor_rows,
            "selection_sha256": _digest(selection_path) if selection_path else None
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return selection_path
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
