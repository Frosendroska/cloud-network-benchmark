# Halle native-failure repair — 8 November 2026

Scope: repair the failures reported at `f7c3d4cf569c00240af8bed33829834d82d2d127`.
Keep `/u/halle/brek/home_at/cnb/evidence/setup-UKjzH6Er/` unchanged. This is offline
implementation work. No halle login, cloud call, credential use, paid run, service change,
or Thesis edit was performed by this repair.

## Causes and changes

- **Scoped cancellation:** the embedded Python received nine arguments and unpacked eight.
  The two Linux test runners also decoded the SSH payload incorrectly: the command is
  `sh -c 'python3 -c ...'`. They now execute the Python payload inside that shell layer.
  The real fork test holds its leader until the child and `/proc` identity are observed.
  Cancellation checks the durable deployment/invocation record, boot, original leader's
  start ticks, PGID/SID and member identity; it rechecks identity before escalation.
  Zombies count as exited workloads, not live processes. Reaping remains the parent's or
  host reaper's responsibility. TERM and KILL share one bounded allowance. Synthetic tests
  execute the exact shipped payload on every OS, including zombie-only/orphan sessions,
  stale records, boot/session/start-tick mismatches, reuse during TERM, and timeout.
- **Four gate failures:** stubbed metadata tests accidentally depended on an existing
  `/Users/.../Thesis`. Each now supplies its own temporary path. The real gate resolves
  `THESIS_ROOT` or the normal sibling/worktree Thesis location; missing real context still
  fails. Terraform preflight now checks the host's Linux/Darwin amd64/arm64 package path
  instead of always requiring `darwin_arm64`. Tests reject a Darwin-only cache on Linux.
- **Five fake-provider validity failures:** campaign initialization tried to read genuine
  Thesis provenance on a host without that repository. Only these explicitly synthetic
  fixtures now supply `StaticGitProvenance` for their design identity. Implementation
  identity remains genuine, exercising the staged-source guard. Production provenance and
  validation rules are unchanged. Calibration/day comparison-ineligible findings are
  expected mode restrictions, not permission to mark those modes comparable.
- **Replay peak:** the old artifact verifier allocated the next 1 MiB `read()` chunk before
  releasing the previous one. It now uses one reusable `bytearray` with `readinto()`.
  Checksums, byte budgets, full-file verification and the original 3 MiB replay assertion
  remain. A separate bounded-allocation regression also detects corruption in the partial
  last chunk. The 100,000-row replay passed on macOS; observed peak was 1,063,818 bytes
  under Python 3.12.11/Rosetta x86_64. This is not a Linux memory measurement.

These are maintenance corrections to F09/F10 and the artifact integrity implementation;
no measurement protocol, VM selection, duration, admission, budget, expiry or full-resource
cleanup requirement changes.

## Local verification

The focused gate tests passed (42). Guest/artifact checks passed (37, with the two native
Linux `/proc` cases skipped on macOS). Six Terraform roots passed formatting/validation;
the four mock-plan suites passed. Provider plugins required local socket permission outside
the sandbox; execution used a filesystem mirror and an environment with cloud credentials
removed. The initial sandbox plugin-start failures are retained separately.

At tested code `7d7955da34b7baea152f3ab4e2a6c79e29295a42`, the full native macOS arm64
suite (Python 3.12.14) passed: **1,592 passed, two Linux `/proc` skips**, one existing tar
extraction deprecation warning, 877.48 seconds, exit 0. JUnit contains 1,594 cases and no
failures/errors. This includes every reported provider projection case and the large replay.
Evidence: `/tmp/cnb-halle-repair-full-tlflj8vk/`; Terraform static evidence:
`/tmp/cnb-halle-repair-static-jao58kli/`. Schema, compilation and whitespace checks passed.
Existing user runbook edits were preserved; this was not a clean-checkout renewal of the
strict F10 gate. The follow-up commit only refines this handoff, leaving code/tests equal
to the tested revision. The final evidence report and checksums accompany the bundle.

A separate native-arm64 allocation check measured 2,102,230 bytes for the previous checksum
read loop and 1,054,708 bytes for the reusable-buffer implementation. The original replay
limit failure was reported on Linux; the pre-fix macOS replay passed. Neither measurement
substitutes for the halle rerun.
Local Git reads initially stalled on materialized files/objects; verified object reads and a
Git stat-cache refresh restored normal operations without changing staged file contents.

The later halle rerun below qualifies the two local Linux `/proc` tests. Real SSH,
provider expiry/deletion, source-built tools, logout/reboot persistence and cloud behavior
remain unqualified. `Linger=no` and maintenance announced for 11 November remain operational gaps.
The original ~949 MiB setup/fixture size is not the volume of a real diagnostic day.

## Halle rerun and filesystem-buffer correction

The user reported revision `d3115f371b45ce16ca343814f266915a53c1eeb8` from
`/u/halle/brek/home_at/cnb/evidence/native-repair-oKUyLNUZ/`: all **13 scoped-stop
checks passed**, including both native Linux `/proc` cases. The full suite reported
**1,589 passed, one failed, four skipped** in 997.30 seconds. Schema and whitespace
checks passed. The skips were the missing genuine Thesis checkout and three missing
PowerShell-runtime cases. Keep that evidence directory unchanged.

The remaining failure was the new artifact-integrity memory regression: 2,100,252 bytes
against its unchanged 1.5 MiB limit. Python 3.12 chooses its default binary I/O buffer
from the file's block size; this buffer exists in addition to the verifier's reusable
1 MiB buffer ([CPython 3.12.3 implementation](https://github.com/python/cpython/blob/v3.12.3/Modules/_io/_iomodule.c#L330-L374)).
Locally forcing a 1 MiB default I/O buffer reproduced the failure at 2,100,454 bytes.
The subsequent archive's filesystem probe confirms both `st_blksize` and Python's
default block size were 1,048,576 bytes on halle.

The verifier now opens the file with `buffering=0`, since it supplies its own read buffer.
The regression models both 8 KiB and 1 MiB default buffers and still checks the complete
checksum, budget callbacks and corruption in the partial final chunk. Both the 1.5 MiB
contract-test limit and 3 MiB large-replay limit remain unchanged. The subsequent native
rerun reported below confirms this follow-up; the earlier run remains a failed attempt.

Local follow-up verification: **99 passed** in 52.05 seconds under native macOS arm64
Python 3.12.14, covering the artifact contract, retrieval, acquisition and integration
producer suites, including the 100,000-row replay. JUnit: `/tmp/cnb-nfs-repair-focused.xml`.
This was focused verification; the full local suite was not repeated for this two-line
production change.

### Successful halle follow-up

The user supplied the summary for revision
`3811619504e2eb94e953794cc24d8820e76eea35`, with evidence at
`/u/halle/brek/home_at/cnb/evidence/native-buffer-repair-7EuvRZxc/`.
Both focused integrity cases passed without skips. The full Python suite exited 0:
**1,591 passed, four skipped, no failures**, from 1,595 cases. Schema and whitespace
checks also exited 0. At that point, the four skips were the genuine Thesis traceability
case and three PowerShell-runtime cases; the subsequent focused rerun below resolves them.

This establishes that the reported native repair failures are resolved on halle.
It does not establish a complete strict offline gate or real cloud acceptance.
The downloaded archive `/Users/katya/Downloads/halle-buffer-check-summary.zip` was then
inspected locally: ZIP integrity, source revision, JUnit counts, all four exit codes,
logs and a clean remote checkout agree with the summary. The full suite took 965.43
seconds, with one existing tar-extraction deprecation warning. Archive SHA-256:
`b20206701cf6dc67f008d77ad27ca9f1b2c40e3e7a77c28d2016a1c269e72f29`.
Preserve this directory, downloaded archive and both earlier attempts. No further code
repair is indicated by this result.

### Previously skipped checks completed on halle

The initial prerequisite setup installed PowerShell 7.5.11 and verified its archive, but
the private Thesis HTTPS clone failed because halle had no GitHub credentials. Preserve
`prerequisites-6zT44qyQ/` and `prerequisites-mwxq96iN/` as failed setup attempts.
The implementation repository is public; the Thesis repository is private. Setup was
corrected to transfer a standalone Git bundle of the published Thesis revision, without
copying credentials or the Mac's uncommitted changes. Bundle SHA-256:
`7babfd4cb3000410037702873d137b5cf7015af1b5394756a9425dc304300cf1`.

The user then reported **four passed, no failures or skips, exit 0**, with evidence at
`/u/halle/brek/home_at/cnb/evidence/prerequisites-5sgIiB35/`. The implementation revision
is `3811619504e2eb94e953794cc24d8820e76eea35`; the genuine Thesis checkout is
`64de99536074095acbc75c0f57e24ce0cc5b9e4c`. These are the experiment traceability check
and three fake-Azure PowerShell retry cases. This summary's archive has not yet been
inspected locally.

Together with the full-suite run above, all 1,595 Python cases have passed across two
native runs at the same implementation revision. This is not a single full-suite run
with zero skips, nor completion of the strict offline gate. Next retain and inspect the
prerequisite archive, then verify the native Terraform setup and run the strict offline
gate with both genuine repositories. Cloud acceptance remains a separate stage.

### Strict-gate Linux provider-lock follow-up

The mentoring-chat handoff supplied the first strict-gate result at
`/u/halle/brek/home_at/cnb/evidence/offline-gate-KtnJwy37/`, at implementation
`3811619504e2eb94e953794cc24d8820e76eea35` and genuine Thesis
`64de99536074095acbc75c0f57e24ce0cc5b9e4c`. Its full Python suite passed all **1,595
cases in one run**, without failures/skips, with all 112 required identities present.
The overall gate failed at `validate-aws-pair`: the unpacked Linux AWS 6.70.0 package
did not match the recorded checksums. Five checks passed, one failed, fourteen were
not run; the source remained clean and unchanged. Preserve this attempt.

The locks recorded the release ZIP hashes and macOS content hashes, but lacked Linux
amd64 content hashes. A ZIP `zh` cannot validate an unpacked directory; a content `h1`
can ([HashiCorp checksum schemes](https://developer.hashicorp.com/terraform/language/files/dependency-lock#new-provider-package-checksums)).
The existing filesystem mirror is
`/u/halle/brek/home_at/cnb/tools/provider-mirror-AwkH5Pia`; its packages and initialized
roots can be reused. This correction does not require downloading providers again.

Commit `e4f0f2d3aefb404d96fdbaf173f4e8b99165cd47` adds exactly one Linux amd64 `h1`
to each of the six lockfiles, with no other source changes:

| Provider | Pinned version | Linux amd64 content hash |
| --- | --- | --- |
| AWS | 6.70.0 | `h1:HCr4MYgxlkFBOAeWKOoktNNzZaQX8ZZY4YTxmL/1V/Y=` |
| AzureRM | 4.81.0 | `h1:olVprUWhmFTARlydw/r7VIx4fata2gy3zsAWfPGRrhg=` |
| Google | 7.46.1 | `h1:Hgv4BX9846ucZ6XcEql0DPfACoXqlcLat9SjUAn7YLI=` |

The hashes match Terraform 1.16.6's signed-origin Linux lock-generation results retained
at `/private/tmp/cnb-linux-lock-review-x28hg32s`. Verification confirmed that every
existing hash, version, constraint and other source byte was preserved. Six roots
passed local macOS formatting and validation, 12 checks, using the existing mirror and
a sanitized environment: `/private/tmp/cnb-linux-locks-static-cp7d9dw2/`. The Python
suite was not repeated locally for six metadata additions. The subsequent halle result
below confirms the Linux Terraform checks and renewed strict gate.

The incremental `halle-linux-locks.bundle` requires `3811619` and contains only this
commit. Its SHA-256 is
`c1e99638168669ef8391c92ccc1b8d262e56bd859b15bcb9d6c4a8384ea37428`.
The companion `check-halle-linux-locks.py` verifies bundle, source and tool identities,
updates a clean checkout without GitHub authentication, then runs six-root formatting
and validation plus the four registered mock-plan suites. It uses the existing packages,
with a filesystem-only provider configuration and sanitized environment. After those 16
checks pass, its default action renews `scripts/validate_offline.py` in fresh evidence;
`--targeted-only` stops after the Terraform checks. Neither mode runs real apply or
launches cloud resources. Preserve all earlier evidence and the current user runbook edits.

### Successful Linux Terraform and strict-gate rerun

The user supplied the successful summary from
`/u/halle/brek/home_at/cnb/evidence/linux-lock-repair-g01wcey7/`:

- Implementation: `e4f0f2d3aefb404d96fdbaf173f4e8b99165cd47`;
  tree: `9e1b7c35f01ff6a6f56e597ed63963b6f39e3758`.
- Genuine Thesis: `64de99536074095acbc75c0f57e24ce0cc5b9e4c`.
- Targeted Terraform checks: **16/16 passed**.
- Strict offline gate: **20/20 passed**, no failures or omitted checks, source unchanged.
- Python coverage: **1,595 tests**, zero failures/skips, all **112 required identities**,
  no missing identities, unexpected skips or platform gaps.
- `cloud_verified=false`; real provider/service acceptance remains separate.

This completes the reported native repair and its strict offline rerun at the identified
revision. The summary is user-supplied; its `summary.zip` has not yet been downloaded or
inspected locally. Next preserve that small archive on the Mac and inspect its detailed
logs/JUnit/checksums. Keep the original failed gate and every earlier repair attempt.
No additional source repair or repeated test run is indicated by this successful result.

### GCP concrete image-name validation — 9 November

A later settings-only integration check exposed a separate gap at `e4f0f2d`:
Google's observed `READY`, `X86_64`, `GVNIC` image is
`projects/ubuntu-os-cloud/global/images/ubuntu-2404-noble-amd64-v20261105`.
Both `GcpRuntimeSettings.source_complete` and campaign preflight required a different
`ubuntu-2404-lts-amd64-...` spelling. The original no-cloud failure and public-image
readback remain under halle's `private/gcp-acceptance-draft/`; neither file was changed
by the source repair.

Commit `c24d27ce7071c0b890d6304f75747a6ac9674db3` adds one shared validation rule in
`src/cloud_network_benchmark/providers/gcp.py`, used by that model and
`src/cloud_network_benchmark/campaign_inputs.py`. It accepts the observed dated Noble
identity and preserves the dated legacy names already used by fixtures/configurations.
It requires exactly the Frankfurt/London mapping, the official `ubuntu-os-cloud`
project, Ubuntu 24.04 amd64 and a concrete date suffix. Family references, latest/current
aliases, wrong project/OS/release/architecture, URL/query/fragment forms and unsafe
paths remain rejected. The old broad suffix accidentally accepted `...-latest`; the
new rule rejects it. Actual provider image, architecture and GVNIC readback checks are
unchanged. No measurement, scenario, funding or admission rule was changed.

New `tests/integration/test_gcp_image_validation.py` runs the same 56 cases through
the provider model and full campaign preflight, with external process execution
forbidden. The pre-fix checks reproduced rejection of the observed image and acceptance
of the legacy latest alias in both entry points. After repair, **56/56** new regressions
and **222/222** affected local tests passed, with zero skips/failures. Schema,
compilation and whitespace checks passed. Local affected evidence:
`/private/tmp/cnb-gcp-image-local-ziwqcult/` (206.45 seconds, Python 3.12.14).
The earlier incomplete temporary environment and missing jsonschema collection attempt
were setup issues, corrected in a fresh isolated environment before the successful run.

The incremental `halle-gcp-image.bundle` requires `e4f0f2d`, imports only these three
source/test files and was verified against the saved independent base clone. SHA-256:
`91a0ff2154330e29f854f74857a0b352cf7e77e4e99158289a9253e42a5edcb1`.
The helper uses only the previously approved existing SSH master, with no agent
forwarding or password collection. It runs the affected suite and schema/compile/diff
checks, then reads the existing private draft for image validation with subprocess and
network calls blocked. It preserves the private drafts and earlier failure evidence.
The full strict gate is not
repeated for this bounded validator change. The `e4f0f2d` prerequisite pass remains
historical evidence for that revision; cloud acceptance and launch authorization remain
separate.

Native verification at clean `c24d27c`, tree `5ec9b1502e43eb7609625deaa67d48067696fde1`:

- `/u/halle/brek/home_at/cnb/evidence/gcp-image-repair-_qh867pg/`: **222 passed**,
  zero failures/skips, including all **56** new cases, in 683.59 seconds. Schema,
  compilation and whitespace checks also exited 0. The wrapper's later settings step
  failed with `AttributeError` because it assumed already-populated cost-source paths;
  this does not invalidate the retained passed pytest/JUnit checks.
- A settings-only diagnostic at `gcp-image-repair-qhiakuva/` identified that draft
  `workflow_cost_handoff_path` and `support_cost_path` are both null. No source change
  or repeated test run was needed for this helper assumption.
- `/u/halle/brek/home_at/cnb/evidence/gcp-image-repair-7b9d37dg/`: the corrected
  image-only check **passed for the actual private mapping**, with **zero external
  calls**, process/network guards enabled, private draft checksums unchanged and source
  unchanged. The full actual runtime model is explicitly **not ready/not run** until
  its two mandatory cost-source references are prepared. No evidence or price was
  invented to fill them.

Both small sanitized ZIPs were copied to the local artifact directory and inspected:
ZIP integrity, revision, JUnit counts and exits agree. `native-tests-summary.zip`:
`1190b34bccb169abe40d503bb55332fa82428b1dcdd29aee0b3a1d1eca424cf0`;
`native-image-summary.zip`:
`c8289592fcb3be21e692d79b0359e2d0c31ce1b7fe1615aeecbd8a09fbd2ad40`.
They are under
`/Users/katya/.codex/visualizations/2026/11/07/01a1b6c4-1b78-7c83-b5f0-4f2fbb4695da/gcp-image-repair-evidence/`.
The image-name source repair is complete. Source-bound cost/support handoffs, funding,
new-revision initialization/draft binding and concrete launch authorization remain
separate preparation. No resources, APIs, billing, credentials or security settings
were changed. No real cloud acceptance is claimed.

## Historical initial transfer and update on halle (no sudo)

The delivered files are `halle-native-repair.bundle`, `halle-repair-revision.txt` and
`halle-native-repair.bundle.sha256`. Copy them to a **new** `$HOME/cnb/transfers` directory
using your usual authenticated transfer. They contain source, not credentials or results.
The incremental bundle expects the original tested revision in the existing clone.

Run in Bash inside the existing halle session:

```bash
set -euo pipefail
umask 077
cd "$HOME/cnb/transfers"
sha256sum -c halle-native-repair.bundle.sha256
CNB_FIX=$(cat halle-repair-revision.txt)
[[ "$CNB_FIX" =~ ^[0-9a-f]{40}$ ]]
cd "$HOME/cnb/repository"
test -z "$(git status --porcelain)"  # Stop and preserve any local changes.
git bundle verify "$HOME/cnb/transfers/halle-native-repair.bundle"
git fetch "$HOME/cnb/transfers/halle-native-repair.bundle" codex/halle-native-repairs
test "$(git rev-parse FETCH_HEAD)" = "$CNB_FIX"
git switch --detach "$CNB_FIX"
. "$HOME/cnb/venv/bin/activate"
python -m pip check
mkdir -p "$HOME/cnb/evidence"
CNB_EVIDENCE=$(mktemp -d "$HOME/cnb/evidence/native-repair-XXXXXXXX")
export CNB_EVIDENCE
printf '%s\n' "$CNB_FIX" > "$CNB_EVIDENCE/revision.txt"
uname -a > "$CNB_EVIDENCE/uname.txt"
python --version > "$CNB_EVIDENCE/python-version.txt"
python -m pip freeze > "$CNB_EVIDENCE/python-packages.txt"
printf 'Fresh evidence: %s\n' "$CNB_EVIDENCE"
```

Small Linux verification first (actual local processes, no SSH/cloud or sudo):

```bash
set +e
env -i HOME="$HOME" USER="$USER" PATH="$HOME/cnb/venv/bin:/usr/bin:/bin" \
  LANG=C.UTF-8 PYTHONPATH=src \
  python -m pytest -q tests/unit/test_f09_guest.py -k scoped_stop \
  --basetemp="$CNB_EVIDENCE/proc-work" \
  --junitxml="$CNB_EVIDENCE/proc.xml" 2>&1 | tee "$CNB_EVIDENCE/proc.log"
CNB_EXIT=${PIPESTATUS[0]}
set -e
printf '%s\n' "$CNB_EXIT" > "$CNB_EVIDENCE/proc.exit"
test "$CNB_EXIT" -eq 0
```

Require both real `/proc` cases to **pass**, with no skip. Then run the full Python suite
with new paths inside the same fresh evidence directory:

```bash
set +e
env -i HOME="$HOME" USER="$USER" \
  PATH="$HOME/cnb/venv/bin:$HOME/cnb/tools/bin:/usr/bin:/bin" \
  LANG=C.UTF-8 PYTHONPATH=src MPLBACKEND=Agg \
  MPLCONFIGDIR="$CNB_EVIDENCE/matplotlib" \
  nice -n 10 python -m pytest -q --basetemp="$CNB_EVIDENCE/full-work" \
  --junitxml="$CNB_EVIDENCE/pytest.xml" 2>&1 | tee "$CNB_EVIDENCE/pytest.log"
CNB_EXIT=${PIPESTATUS[0]}
set -e
printf '%s\n' "$CNB_EXIT" > "$CNB_EVIDENCE/pytest.exit"
python -m cloud_network_benchmark.contracts.schema \
  --output specs/003-v2-shared-contracts/contracts --check \
  > "$CNB_EVIDENCE/schema.log" 2>&1
git diff --check > "$CNB_EVIDENCE/diff-check.log" 2>&1
git status --porcelain > "$CNB_EVIDENCE/end-status.txt"
du -sh "$HOME/cnb/repository" "$HOME/cnb/venv" "$CNB_EVIDENCE"
printf 'pytest exit: %s\nEvidence: %s\n' "$CNB_EXIT" "$CNB_EVIDENCE"
```

An absent `pwsh` still skips three Azure fake-script tests. Missing real Thesis context still
skips its one traceability test. Those are separate prerequisites; a Python exit 0 with
these skips is not the complete F10 gate. For a real Thesis checkout, set `THESIS_ROOT` to
its actual path and include it explicitly in the `env -i` invocation; never use a synthetic
fixture revision as operational design provenance.

## Optional user-space tools for the omitted checks

No cloud CLI is needed for the offline Python/static gate. The following downloads
Terraform/PowerShell software only. It has been reviewed against the vendors' binary
installation instructions, but **has not been executed on halle**. If runtime system
libraries are absent, preserve the error for ITO; do not try sudo or silently change tools.

Use the official [Terraform binary verification guide](https://developer.hashicorp.com/terraform/tutorials/cli/verify-archive),
[1.16.6 release](https://releases.hashicorp.com/terraform/1.16.6/),
[PowerShell archive method](https://learn.microsoft.com/en-us/powershell/scripting/install/alternate-install-methods?view=powershell-7.5)
and [7.5.11 release hashes](https://github.com/PowerShell/PowerShell/releases/tag/v7.5.11).

```bash
set -euo pipefail
umask 077
mkdir -p "$HOME/cnb/downloads" "$HOME/cnb/tools/bin"
CNB_TOOLS=$(mktemp -d "$HOME/cnb/tools/offline-XXXXXXXX")
cd "$CNB_TOOLS"
curl -fL --proto '=https' --tlsv1.2 -o terraform.zip \
  https://releases.hashicorp.com/terraform/1.16.6/terraform_1.16.6_linux_amd64.zip
curl -fL --proto '=https' --tlsv1.2 -o terraform_SHA256SUMS \
  https://releases.hashicorp.com/terraform/1.16.6/terraform_1.16.6_SHA256SUMS
python3.12 - <<'PY'
import hashlib, zipfile
from pathlib import Path
name = 'terraform_1.16.6_linux_amd64.zip'
rows = [line.split() for line in Path('terraform_SHA256SUMS').read_text().splitlines()]
expected, = [row[0] for row in rows if row[-1].lstrip('*') == name]
assert hashlib.sha256(Path('terraform.zip').read_bytes()).hexdigest() == expected
with zipfile.ZipFile('terraform.zip') as archive:
    Path('terraform').write_bytes(archive.read('terraform'))
Path('terraform').chmod(0o700)
PY
curl -fL --proto '=https' --tlsv1.2 -o powershell.tar.gz \
  https://github.com/PowerShell/PowerShell/releases/download/v7.5.11/powershell-7.5.11-linux-x64.tar.gz
printf '%s  %s\n' \
  82a8b13d92b0f3ae48e56cf2f3f7961679371736ca90145ca71617c2913ba9d8 \
  powershell.tar.gz | sha256sum -c -
mkdir powershell
tar -xzf powershell.tar.gz -C powershell
chmod u+x powershell/pwsh
./terraform version
./powershell/pwsh --version
# Refuse to replace existing commands. Reuse reviewed installations if present.
ln -s "$CNB_TOOLS/terraform" "$HOME/cnb/tools/bin/terraform"
ln -s "$CNB_TOOLS/powershell/pwsh" "$HOME/cnb/tools/bin/pwsh"
export PATH="$HOME/cnb/tools/bin:$HOME/cnb/venv/bin:$PATH"
```

Terraform's checksum above is fetched over vendor HTTPS, not a separately verified GPG
signature. The PowerShell SHA-256 is pinned to the linked release. Retain the downloads,
checksums, actual version output and tool directory in setup evidence.

Before invoking the strict gate, prepare a **Linux amd64** filesystem provider mirror from
the six committed roots, with no cloud credentials; provider download is a setup step,
not a cloud operation. Run under the sanitized `env -i` pattern above:

```bash
cd "$HOME/cnb/repository"
CNB_MIRROR=$(mktemp -d "$HOME/cnb/tools/provider-mirror-XXXXXXXX")
for CNB_REL in aws/pair aws/safety azure/pair azure/expiry gcp/pair gcp/protection; do
  terraform -chdir="terraform/$CNB_REL" providers mirror -platform=linux_amd64 "$CNB_MIRROR"
done
CNB_TFRC="$CNB_MIRROR/terraform.rc"
printf 'disable_checkpoint = true\nprovider_installation {\n filesystem_mirror { path = "%s" }\n}\n' \
  "$CNB_MIRROR" > "$CNB_TFRC"
export TF_CLI_CONFIG_FILE="$CNB_TFRC" CHECKPOINT_DISABLE=1 TF_IN_AUTOMATION=1
for CNB_REL in aws/pair aws/safety azure/pair azure/expiry gcp/pair gcp/protection; do
  terraform -chdir="terraform/$CNB_REL" init -backend=false -lockfile=readonly -input=false
done
# `providers mirror` writes packed ZIPs. The gate also checks the unpacked
# version/platform layout; materialize it after init has verified the ZIPs.
export CNB_MIRROR
python - <<'PY'
import os, zipfile
from pathlib import Path
mirror = Path(os.environ['CNB_MIRROR']) / 'registry.terraform.io/hashicorp'
for provider, version in [('aws', '6.70.0'), ('azurerm', '4.81.0'), ('google', '7.46.1')]:
    root = mirror / provider
    archive_path = root / f'terraform-provider-{provider}_{version}_linux_amd64.zip'
    destination = root / version / 'linux_amd64'
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(destination)
    for binary in destination.glob('terraform-provider-*'):
        binary.chmod(0o700)
PY
# Requires the actual read-only Thesis checkout, not a fixture directory.
export THESIS_ROOT="$HOME/cnb/Thesis"
test -d "$THESIS_ROOT/.git"
CNB_GATE_PARENT=$(mktemp -d "$HOME/cnb/evidence/offline-gate-XXXXXXXX")
python scripts/validate_offline.py --evidence-dir "$CNB_GATE_PARENT/checks" \
  --terraform "$HOME/cnb/tools/bin/terraform" --pwsh "$HOME/cnb/tools/bin/pwsh" \
  --provider-mirror "$CNB_MIRROR"
```

Use the actual Thesis path if different. The gate rejects dirty source, missing real
context, wrong-platform providers, unexpected skips and failed checks. It launches no
infrastructure. Preserve a failed attempt and use a new evidence path for every rerun.
