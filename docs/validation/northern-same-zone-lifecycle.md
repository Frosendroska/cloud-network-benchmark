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
has no Terraform executable. After the initial source-transfer rejection,
explicit human authorization on 13 November permitted the isolated Halle
validation below.

## Native repair and validation

The first native run at `4fe4f5f9f98b4aedd8453813e53611af2430e29d`
failed on unparenthesized multiline HCL boolean expressions. Commit
`2d12fd35b3e85d4e620e338a1c329711fcc52da6` preserved those conditions while
fixing the syntax and applying Terraform formatting. Its native mock plans
then exposed the missing mock for the AWS `london` provider alias. Commit
`d83d271f1acb097605d476dafb3e28b1f2a422fe` adds that alias mock; production
provider configuration and admission conditions are unchanged by these repairs.

A fresh isolated checkout at `d83d271f1acb097605d476dafb3e28b1f2a422fe`
passed **22/22 driver checks** using Python 3.12.3 and Terraform 1.16.6 on
Linux amd64:

- Both northern configuration validations, schema export check, and diff check.
- Init with the existing local mirror and `-lockfile=readonly`, recursive format
  check, and validate for AWS pair/safety and GCP pair/protection: 12 checks.
- Three mocked-plan suites: AWS pair 3/3, GCP pair 3/3, GCP protection 7/7;
  **13 plans passed, none failed or skipped**.
- All 11 affected contract, provider, sequence, campaign-input and factory test
  files, including the eight broad three-provider cases: **281 passed in
  400.31 seconds, zero failures, errors or skips**.
- Two tool-version checks. The start and end Git states match the exact clean
  source revision above.

The run used an empty HOME/config/cache, `/var/tmp` scratch, `nice 10`, the real
Halle `THESIS_ROOT`, and the existing provider mirror. Its explicit environment
excluded cloud credentials. The unchanged 1,718-case baseline suite was not
repeated.

Native evidence is retained at
`/u/halle/brek/home_at/cnb/evidence/north-native-final-AFBLgx29`.
The 44,271-byte `northern-native-evidence.zip` contains the driver, sanitized
environment, summary, logs, JUnit, and prior-failure provenance. Local download
verification passed ZIP CRC, all 56 manifest entry hashes, every check-log hash,
JUnit counts/hash, and the source-state assertions.

| Artifact | SHA-256 |
| --- | --- |
| `northern-native-evidence.zip` | `2979057d563a4820cb486036a49d61531bdb2940cc3c0f3f9e539c6178bec640` |
| `summary.json` | `91983c6edb0b5a56e7f96efee6549828a85075f0b370adf828110d0341490202` |
| `pytest.xml` | `f95d7a30245eff697221b9caa148f3d72173f23d0561e96278accd00af6d70de` |

The original failed source, logs and summary remain untouched in
`/u/halle/brek/home_at/cnb/evidence/north-native-4fe-RQfi2Ss8` and
`/u/halle/brek/home_at/cnb/evidence/north-native-repair-ge7RDMDj`.

No Terraform apply/destroy or cloud API call is part of this validation. A
passing offline test cannot establish real provider permissions, image
availability, expiry timing, result retrieval or billable-resource absence.
