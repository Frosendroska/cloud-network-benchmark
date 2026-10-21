# Executable V2 configurations

Each YAML has a distinct `config_id`; CSV schedule entries will reference that ID rather than
creating another plan. `scheduled_start` is null until an executed schedule row supplies a UTC
slot. Pending N, price estimates and controller CIDR remain explicit unavailable evidence. These
configurations describe intent only and do not authorize admission or resource creation.

| Configuration | Config ID | Experiment ID | Mode / scenario | Thesis mapping |
| --- | --- | --- | --- | --- |
| `experiments/exp-001-multi-provider.yaml` | `exp-001-short` | `EXP-001` | short_study / same_zone (P0) | [EXP-001 row](../Thesis/experiments/EXPERIMENTS.md#L30) |
| `experiments/exp-002-cross-zone.yaml` | `exp-002-short` | `EXP-002` | short_study / cross_zone (P2) | [EXP-002 row](../Thesis/experiments/EXPERIMENTS.md#L32) |
| `experiments/exp-003-placement-optimization.yaml` | `exp-003-short` | `EXP-003` | short_study / placement_optimization (P1) | [EXP-003 row](../Thesis/experiments/EXPERIMENTS.md#L31) |
| `experiments/exp-004-inter-region.yaml` | `exp-004-short` | `EXP-004` | short_study / inter_region (P3) | [EXP-004 row](../Thesis/experiments/EXPERIMENTS.md#L33) |
| `experiments/exp-001-calibration.yaml` | `exp-001-calibration` | `EXP-001` | calibration / same_zone (P0) | [EXP-001 retained modes](../Thesis/experiments/EXPERIMENTS.md#L30) |
| `experiments/exp-001-diagnostic-day.yaml` | `exp-001-day` | `EXP-001` | diagnostic_day / same_zone (P0) | [DAY stage](../Thesis/experiments/EXPERIMENTS.md#L17) |
| `tests/exp-900-single-provider.yaml` | `exp-900-acceptance` | `EXP-900` | acceptance / failure_check | [validation identities](../Thesis/experiments/EXPERIMENTS.md#L34) |
| `tests/exp-901-azure-single-provider.yaml` | `exp-901-acceptance` | `EXP-901` | acceptance / failure_check | [validation identities](../Thesis/experiments/EXPERIMENTS.md#L34) |
| `tests/exp-902-gcp-single-provider.yaml` | `exp-902-acceptance` | `EXP-902` | acceptance / failure_check | [validation identities](../Thesis/experiments/EXPERIMENTS.md#L34) |
| `tests/exp-903-three-provider-smoke.yaml` | `exp-903-acceptance` | `EXP-903` | acceptance / failure_check | [validation identities](../Thesis/experiments/EXPERIMENTS.md#L34) |

The four ordinary research configs select AWS, Azure and GCP. Acceptance validation configs are
registered `failure_check` cases and may exercise one selected provider. S1/P0 and S3/P1 remain
the primary research scenarios; S2/P2 and S4/P3 are configured for offline support but gated from
execution. Calibration and diagnostic_day retain their own S1 pair and use separate configs.

Research configs encode the selected Ubuntu 24.04 amd64 shapes, 32 GiB provider-specific disks,
private benchmark network, public management address intent, MTU/DSCP/ECN/offload settings, native
ports, strict host identity policy and provider acceleration. Each run uses isolated address space;
the subnet choices and placement semantics follow the current Thesis handoff. Intended configuration
never substitutes for observed provider IDs or VM metadata.

The fixed protocol is represented in each config: 60 seconds per short/calibration phase and ten
seconds per acceptance phase; Sockperf 64-byte UDP full RTT at 1,000 messages/second with monotonic
timing; iperf3 receiver-side A-to-B TCP with CUBIC, sender zerocopy and 131,072-byte buffers. All
modes keep five-second warm-up, selected stabilization and sampling settings. Calibration orders
its 12 loaded trials as 1/4/2/8, 8/2/4/1 and 2/8/1/4 streams. DAY is one 86,400-second TCP unit
with ≤300-second local probe chunks and no scheduled TCP restart.

The dated run-register source is identified as 14 October 2026 planning input; the source hash and
scenario-specific estimate remain unavailable in this checkout. The $300 overall cap and $50
protected buffer are recorded as limits, not an admission result or live quote. F11 resolves the
registered source and reservation before any launch. Every local preview reports pending admission.
