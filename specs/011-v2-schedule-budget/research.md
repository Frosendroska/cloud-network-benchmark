# F11 research and engineering decisions

Date: 2026-10-22. Thesis read-only revision `d9d1051e2c908f0665ea957ab4b0db9047fb6fbf`. Assigned base `a07c1c7140f158f6ae7230b298c41c6e82be974e`; coordinator-verified merged AWS main integrated by fast-forward to `e03a07eaa5ed9ea372c695db19fd6fbcffdad024` before delivery.

## R1 — Source scope and precedence

**Decision**: Follow AGENTS, constitution 2.0.0, WORKFLOW, current Thesis design, latest DECISIONS, FRAMEWORK_IMPLEMENTATION_NOTES, EXPERIMENTS, exact F11 prompt, M04, roadmap and STATUS/TASKS. The 19 October concurrent 28+28 refinement supersedes separate weeks, alternating placement and hourly-DAY launch examples. Formal advisor approval/paid activity remain unrecorded.

**Evidence**: Coordinator-supplied connected-source extracts refer to spreadsheet `1902IXzZ3nJ9mToJLjGkOlg_LObUeVYR1rxbXbBs74VE`. `/private/tmp/cnb-run-register-source.json` identifies acquisition 2026-10-21 and `Budget!A1:H35`, `Runs!A1:Z40`, `'Cost model'!A1:J65`; exact file SHA-256 `a1683a880dc0ab5556cafd5f2faae7f7cccda41fa9ee22d9dff90dd485f1195c`. Complement `/private/tmp/cnb-run-register-remaining-source.json` covers `Runs!A41:I100`, exact SHA-256 `6a091f5f02f550ba4017c2d2e1049fdee536f66c180c6f2f233ce3a4e46e9b59`; it has no independent acquisition-date field, so its paired-context attribution is explicit, not a claimed workbook revision.

Returned nonempty run rows total 81: 63 Required, nine Conditional, nine Deferred; 28 M-S1 and 28 M-S3. Addressed ranges need not be entirely nonempty. Prices were checked October 14. Hashes prove supplied extract bytes only, not a workbook hash/revision, complete formulas, live contents or current prices. No source writes/fresh price verification occurred.

**Rationale**: Bounded extracts plus authoritative local snapshots suffice for offline engineering. Runtime input identity hashes actual saved scoped payload bytes with range/date/normalization stated. A small implementation fixture retains selected values with its own scope/hash rather than the workbook or an unavailable temporary file.

**Alternatives considered**: Inventing workbook revisions, labeling subsets as workbook hashes, or requiring full-workbook import would hide provenance or add unnecessary scope.

## R2 — Primary register and funding

**Decision**: Preserve $100.40 primary + $1.95 replacements + $20.47 tax + $25 uncertainty = $147.82; protect $50 outside $250 working. Primary comprises six acceptance/calibration experiments ($2.91), DAY ($61.09), 28 S1 ($18.20), 28 S3 ($18.20): 63 experiments. DAY includes $15 export; ancillary pair-hour reserve already covers disks/public addresses/ordinary transfers. Optional $192.20/$680.58 illustrations remain excluded; deferred examples are not an agreed S2/S4 schedule.

Move per-run primary/replacement/tax/uncertainty allocations from remaining buckets to active ceilings. Incurred costs reduce only corresponding unspent ceiling liability; release preserves actual/estimated costs and never changes unknown cost to zero. Record overruns and block further launch without rewriting costs. A refreshed review can update remaining estimates while retaining historical identities.

**Rationale**: Counting unchanged future primary cost plus an active reservation duplicates it; freeing liability merely because compute expired undercounts charges. Every component needs scope, units and inclusion rules.

**Alternatives considered**: Per-invocation budgets miss concurrent costs; unallocated headroom is not optional permission; free-tier assumptions are unsupported.

## R3 — Provider support inclusion and earlier trigger

**Decision**: Use merged F05's actual dated fixture `tests/fixtures/aws/cost-inputs/aws-ssm-automation-public-2026-10-22.json`, SHA-256 `315d3e49755396597f97cac3512b9843fcf1c95083fadc11fae667c770215a61`. With no free tier, two $0.002 steps + 330 × $0.00003 script/action seconds = $0.0139 per unique region, $0.0278 for S4 before tax. This is separate from October 14 base envelopes and receives named uncertainty funding, not a claim that the historical forecast already verifies it.

AWS `complete_workflow_cost_handoff` identifies `source_path`, `source_sha256` matching ReservationEvidence, `expiry_input_id`, `expiry_input_sha256`, `expiry_cost_allowance_per_region_usd`, sorted `expiry_cost_regions`, and `expiry_cost_included_usd`. The amount covers every involved region and is contained within the complete reservation. Bind actual saved payload/fixture bytes; do not alter AWS/shared validation.

AWS normal-work trigger is cap end minus360 seconds: 300-second discovery horizon, 330-second action timeout and planned margin. Complete workflow must fit before it. Azure/GCP supply identified provider-owned policy/cost inputs; offline tests may use explicitly synthetic ones, while paid admission blocks missing inputs. No unfinished sibling import or zero-cost default.

**Rationale**: AWS proves its safety amount only; F11 proves complete accounting. Earlier expiry reduces DAY's usable overhead inside unchanged cap87,600.

**Alternatives considered**: Adding support on top of unchanged uncertainty duplicates funding; subtracting it without an inclusion record hides cost. Enlarging caps is unapproved.

## R4 — Cap and final-copy policy

**Decision**: Durable accepted reservation activates original cap immediately before expiry registration. F09 registers all providers before creation. Record actual UTC and one live monotonic mapping; registration/setup/readiness/stabilization/mode/wait/final-copy/cleanup all consume cap. Recheck remaining allowances against earliest trigger without resets.

Reuse merged F02R: at most one retry per failed artifact within original remainder minus cleanup allowance; every outcome requests normal cleanup and preserves local partial evidence. Unexported remote data may be lost at deletion and remains unavailable. No extra timer, retention/recovery promise, periodic copy or experiment repeat. F11 declares/validates this handoff, not retrieval/deletion.

**Rationale**: Current sources deliberately delegate engineering policies, and merged access artifacts already declare this ordering/copy rule.

**Alternatives considered**: Activation after creation/registration, new stage caps, a separate15-minute copying timer or increased DAY cap conflict with source bounds.

## R5 — Serialization and interruption

**Decision**: Local JSON under `fcntl.flock`; private permissions, strict read validation, temp write/fsync/replace/parent fsync. Refuse symlink/nonregular targets and malformed state; explicit exclusive initialization cannot replace a scope. Store stable entry/campaign claims independent of source hash, exact input fingerprints, unique charge events, per-provider creation-intent grants, funding moves and unresolved liabilities. Identical retry returns `newly_reserved=false` without fresh creation eligibility; changed identity inputs fail.

Interrupted durable reservation/creation claim stays active/uncertain; never auto-release/re-create/repeat. Different controller session cannot reuse the previous monotonic origin for creation. Preserve accounting and require F09 recovery/cleanup; provider expiry remains independent. Retry after ambiguous I/O rereads under lock before any action.

**Rationale**: Separate local invocations need common accounting, not a service. Kernel locks release on death; lock-file existence/TTL would be unsafe. Restart or UTC reversal must not extend work.

**Alternatives considered**: Memory-only state, source-hash-only identity, TTL lock deletion, distributed leases and databases lose safety or exceed scope.

## R6 — Schedule and actual timing

**Decision**: Existing config IDs/ScheduleEntry row evidence remain authoritative. Entry IDs identify register executions; existing EXP-* experiment_id maps configuration to research. Require28 S1/P0 plus28 S3/P1, seven consecutive dates × four named windows with matching UTC starts. Keep DAY/CAL one entry/reservation each. Pending times/N remain inspectable but not activatable. Distinct campaign/child identities isolate P0/P1 namespaces; report collision/demand, do not allocate infrastructure.

Actual per-phase provider starts classify skew: ≤60 target met; (60,300] eligible with target-missed finding; >300 excluded; missing unavailable. Coordinator confirmed this matches F08's frozen policy. No F08 runtime dependency/data-validity change. Activation after slot is missed without catch-up; preparation/wait consume cap. Already admitted delays retain actual timing evidence.

**Rationale**: Equal scheduled timestamps do not prove measurement overlap; unknown windows cannot become defaults or retained bins become deployments.

**Alternatives considered**: Excluding all >60 strengthens source policy unnecessarily; omitting target-missed finding hides evidence. Alternating order/separate-week policy is superseded.

## Resolved unknowns and handoff

No critical research/source conflict found. N, UTC windows, refreshed prices/support inputs, optional scope and incomplete-day review remain intentional evidence gates. F09 owns lifecycle/cleanup, providers actual expiry mechanisms, F08/F12 validity/analysis and F10 final clean-checkout gate. Planning has not established application/cloud readiness.
