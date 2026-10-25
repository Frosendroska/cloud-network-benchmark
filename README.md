# Cloud Network Benchmark

Cloud Network Benchmark is a small framework for reproducible VM-to-VM network measurements across AWS, Microsoft Azure and Google Cloud. The V2 protocol measures Sockperf UDP full RTT and receiver-side iperf3 A-to-B TCP throughput, with one-second host diagnostics saved locally during a run. The framework keeps provider-specific provisioning and cleanup behavior explicit and validates copied result bundles against their saved configuration and evidence.

Supported experiment modes retain their own protocols: short studies use idle, single-flow and calibrated aggregate phases; calibration uses one retained VM pair for the ordered stream-count trials; diagnostic days retain one continuous TCP run and use bounded local probe chunks. The framework does not infer study dates or a common stream count. Results describe the selected VMs, regions, placement and observation period, not universal provider rankings or maximum bandwidth.

## Start offline

From the repository root, use a Python virtual environment and install the local test and analysis dependencies:

```bash
python -m pip install -e '.[test,analysis]'
cloud-network-benchmark validate --config configs/tests/exp-903-three-provider-smoke.yaml
cloud-network-benchmark dry-run --config configs/experiments/exp-001-multi-provider.yaml
```

`validate`, `resolve` and `dry-run` inspect local configuration without contacting providers. A valid configuration can still have launch blockers: research N, schedule windows and dated cost evidence remain pending until their required evidence exists. `init` writes local campaign manifests and snapshots; it does not provision resources.

The full offline gate requires a clean checkout, prepared local Python/Terraform/PowerShell tools and initialized locked providers. See [F10 validation](docs/validation/f10-offline-gate.md) for the exact tested command, environment, evidence and two Linux `/proc` checks not run on macOS. The gate does not install missing tools or providers.

## Inspect a saved experiment

The lifecycle preview reads the selected configuration, optional schedule row and cost inputs without reserving budget or contacting a provider:

```bash
PYTHONPATH=src python -m cloud_network_benchmark.lifecycle_cli preview \
  --config configs/experiments/exp-001-multi-provider.yaml \
  --cost-inputs /path/to/saved-cost-inputs.json \
  --runtime-config /path/to/sourced-runtime-references.json \
  --format json
```

The preview requires existing local cost inputs and source-bound runtime references; the paths above are placeholders. It reads those files without reserving budget, registering expiry or contacting a provider.

`cloud-network-benchmark lifecycle run` is a live operation. With matching initialized manifests, runtime references and passing admission gates, it can reserve budget, register provider expiry, provision VMs, measure, retrieve results and clean up. Missing prerequisites cause rejection; this is not an unconditional offline lock. It requires explicit `--execution-scope paid_request` and separate launch authorization. Follow the [controlled paid-acceptance handoff](docs/paid-acceptance.md); offline tests do not establish real cloud readiness.

`lifecycle cleanup` is also a live operation. It rebuilds provider cleanup from the original private recovery journal and saved settings, can delete resources, and reconciles the original budget liability. It never resumes measurement. `lifecycle recommend-common-n` reads saved F08 calibration results and reports an evidence-based recommendation without changing configuration.

Before measurement, the lifecycle adapter stages the selected package revision to VM A through the existing SSH/SCP trust boundary, checks the archive and source revision, creates a deployment-local Python virtual environment, installs the dependency ranges already declared in `pyproject.toml`, and records the guest Python, Pydantic and PyYAML versions. Offline tests fake the guest installation command; they do not contact clouds or install anything on a guest.

## Cloud credentials and SSH keys

Keep cloud credentials outside this repository. Provider CLI and Terraform subprocesses use the controller's normal credential stores and inherited environment. The framework does not load `.env` files or accept cloud secrets in experiment YAML, runtime JSON, manifests or result bundles.

| Provider | Local setup for a separately authorized live run |
| --- | --- |
| AWS | Use a named AWS CLI profile, preferably IAM Identity Center/SSO when available. Configuration is in `~/.aws/config`; access-key profiles, if required, use `~/.aws/credentials`. Select the profile with `AWS_PROFILE`. See [AWS profile authentication](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html). |
| Azure | Sign in through `az login` and select the intended subscription with `az account set --subscription ...`. Azure CLI manages its local credential cache; runtime subscription/tenant IDs must match the intended identity. See [Azure CLI authentication](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli). |
| GCP | Authenticate the CLI with `gcloud auth login`, and configure Terraform's Application Default Credentials with `gcloud auth application-default login`. These are separate credential stores; use the intended account/project consistently. See [local ADC setup](https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment). |

These sign-in steps contact the provider and are not part of the offline gate. No cloud credentials are needed for offline validation or saved-result analysis. For unattended acceptance/day runs, verify credential lifetime and recovery access before launch.

SSH credentials are separate from cloud API credentials. Keep the private key under `~/.ssh/` with owner-only permissions (for example, `chmod 600 ~/.ssh/cnb_ed25519`). The runtime `authentication_reference` is the absolute path to that SSH private key, passed to `ssh/scp -i`; it is not an AWS profile, cloud token or key contents. Supply the corresponding public-key path in the provider runtime options. Keep the private recovery directory outside Git, and never commit private keys, provider tokens or Terraform state.

## Research context

This framework supports the Master's thesis *Benchmarking Network Performance across Clouds*. The thesis repository owns research questions, methodology, experiment design and interpretation; this repository owns executable configuration, provider code, orchestration, raw result bundles, validation and implementation documentation. The thesis describes the research questions, measurement design, and interpretation of results.

## Analyze saved results

Install the optional local renderer with `python -m pip install -e '.[analysis]'`, then point `analyze` at a selection of saved validator inputs and validation results. The command revalidates selected evidence and writes a new, source-disjoint report directory. A successful exit means publication succeeded; it does not mean every selected observation is valid or comparable.

```bash
cloud-network-benchmark analyze --selection selection.json --output results/processed/report-001
```

See [native analysis](docs/native-analysis.md) for the selection format, output files, and evidence limits.
