# Quickstart: Validate F02 Offline

This guide validates VM access, bootstrap, readiness, and retrieval with fake remote actions. It must not require cloud credentials, live VMs, Terraform, SSH, SCP, or benchmark traffic.

## Prerequisites

- Python 3.9+
- Development dependencies from `pyproject.toml`
- F01 contracts in the same checkout

## Focused validation

From the repository root:

```bash
python -m pytest tests/contract/test_remote_access_contract.py tests/unit/test_bootstrap.py tests/unit/test_readiness.py tests/unit/test_retrieval.py tests/integration/test_remote_sequences.py -q
```

Expected result: all F02 tests pass and no test invokes a cloud or real remote command.

## Required fake scenarios

1. Valid two-VM handoff and rejection before the first action for malformed roles, addresses, or SSH references.
2. Successful bootstrap on both VMs with pinned-tool and cloud-init evidence.
3. Command/cloud-init failure, timeout, and interruption with redacted VM-specific evidence.
4. SSH success followed by private-path failure; server-not-ready; delayed readiness; readiness timeout.
5. Complete SCP retrieval, missing source, transfer/integrity failure, partial success, interruption, and destination collision.

## Acceptance checks

- Readiness is false until private-path and server-health checks pass.
- Every timeout terminates within its configured fake deadline and records attempts and last output.
- Retrieval preserves successful artifacts when another transfer fails and never overwrites a destination.
- No fixture or durable failure record contains credentials or private keys.

See [data-model.md](data-model.md) and [contracts/remote-access.md](contracts/remote-access.md).
