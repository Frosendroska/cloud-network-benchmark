# Saved-plan review repair — AWS F05 / GCP F07

Date: 14 November 2026. Branch: `codex/saved-terraform-plan-review`.
Base: `cfeefba77fcbe5a543a8796b5940ab5a6038825a`; executable baseline `d83d271`.
This is a bounded repair of the existing F05/F07 apply boundary, not a new
application feature or authorization for paid work. GCP owns its provider scope;
this shared repair record is linked from its tasks.

## Specification and clarification

R1. Before each AWS safety/pair and GCP protection/pair apply, generate a real
saved Terraform plan using the original private vars/state. Inspect its JSON,
require exact input variables and provider resource allowlists, reject drift,
unexpected creation, existing managed resources, deletes, updates and replacement.
Validate critical provider-specific machine/image/disk/IP/network/expiry scope.
R2. Retain private binary/JSON, source/input/state hashes, scope result and an
operator review receipt. Bind the receipt to this exact request, including full
plan JSON. Recheck those bytes immediately before applying the same saved plan;
never recompute on apply. No Terraform vars or auto-approve on saved-plan apply.
R3. Provide a bounded file review point inside the original command/registration/
precreate deadline. No receipt, rejection, tampering or timeout blocks apply.
Default zero wait fails closed. Optional runtime wait changes are source-bound
and require fresh financial review. This receipt records technical inspection,
not financial authorization. No automatic cloud retry or new run IDs.
R4. Preserve expiry-before-VM, financial admission, bounded result transfer,
original identities, evidence, deadline and cleanup-only recovery. Destroy never
enters this gate. Private plans/state/JSON must remain outside Git.
R5. Focused credential-free tests prove exact scope, reviewed-plan identity,
rejection, deadlines and cleanup accessibility; retain precise native limitations.

## Plan and artifact analysis before implementation

Use one small shared saved-plan command helper with explicit AWS/GCP validators;
intercept only Terraform apply in the existing provider command boundaries.
Expose an optional bounded review wait in existing provider options. Capture
source files recursively so the actual child Terraform roots and expiry script
are included in financial source bindings. No new infrastructure/dependency.
Existing fake-stage tests may isolate the new plan boundary explicitly; dedicated
repair tests must exercise the real boundary with fake commands and real private
files. Old stage tests are not proof of saved-plan review.

Constitution check: I/III exact hashes/private evidence; II local files only;
IV separate provider scopes; V fail-closed launch and independent cleanup. No
research protocol, quota, money or approval change. No source conflicts found.
Sources read: AGENTS, constitution 2.1.0, current Thesis design, DECISIONS,
FRAMEWORK_IMPLEMENTATION_NOTES, experiment/prompts/M04/M05 and BUDGET_RULES.

## Tasks

- [x] SP1 Inspect original apply/deadline/cleanup and binding paths; preserve user edits.
- [ ] SP2 Implement private saved-plan creation, explicit scope checks, bounded receipt and same-plan apply.
- [ ] SP3 Wire AWS/GCP only and recursive executable source binding.
- [ ] SP4 Focused fake-command rejection/identity/deadline/cleanup tests and affected regressions.
- [ ] SP5 Record validation and executable operator recipe; converge and selectively commit.
