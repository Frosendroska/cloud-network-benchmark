# Bounded provider precreation follow-up

14 November 2026. Base implementation a46593d, documentation 411d318.
Scope: repair the source-shaped failures reported by the coordinating execution
chat; affected credential-free checks locally and in a fresh Halle directory,
then commit and push. Original paid attempt roots, inputs, authority, state,
ledger and evidence remain untouched. No cloud retry or new live authority.

## Specification and reviewed plan

- PB1: AWS uses the supported DocumentNamePrefix lookup and exact-name,
  version and parameter ownership checks. One Start only. A typed server
  rejection and complete exact-document absence may establish rejection;
  ambiguous transport/read failures retain uncertainty and support resources.
  Preserve bounded redacted start/read diagnostics. The cause of the reported
  assume-role rejection remains unresolved; do not broaden trust or retry Start.
- PB2: GCP accepts only the evidenced Terraform-managed label in addition to
  original ownership labels, and equivalent aware representations of the exact
  deadline. Preserve immutable identity, STOP, project and all other checks.
  Persist named, hashed mismatch diagnostics through rollback. Use complete
  unfiltered project-wide cleanup lists and owned postfilters; warnings,
  malformed data and incomplete results remain unknown.
- PB3: Both Azure roots disable automatic resource-provider registration.
  Read and verify the expected Enabled subscription/tenant and the four required
  already Registered providers before planning. No registration mutation.
  Accept ResourceGroupNotFound for prospective VM queries only after independent
  exact-parent absence in the original subscription, then complete subscription
  inventory and existing role/support checks. Wrong identity, authorization,
  unrelated not-found and partial results cannot establish cleanup.
- PB4: Preserve original expiry, reservation, source, financial, transfer and
  cleanup guards, pinned versions/locks and the 180-second plan timeout. Its
  reported cause remains unresolved. Retained liabilities and consumed approval
  belong to execution accounting; offline repair neither releases nor renews them.

The constitution and required design/decision/implementation routing have been
reviewed for this bounded repair series. No research protocol changes or Thesis
edits. One writer, same repair branch; no delegation. Owned files: the AWS/GCP
drivers, Azure provider/scope validator, Azure versions.tf roots, affected tests,
this repair artifact and bounded validation evidence.

## Tasks and convergence

- [x] PB1 AWS lookup, rejection recovery and private diagnostics with regressions.
- [x] PB2 GCP canonical properties, durable diagnostics and complete inventory.
- [x] PB3 Azure no-registration preflight and typed precreation absence.
- [x] PB4 Affected local checks and exact-source isolated native delta.
- [x] PB5 Record evidence, unresolved causes and source identity; commit/push.

Reported live outcomes are execution-chat evidence, distinct from these tests:
all three attempts stopped before VM creation, zero benchmarks, and independently
closed cleanup. The EUR8 attempt authority is consumed. No offline test is
provider acceptance or authority for another run.

Local affected delta: 461 passed, three unchanged PowerShell cases deselected.
After the final diagnostic refactor, all 86 GCP-selected cases passed. Exact-source Halle validation passed 461/461 Python cases and 8/8 Azure mocked
plans at 2cb096a. Source/locks stayed unchanged. Nine selected regressions fail
as expected on a46593d. Archives, JUnit and executable hashes were independently
verified; see docs/validation/provider-precreation-boundaries.md and its evidence
JSON. Implementation and evidence are committed on the existing repair branch.

Final audit addendum: require the primary AWS CLI rejection (with blank stdout),
and reject empty/foreign-prefix metadata document names as incomplete evidence.
Four new negative cases cover those edges. The affected AWS-only delta is rerun
on the final source; unchanged GCP/Azure and Terraform evidence stays bound to
2cb096a and its executable/root hashes.

AWS addendum aae0918: 117/117 local and exact-source Halle cases pass, zero
failures/errors/skips. Source clean before/after; CRC, ten payload and ten source
hashes plus JUnit verified. Seven unchanged source/root hashes retain the prior
GCP/Azure/eight mocked-plan evidence without rerunning that unchanged baseline.
