# Provider precreation recovery delta

Implementation: **aae091871490f9754132f0641d4b2c42cb468274**, following
a46593d and its 411d318 evidence. The three-provider delta is 2cb096a; the
final AWS-only audit addendum is aae0918. This is affected offline validation, not a
complete strict-gate rerun or provider acceptance. No cloud command or paid retry
was executed by this repair. The coordinating execution chat owns the original
attempts, cleanup reconciliation and financial accounting.

## Repairs and remaining uncertainty

**AWS.** Recovery uses the supported `DocumentNamePrefix` filter, then exact
document-name, execution-ID, version and original-parameter checks. Neighboring
documents are neither read nor cancelled. One Start only. A typed
`InvalidAutomationExecutionParametersException` plus complete exact-document
absence records a bound rejection; later cleanup repeats that lookup in the
original account after complete EC2 inventory. Lost responses, incomplete or
failed reads, altered bindings and historical uncertainty retain support and
open cleanup. Exceptions before recording an execution identity also preserve
support. Only the primary CLI rejection with blank stdout can establish rejection; empty
or unrelated-prefix metadata document names remain unknown.
Start/recovery/confirmation diagnostics are private, redacted, hashed
and bounded to 8,192 characters per stream.

The reported assume-role rejection is confirmed by the execution handoff, but
its cause remains unknown: trust versus propagation has not been established.
This change neither broadens trust nor retries Start and does not claim to
repair the role-assumption cause.

**GCP.** Immutable template validation accepts the two original ownership labels
with only the known optional `goog-terraform-provisioned=true` addition. It accepts
aware timestamp representations of the same exact instant, without deadline
tolerance. Numeric ID, unique identity, project, role/zone, STOP, disk, network,
metadata and remaining property checks stay strict. Named mismatch fields and
observed-field hashes survive an outer failure/rollback save; raw SSH/bootstrap
metadata is not copied into these diagnostics. At most 16 mismatch records remain.

Cleanup uses unfiltered project-wide lists for regional/global collections and
then selects exact namespace ownership or saved identities. It ignores unrelated
default resources, keeps owned leftovers blocking, and rejects every list warning,
malformed list shape and foreign-project observation. The retained paid state
shows the extra Terraform label; the original API body was not retained, so the
exact property that caused that attempt's rejection cannot be proved.

**Azure.** Both Terraform roots set
`resource_provider_registrations = "none"`. The plan scope validator requires
that value and forbids an explicit nonempty registration list. Before planning,
read-only preflight verifies the expected Enabled subscription/tenant and already
Registered Compute, Network, Automation and Authorization namespaces. It never
registers providers. CLI calls carry the original subscription explicitly.

Cleanup verifies original inputs and identities, reads the expected account and
exact parent-group existence, then accepts typed `ResourceGroupNotFound` for the
prospective VM IDs only when that original parent is independently absent. A
complete subscription resource inventory must agree; otherwise cleanup stays
open. Proven parent absence skips child queries under that absent parent, while
original Terraform destruction, final group absence and role/assignment absence
remain required. Wrong identity, authorization failures, unrelated not-found,
warnings, blank/partial pages, contradictions and residual roles cannot close it.

The reported activity log contains registration requests for 58 namespaces.
Their previous registration states are unknown; this does not establish 58 newly
enabled providers. Nothing was unregistered. The exact cause of the original
180-second expiry plan timeout remains unresolved. Its deadline was not increased.

## Validation and evidence

- Local affected delta: **461 passed**, three unchanged PowerShell process cases
  deselected. After the final diagnostic refactor, all 86 GCP-selected cases passed.
- Exact-source Halle delta: **461/461 passed**, zero failures/errors/skips; the
  same three PowerShell cases were deselected. Source was clean before and after.
- Final AWS audit addendum: **117/117 passed locally and on Halle** at
  aae0918, zero failures/errors/skips, source clean before/after. These checks cover the four
  additional malformed/secondary-error cases and affected AWS recovery paths.
- Both Azure roots passed native format, readonly-lock initialization and validate;
  **8/8 mocked plans passed** using Terraform 1.16.6 and the filesystem mirror.
  The pinned AzureRM 4.81.0 schema also exposes the registration-suppression field.
- Nine selected new regressions reproduce **9 expected failures, zero errors**
  on a46593d with tracked executable source unchanged. Only the new test file and
  its updated Azure document fixture were copied into that synthetic clone.
- Archives were downloaded and independently verified: CRC, all 18 native and
  three regression payload hashes, both JUnit summaries, ten exact-source hashes
  and unchanged executable baseline hashes. The final AWS archive additionally
  passed CRC, ten payload hashes, JUnit and ten exact-source hashes. Seven
  unchanged hashes bind the original GCP/Azure/root evidence to final source.
  The archives are retained outside Git.

The affected checks include real admission/financial/ledger and default factory
paths, CLI failure projection and saved cleanup receipt replay. The new Azure
timeout regression injects a source-shaped 180-second timeout at the real saved
plan boundary, then exercises independent absence or retained cleanup liability.
No financial or saved-plan production bypass was added.

Native root:
`/u/halle/brek/home_at/cnb/saved-plan-native-wsJSS7/provider-precreation-hZ2QYOes`.
Its `evidence.zip` SHA-256 is
`29af1b18c286e6804cf1152c40219a2048152371f8bf8ebff1a4976c7d340cb5`.
Regression root:
`/u/halle/brek/home_at/cnb/saved-plan-native-wsJSS7/provider-precreation-regression-e92dzmp2`.
Its `evidence.zip` SHA-256 is
`9564f35d5cc31c0bd318abf435b58201eb1dc28def68722f6cf58a99d2107bad`.
Final AWS root:
`/u/halle/brek/home_at/cnb/saved-plan-native-wsJSS7/provider-precreation-aws-qle01QtP`.
Its `evidence.zip` SHA-256 is
`25dc7c3bf83436c54fc1665a41b91ee84c09be3679f7870a1d83e13a913b0794`.
Machine-readable checks, fingerprints and environment keys are recorded in
[provider-precreation-boundaries-evidence.json](provider-precreation-boundaries-evidence.json).
The reproducible affected driver is
[check-provider-precreation-boundaries.py](check-provider-precreation-boundaries.py).

The original private execution roots, frozen inputs, Terraform state, ledger and
authority remain untouched. Terraform versions, constraints and lock hashes,
expiry/transfer guards and financial liabilities are preserved. The prior EUR8
fixed-attempt authority is consumed. Future live work needs newly reviewed
source-bound inputs and explicit authority; these tests provide neither.
