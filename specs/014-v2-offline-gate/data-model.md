# F10 local evidence model

No public runtime DTO/schema changes. These are plain local JSON evidence objects, not research observations.

| Entity | Required fields and rules |
| --- | --- |
| Gate evidence | `format_version=1`, exact `head_sha` and Git tree, start/end clean status, safe host/interpreter/tool/package versions, Thesis revision or explicit unavailable reason, invocation argv, UTC start/end, offline result, checks, required-coverage results, platform gaps and limitations. Never export the full environment or credential values. |
| Check result | Stable name/scope, argv, exit code or explicit unavailable reason, monotonic duration, passed/failed/not_run state, relative log path and SHA-256 when a log exists. Not run never counts as passed. |
| Test evidence | Parsed JUnit counts and exact required test/parameterized identities, missing/unexpected skipped cases and report parse errors. Failed/error/empty/malformed required reports prevent success. |
| Platform gap | Exact test identity, actual skip reason and Darwin host; only the two declared unavailable-Linux-`/proc` identities are allowed. `native_linux_verified=false`; obligation to execute them on Linux persists. |
| Replay evidence | Existing F09 saved input/result paths and native source hashes, F12 selection and analysis revision, generated relative filenames/bytes/hashes. Reuse F12 provenance; no independent fixture truth. |
| Paid handoff | Registered case IDs and scope, required real evidence, admission/cleanup conditions and explicitly pending N/windows/price/permission/native outcomes. Documentation only. |

State: preflight → running → passed_offline / failed / incomplete. Any dirty/revised checkout, missing prerequisite/scenario/report, failed check or unexpected skip forbids passed_offline. The exact two allowed Darwin skips remain distinct `platform_gaps` in passed_offline evidence and forbid any native-Linux/cloud-readiness claim. Interrupted runs preserve available evidence as incomplete/failed. Replays are temporary test artifacts; the coordinator retains final full-command logs outside versioned source and commits a concise evidence record.
