# Validation and handoff

Use `/private/tmp/cnb-acquisition-evidence`, branch `codex/f01r-acquisition-evidence`, base `7a70a3139c5ee9efa1f8ea13c3f6de40e9048121`. Only planning artifacts exist at this stage. Commands for new tests below are **planned; not yet run**. Existing scripts/CLI routes were inspected before listing them.

## Planning checks

Observed unchanged-runtime baseline during planning: `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q` exited **0**, **627 passed in 26.59s**, at the base runtime above with only these planning files added. Canonical schema `--check` exited **0**. Artifact structure/link/task coverage check exited **0**: 13 requirements/outcomes mapped, 20 sequential unchecked tasks. These results do not test the unimplemented observation hooks.

With explicit `GIT_BRANCH_NAME=codex/f01r-acquisition-evidence` and `SPECIFY_FEATURE_DIRECTORY=specs/016-acquisition-evidence`:

```sh
.specify/scripts/bash/check-prerequisites.sh --json --paths-only
.specify/scripts/bash/setup-plan.sh --json
.specify/scripts/bash/setup-tasks.sh --json
.specify/scripts/bash/check-prerequisites.sh --json --require-spec --require-tasks --include-tasks
git diff --check
```

Spec Kit's scripts report the directory-derived feature label `016-acquisition-evidence`; actual Git branch must independently remain the assigned branch. No extensions.yml is installed, so there are no hooks for these five stages. `.specify/feature.json` is ignored and local to this worktree.

## Implementer checks

```sh
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_acquisition_contract.py tests/unit/test_acquisition.py tests/unit/test_bootstrap.py tests/unit/test_measurement_timing.py tests/integration/test_acquisition_producer.py
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m cloud_network_benchmark.contracts.schema --output specs/003-v2-shared-contracts/contracts --check
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
git diff --check
```

Required outcome after implementation: exits 0, no skipped/unexplained failures; existing command/sequence/bootstrap/native retrieval checks remain intact. Export canonical schemas before --check if models change. Record actual checked head, environment, exact commands and results in docs/validation/acquisition-evidence.md. Terraform is unchanged; Terraform checks are not applicable here.

Apply/compile checks must use the pinned upstream sources and packaged patches without network traffic or benchmark execution. Exact commands depend on the implementation's verified archive names and build packaging; record the real commands/exit results rather than inventing them now. Check zero-fuzz patch application, upstream diff limited to observations, compiler/config flags and HAVE_CLOCK_GETTIME. A local offline Linux toolchain is preferred. If unavailable, say **compile not run**, preserve source/static evidence and leave actual build proof open for the coordinator; never substitute fake logs for a compile result. Do not start containers/VMs/clouds or send loopback traffic to remedy this.

## Requirement-discriminating acceptance matrix

| Case | Expected evidence outcome |
| --- | --- |
| Materialized acceptance/short/calibration/day via actual F03 fake boundary | Source grammar/raw hashes/session/clock links produce conservative windows; protocol/argv unchanged |
| Startup delay, sender-only start, receiver not entered, missing filter or termination | No invented probe window; reason retained |
| Dropped/reordered replies and slow/zero throughput | Counts/RTT/native bytes retained separately; no performance exclusion |
| Day chunk rotation | Distinct chunk support/restart gaps; one continuous TCP command/session |
| Process interrupted, truncated final log, omitted or partial native interval | Preserve closed subsets; no extrapolated tail/full duration |
| Altered bytes/paths/build patch/binary/capability/command action/session cookie/endpoints | Reject/unavailable support before coverage; no nearest-time association |
| Mixed boot/domain, UTC step, absent error mapping, native microsecond precision | Conservative uncertainty or unknown; never zero error by default |
| Exactly .99 vs uncertain straddling .99; disjoint sample/TCP times | No threshold promotion or sample-hull substitution |
| Old V2 bundle | Readable with acquisition unavailable |
| Replay repeated | Identical derived values and unchanged hashes; bounded chunk/session processing |

F08 consumer acceptance occurs only after this producer amendment merges. It must exercise validate_bundle, summaries/comparison and common-N through its own materialized fixtures, including valid frozen-N eligible peers from prior calibration. This planning packet neither runs nor claims those checks.

## Separate paid acceptance limits

Offline checks cannot prove real hook timing, probe/receiver scheduling, installed capability on guest OS, clock synchronization accuracy, observation overhead, real disk/export growth, network/cloud behavior or cleanup. Preserve the modified build identity. The registered ten-second acceptance and existing calibration/disk/export gates supply real evidence after F10 and separate authorization. No additional overhead experiment/campaign or software-freeze approval gate is added here.
