# F08 Offline Validation Record

This record covers the credential-free F08 implementation in `/private/tmp/cnb-f08`, branch `codex/f08-v2-validation`. No cloud API, SSH, Terraform apply, benchmark traffic, secrets, paid work or Thesis write was used. Sockperf and iperf3 artifacts are synthetic, source-format fixtures.

Environment: Python 3.11.13, `/private/tmp/cnb-offline-venv/bin/python`, `PYTHONPATH=src`, `THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`.

| Check | Result |
|---|---|
| Spec Kit prerequisites (`--require-tasks --include-tasks`) | Exit 0; feature directory resolved to `/private/tmp/cnb-f08/specs/010-v2-native-validation`; required artifacts were present. |
| F08-focused native, validation, calibration, summary, contract and replay tests | Exit 0; 78 passed at the integrated head. |
| Full offline test suite | Exit 0; 637 passed at the integrated head. |
| Spec Kit prerequisites (`--require-tasks --include-tasks`) | Exit 0 at the integrated head. |
| F01R shared-contract schema drift check | Exit 0 with `python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check`. |
| Historical/shared contract preservation | No F08 changes in contracts or historical F01/F02 specs relative to merged main `2a958c84523274455c5b91fd75ae3d513eead4d9`; the inherited clock-contract schema update remains from merged F01R. |
| `git diff --check` | Exit 0 after integration. |

The tested integrated source head is `a29b51fc881076c1bb28b872e92a6dad1cb974e1` (F08 implementation checkpoint `f42aaef` plus merged F02R main). T039 now materializes F01R bundles from initializer/resolver artifacts for acceptance, short study, three-provider calibration and diagnostic day with all 288 probe chunks. Replay invokes validation, summary, comparison and calibration selection against saved bytes; deterministic reruns and a saved-native SHA tamper are checked. The fixtures include actual serialized configuration, resolved and schedule snapshots, manifests, commands, timings, both-role metadata/diagnostics and native Sockperf/iperf3 evidence. No validity object or parser was mocked. Short-study results preserve pending CommonN and remain comparison-ineligible. All cloud and paid acceptance remain separate authorized stages; F10 remains the separate offline system-readiness gate.
