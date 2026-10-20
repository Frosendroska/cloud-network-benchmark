# R00 V2 governance reconciliation

Completed governance stage: 21 October 2026. Worktree `/private/tmp/cnb-r00`, branch
`codex/r00-v2-governance`, implementation base `6e11b2412143222e48a1e9af0a8d44d85be3061c`.
This records governance checks only; it does not establish V2 runtime or cloud readiness.

## Sources and scope

Research context was read without modification at Thesis revision
`d45655364ef21423cfe6cae177e1eec23b44acf9`, rooted at
`/Users/katya/Desktop/University/Masters/Thesis`:

- `advisor/docs/design_document.md`: current V2 snapshot checked 19 October, sections 3–10.
- `DECISIONS.md`: 17–19 October V2 protocol, safety, retrieval and concurrent-study decisions;
  the latest concurrent-primary refinement overrides the earlier separate-week alternative.
- `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md`: retained command/mode/evidence details,
  schedule clarification and explicit engineering choices still to settle.
- `experiments/EXPERIMENTS.md`: registered modes, fixed settings, budget/lifetime inputs and gates.
- `tracking/M04_implementation.md`, `tracking/IMPLEMENTATION_PROMPTS.md` R00 and dependency table,
  `tracking/framework_implementation_roadmap.html`, `STATUS.md`, `tracking/TASKS.md`:
  migration ownership, offline acceptance and evidence boundaries.

The actual installed `speckit-constitution/SKILL.md` was read, its active scaffold resolved,
existing constitution preserved where applicable, and pre/post hooks checked: no
`.specify/extensions.yml` exists. The skill's scope was constitution only. After that stage,
separately authorized R00 work updated AGENTS.md routing and this record. No application feature
specification was invented. Runtime code, schemas, executable configs, tests and historical
F01/F02 artifacts were not changed. No templates required amendment: the resolved generic
constitution scaffold already supports the five principles and existing section hierarchy.
Tool installations and independent feature.json state remain local and uncommitted.

Owned files: `.specify/memory/constitution.md`, `AGENTS.md`, and this document. No public runtime
interfaces change in R00. F01R owns the next contract migration (config/modes, native artifacts,
identity, provenance and schedule/reservation/expiry interfaces); dependents consume it only after
merge. WORKFLOW.md and state.template.json were checked and already match the current graph:
R00 → F01R → F02R/F03–F08/F11; F12 after F08; F09 after F02R/F03–F08/F11; F10 after F09/F12.

## Amendment and explicit supersessions

Constitution **1.1.0 → 2.0.0** is a MAJOR amendment: FLENT-only measurement/artifact assumptions
and an exclusively short three-phase execution model are incompatible with V2. Ratification stays
2026-09-24; amendment date is 2026-10-21. All five principles were updated without adding/removing
sections: I extends deployment/schedule traceability; II defines native modes; III preserves
native evidence and eligibility; IV preserves provider semantics and current study scope;
V adds reservations/expiry and the distinct offline/paid gates. The temporary Sync Impact Report
was checked and removed before commit as the installed skill requires.

| Historical assumption | Current source-backed governance |
| --- | --- |
| FLENT, ICMP latency, netperf/netserver and required `.flent.gz` | Sockperf 64-byte/1,000-mps UDP full RTT with monotonic timing; receiver-side iperf3 TCP CUBIC with sender zerocopy; native CSV/JSON/JSONL and logs |
| All runs use three short phases; duration/four-stream research count still open | Fresh research: 60 measured seconds/phase; acceptance: ten seconds, N=4 before calibration; specified retained calibration selects common N; retained DAY has continuous 86,400-second TCP and ≤300-second local probe chunks |
| AWS-only/hourly diagnostic launches; extra three-pair/reversed placement order | One all-cloud DAY row/reservation; 28 P0 and 28 P1 fresh-pair experiments concurrently in the same week/windows, with independent state/resources/results |
| Progressive remote copies, mandatory Parquet or performance-warning rules | Native local files; close/copy/verify after measurement, then normal cleanup; local one-second diagnostics; no automatic performance-warning labels |
| Smoke tests alone finish the migration; scheduling/analysis deferred | F11 scheduling/budget and F12 native deterministic reports/replay precede the expanded credential-free F10; paid all-cloud acceptance is separate |
| Software/image freeze approval gate | Record actual image/tool/source/build identity and normal locks; no added approval gate. Common calibrated N remains fixed for research |

Repository boundaries, immutable evidence, honest missing/failure state, secret exclusion and
provider-specific semantics are retained. Small provider expiry is expressly required, not an
exception permitting a new orchestration platform. Register it before VM creation; stop AWS/GCP
compute and deallocate Azure compute; still verify complete billable-resource deletion.

## Assumptions and remaining feature clarifications

No current-source research conflict remains. Explicit supersessions resolve the historical FLENT
mismatch; user authorization to implement V2 is not formal advisor approval or paid authorization.
Local research snapshots are the supplied planning inputs. Prices were not refreshed by R00 and
no calibration values, study dates, UTC windows or research results were invented.

Owning features must record design-consistent engineering choices before implementation:

- F01R declares the shared mode, identity, timing, admission and expiry interface contracts.
- F03/F08 declare short overlap/coverage rules; F08/F11 declare 60–300-second skew eligibility.
  Preserve the ≤60-second target, >300-second comparison exclusion, ≤100 ms alignment rule and
  diagnostic full-duration/≥99% coverage gate.
- F11 defines the cap-start event; F02R/F09 define failed-copy handling within the overall caps
  without inventing destructive retention policies or extra copying timers. Local inputs specify
  20-minute short/acceptance, 30-minute calibration and 1,460-minute DAY overall caps. Full-cost
  reservations account for concurrent work within $300 while protecting $50; dated forecasts are
  not actual charges or refreshed prices.
- F05/F06/F07 choose the smallest supported provider expiry mechanisms and account for their
  costs/permissions. Real acceptance must prove behavior; fakes cannot establish it.

The README still describes the historical FLENT implementation and advertises a `run` invocation
without verified V2 CLI support. F09/F10 must reconcile user-facing execution instructions with
actual delivered commands before publishing runtime readiness; R00 does not validate that command.

## Acceptance evidence

The resolver ran from this worktree with explicit branch context:

```bash
GIT_BRANCH_NAME=codex/r00-v2-governance .specify/scripts/bash/resolve-template.sh constitution-template --json
```

Exit **0**: the active scaffold resolved. Constitution-only checks passed before routing changes:
five principle headings, scaffold section hierarchy, version/report agreement, ISO dates, no
unresolved placeholder or vague SHOULD, no extension hooks; the temporary report was removed.

The following reproducible check is scoped to R00 governance and read-only Thesis context:

```bash
GIT_BRANCH_NAME=codex/r00-v2-governance python3 - <<'PY'
from pathlib import Path
import json
import re
import subprocess

def git(*args, cwd=None):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()

base = '6e11b2412143222e48a1e9af0a8d44d85be3061c'
thesis = Path('/Users/katya/Desktop/University/Masters/Thesis')
assert git('branch', '--show-current') == 'codex/r00-v2-governance'
assert git('rev-parse', 'HEAD', cwd=thesis) == 'd45655364ef21423cfe6cae177e1eec23b44acf9'
c = Path('.specify/memory/constitution.md').read_text()
a = Path('AGENTS.md').read_text()
w = Path('docs/autonomous/WORKFLOW.md').read_text()
assert re.findall(r'^## (.+)', c, re.M) == [
    'Core Principles', 'Implementation Boundaries', 'Development Workflow', 'Governance']
assert len(re.findall(r'^### ', c, re.M)) == 5
assert '**Version**: 2.0.0 | **Ratified**: 2026-09-24 | **Last Amended**: 2026-10-21' in c
assert not c.startswith('<!--') and not re.findall(r'\[[A-Z][A-Z_]+\]', c)
assert not re.search(r'\bSHOULD\b', c, re.I)
for token in ['Sockperf UDP full RTT', 'receiver B', '86,400', '99%', '100 ms',
              '$300', '$50', 'deallocate Azure', 'failed and interrupted',
              'F01R/F02R', 'software-freeze approval gate']:
    assert token in c, token
for old in ['lifecycle orchestration, FLENT execution', 'raw `.flent.gz` files when produced',
            'including FLENT details', 'three sequential FLENT phases']:
    assert old not in c + a, old
for source in ['advisor/docs/design_document.md', 'DECISIONS.md',
               'experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md',
               'experiments/EXPERIMENTS.md', 'tracking/IMPLEMENTATION_PROMPTS.md',
               'tracking/M04_implementation.md', 'tracking/framework_implementation_roadmap.html',
               'STATUS.md', 'tracking/TASKS.md']:
    assert (thesis / source).is_file(), source
    assert source in a, source
assert 'F10 follows F09 and F12' in a
assert 'F10 follows F09 and F12' in w
state = json.loads(Path('docs/autonomous/state.template.json').read_text())
expected = {'R00': [], 'F01R': ['R00']}
expected.update({f: ['F01R'] for f in ['F02R', 'F03', 'F04', 'F05', 'F06', 'F07', 'F08', 'F11']})
expected.update({'F12': ['F08'], 'F09': ['F02R', 'F03', 'F04', 'F05', 'F06', 'F07', 'F08', 'F11'],
                 'F10': ['F09', 'F12']})
assert {f: item['depends_on'] for f, item in state['features'].items()} == expected
allowed = {'.specify/memory/constitution.md', 'AGENTS.md',
           'docs/autonomous/r00-v2-governance.md'}
changed = set(git('diff', '--name-only', base).splitlines())
new = set(git('ls-files', '--others', '--exclude-standard').splitlines())
assert changed and changed | new <= allowed, sorted(changed | new)
assert not git('diff', base, '--', 'specs', 'src', 'tests', 'configs', 'terraform')
assert not Path('.specify/extensions.yml').exists()
print('PASS: R00 source identity, routing, constitution, dependency and governance-only scope checks')
PY
git diff --check
```

Exit **0** for both the Python governance check and `git diff --check`. Source-backed semantic
review confirmed the fixed mode protocol, native evidence, diagnostics, expiry/budget sequence,
current primary scope and explicit historical supersessions above. WORKFLOW.md and the dependency
template needed no edits. No application tests, Terraform checks, benchmark traffic, clouds,
paid acceptance, calibration or full V2 system gate were run; none is claimed passed by R00.

The coordinator separately ran the unchanged legacy baseline at implementation base `6e11b24`
from the main checkout using
`PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q`: exit **1**,
**203 passed, 3 failed**, recorded in `/private/tmp/cnb-baseline-pytest.log` (log read by R00).
The failures concern the old Thesis EXP-001 heading, scenario-default contract expectations and a
remote-handoff fixture whose run ID lacks its required provider suffix. They precede R00 and
belong to the F01R/F02R migration; R00 did not repair or rerun application tests. They are not a
passing combined V2 suite. In external worktrees, supply
`THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis` when running those checks.

Next stage: coordinator verifies the scoped commit/PR and merges R00, then dispatches F01R
specify → clarify → plan → tasks → analyze against the merged governance. No dependent feature is
unblocked by an unmerged R00 commit. The coordinator records exact delivered head and merge SHA;
this document deliberately does not claim its own future commit hash.
