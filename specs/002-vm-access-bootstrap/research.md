# Research: F02 VM Access, Bootstrap, Readiness, and Retrieval

## Decision: Reuse F01's shell-free command boundary

**Rationale**: F01 already defines fakeable requests/results with outcomes, timing, output, and redaction. SSH and SCP become action types within that boundary.

**Alternatives considered**: Direct subprocess calls would hinder timeout/failure injection and violate offline-first validation; a second runner would duplicate F01 semantics.

## Decision: Normalize exactly two VM records with fixed roles

**Rationale**: F05-F07 retain provider-specific deployment details. F02 validates only the handoff needed for the thesis direction: VM A client/traffic generator to VM B server/receiver.

**Alternatives considered**: Provider-specific access models leak Terraform concerns; an unbounded host list weakens role and direction validation.

## Decision: Use ordered, bounded readiness stages

**Rationale**: Bootstrap/cloud-init precede private-path and server checks. Per-stage deadlines, polling attempts, and last output make fake tests deterministic and failures actionable.

**Alternatives considered**: Parallel or unbounded checks obscure prerequisites and risk hanging paid runs.

## Decision: Retrieve declared artifacts into exclusive destinations

**Rationale**: F01 owns canonical paths and immutable evidence. A source/destination manifest makes partial retrieval explicit and prevents overwrite.

**Alternatives considered**: Broad directory copies hide missing artifacts and risk evidence collisions.

## Decision: Keep pins configurable with Ubuntu as the current target

**Rationale**: The design targets Ubuntu and pinned FLENT/netperf, while exact versions and sources may change during calibration.

**Alternatives considered**: Baked images and containers violate the deliberately small architecture and F02 scope.
