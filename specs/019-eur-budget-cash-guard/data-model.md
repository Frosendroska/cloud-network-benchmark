# Data model
Typed authorization card references exact hashed local JSON source. It binds ledger scope, batch/stage,
explicit human/validity fields, unique exact run bindings and positive integer resource counts. approved_run_count counts unique provider child run IDs across all entries. Each
binding includes campaign/config/resolved/runtime/code/price/run identities and provider resource scope.
Limits are explicit decimal EUR strings; project<=300, working+protected<=project, per-provider/stage/run/
batch and cash. Official price/FX captures have hashes, URLs and offset-aware checked/valid-until times.
Liabilities round up cents; limits round down. Offsets are unsupported and rejected.
Financial commitment stores source identity, binding, approved caps, per-provider EUR maxima and cash,
FX plus external totals. It joins the existing reservation under the same lock; release does not erase it.
Legacy reservations without financial snapshots block financial admission and remain readable for cleanup.
Revalidation rereads bound sources before new work; no fresh requirements attach to cleanup/reconcile.
