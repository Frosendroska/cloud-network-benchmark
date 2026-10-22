# F08 Offline Validation Record

This record covers the credential-free F08 implementation in `/private/tmp/cnb-f08`, branch `codex/f08-v2-validation`. No cloud API, SSH, Terraform apply, benchmark traffic, secrets, paid work or Thesis write was used. Sockperf and iperf3 artifacts are synthetic, source-format fixtures.

Environment: Python 3.11.13, `/private/tmp/cnb-offline-venv/bin/python`, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`.

| Check | Result |
|---|---|
| Spec Kit prerequisites (`--require-tasks --include-tasks`) | Exit 0; feature directory resolved to `/private/tmp/cnb-f08/specs/010-v2-native-validation`; required artifacts were present. |
| Focused validation and native replay (`test_validation.py`, `test_native_validation_replay.py`) | Exit 0; 42 passed. Includes schedule-content mutation and lazy JSONL parsing checks. |
| Full offline test suite | Exit 0; 495 passed. |
| CLI entrypoint help | Exit 0. |
| `git diff --check` | Exit 0 before final documentation/task-marker changes; rerun for final tree. |

The tested implementation commit is `7c78bee4f226e63b428eecf8f48346ed0dc6a76d`. The all-mode fixture replay task T039 remains open: current integration coverage parses materialized native files and validates mode shapes, but it does not yet build complete F01R bundles and run validation, summaries, comparison and common-N selection against them. Therefore the tests above do not establish the planned complete-bundle replay gate. All cloud and paid acceptance remain separate authorized stages; F10 remains the separate offline system-readiness gate.
