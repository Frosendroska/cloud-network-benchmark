# F08 Offline Validation Record

This record covers the credential-free F08 implementation in `/private/tmp/cnb-f08`, branch `codex/f08-v2-validation`. No cloud API, SSH, Terraform apply, benchmark traffic, secrets, paid work or Thesis write was used. Sockperf and iperf3 artifacts are synthetic, source-format fixtures.

Environment: Python 3.11.13, `/private/tmp/cnb-offline-venv/bin/python`, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`.

| Check | Result |
|---|---|
| Spec Kit prerequisites (`--require-tasks --include-tasks`) | Exit 0; feature directory resolved to `/private/tmp/cnb-f08/specs/010-v2-native-validation`; required artifacts were present. |
| F08-focused native, validation, calibration, summary, contract and replay tests | Exit 0; 82 passed against code checkpoint `26581dcd17b91719a6b8229639551343e45b1d9b`. |
| Full offline test suite | Exit 0; 709 passed against code checkpoint `26581dcd17b91719a6b8229639551343e45b1d9b`. |
| `compileall -q src/cloud_network_benchmark` | Exit 0. |
| F01R shared-contract schema drift check | Exit 0 with `python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check`. |
| Historical/shared contract preservation | `git diff --name-only origin/main -- src/cloud_network_benchmark/contracts specs/001-core-offline-contracts specs/002-vm-access-bootstrap specs/003-v2-shared-contracts` returned no paths; merged main is `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121`. |
| `git diff --check` | Exit 0 after code integration. |

The final feature branch contains code checkpoints `87177a3f2daf5e60f6628c86fc3e576c71c138bd` and `26581dcd17b91719a6b8229639551343e45b1d9b`, plus merge commit `6cf5ecf9b8cfc7765a4b7a92868c0c33817181a5` bringing in F03 from main `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121`. T039/T047 materialized replays invoke validation, summary, comparison and calibration selection against saved F01R bytes; they verify deterministic reruns and source hashes, and use 288 one-second receiver intervals for DAY. The F03 `MeasurementTiming` producer currently records `overlap_seconds` as unavailable and F01R has no source-bound active-probe acquisition-window intervals. Therefore the validator reports coverage unavailable and replayed acceptance/calibration/day bundles remain partial; frozen-N short-study peers remain ineligible. F08 does not invent interval evidence or a schema field. T047 remains unchecked pending a coordinator-owned producer/contract capability decision. This is not cloud or research evidence. Real cloud and paid acceptance remain separate authorized stages; F10 remains the separate offline system-readiness gate.
