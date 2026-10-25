# README correction and duplicate-file cleanup — 25 October 2026

The README now distinguishes local inspection from live lifecycle execution and cleanup, points to the final F10 gate and separate paid handoff, and explains external cloud credential stores and the SSH private-key reference. Application, test, configuration and Terraform behavior are unchanged.

All 18 untracked files whose names ended in ` 2` were preserved before removal from the checkout. Fifteen differ from the current canonical files; none were assumed disposable or merged into current code. The local backup is `.codex/autonomous-evidence/duplicate-backup-20261025T202728Z/`: `files/` retains original relative paths and metadata, `archive.tar.gz` is a second copy, and `manifest.json` records original/canonical paths, sizes, timestamps and SHA-256 values. Every file in both copies was checked before removing any original and verified again afterwards; canonical files remained byte-identical. To restore, copy `files/<original_path>` back to the repository at `<original_path>` after checking that the destination does not exist. The backup is local and ignored by Git, not an off-device backup. Archive SHA-256: `f060c49e7d92b041bacd4598ed1cd1e361fa9a24e216424c91474b36bad817cc`.

The full offline gate passed at clean commit `09fd48bdc4b3754a7c4f4f23850485501ec18b69`, with the same source revision and clean checkout at completion:

```sh
PATH=/private/tmp/cnb-offline-venv-20261025/bin:/private/tmp/cnb-tools/powershell-7.5.11:$PATH \
THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis \
MPLBACKEND=Agg MPLCONFIGDIR=/private/tmp/cnb-matplotlib-config \
/private/tmp/cnb-offline-venv-20261025/bin/python scripts/validate_offline.py \
  --evidence-dir /private/tmp/cnb-readme-cleanup-offline-evidence-20261025 \
  --terraform /private/tmp/cnb-tools/terraform \
  --pwsh /private/tmp/cnb-tools/powershell-7.5.11/pwsh \
  --provider-mirror /private/tmp/cnb-f10-provider-mirror
```

Result: exit 0, **1,479 passed, two exact Darwin `/proc` cases not run, one existing tarfile deprecation warning**; all **20 checks** passed and all **112 required identities** were present. This includes full pytest, independent required-test collection, schema/diff checks, six Terraform roots' formatting/validation and four existing mocked Azure/GCP plan suites. Prepared provider roots were initialized separately using the existing filesystem-only mirror, `-backend=false` and `-lockfile=readonly`; no downloads or cloud calls were made. The normal reviewed execution permission permits local Terraform provider Unix sockets.

Raw evidence is in the command's output directory and the verified durable copy `.codex/autonomous-evidence/readme-cleanup-09fd48b/`. Summary SHA-256: `6f0d4698e76baa0f5ab7e880f90f55f64ca73d6657533c2bfb91f4db0b68db75`. All command-log and JUnit hashes were verified against that summary. In addition, 17 safe CLI/help/config checks passed (including all ten tracked YAML configurations), README local links resolved, Python compilation and `git diff --check` passed, and both duplicate backup copies verified.

This evidence record is a documentation-only follow-up to the tested commit; executable/test/config/Terraform content is unchanged. No provider login, credential-content inspection, provisioning, live cleanup, traffic or paid run was performed. The two Linux process-identity gaps and real native-tool/provider acceptance remain unproved as described in [F10 evidence](f10-offline-gate.md).
