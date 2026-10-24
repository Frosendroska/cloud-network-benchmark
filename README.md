# Cloud Network Benchmark

Cloud Network Benchmark is a small framework for measuring private VM-to-VM network performance across AWS, Microsoft Azure, and Google Cloud. It provisions a comparable pair of Ubuntu VMs, runs a consistent sequence of FLENT measurements, and saves the raw results and run metadata for later analysis.

The framework supports same-zone, cross-zone, provider-specific placement optimization, and inter-region experiments. Each observation measures idle latency, single-flow TCP performance, and aggregate TCP performance with concurrent latency measurements. Results describe the selected VMs, regions, placement, and observation period; they are not universal rankings of cloud providers or claims of maximum bandwidth.

## Run an experiment

You need Python 3.9 or later, Terraform, and credentials for the cloud provider selected in your experiment configuration. Configure the provider's standard credentials and confirm that the account can create and destroy the required VMs and networking resources.

```bash
git clone https://github.com/Frosendroska/cloud-network-benchmark.git
cd cloud-network-benchmark
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cloud-network-benchmark run --config configs/experiments/exp-001-multi-provider.yaml
```

Choose or prepare an experiment configuration for the providers and scenarios you want to run. A run creates temporary infrastructure and incurs cloud charges. The framework saves its manifest and raw measurements locally under `results/`; retain these files with the configuration used to produce them.

## Research context

This framework supports the Master's thesis *Benchmarking Network Performance across Clouds*. The thesis describes the research questions, measurement design, and interpretation of results.

## Analyze saved results

Install the optional local renderer with `python -m pip install -e '.[analysis]'`, then point `analyze` at a selection of saved validator inputs and validation results. The command revalidates selected evidence and writes a new, source-disjoint report directory. A successful exit means publication succeeded; it does not mean every selected observation is valid or comparable.

```bash
cloud-network-benchmark analyze --selection selection.json --output results/processed/report-001
```

See [native analysis](docs/native-analysis.md) for the selection format, output files, and evidence limits.
