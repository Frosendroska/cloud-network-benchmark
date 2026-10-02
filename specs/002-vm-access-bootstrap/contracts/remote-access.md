# F02 Remote Access Contract

F02 consumes F01 provider deployment output and returns manifest-ready evidence. It does not apply Terraform, destroy resources, execute FLENT phases, or validate benchmark results.

## Operations

```text
validate_connection(output) -> ValidatedConnection
prepare(connection, bootstrap_config, command_runner) -> BootstrapResult
check_readiness(connection, readiness_config, command_runner) -> ReadinessResult
retrieve(connection, retrieval_manifest, command_runner, filesystem) -> RetrievalResult
```

All operations reject invalid connection data before remote actions. `command_runner` is F01's fakeable `run(CommandRequest) -> CommandResult` protocol.

## Action Types

| Action type | Target | Purpose |
| --- | --- | --- |
| `ssh` | VM A or VM B | Bootstrap, cloud-init, private-path, or server-health command. |
| `scp` | VM A or VM B | Copy one declared artifact to one exclusive local destination. |

Requests use argument arrays with shell execution disabled. Authentication references are runtime-only and never serialized as secret values.

## Sequence Guarantees

1. Validate both VM records and roles.
2. Bootstrap VM A and VM B, including cloud-init completion.
3. Check private path from VM A to VM B and benchmark-server health on VM B.
4. Retrieve only declared artifacts after later phases/collectors request it.

Any failed, timed-out, or interrupted prerequisite prevents the next stage. Already captured evidence is retained.

## Failure Categories

`invalid_connection`, `ssh_failed`, `bootstrap_failed`, `cloud_init_failed`, `private_path_failed`, `server_not_ready`, `readiness_timeout`, `scp_failed`, `artifact_missing`, `integrity_failed`, `destination_collision`, `retrieval_timeout`, and `interrupted`.

## Security and Evidence Rules

- Never persist private keys, credential contents, secret environment values, or sensitive stdin.
- Redact command output before durable evidence storage when configured secret patterns occur.
- Refuse destination collisions; do not overwrite F01 result artifacts.
- Preserve the original failure separately from any cleanup failure handled by F09.
