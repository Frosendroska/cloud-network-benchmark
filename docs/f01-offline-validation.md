# F01 Offline Validation Evidence

**Date**: 2026-10-02 (F01 convergence follow-up)

**Feature**: `001-core-offline-contracts`

**Scope**: Credential-free local validation only

## Environment

| Component | Version |
| --- | --- |
| Python | 3.13.7 |
| pytest | 8.4.2 |
| Pydantic | 2.13.5 |
| PyYAML | 6.0.3 |
| jsonschema | 4.26.0 |

## Commands Executed

```bash
.venv/bin/python -m cloud_network_benchmark validate --config configs/experiments/exp-001-multi-provider.yaml --format json
.venv/bin/python -m cloud_network_benchmark resolve --config configs/experiments/exp-001-multi-provider.yaml --format json
.venv/bin/python -m cloud_network_benchmark dry-run --config configs/tests/exp-900-single-provider.yaml --format human
.venv/bin/python -m cloud_network_benchmark init --config configs/experiments/exp-001-multi-provider.yaml --results-root <temporary>/results --format json
THESIS_ROOT=../Thesis .venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src
```

The final complete suite passed **195 tests in 2.15 seconds** with `THESIS_ROOT` set to the read-only Thesis repository, on Python 3.13.7. Without that variable, the cross-repository traceability test is skipped. The performance test separately enforces resolution below one second and local initialization below two seconds, excluding injected filesystem failures.

## Evidence

- The three-provider campaign resolved exactly AWS, Azure, and GCP.
- All eight executable configurations validated against the same schema and resolved offline: the four three-provider S1-S4 primary campaigns, three provider-specific smoke campaigns, and one reduced three-provider smoke campaign. The main scenario matrix and provisional parameters are design-listed, not represented as advisor-approved.
- Provider-native regular/spot fields were preserved for AWS, Azure, and GCP, and invalid purchase values were rejected before side effects.
- Isolated initialization produced one parent manifest, three child manifests, three immutable child directories, and three byte-identical `config.yaml` snapshots.
- Every generated parent and child manifest validated against its published Draft 2020-12 JSON Schema.
- Failure injection covered each child-manifest reservation, result-directory, config-snapshot, and atomic-update boundary.
- Campaign reservation is atomic, and a child run ID is linked into the parent only after atomic child-manifest creation; every injected post-reservation failure retained exactly one valid failed manifest for each assigned ID.
- Recovery tests include a failed first recovery update and prove the no-fault fallback preserves valid parent and child evidence with structured recovery details.
- Parent state is recomputed after child execution and cleanup changes, and VM/provider/tool evidence can be atomically replaced from unavailable to observed values.
- Runtime validation and JSON Schema agree that S1-S3 require multi-flow, while S4 may disable it without an upload-stream count.
- Required provider locality, VM shape/image/user, phase name, and placement identifiers reject whitespace-only values in runtime validation and campaign JSON Schema; initialization tests prove rejection precedes ID allocation and result-root creation.
- Parent and child manifest models and schemas reject empty campaign IDs, including fixtures whose child run ID retains the provider suffix.
- Manifest runtime models and schemas reject malformed experiment IDs; child-reference schemas validate canonical manifest/result path shapes, while exact filename-to-`run_id` equality remains a runtime cross-property invariant.
- Initialization resolves the Thesis repository from `THESIS_ROOT`, linked-worktree Git metadata, or an adjacent `Thesis` directory and records deterministic injected design commits in offline tests.
- Implementation commit provenance resolves linked-worktree shared Git refs from `commondir`, including packed refs; unavailable evidence is retained only when repository metadata cannot provide a commit.
- All executable configuration IDs map to concrete Thesis experiment or smoke-validation anchors.
- Collision tests preserved every pre-existing byte.
- Socket and subprocess guards covered `validate`, `resolve`, `dry-run`, and `init`; no cloud API, Terraform, SSH, SCP, FLENT, or paid resource was contacted.
- Deterministic fake Git providers supplied commits in offline tests; normal initialization read repository provenance without launching Git.

## Dry-Run Acceptance Review

The fixture matrix contains all 12 provider/scenario combinations: AWS, Azure, and GCP crossed with `same_zone`, `cross_zone`, `placement_optimization`, and `inter_region`. All eight committed executable configs were also rendered in human mode, including all four scenario campaigns and the single-/three-provider test campaigns.

The human renderings were reviewed as concise summaries within five minutes. Each provider/scenario fixture visibly contained the config source and hash, experiment and provider, canonical scenario, VM A/client and VM B/server placement and image, A-to-B direction, benchmark order and parameters, manifest and result paths, validator expectations, lifecycle and cleanup outline, artifact list, and planned action classes. The all-provider scenarios exercise all 12 provider/scenario combinations; the reduced tests make provider selection and cost-oriented shapes apparent. This is lightweight acceptance evidence, not a runtime scheduling or UI feature.

## Limitations

- Manifest and result paths are repository-relative beneath the configured results root when it is inside the checkout; an isolated results root outside the checkout uses normalized absolute POSIX paths.
- F01 does not provision infrastructure, connect to VMs, execute benchmarks, collect diagnostics, or validate real result contents.
- The subprocess-backed command runner is a downstream execution boundary and was tested through scripted fakes; F01 CLI commands do not call it.
- Real cloud smoke tests, provider availability checks, calibration, recurring triggers, and paid campaigns remain outside this offline feature.
- Tool and VM/provider metadata that do not exist at initialization remain explicit unavailable evidence until later features observe them.
