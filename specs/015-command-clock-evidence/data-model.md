# Command timing data model

See [contract](contracts/command-timing.md). All existing fields and outcomes remain.

## CommandResult extension

| Field | Type/default | Constraint |
| --- | --- | --- |
| duration_basis | `Literal['utc','monotonic']`, utc | Selects duration consistency rule |
| monotonic_started | `Optional[float]`, None | Monotonic mode: finite nonnegative number; booleans rejected |
| monotonic_finished | `Optional[float]`, None | Monotonic mode: finite nonnegative number, finish >= start; booleans rejected |
| utc_elapsed_discrepancy_seconds | `Optional[float]`, None | Monotonic mode: finite signed number; booleans rejected |

Existing duration_seconds: finite nonnegative number; booleans rejected. Both timestamps: aware, normalized to UTC preserving instants. UTC basis: all new numeric fields None, finish >= start, duration matches UTC delta within 0.001 s. Monotonic basis: all three numeric fields required, duration matches monotonic delta within 0.001 s, discrepancy matches UTC delta minus monotonic delta within 0.001 s; UTC may run backward. Reject nonfinite numbers before arithmetic. Equality at tolerance boundary is accepted.

## CommandEvidence extension

| Field | Type/default | Constraint |
| --- | --- | --- |
| duration_basis | `Literal['utc','monotonic']`, utc | Same basis as projected result for monotonic opt-in |
| utc_elapsed_discrepancy_seconds | `Optional[float]`, None | Same finite signed discrepancy as projected result for monotonic opt-in; booleans rejected |

Monotonic basis: existing monotonic_started/monotonic_finished Evidence values both available, finite nonnegative and ordered; both UTC Evidence values available, aware and normalized; result dictionary supplies a valid monotonic CommandResult durable projection. Outer UTC instants/basis match result; monotonic values/discrepancy agree within 0.001 s. Apply projected result's own timing and outcome consistency; retain existing outcome rules.

UTC basis retains existing native validation: available monotonic endpoints finite/nonnegative and ordered when both present; available UTC endpoints aware and ordered when both present. Partial/unavailable existing evidence remains allowed. Native discrepancy must be None under UTC basis; explicitly monotonic result must not be wrapped as UTC. Old result dictionaries need not acquire previously absent fields. Boolean observed monotonic values are rejected in both bases.

## Relationships and invariants

Producer plus deployment/unit/build identity follows native evidence. Monotonic endpoints describe that producer's single local domain, never elapsed between machines; synchronous runner uses controller timing. Wrapper and projection cannot carry contradictory timing. Existing ClockMapping synchronization/error estimates remain independent.

CommandEvidence stays nested in ChildRunManifest and ValidatorInput under schema_version=2. Only those active schema exports change. No registry/schema-set addition or validity/comparison state transition.
