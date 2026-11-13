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
<same saved.tfplan>`: no vars, auto-approve or recomputed plan. Failure preserves
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
   python -m cloud_network_benchmark.terraform_plan /absolute/private/action-review-directory \
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

Local Python 3.13.7: the final 306 affected cases passed with zero failures/errors/skips,
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
bypass or auto-approval option exists. Native validation is not claimed;
no unchanged full offline baseline was repeated.


Executable repair commit: `f1918f0d3a93c10e618647021318b11a0f8e02a3`.
The later validation commit adds two focused tests and documentation only; the
four changed executable files remain byte-identical to this implementation commit.
[Machine-readable source and JUnit bindings](saved-terraform-plan-review-evidence.json)
retain their exact SHA-256 values. These are the source changes the execution chat
must review: `terraform_plan.py`, `providers/aws.py`, `providers/gcp.py` and
`lifecycle_runtime.py`. Rebuild the full financial binding from the actual selected
runtime/source; do not splice these hashes into the old card or reset its ledger.

Halle tools/read-only access were confirmed and a new empty private validation
directory was created at `/u/halle/brek/home_at/cnb/saved-plan-native-wsJSS7`.
Automatic approval review **rejected** transfer of the source/tests/documentation
because explicit trusted user authorization for that payload and destination was
required. No source was transferred and no native tests ran. An explicit transfer
question is pending; no alternate egress path was used. Local Git export is
`/private/tmp/cnb-saved-plan.fast-export`. No frozen source, credentials, launch
inputs or existing remote checkout was modified. Stalled local archive processes
were stopped without touching the SSH master or remote host.

Eighteen affected factory cases were collected but not run locally because this
venv lacks optional matplotlib. They are intended for the isolated native delta,
together with the affected repair/provider/runtime/financial tests. The old
1,718-case/20-check and d83d271 281-case native results remain historical evidence,
not validation of this executable repair. Real saved-plan provider compatibility,
permissions, network/guest interface assumptions, independent expiry, live
transport and absence after cleanup remain unproved until separate authorized
provider acceptance. The proposed native builtin-only Terraform check would also
verify same-plan application/custom local state-path semantics without a cloud.
