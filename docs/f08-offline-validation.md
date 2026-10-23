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


## Resume after acquisition contract merge

After the recorded checkpoint above, F08 preserved its pre-merge edits in commit `e8b600d3cd30a9c31cf3e0cb50583e2518df08d` and integrated acquisition PR #16 via F01R commit `6971ba72cf5031ad50c4658606cc1d55c10242ac`. The merged acquisition sources unblock source-bound consumer work for representable short-mode artifact identities. F08 now verifies materialized build sidecar bytes and hashes, matches command build identity, replays the public acquisition helper, derives conservative source-bound active windows, and restricts RTT sample selection to those windows. Diagnostic-day replay remains incomplete: the day layout and `ValidatorInput` cannot express the unique chunk-scoped identities required by the helper. This is a confirmed shared-contract ownership gap assigned separately to F018; F08 has not fabricated artifact IDs or edited shared contracts. No Linux acquisition behavior or cloud behavior is proven.


## T047 materialized non-DAY replay checkpoint

F08 now materializes acquisition observation bytes and four role/tool build-identity sidecars for acceptance, short-study, and calibration replay. The fixtures use the canonical unit-local observation artifact IDs, actual F01R `bind_acquisition_sources` and public replay helpers, native iperf3 session cookies/settings/stream rows, and command evidence for the persistent `systemd-run` listener. The validation consumer recognizes this recorded F02R launcher form only when its acquisition environment, installed iperf3 path, receiver mode, bind address, port, and streaming flags are present. It does not infer those settings from an arbitrary argument substring.

Observed focused result at the current uncommitted checkpoint: acceptance bundles are valid and complete; all three materialized provider calibration bundles are valid and `select_common_n` selects N=2 with plateau evidence and a source digest; three frozen-N short-study bundles with prior-calibration provenance are valid and `evaluate_comparison` marks the three peers eligible; pending-N short-study bundles remain partial and ineligible. The helper invokes validation and summaries against saved bundle bytes, repeats validation deterministically, checks recorded hashes, and confirms a native CSV mutation is invalid before restoring the bytes. These remain synthetic offline contract fixtures, not cloud, Linux runtime, or research observations.

T047 remains unchecked because continuous DAY still depends on the serialized F018 shared-layout/identity repair. Its current replay remains partial and does not yet prove 86,400 one-second source-supported receiver intervals intersected with 288 native probe chunks. The earlier status entry above is historical at its checkpoint; subsequent merged source capability now supports the non-DAY evidence described here.


Verification after the non-DAY replay update (working tree based on `e8f319a`): F08-focused native/validation/calibration/summary/replay suite: 82 passed; full credential-free offline suite: 1,088 passed in 133.33 seconds with the bundled PowerShell on `PATH`; schema drift check, `compileall`, and `git diff --check`: exit 0. The full suite includes the existing 288-chunk DAY fixture, which remains partial and is not treated as evidence that T047 DAY acceptance is complete.
