# Experiment Configs

This directory stores executable experiment configuration for the implementation framework.

Each config must use a stable `experiment_id` that maps back to the thesis-side design in `../Thesis/experiments/` and `../Thesis/DECISIONS.md`.

Do not duplicate long methodology text here. Configs should capture how a concrete observation is executed.

Expected fields once configs are implemented:

- `experiment_id`
- `scenario`
- `provider`
- `regions` and `zones` where relevant
- `vm` configuration
- `benchmark` phases, tool parameters, durations, and stream counts
- repetition or schedule settings
- validation expectations

The first implementation milestone should focus on smoke observations and validation before real experiment campaigns.
