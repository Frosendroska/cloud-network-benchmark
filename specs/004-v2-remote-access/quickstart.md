# F02R offline validation guide

This is an implementation acceptance guide. Planning checks do not establish that the not-yet-built V2 remote behavior passes.

## Prerequisites

Use `/private/tmp/cnb-f02r`, branch `codex/f02r-v2-remote-access`, and merged F01R base `cef838e42fc620f90b5943f5a8d1528ddc0b0724`. The offline environment already provides pytest/Pydantic/YAML; do not fetch packages, sources or tools as part of tests. Canonical Thesis context is read-only at `/Users/katya/Desktop/University/Masters/Thesis`. Test connection files are synthetic temp files, never real keys/credentials. No actual SSH/SCP, cloud command or benchmark client may run.

## Run after implementation

From `/private/tmp/cnb-f02r`, run the focused suite (the new integration path is created by the implementation tasks):

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_remote_access_contract.py tests/unit/test_bootstrap.py tests/unit/test_readiness.py tests/unit/test_retrieval.py tests/integration/test_remote_sequences.py tests/integration/test_v2_remote_access.py
```

Then run the complete regression:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
git diff --check
git status --short
git rev-parse HEAD
```

Expected: commands exit 0, no unrelated changes, recorded exact tested head. Test counts are measured at execution, never copied from historical F02/F01R. Record commands/results in `docs/f02r-remote-access.md`. The executable and existing test paths were inspected during planning; the new path is planned and must not be claimed as run before it exists.

## Required observations

| Case | Evidence required | Requirements |
| --- | --- | --- |
| Valid two-VM preparation | Actual recorded strict SSH argv, both source digests verified before builds, compiler/binary path/version/hash, complete capability observations, B units/private bindings/ACL; repeat preparation preserves one instance and logs | FR-001–005, SC-001 |
| Negative preparation | Missing/empty/mismatched host refs, source hash mismatch, failed build, missing flags and stale service cause failure; no later setup/readiness success; all exported evidence redacted | FR-002–006 |
| Shared readiness deadline | Fake time spent in earlier VM/check/build reduces later budget; every command/wait capped; no invocation after zero; same deadline passed from prepare through readiness | FR-006–007, FR-012, SC-002 |
| Specific observation predicates | Both cloud-init states, ICMP and TCP connect, exact UDP/TCP private sockets, correct units/binaries and narrow ACL; wrong bind/process/port/rule fails without service start/restart | FR-005–007 |
| Closed inventory | Measurement active/open producers rejects before copy; closed incomplete-day chunks wait until experiment end; both-role native artifacts with dynamic resolved unit IDs | FR-008, SC-004 |
| Materialized copy | Fake creates bytes at the actual request staging destination; compare remote/local size/hash and invoke shared materialized-file verifier on final path; stdout-only success is rejected | FR-009, SC-003 |
| Partial and retry | Missing/corrupt/truncated/zero-byte cases, optional absence, one-VM loss; verified earlier and failed attempt bytes survive; max two attempts per file; continue eligible items; interruption stops; no cap extension | FR-009–012, SC-003–004 |
| Path and secret boundary | Traversal, symlink escape, duplicate IDs/destinations/collisions and hostile remote values rejected; no overwrite; nested success/failure exports contain no supplied secret value, key block or private credential path | FR-002, FR-010 |
| Caller composition | All four V2 modes use frozen layout/identity; source F01R contracts unchanged; original primary failure remains separate; cleanup-required always returned; cleanup reserve stops copy before overall cap | FR-001, FR-011–013, SC-004 |

Inspect the concrete fake requests/observations and local bytes. A fake returning generic successful stdout for every command cannot prove source verification, listener health, capability detection or transfer integrity.

## Limits

No provider Terraform files change, so F02R does not claim Terraform validation. Real compilation, upstream capability semantics, authenticated SSH/SCP, guest/provider security enforcement, persistent server output and provider deletion remain unrun. They require separate authorized acceptance after F10. Normal cleanup may lose remote bytes that could not be exported within the original cap; the result must say so. No prices, N, UTC windows, research measurements or advisor approval are created by these checks.
