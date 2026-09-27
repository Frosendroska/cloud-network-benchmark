# F01 Contract Index

These contracts are the public boundary that F02-F08 may import. Field semantics and lifecycle rules are defined in [data-model.md](../data-model.md).

| Contract | Purpose | Primary consumers |
| --- | --- | --- |
| [campaign-config.schema.json](campaign-config.schema.json) | Structural campaign YAML contract; semantic provider/scenario rules are applied by the resolver. | CLI, config fixtures, F09 |
| [campaign-manifest.schema.json](campaign-manifest.schema.json) | Parent campaign identity, explicit start, child references, tool versions, and aggregate state. | F09, F08 |
| [child-run-manifest.schema.json](child-run-manifest.schema.json) | Provider child identity, resolved observation, VM/provider/tool evidence, execution state, and cleanup state. | F02-F09 |
| [shared-contracts.md](shared-contracts.md) | Deployment, connection, artifacts, validator, command-runner, and dry-run boundaries. | F02-F08 |
| [cli.md](cli.md) | Credential-free command syntax, output, exit status, and side-effect guarantees. | Users, tests, F09 |

JSON Schemas use draft 2020-12. YAML configuration is parsed into ordinary JSON-compatible values before structural and semantic validation. Runtime Pydantic models are authoritative for cross-field rules that JSON Schema cannot express cleanly, especially exact selected-provider matching and scenario-specific placement invariants.

Contract changes after F01 merge require review across every dependent feature. Additive fields must have explicit optionality and defaults; renamed fields, enum changes, path changes, and changed lifecycle semantics are breaking changes and require a schema-version increment.
