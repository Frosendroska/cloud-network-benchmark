# Parent aggregate projection for peer timelines

F09 production batch projection updates child manifests one provider at a time, while provider
workflows can produce overlapping event times. Each child still rejects an event earlier than its
own `updated_at`. When a valid child event predates the parent manifest's current high-water time,
the parent records the recomputed aggregate at `max(parent.updated_at, child_event_time)` and keeps
the observed peer time in the event detail. This preserves child causality and parent timestamp
monotonicity without requiring provider workflows to be serialized or changing measurement times.

The generic `recompute_parent_manifest` entry point remains strict for caller-supplied timestamps.
The child-update path is scoped to an actual referenced child manifest whose saved `updated_at`
matches the event being projected. The overlapping-timeline regression processes two selected
providers in opposite orders and checks the same aggregate/high-water result; stale transitions on
one child remain rejected. This is lifecycle bookkeeping only and does not alter benchmark timing,
provider expiry, cost admission, or research protocol.
