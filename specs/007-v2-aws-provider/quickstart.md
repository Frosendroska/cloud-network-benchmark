# F05 offline validation guide

Planning artifact only: the following feature test paths and Terraform roots must first be implemented. They have **not run** at planning handoff. No actual AWS setup, credentials, Terraform plan/apply/destroy or real SSH is needed.

From `/private/tmp/cnb-f05`, with `PYTHONPATH=src`, use the existing `/private/tmp/cnb-offline-venv/bin/python` runtime. All shell calls must use that assigned working directory and `login:false`; prefix with `test "$PWD" = /private/tmp/cnb-f05`.

```sh
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/unit/test_aws_provider.py tests/unit/test_aws_expiry.py tests/contract/test_aws_provider_contract.py tests/integration/test_aws_provider_sequences.py
PYTHONPATH=src /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_v2_handoffs.py tests/contract/test_deployment_contract.py tests/unit/test_v2_admission_artifacts.py
/private/tmp/cnb-tools/terraform -chdir=terraform/aws/pair fmt -check
/private/tmp/cnb-tools/terraform -chdir=terraform/aws/safety fmt -check
/private/tmp/cnb-tools/terraform -chdir=terraform/aws/pair init -backend=false -input=false
/private/tmp/cnb-tools/terraform -chdir=terraform/aws/safety init -backend=false -input=false
/private/tmp/cnb-tools/terraform -chdir=terraform/aws/pair validate -no-color
/private/tmp/cnb-tools/terraform -chdir=terraform/aws/safety validate -no-color
git diff --check
```

Verified executable `/private/tmp/cnb-tools/terraform version`: Terraform v1.16.6, darwin_arm64. Initialization may download HashiCorp providers from the registry; this is a tool dependency download, not a cloud operation. Tests inject all external commands, prohibit live provider execution and may use fake AWS clients for the exact inline expiry source. Use credential-empty environments/disabled metadata for static checks and record exclusions. Never turn validation into a plan or paid acceptance.

Expected offline evidence:

1. S1–S4 static assertions: two selected VMs, security/placement/network/peering, no Spot/Express/EFA/platform resources; provider schema validation succeeds.
2. Traces prove reservation/cap start before safety setup, authenticated all-region registration before pair apply, and zero pair calls on failed/uncertain registration.
3. The exact inline expiry script is executed against paginated fake EC2 data/fake time: initially empty selection, delayed tags/pending state, retries/throttling, extra owned IDs, foreign tags, deadline exhaustion, and stop requests versus observed stopped states. No real 300-second wait.
4. Complete and partial normalized outputs preserve actual region/image/shape/zone ID and network limits. Authenticated console/scan fixtures prove strict host trust and failure on missing/stale/mismatched evidence.
5. Success/failure/interruption/controller-loss traces preserve private state and primary error; cleanup checks all regions/resources, cancellation readback and unknown/leftover launch blockers.

Record exact head/base, environment, lock versions, command/exit results and limitations in `docs/f05-aws-provider.md` after implementation. Fake controller loss demonstrates the independent artifact's behavior through mocked AWS; it does not prove real IAM, execution durability, stop latency, networking, billing or deletion. Those require the later registered paid acceptance stage after F10.
