# F02R offline implementation record

This record tracks implementation of the reviewed F02R migration. It does not claim real SSH,
SCP, builds, cloud firewall enforcement or provider cleanup evidence.

## Baseline and ownership

- Base: `cef838e42fc620f90b5943f5a8d1528ddc0b0724` (merged F01R).
- Planning head: `44ea1ececafe8a4008a9342757a3e76afe5089c2` on `codex/f02r-v2-remote-access`.
- Migration targets: existing `access.py`, `bootstrap.py`, `readiness.py`, `retrieval.py` and their
  focused tests. Frozen shared contracts and schemas are not owned by this feature.
- Historical `specs/001-*` and `specs/002-*` are preserved.
- Initial command: `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q` — exit 0, 422 passed.

## Implementation evidence

The implementation is limited to the four existing remote modules, their narrow root exports,
F02R-owned synthetic fixtures/tests, and this record. Shared F01R contract/schema files and
historical specs 001/002 have no diff from the merged F01R base.

- Tested implementation SHA: `d1372332f0b73c31d8fe12f35b82707374563a0d`.
- Focused command from `quickstart.md`: exit 0, **53 passed**.
- Full regression command from `quickstart.md`: exit 0, **464 passed** (422 at baseline).
- `git diff --check`: exit 0.
- Ownership/frozen-contract/historical-spec diff inspection against `cef838e42fc620f90b5943f5a8d1528ddc0b0724`: no changes found in shared contracts, schemas, or specs 001/002.
- No CLI/schema command is specified by the feature quickstart, and F02R changes no Terraform files.

### Phase 7 convergence repairs

The appended T025–T036 defects were repaired in the remote modules and F02R tests. Retrieval now
uses an executable POSIX inventory script, checks every concrete required declaration, preserves
typed failure/partial-copy evidence, stops after interrupted inventory, and preflights all item
identities and destinations. Bootstrap safely quotes/rejects nested shell values, checks install
identity before rebuild, validates observed compiler/tool capabilities, verifies exact listener
and ordered ACL/process state, and preserves accumulated evidence at command boundaries. Readiness
checks full prepared identity, exact listener predicates, redaction, and the original deadline.
Integration tests use frozen F01R layouts and typed reservation/expiry evidence; they remain
credential-free and local.

- Focused quickstart command: exit 0, **53 passed**.
- Full regression command: exit 0, **464 passed**.
- `git diff --check`: exit 0 on the tested implementation and after the final documentation edits.
- Shared F01R contracts/schemas and historical specs 001/002 remain unchanged against the F01R base.
- No real SSH/SCP, upstream download/build, cloud call, benchmark, or guest/provider enforcement was run.

### Phase 8 convergence repairs

T037–T043 preserve interruption and transport-preflight failures as typed results with accumulated
actions; failed partial transfers retain observed byte size/hash. Systemd observers now request
`MainPID`, accept a successful start command without fabricated stdout, then verify the live `/proc`
executable path/hash and bind its PID to the socket observation. ACL checks share one exact ordered
owned-chain predicate and reject additional accept/jump rules. Build observations redact supplied
credential references and secret text, while capability checks use separate Sockperf server and
under-load help responses and iperf3-specific options. Retrieval checks the original cleanup budget
after local hashing and final materialized verification, preserving failed integrity evidence. The
integration suite composes F01R layouts with typed caller reservation/expiry evidence across all
four modes, including missing B metadata and a failed later probe chunk.

- Tested implementation SHA: `cef4b0abc87522c9354505ccf7944552e8d3676c`.
- Focused quickstart suite: exit 0, **60 passed**.
- Full regression suite: exit 0, **471 passed**.
- Schema checks are included in the full suite (`tests/contract/test_v2_schema_export.py` and
  `tests/contract/test_schema_integrity.py`); no shared schema files changed.
- `git diff --check`: exit 0; historical specs and F01R/Frozen contracts remain unchanged.
- Real SSH/SCP, downloads/builds, cloud calls, benchmark traffic, and provider enforcement remain
  unrun.

Focused command:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_remote_access_contract.py tests/unit/test_bootstrap.py tests/unit/test_readiness.py tests/unit/test_retrieval.py tests/integration/test_remote_sequences.py tests/integration/test_v2_remote_access.py
```

Full regression command:

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
```

### Phase 9 convergence repairs

T044–T047 repair request-boundary identity loss and zero-budget construction, native systemd
service observations, valid nft command/output handling, and authentic `cc --version` identity.
Retrieval hashes local files in bounded chunks and rechecks the original deadline after local
filesystem work and before publication. The composed offline integration fixture verifies the
service log directory and file mode setup permits same-user retrieval of root-created service logs.
T048 updates the shared artifact verifier to stream in 1 MiB chunks and accepts an optional
backward-compatible `budget_check(size, sha256_prefix)` callback. F02R uses the original cleanup
deadline through that callback, stops verification at the cap, and records prefix integrity when
verification stops mid-file. The coordinator authorized this narrow implementation ownership
extension after F01R clock PR #9 merged; serialized fields and schemas are unchanged.

- Tested implementation SHA: `e2e7247e3d4e22abbb877b02b65b7b761f81dc1f` on
  `codex/f02r-v2-remote-access`.
- Focused quickstart and materialized-verifier checks: exit 0, **108 passed**.
- Full regression suite: exit 0, **547 passed**.
- Frozen schema checks: exit 0, **7 passed**; no schema files changed.
- `git diff --check`: exit 0. Compared with merged F01R clock base `a468bb0219ff1f61bae6cbb3577ab797bdb9763b`, the only shared-contract source change is `contracts/artifacts.py`; published schemas and historical specs 001/002 are unchanged.
- T044–T048 are checked in `tasks.md`.

Primary format references used for the implementation check: systemd v255
[`dbus-execute.c`](https://raw.githubusercontent.com/systemd/systemd/v255/src/core/dbus-execute.c)
and [`execute.c`](https://raw.githubusercontent.com/systemd/systemd/v255/src/core/execute.c)
show the D-Bus output mode separately from the configured `append:<path>` unit setting; the
[nftables manual](https://www.netfilter.org/projects/nftables/manpage.html) documents supported
`add` and `list` syntax. These references establish command formats only. No real systemd guest,
nft enforcement, SSH/SCP, download/build, cloud call, benchmark, or provider behavior was tested.

**Validation commands** (all run with the worktree as the current directory):

```sh
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_remote_access_contract.py tests/unit/test_bootstrap.py tests/unit/test_readiness.py tests/unit/test_retrieval.py tests/integration/test_remote_sequences.py tests/integration/test_v2_remote_access.py tests/contract/test_v2_handoffs.py
# exit 0: 108 passed (includes F02R contract, integration and materialized-verifier tests)
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q
# exit 0: 547 passed
PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis /private/tmp/cnb-offline-venv/bin/python -m pytest -q tests/contract/test_v2_schema_export.py tests/contract/test_schema_integrity.py
# exit 0: 7 passed
git diff --check
# exit 0
```

## Assumptions and limits

- The caller supplies the original `RemoteDeadline`, matching deployment identity, closed producer
  inventory, and provider-approved pinned source URLs/references/digests. F02R consumes these
  records; it does not register expiry, create VMs, start a measurement, renew a cap, or clean up.
- Retrieval completion means all required declared files were physically copied and matched to the
  supplied remote size/hash. It does not validate benchmark semantics or change eligibility.
- Every source URL, compiler output, listener response, identity file, and transferred byte used in
  tests is synthetic. Tests invoke no real SSH/SCP, download/build tool, benchmark, cloud API, or
  provider firewall. Real source availability/build behavior, authenticated transport, guest ACL
  enforcement, cloud networking and cleanup remain unrun and require the later authorized stages.
- No Thesis file, cloud resource, credential, secret, Terraform state/plan, or raw result bundle was
  changed or created.
