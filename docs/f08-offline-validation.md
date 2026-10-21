# F08 Offline Validation Record

This record documents credential-free verification of F08 in `/private/tmp/cnb-f08`, branch `codex/f08-v2-validation`. Thesis sources were read-only. No cloud API, SSH, Terraform apply, benchmark traffic, secrets, or paid work was used.

Environment: Python 3.11.13, `/private/tmp/cnb-offline-venv/bin/python`, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`. All commands below ran with the worktree as the current directory.

| Check | Command | Result |
|---|---|---|
| Spec Kit prerequisite and task gate | `GIT_BRANCH_NAME=codex/f08-v2-validation SPECIFY_FEATURE_DIRECTORY=specs/010-v2-native-validation .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` | Exit 0; feature directory resolved under `/private/tmp/cnb-f08`; required spec, plan, research, data model, contracts, quickstart and tasks available. |
| Reviewer checklists | Count `[x]` and `[ ]` entries under `specs/010-v2-native-validation/checklists/` | 32 checked, 0 unchecked; checklist files were not modified. |
| Focused native/validation/calibration/summary/contract/replay tests | `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_native.py tests/unit/test_validation.py tests/unit/test_calibration.py tests/unit/test_summaries.py tests/contract/test_native_validation_interface.py tests/integration/test_native_validation_replay.py` | Exit 0; 66 passed. |
| Full credential-free regression | `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q` | Exit 0; 488 passed. |
| CLI entrypoint | `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark --help` | Exit 0; usage rendered. |
| Existing validation CLI help | `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark validate --help` | Exit 0; required `--config` and output format documented. |
| Source whitespace | `git diff --check` | Exit 0 before the final documentation-only commit; rerun for final tree. |

The tested implementation revision is `fb46e923e21a473f07c3ae20bcb94c4069438fcb`. It includes source, fixture and test changes; the focused and full suites passed against that exact committed implementation. The final feature revision additionally contains this record, the completed task checkboxes and the updated feature quickstart; its exact HEAD is returned in the coordinator handoff after the documentation commit.

F08 evidence remains synthetic and offline. This record does not claim cloud readiness, actual measurement success, paid acceptance or thesis results. F10 remains the separate offline system-readiness gate.
