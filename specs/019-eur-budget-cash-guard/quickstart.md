# Offline validation
Use an existing Python3.12+ test environment with repository development dependencies.
`PYTHONPATH=src python -m pytest -q tests/unit/test_financial_controls.py tests/integration/test_financial_admission.py`
Expect synthetic cases only, zero provider calls for every real guard rejection; concurrent reservations
are exercised only in offline_simulation. Inspect existing recovery tests for cleanup independence.
`python3 scripts/render_campaign_runbook.py --check` verifies generated presentation.
Full/native evidence and exact revision will be recorded in `docs/validation/eur-financial-guard.md`.
Never use a fixture/card from tests as authority or initialize a live ledger to run these checks.
