# F01 Offline Validation Evidence

**Date**: 2026-10-02 (F01 convergence follow-up)

**Feature**: `001-core-offline-contracts`

**Scope**: Credential-free local validation only

## Environment

| Component | Version |
| --- | --- |
| Python | 3.9.2 |
| pytest | 8.4.2 |
| Pydantic | 2.13.5 |
| PyYAML | 6.0.3 |
| jsonschema | 4.16.0 |

## Commands Executed

```bash
python3 -m cloud_network_benchmark validate --config configs/experiments/exp-001-multi-provider.yaml --format json
python3 -m cloud_network_benchmark resolve --config configs/experiments/exp-001-multi-provider.yaml --format json
python3 -m cloud_network_benchmark dry-run --config configs/tests/exp-900-single-provider.yaml --format human
python3 -m cloud_network_benchmark init --config configs/experiments/exp-001-multi-provider.yaml --results-root <temporary>/results --format json
python3 -m pytest tests/unit tests/contract tests/integration -q
python3 -m compileall -q src
```

The final complete suite passed **115 tests in 1.75 seconds** on the recorded development host. The performance test separately enforces resolution below one second and local initialization below two seconds, excluding injected filesystem failures.

## Evidence

- The three-provider campaign resolved exactly AWS, Azure, and GCP.
- All eight executable configurations validated against the same schema and resolved offline: the four three-provider S1-S4 primary campaigns, three provider-specific smoke campaigns, and one reduced three-provider smoke campaign.
- Provider-native regular/spot fields were preserved for AWS, Azure, and GCP, and invalid purchase values were rejected before side effects.
- Isolated initialization produced one parent manifest, three child manifests, three immutable child directories, and three byte-identical `config.yaml` snapshots.
- Every generated parent and child manifest validated against its published Draft 2020-12 JSON Schema.
- Failure injection covered each child-manifest reservation, result-directory, config-snapshot, and atomic-update boundary.
- Campaign reservation is atomic, and a child run ID is linked into the parent only after atomic child-manifest creation; every injected post-reservation failure retained exactly one valid failed manifest for each assigned ID.
- Recovery tests include a failed first recovery update and prove the no-fault fallback preserves valid parent and child evidence with structured recovery details.
- Parent state is recomputed after child execution and cleanup changes, and VM/provider/tool evidence can be atomically replaced from unavailable to observed values.
- Runtime validation and JSON Schema agree that S1-S3 require multi-flow, while S4 may disable it without an upload-stream count.
- All executable configuration IDs map to concrete Thesis experiment or smoke-validation anchors.
- Collision tests preserved every pre-existing byte.
- Socket and subprocess guards covered `validate`, `resolve`, `dry-run`, and `init`; no cloud API, Terraform, SSH, SCP, FLENT, or paid resource was contacted.
- Deterministic fake Git providers supplied commits in offline tests; normal initialization read repository provenance without launching Git.

## Dry-Run Acceptance Review

The fixture matrix contains all 12 provider/scenario combinations: AWS, Azure, and GCP crossed with `same_zone`, `cross_zone`, `placement_optimization`, and `inter_region`.

All 12 human renderings were reviewed as concise summaries. Each visibly contained the config source and hash, experiment and provider, canonical scenario, VM A/client and VM B/server placement and image, A-to-B direction, benchmark order and parameters, manifest and result paths, validator expectations, lifecycle and cleanup outline, artifact list, and planned action classes. Automated matrix generation and structural inspection remain comfortably inside the five-minute review bound. This is acceptance evidence, not a runtime scheduling or UI feature.

## Limitations

- F01 does not provision infrastructure, connect to VMs, execute benchmarks, collect diagnostics, or validate real result contents.
- The subprocess-backed command runner is a downstream execution boundary and was tested through scripted fakes; F01 CLI commands do not call it.
- Real cloud smoke tests, provider availability checks, calibration, recurring triggers, and paid campaigns remain outside this offline feature.
- Tool and VM/provider metadata that do not exist at initialization remain explicit unavailable evidence until later features observe them.
