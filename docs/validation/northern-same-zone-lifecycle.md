# Northern same-zone lifecycle preparation

This bounded 13 November 2026 update prepares two supervised **acceptance**
component checks: EXP-900 in AWS `eu-north-1` / `eu-north-1a`, and EXP-902 in GCP
`europe-north1` / `europe-north1-a`. Each selects one provider, two VMs of the
existing fixed shape, 32-GiB disks, one public IPv4 per VM, three ten-second
phases, and a 1,200-second whole-workflow cap. The existing Frankfurt/London
research configurations remain the study baseline. Northern study geography,
an Azure attempt, and a paid launch are outside this update.

The checked-in review configurations are
[`exp-900-single-provider-north.yaml`](../../configs/tests/exp-900-single-provider-north.yaml)
and [`exp-902-gcp-single-provider-north.yaml`](../../configs/tests/exp-902-gcp-single-provider-north.yaml).
They derive from the private Halle drafts while using distinct `config_id` values
so the checked-in matrix remains unambiguous. The controller management `/32`,
current EUR price/FX/liability sources, exact human authorization, and live
ledger remain unresolved. The historical USD admission metadata in these V2
YAML records is not spending authority; current paid admission uses the separate
source-bound EUR and personal-cash controls described in
[`eur-financial-guard.md`](eur-financial-guard.md) and the Thesis
[`BUDGET_RULES.md`](../../../Thesis/tracking/BUDGET_RULES.md).

The contract admits the northern pairs only for these acceptance identities,
`failure_check` context and `same_zone` locality. Provider settings, Terraform
bindings, observed outputs, independent compute expiry and later scoped cleanup
must all retain the selected region and account/project. Invalid region, zone,
image and ownership readbacks fail closed. The original attempt's saved source
and deployment scope governs cleanup after a changed config or expired approval;
cleanup is not a second launch.

## Validation boundary

The unchanged `e77fa72416e5c25cc8255ec49613815f6296a3d5` baseline has an
independently checked Halle strict offline pass: 20/20 checks, 1,718 Python
cases, zero failures/errors/skips, all 112 required identities and unchanged
source. Evidence is at
`/u/halle/brek/home_at/cnb/evidence/north-safety-gate-QAviq08J`;
the downloaded `native-safety-summary.zip` SHA-256 is
`3503fa92a6bb4e851a7dda19183c8f72b02e0eb20da7aadfab261e8388fa91e0`.
That baseline does not validate this northern change. On the northern
source, local Python 3.13 checks passed as follows:

- AWS provider unit cases: 62 passed.
- AWS F09 factory cases, including the Stockholm provision, native retrieval,
  and scoped cleanup fake: 6 passed.
- Contract, schema, GCP image/provider, and GCP factory cases (excluding the
  eight unrelated broad three-provider parameterizations): 149 passed.
- The generated V2 schema `--check` and staged `git diff --check` passed.

The broader local affected-test invocation recorded 221 passed and four
failures. Three reached the pre-existing F12 plot test without local optional
`matplotlib`; the fourth was the existing diagnostic-day GCP fake retrieval
after repeated retries in the concurrent run. An isolated rerun of that
diagnostic-day case passed retrieval and failed only at the same missing plot
dependency. These results are not counted as a passing full gate. This machine
has no Terraform executable, so Terraform format, validate, and mock-plan
checks on the changed roots remain pending. Automatic approval review rejected
the isolated Halle source transfer; no changed source was uploaded.

No Terraform apply/destroy or cloud API call is part of this validation. A
passing offline test cannot establish real provider permissions, image
availability, expiry timing, result retrieval or billable-resource absence.
