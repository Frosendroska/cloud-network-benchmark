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

Local Python 3.13.7: 304 affected cases passed with zero failures/errors/skips,
including 59 real saved-plan boundary cases and the financial/runtime/provider
regressions. JUnit: `/private/tmp/cnb-saved-plan-local.xml`. Earlier invocations
using the system Python 3.9 and a Python without pytest did not collect tests;
those are setup failures, not passing checks. A fixture omission (`stderr`) in
the new test runner was repaired before the passing run.

Historical fake stage suites explicitly mock the new saved-plan boundary: their
old apply/output fakes cannot emit real plan files. Their pass proves retained
stage behavior only. The new repair suite exercises the real boundary with
materialized private files, fake external commands and explicit operator receipts,
including the four provider dispatches and review-free destroy. No production
bypass or auto-approval option exists. Native evidence will be recorded below;
no unchanged full offline baseline is repeated.
