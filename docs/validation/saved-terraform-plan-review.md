# Saved Terraform plan inspection — bounded AWS/GCP repair

The previous executable `d83d271` could apply provider variables directly. This
repair inserts a saved-plan review before **all four** AWS safety/pair and GCP
protection/pair applies. Azure is unchanged. No paid test or cloud API command
was run during implementation. The old frozen launch source/card and original
run IDs are not overwritten or regenerated. Its approval cannot silently bind
to changed executable bytes or a new window.

## Behavior

Within the original command budget (including private init), the provider runs
`terraform plan -out=<private saved.tfplan>` with the original state and vars,
then `terraform show -json <same saved.tfplan>`. Automated inspection requires
exact input variables, fresh expected managed addresses/provider identities and
create-only actions, with no drift/import/replacement/update/delete/deferred
scope. Explicit AWS checks cover two fixed-shape/image/root-disk/public-IPv4
instances, CIDRs/zones/ingress, and scoped SSM role/policy/script support. GCP
checks cover protection networks/firewalls/two templates, image/disk/GVNIC/
public-IPv4/native absolute STOP scheduling, placement, and pair project/zone/
unique-template identities. Known critical values cannot remain unknown.

The operator must inspect the **entire** JSON, including unknown/computed values,
provider configurations, resource references, routes, bootstrap, labels and all
support resources, against the exact reviewed source/card. Automated checks do
not establish provider permissions or certify every provider default. This is a
technical review receipt; existing financial admission remains mandatory.

Each new `*-review-*` private directory contains the binary, complete sensitive
JSON, `review-request.json`, operator/accepted receipts and apply outcome or
failure category. Directories are 0700; files 0600. The request binds full binary/
JSON, original input/state, source (including lock/expiry script), action and
resource scope hashes. Inputs/state/source/binary/JSON are rechecked immediately
before apply. The apply argv is only `terraform -chdir=<root> apply -input=false
-state=<original attempt state> <same saved.tfplan>`: no vars, auto-approve or
recomputed plan. Failure preserves
evidence and follows original cleanup handling. Destroy/recovery bypasses this
launch-only gate even without a receipt; failed cleanup still blocks launch.

AWS safety review consumes its original registration deadline; AWS pair consumes
its precreate slack. GCP pair plan/review also reserves the complete remaining
workflow before its original STOP time. No wait extends those deadlines.

## Operator recipe for the execution chat (not execution authorization)

1. Review/rebind the changed implementation revision, all Python files and the
   actual child Terraform roots/locks/expiry source. Financial source discovery
   now recurses through those roots; the older binding missed child files.
   Retain original run IDs and frozen preparation artifacts. Establish a new
   exact approved window if the previous window has elapsed. Do not initialize
   or reserve a ledger, register expiry or launch from this implementation chat.
2. In a **separate reviewed runtime copy**, explicitly set
   `providers.aws.options.plan_review_timeout_seconds` and the corresponding GCP
   option, for example 30. This changes the runtime hash and requires rebinding;
   it is not permission to enlarge registration or total lifetime. Omission means
   zero wait and a missing receipt immediately blocks apply. Review occurs while
   the supervised controller is running, inside its existing budget. Prepare the
   second controller terminal and review source before starting that stage.
3. Watch the private attempt directory for `<action>-review-*/review-request.json`.
   First inspect AWS **safety** (role, trust, Stop-only policy, tags and exact SSM
   script); after registration/readback inspect AWS pair. For GCP inspect
   **protection** (network/firewalls/templates/STOP time) then pair (the exact
   unique template links). A multi-region attempt has separate regional AWS
   support reviews. No blind or automatically approving watcher is permitted.
4. Read `saved-plan.json` privately and compare every resource/action, provider,
   original account/project, region/zone, shape, image, two disks/IPs, networks,
   bootstrap, expiry and ownership to the source-bound approved scope. Inspect
   `review-request.json`, original vars/state and source hashes. Do not copy
   secret-bearing JSON/plan/state into chat, Git or public result bundles.
5. After completing that inspection, record it from the second terminal using
   the actual absolute directory and the SHA-256 of the request you inspected:

   ```sh
   PYTHONPATH=/absolute/reviewed/source/src /absolute/venv/bin/python -m cloud_network_benchmark.terraform_plan /absolute/private/action-review-directory \
     --request-sha256 ACTUAL_INSPECTED_REQUEST_SHA256 \
     --reviewer 'operator identity' \
     --inspection 'Actual checks and comparison with the exact approved source/card'
   ```

   This command only atomically publishes the technical receipt; it never calls
   Terraform. The already-running provider checks it, rechecks all hashes and
   applies the reviewed binary before its original deadline. Do not run manual
   Terraform apply to bypass the framework. On timeout/rejection, let the original
   cleanup path finish; preserve evidence and do not automatically retry.

## Validation

Local Python 3.13.7 before the state-path correction: 306 affected cases passed
with zero failures/errors/skips,
including 61 real saved-plan boundary cases and the financial/runtime/provider
regressions. JUnit: `/private/tmp/cnb-saved-plan-final-local.xml`.
The prior 304-case run and 63-case final delta also passed; their separate hashes
are retained in the adjacent evidence JSON. Earlier invocations
using the system Python 3.9 and a Python without pytest did not collect tests;
those are setup failures, not passing checks. A fixture omission (`stderr`) in
the new test runner was repaired before the passing run.

Historical fake stage suites explicitly mock the new saved-plan boundary: their
old apply/output fakes cannot emit real plan files. Their pass proves retained
stage behavior only. The new repair suite exercises the real boundary with
materialized private files, fake external commands and explicit operator receipts,
including the four provider dispatches and review-free destroy. No production
bypass or auto-approval option exists. The final focused native validation is
recorded below; no unchanged full offline baseline was repeated.



## Native audit and state-path correction — 14 November 2026

Current executable repair: `4da3cc95f1ca46ea7ce67141a01facdb83f90343`.
It supersedes `f1918f0d3a93c10e618647021318b11a0f8e02a3`: native Terraform
showed that a saved-plan apply **does not retain the legacy `-state` override**
used while planning. The old argv applied the saved binary into the source
root's default state location. That breaks attempt isolation and original-state
cleanup. The correction passes `-state=<original attempt state>` on saved-plan
apply too, while retaining the same binary and excluding vars/replanning.
The updated four-root regression asserts that exact original-state argument.

The authorized isolated f1918f0 run completed with Python 3.12.3 / Terraform
1.16.6, clean source before/after and **322 Python cases passed**, zero failures,
errors or skips, including all 18 affected factory cases. The driver overall
**did not pass**: its custom-state-path check failed. An independent builtin
probe confirmed the custom state was absent and default state existed. A separate
builtin-only probe with the original `-state` flag passed: original saved input,
custom state present, default state absent. These probes create only local
`terraform_data` state; they do not contact a provider or create billable resources.
The probe also accounts for Terraform's typed `input` encoding in state.

Four native mock-provider roots passed scope inspection (AWS pair 14 managed
addresses, safety 3; GCP protection 8, pair 3). Terraform test's verbose JSON
requires an envelope adapter; native resource changes were preserved and input
variables supplied through a scratch-only output. The original GCP protection
mock left project unknown, which the validator correctly rejected. The final
synthetic fixture explicitly supplied that computed project at plan time. This
is schema/mock evidence, not real provider planning, permissions or acceptance.
Initial harness failures and the unknown-project rejection remain in the archive.

Source/test transfer of f1918f0 was explicitly approved. Automatic approval review
rejected the later 38a5507 payload as outside that exact approval, so only f1918f0
was transferred. The frozen launch inputs, source and existing checkouts remain
unchanged; validation used `/u/halle/brek/home_at/cnb/saved-plan-native-wsJSS7`.
The user subsequently approved corrected **4da3cc9** explicitly. Only that exact
commit was transferred into a separate `state-repair-source` checkout; later
documentation commits were excluded. The corrected source passed 71 focused
local cases, zero failures/errors/skips, in
`/private/tmp/cnb-saved-plan-state-repair.xml`. The unchanged broad suite was not
rerun; its f1918f0 Python result is retained separately from the final delta.

The f1918f0 archive was downloaded and verified for ZIP CRC, all 56 manifest
entries, each driver log hash, exact revision/source hashes and JUnit counts/hash.
Archive: `/private/tmp/cnb-saved-plan-f1918f0-native-evidence.zip` (8,981,641 bytes).
Native original: `/u/halle/brek/home_at/cnb/saved-plan-native-wsJSS7`.

| Artifact | SHA-256 |
| --- | --- |
| f1918f0 native archive | `bd7584bda63c23304e57c97caf71742103cc0f257510a0f41b947d59c8a6263e` |
| original summary (overall failure retained) | `c704c6683319838c50185fc483ea7a6c5dd72dd411948070311f140f6ded9cf2` |
| original 322-case JUnit | `bdbdc35b886f01309399ba884f6d7b66898b636660a3eb13d5243511681e0e70` |


## Final corrected native delta — passed

At exact source `4da3cc95f1ca46ea7ce67141a01facdb83f90343`, all **7/7 driver
checks** passed, including **71 Python cases**, zero failures/errors/skips, and
an integration probe of the actual corrected helper with Terraform 1.16.6.
The probe used builtin-only local HCL, a test-only scope adapter admitting exactly
one `terraform_data` resource, and a synthetic technical review receipt. Provider
scope validation is covered separately by the unchanged native mock checks.
There was no production bypass, provider plugin invocation, cloud API or billable
resource in this probe.

The actual helper generated/showed a saved binary, bound the inspection receipt,
applied that **same binary** with the original `-state` path, wrote the expected
saved input to that private state, and created **no default state** in the source
root. Source was clean and unchanged before/after; all four native executable
hashes match the current local source. This closes the native state-path finding.
The original failed audit remains preserved and is not relabelled a complete pass.
The earlier 322-case run supports unchanged provider/factory/runtime behavior;
the focused 71-case delta validates the corrected helper and cache boundaries.
Neither run is real provider acceptance or a repeated full F10 baseline.

Final evidence directory:
`/u/halle/brek/home_at/cnb/saved-plan-native-wsJSS7/state-repair-validation`.
Downloaded archive: `/private/tmp/cnb-saved-plan-4da3cc9-native-delta.zip`
(10,751 bytes). Verification passed ZIP CRC, all 14 manifest entries, each driver
log hash, exact revision/clean-state checks, all executable hashes, JUnit counts/
hash and helper proof. Plan/state bytes were excluded from this archive and Git.

| Final artifact | SHA-256 |
| --- | --- |
| corrected native archive | `113b1659848862d4099652637241dc70a645b989f8b90db1c7b3f2e8bd4eebe2` |
| corrected summary | `86057e05005c39f4e3159e12352bb44fbdf0cadcc7d3c2dbf132e9ac8778fc31` |
| corrected 71-case JUnit | `7452ad54069b8145dc91788e9b9d275d7fc15d5e3d832ca80e762ff949b4eb01` |

[Machine-readable source/test/native bindings](saved-terraform-plan-review-evidence.json)
contain the current four executable hashes. Only `terraform_plan.py` changed in
the state-path correction; AWS/GCP/lifecycle source bytes retain their f1918f0
hashes. Rebuild the full financial binding for the actual reviewed source/runtime;
do not splice hashes into the old card, reset a ledger, regenerate run IDs or
silently reuse the expired execution window. No cloud resources, expiry
registration, financial ledger/reservation, paid attempt or cloud API call was
made. Real saved-provider-plan compatibility, provider acceptance and live
transport/expiry/cleanup remain separate and unproved.
