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

## Research context

This framework supports the Master's thesis *Benchmarking Network Performance across Clouds*. The thesis repository owns research questions, methodology, experiment design and interpretation; this repository owns executable configuration, provider code, orchestration, raw result bundles, validation and implementation documentation.
