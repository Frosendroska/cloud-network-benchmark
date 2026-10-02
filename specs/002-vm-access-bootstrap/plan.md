# Implementation Plan: VM Access, Bootstrap, Readiness, and Retrieval

**Branch**: `002-vm-access-bootstrap` | **Date**: 2026-09-27 | **Spec**: [spec.md](spec.md)

## Summary

Implement the provider-neutral remote-access boundary consumed after F05-F07 return normalized Terraform outputs. The feature validates a two-VM handoff, renders minimal pinned bootstrap, executes SSH/SCP through F01's fakeable command boundary, establishes cloud-init/private-path/server readiness, and retrieves declared artifacts into immutable F01 paths. Provider Terraform, benchmark phase execution, and real-cloud validation remain out of scope.

## Technical Context

**Language/Version**: Python >=3.9

**Primary Dependencies**: Existing Pydantic v2 models, F01 command-runner protocol, standard library

**Storage**: Local immutable result bundle paths and manifest-ready structured records; no new service or database

**Testing**: pytest unit, contract, and fake integration tests; no credentials or live SSH/SCP

**Target Platform**: Local Linux/macOS controller targeting Ubuntu VMs

**Project Type**: Python library and CLI lifecycle component

**Performance Goals**: Every readiness and retrieval operation honors configured bounded deadlines; F02 introduces no throughput target

**Constraints**: Offline-capable tests, shell-free argument execution, secret redaction, immutable destinations, explicit VM A-to-B roles, no provider resource logic

**Scale/Scope**: Exactly two VMs per observation; ordered remote actions, bounded polling, and declared artifact retrieval for one run

## Constitution Check

- **I. Traceability**: PASS — records retain F01 `run_id`, provider, scenario, roles, and action evidence for manifests.
- **II. Small architecture**: PASS — local controller plus minimal bootstrap and SSH/SCP; no agents, storage services, containers, or queues.
- **III. Immutable results**: PASS — retrieval uses F01 paths, refuses collisions, preserves partial evidence, and records unavailable values honestly.
- **IV. Provider explicitness**: PASS — F02 consumes normalized outputs and does not hide provider Terraform semantics.
- **V. Offline-first**: PASS — all external actions are replaceable and fake-backed; cleanup remains F09 ownership.

## Project Structure

```text
src/cloud_network_benchmark/
├── access.py
├── bootstrap.py
├── readiness.py
├── retrieval.py
└── ... existing F01 modules

tests/
├── contract/test_remote_access_contract.py
├── integration/test_remote_sequences.py
└── unit/test_bootstrap.py, test_readiness.py, test_retrieval.py
```

**Structure Decision**: Extend the existing single Python package with focused modules and fake-backed tests. Do not add a second package or provider-specific access layer.

## Complexity Tracking

No constitution violations. The design stays within the existing package and F01 command boundary.
