# Implementation Plan: Bounded paired WEEK dispatch

**Branch**: `codex/week-slot-concurrency-20261120` | **Spec**: [spec.md](spec.md)

## Summary

Qualify the existing production WEEK scheduler with a frozen, explicit two-member P0/S1 + P1/S3 dispatch policy. Verify its source hash at preparation and run/restart boundaries, validate every slot as an exact pair, and stop later weekly dispatch after a missed or failed slot. Keep acceptance serial and reuse existing DAY/native paths.

## Technical Context

- **Language**: Python 3.12+; standard-library threads and file locks; existing YAML/JSON evidence and F11 ledger.
- **Components**: `campaign_inputs.py` prepares the source-bound policy; `campaign.py` enforces pair and journal rules; `lifecycle_runtime.py` accepts the policy evidence reference; campaign tests cover concurrency and reconciliation.
- **Storage**: Existing one ledger, campaign journal, isolated per-entry runtime/state/results; no new service or external state.
- **Validation**: Focused Linux/native unit and integration tests; fakes do not prove cloud behavior.
- **Constraints**: No cloud/credential calls, live ledger mutation, paid launch, timeout/budget change, new DAY duration, or weakening of trust/expiry/cleanup.

## Constitution Check

PASS: preserves one provider-specific lifecycle per entry, source-bound provenance, shared locked ledger, isolated state, single service, cleanup-first failure behavior, and 86,400-second DAY protocol. Offline validation remains distinct from provider acceptance.

## Design

1. Require `weekly_concurrency` evidence with exactly `paired_s1_s3` and cap `2`; write a generated evidence reference and SHA-256 into prepared WEEK state.
2. Freeze the policy into the existing stage digest. Verify the evidence at runner start and before dispatching each slot.
3. Validate unique entry IDs and exactly one P0/S1 plus one P1/S3 with matching launch and scheduled times.
4. Never dispatch partial pairs. Preserve the current serialized acceptance grouping. Block later weekly slots after any miss/failure or unresolved pair state.
5. Use existing DAY rotation, persistent TCP, receiver streaming, close/retrieve and analysis checks without changing DAY code.

## Files

- `src/cloud_network_benchmark/campaign_inputs.py`
- `src/cloud_network_benchmark/campaign.py`
- `src/cloud_network_benchmark/lifecycle_runtime.py`
- `plans/operator-settings.example.yaml`
- `docs/campaign-developer.md`
- `tests/integration/test_campaign_driver.py`
- `tests/integration/test_campaign_admission.py`

## Risks

The candidate pins the available source snapshot. The final reviewed Azure delta is not present in this repository's object database and must be integrated once available, followed by only the affected checks. No provider acceptance is inferred from this feature.
