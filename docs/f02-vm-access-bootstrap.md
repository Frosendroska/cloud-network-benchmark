# F02 VM Access, Bootstrap, Readiness, and Retrieval

F02 owns the provider-neutral boundary after a provider driver returns exactly two VM connection records. VM A is the client/traffic generator and VM B is the server/receiver.

The implementation is credential-free in tests. It uses F01's replaceable command runner for SSH and SCP action records, bounded readiness checks, cloud-init completion, private-path verification, benchmark-server health, and immutable local retrieval destinations.

See the feature design artifacts:

- [F02 specification](../specs/002-vm-access-bootstrap/spec.md)
- [F02 contract](../specs/002-vm-access-bootstrap/contracts/remote-access.md)
- [F02 offline validation](../specs/002-vm-access-bootstrap/quickstart.md)

Provider Terraform resources, FLENT phase execution, result validation, cleanup orchestration, and real-cloud tests are handled by other features.

For review, inspect `tests/fixtures/f02/evidence-review.json`: the successful record shows ordered readiness checks, while the failed record demonstrates redacted failure output. The contract test loads both records as an executable smoke check.
