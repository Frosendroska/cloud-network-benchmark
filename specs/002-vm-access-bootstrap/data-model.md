# Data Model: F02 VM Access, Bootstrap, Readiness, and Retrieval

## Conventions

- Fields use `snake_case`; timestamps are RFC 3339 UTC values.
- Secret material is never stored; authentication fields are runtime references.
- F01 `CommandRequest` and `CommandResult` are reused for every SSH/SCP action.

## Connection Output

Provider-neutral handoff after provisioning.

| Field | Type | Rules |
| --- | --- | --- |
| `run_id` | string | Matches the current F01 child run. |
| `provider` | enum | `aws`, `azure`, or `gcp`. |
| `scenario` | enum | Preserves the F01 scenario term. |
| `vm_a`, `vm_b` | VM Connection | Exactly one each; roles are fixed. |
| `timeouts` | Timeout Policy | Positive, configured, and bounded. |

### VM Connection

| Field | Type | Rules |
| --- | --- | --- |
| `vm_id` | string | Non-empty and unique within the pair. |
| `role` | literal | VM A `client`; VM B `server`. |
| `private_ipv4` | IPv4 string | Valid, required, and different between VMs. |
| `ssh` | SSH Reference | Host, port, user, runtime auth reference. |
| `readiness_expectations` | object | Private target and server-health expectation. |

## Bootstrap Plan and Record

The plan contains pinned FLENT/netperf versions or artifact references, required Linux tools, cloud-init completion signal, and role-specific setup. The record contains resolved values, ordered action IDs, installed-version evidence, cloud-init result, timestamps, and outcome: `succeeded`, `failed`, `timed_out`, or `interrupted`.

## Readiness Record

Ordered checks are SSH connection, cloud-init completion, VM A to VM B private-path probe, and VM B benchmark-server health. Each records attempts, deadline, last output, command result, and outcome. Pair readiness is true only when every required check is ready.

## Retrieval Manifest and Record

Each item contains `artifact_name`, `source_vm`, `source_role`, `source_path`, `destination_path`, expected integrity metadata, and transfer timeout. Results record SCP action ID, bytes, integrity evidence, timestamps, and outcome: `retrieved`, `missing`, `failed`, `integrity_failed`, `timed_out`, `interrupted`, or `collision`.

## Failure Evidence

Every failure contains stage, VM, role, action or source path, category, message, timestamps, deadline when relevant, last output, and partial-result references. The first failure remains primary if a later operation fails.
