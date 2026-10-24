# Cloud Network Benchmark

Cloud Network Benchmark is a small framework for reproducible VM-to-VM network measurements across AWS, Microsoft Azure and Google Cloud. The V2 protocol measures Sockperf UDP full RTT and receiver-side iperf3 A-to-B TCP throughput, with one-second host diagnostics saved locally during a run. The framework keeps provider-specific provisioning and cleanup behavior explicit and validates copied result bundles against their saved configuration and evidence.

Supported experiment modes retain their own protocols: short studies use idle, single-flow and calibrated aggregate phases; calibration uses one retained VM pair for the ordered stream-count trials; diagnostic days retain one continuous TCP run and use bounded local probe chunks. The framework does not infer study dates or a common stream count. Results describe the selected VMs, regions, placement and observation period, not universal provider rankings or maximum bandwidth.

## Inspect a saved experiment

The lifecycle preview reads the selected configuration, optional schedule row and cost inputs without reserving budget or contacting a provider:

```bash
PYTHONPATH=src python -m cloud_network_benchmark.lifecycle_cli preview \
  --config configs/experiments/exp-001-multi-provider.yaml \
  --cost-inputs /path/to/saved-cost-inputs.json \
  --runtime-config /path/to/sourced-runtime-references.json \
  --format json
```

Lifecycle run and recovery commands require the complete paid-run admission evidence and provider runtime adapters. The credential-free implementation stage deliberately rejects a paid run and does not claim cloud readiness; run the offline checks in `docs/validation/f09-v2-lifecycle.md` instead. No paid experiment is launched by this repository workflow.

The root CLI exposes lifecycle commands through `cloud-network-benchmark lifecycle`. It delegates to the isolated lifecycle parser and preserves the existing config and F12 `analyze` commands. `run` accepts an explicit `paid_request` scope plus config, cost inputs, runtime references, result/private roots and controller session; this offline implementation returns a rejection with no provider calls. `cleanup` accepts a private recovery journal and never resumes measurement; it reports that cleanup is unavailable when provider callbacks are not supplied. `recommend-common-n` reads a JSON array of saved F08 calibration results and reports the existing evidence-based recommendation without changing configuration.

Before measurement, the lifecycle adapter stages the selected package revision to VM A through the existing SSH/SCP trust boundary, checks the archive and source revision, creates a deployment-local Python virtual environment, and records the guest Python, Pydantic and PyYAML versions. The offline stage does not download dependencies; a guest without the declared dependencies fails before measurement.

## Research context

This framework supports the Master's thesis *Benchmarking Network Performance across Clouds*. The thesis repository owns research questions, methodology, experiment design and interpretation; this repository owns executable configuration, provider code, orchestration, raw result bundles, validation and implementation documentation. The thesis describes the research questions, measurement design, and interpretation of results.

## Analyze saved results

Install the optional local renderer with `python -m pip install -e '.[analysis]'`, then point `analyze` at a selection of saved validator inputs and validation results. The command revalidates selected evidence and writes a new, source-disjoint report directory. A successful exit means publication succeeded; it does not mean every selected observation is valid or comparable.

```bash
cloud-network-benchmark analyze --selection selection.json --output results/processed/report-001
```

See [native analysis](docs/native-analysis.md) for the selection format, output files, and evidence limits.
