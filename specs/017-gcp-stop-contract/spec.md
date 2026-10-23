# Feature Specification: GCP STOP Contract Migration

**Feature Branch**: `codex/f01r-gcp-stop`

**Created**: 2026-10-23

**Status**: Ready for implementation after the serialized acquisition-contract merge

**Input**: Coordinator-assigned F01R follow-up: align active GCP configuration and deployment contracts with the governing independent compute-STOP fallback; migrate active examples, published schemas and focused checks without implementing the GCP driver.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Declare the required GCP fallback (Priority: P1)

An experiment operator can validate an On-Demand GCP experiment whose emergency expiry stops compute, preserving the separate duty to retrieve evidence and delete all billable resources.

**Why this priority**: Current executable contracts demand DELETE even though the governing protocol requires STOP. This prevents the GCP provider from consuming a conforming input.

**Independent Test**: Validate otherwise identical GCP inputs with STOP and DELETE at both configuration and direct deployment boundaries; only STOP succeeds. Focused offline regression tests are required.

**Acceptance Scenarios**:

1. **Given** any supported V2 mode with otherwise valid GCP settings, **When** the operator declares STANDARD On-Demand provisioning and STOP expiry, **Then** configuration validation accepts the settings and resolution retains them exactly.
2. **Given** an otherwise valid direct GCP deployment input, **When** its expiry action is DELETE, omitted, null, an unknown value or incorrectly cased, **Then** validation rejects it before any external action.
3. **Given** a GCP input with Spot provisioning or an extra preemptible option, **When** validated, **Then** it is rejected; STOP does not change the selected On-Demand purchase model.

### User Story 2 - Use consistent published inputs (Priority: P2)

An operator or downstream consumer sees the same GCP STOP requirement in active examples, resolved inputs, published contracts and offline previews.

**Why this priority**: Updating only a validator leaves runnable examples and independent consumers inconsistent.

**Independent Test**: Check all active GCP configurations, independently validate exported contracts, and inspect existing CLI previews without executing their listed actions.

**Acceptance Scenarios**:

1. **Given** the active acceptance, calibration, short-study and diagnostic configurations, **When** each is validated and resolved, **Then** every included GCP block contains STANDARD and STOP; all four scenario names remain supported with their existing admission gates.
2. **Given** a valid configuration, resolved campaign or direct deployment document, **When** a consumer checks its published contract, **Then** STOP is accepted and DELETE, missing/null action and non-STANDARD provisioning are rejected at each GCP options occurrence, including resolved observations.
3. **Given** existing AWS/Azure examples and historical F01/F02 specifications, **When** this migration is delivered, **Then** their provider semantics and the historical specification bytes remain unchanged.

### Edge Cases

- GCP options are explicit: there is no inferred STOP default or silent rewrite of DELETE.
- An unselected extra provider block remains invalid under existing provider-selection rules.
- Direct deployment construction must enforce the rule even when configuration loading is bypassed.
- Invalid GCP inputs fail through existing sanitized validation errors; rejected values do not authorize external commands.
- Historical saved DELETE configurations are not rewritten or reinterpreted. Resubmission as a new V2 input is rejected until the caller explicitly corrects the settings.
- Expiry STOP is not evidence of full resource deletion; disks, addresses and other billable resources still require normal cleanup verification.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Active V2 GCP configuration and direct deployment inputs MUST require exactly `provisioning_model=STANDARD` and `instance_termination_action=STOP`, preserving existing field names and explicit option presence.
- **FR-002**: Both input boundaries MUST reject DELETE, omitted/null/malformed/unknown/case-variant actions, non-STANDARD provisioning and extra provider options; they MUST NOT silently coerce DELETE into STOP or introduce Spot/preemptible support.
- **FR-003**: Every active executable configuration containing GCP MUST declare STANDARD and STOP; resolution and offline previews MUST preserve the same values across the four modes and supported scenarios.
- **FR-004**: Published configuration, resolved-campaign and direct deployment contracts MUST express the GCP rule at every corresponding options occurrence, agree with runtime acceptance/rejection and be deterministically regenerated from current models.
- **FR-005**: This migration MUST preserve schema version 2, existing identities, provenance/hash derivation, admission gates, AWS/Azure semantics and historical specs 001/002. Existing saved evidence MUST NOT be edited or silently reinterpreted.
- **FR-006**: Feature documentation MUST distinguish independent GCP compute STOP from normal deletion/verification of all billable resources. The GCP driver, registration mechanism, cap policy and real cloud acceptance remain outside this migration.
- **FR-007**: Delivery MUST provide focused offline runtime/schema/configuration/CLI regressions, the existing full offline suite and a clean schema-drift check. It MUST NOT contact clouds, use credentials or generate benchmark/SSH traffic.
- **FR-008**: Shared-code implementation and canonical schema export MUST wait until the coordinator verifies the acquisition-contract follow-up has merged and serializes this writer on that merged base. F07 implementation MUST wait for this migration's verified merge; planning alone establishes no runtime support.

### Key Entities

- **GCP provider options**: Existing explicit purchase-model and emergency-action settings shared by configuration and provider deployment inputs.
- **Active configuration**: A runnable V2 experiment/test example whose resolved options and content hash derive from its actual bytes.
- **Published contract**: The canonical exported validation definition consumed independently of runtime model validation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every conforming GCP case in the declared acceptance matrix is accepted and every forbidden case is rejected at both independent input boundaries.
- **SC-002**: All eight currently active GCP examples retain the required STOP setting through validation, resolution and preview, without changing other provider settings or admitting deferred experiments.
- **SC-003**: Independent published-contract checks agree with runtime on 100% of the declared GCP option cases; repeated contract generation produces identical bytes.
- **SC-004**: All existing offline checks pass after migration, historical F01/F02 specification bytes remain identical, and recorded evidence distinguishes offline proof from unperformed cloud acceptance.

## Assumptions

- Constitution 2.0.0, AGENTS.md, the 18 October safety decision and current F07 prompt already require compute STOP. Correcting contradictory executable DELETE settings is an engineering migration, not a research decision or new advisor approval.
- STANDARD is the existing representation of On-Demand/nonpreemptible GCP intent. No new purchase-mode field, compatibility mode, service or adapter is necessary.
- Planner owns only `specs/017-gcp-stop-contract/` in `/private/tmp/cnb-gcp-stop`, based on `e03a07eaa5ed9ea372c695db19fd6fbcffdad024`. Other worktrees have other writers; their changes must be preserved.
- The acquisition-contract follow-up is a serialization prerequisite for implementation/export, not an unmerged runtime interface consumed by this plan. Only existing stable fields are specified here.
- The eight active examples are an observed baseline, not permission to omit a new GCP example introduced by the required base refresh.
