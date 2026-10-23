# GCP STOP migration: evidence and decisions

Checked 2026-10-23 at clean implementation base `e03a07eaa5ed9ea372c695db19fd6fbcffdad024`; Thesis HEAD observed `85a209e13c225e06d371dc97e60705cc85ae2e14`. Read implementation AGENTS.md, constitution 2.0.0, autonomous WORKFLOW and routed Thesis design, DECISIONS, implementation notes, experiments, current F01R/F07 prompts, M04, roadmap, STATUS and TASKS. Thesis remained read-only. F07 planning at `7328e84dae821089aae34eccb674fb434338590d` was read in `/private/tmp/cnb-f07/specs/009-v2-gcp-provider/` as context.

## 1. Existing STOP governance

**Decision**: Require explicit STANDARD and STOP; reject new V2 DELETE inputs without coercion.

**Rationale**: Constitution IV and the 2026-10-18 safety decision require AWS/GCP stop, Azure deallocation and separate complete billable-resource cleanup. Current F07 requires independent compute-stop expiry registered before creation. `contracts/campaign.py:301` and `contracts/deployment.py:189` instead require DELETE. This is executable migration debt, not conflicting research authority.

**Official verification**: Google's [instance-template command reference](https://docs.cloud.google.com/sdk/gcloud/reference/compute/instance-templates/create) applies `instance-termination-action` to automatic time limits as well as preemption. The [VM runtime-limit guide](https://docs.cloud.google.com/compute/docs/instances/limit-vm-runtime) supports explicit STOP for time limits and requires an action for non-Spot VMs. STANDARD therefore does not make DELETE harmless. F07 owns mechanism/timing/inheritance; this feature aligns requested intent.

**Alternatives considered**: Keep DELETE because STANDARD is nonpreemptible (field also governs time limits); silently override in F07 (changes declared intent); accept both (admits forbidden fallback); add another expiry-mode/compatibility layer (unnecessary). All rejected.

## 2. Existing model/schema locations

**Decision**: Amend the two validators and add GCP-only schema conditionals there; preserve mapping shape, explicit keys and version 2. Add no preemptible flag.

**Rationale**: Direct deployment construction bypasses config validation. Current schemas admit DELETE and STOP although runtime rejects STOP; a validator-only change would leave independent consumers unprotected. Existing canonical export derives from model hooks via `python -m cloud_network_benchmark.contracts.schema`.

**Alternatives considered**: New provider-options class/general discriminator refactor, hand-edited JSON or a schema overhaul for every provider. None is necessary.

## 3. Serialized delivery and history

**Decision**: Plan against stable fields now; wait for coordinator-verified acquisition merge/release before shared edits/export. Preserve published branches without force-push. F07 implementation waits for verified STOP migration merge.

**Rationale**: Shared generated exports from an old base could discard acquisition evidence. No acquisition runtime shape is needed by this plan. Historical specs 001/002 and saved result/config snapshots remain unchanged; new validation rejects old DELETE inputs instead of inventing STOP intent.

**Alternatives considered**: Concurrent schema writers/precomputed old-base exports (stale output); deferring artifact planning (unnecessary dependency).

## Actual baseline probe

Ran the declared Python with `PYTHONPATH=src THESIS_ROOT=/Users/katya/Desktop/University/Masters/Thesis`, loaded `configs/tests/exp-902-gcp-single-provider.yaml`, derived a valid ProviderDeploymentInput and mutated only the action. Exit 0 confirmed the existing debt below; it was not feature acceptance.

| Boundary | DELETE runtime | STOP runtime | DELETE schema | STOP schema |
| --- | --- | --- | --- | --- |
| CampaignConfiguration | accepts | rejects | accepts | accepts |
| ProviderDeploymentInput | accepts | rejects | accepts | accepts |

Eight active GCP YAML examples contain DELETE: six `configs/experiments/` examples and `exp-902`/`exp-903` under `configs/tests/`. Implementation must rescan the merged base. No application/example changes were made during planning.

## Clarification outcome

Zero questions needed. All clarify categories are Clear within this scope: functional behavior, domain/data, interaction/errors, quality constraints, external interfaces, edge cases, tradeoffs, terminology and completion. Existing authority resolves STOP; malformed options reject, no default/coercion is added and cloud behavior remains outside offline proof. The pending item is coordinator serialization release, not research input.

No `.specify/extensions.yml` exists, so before/after hooks are absent. Every script receives explicit feature context. Installed setup/prerequisite scripts automatically persisted this worktree's isolated `.specify/feature.json`; that generated file was removed after script execution to restore the assigned no-active-state handoff. No shared state was written. Scripts report the feature directory basename as JSON `BRANCH`; actual Git branch remains `codex/f01r-gcp-stop`.
