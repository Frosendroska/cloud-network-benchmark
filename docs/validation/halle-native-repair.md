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

Native Linux acceptance is still pending. The macOS checks cannot qualify halle `/proc`,
real SSH, provider expiry/deletion, source-built tools, logout/reboot persistence or cloud
behavior. `Linger=no` and maintenance announced for 11 November remain operational gaps.
The original ~949 MiB setup/fixture size is not the volume of a real diagnostic day.

## Transfer and update on halle (no sudo)

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
