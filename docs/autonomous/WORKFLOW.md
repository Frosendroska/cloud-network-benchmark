# Autonomous offline implementation

Agreed with the user on 20 October 2026. This is an internal development workflow, not part of the benchmark runtime. Preparation does not start implementation. Launch with [KICKOFF.md](KICKOFF.md).

## Control and scope

Use one Codex desktop coordinator chat on GPT-6.1 Sol Medium. Feature work is delegated to bounded subagents in separate worktrees. The same files can be used from Codex CLI, but no CLI daemon, API application or extra scheduler is required. Desktop is preferred for native same-chat heartbeat continuation and progress visibility.

Read implementation AGENTS.md, constitution and the current Thesis sources at `/Users/katya/Desktop/University/Masters/Thesis`: `advisor/docs/design_document.md`, latest `DECISIONS.md`, `experiments/FRAMEWORK_IMPLEMENTATION_NOTES.md`, `experiments/EXPERIMENTS.md`, `tracking/M04_implementation.md` and `tracking/IMPLEMENTATION_PROMPTS.md`. Do not copy research documents into implementation. Research choices remain authoritative; formal advisor approval must not be invented. The 19 October concurrent 28-experiment P0 and P1 studies supersede older three-pair placement examples.

The user authorizes the full dependency-ordered pipeline, agent artifact checks instead of manual reviews, automatic feature PR creation and merging after validation, routine conflict/integration fixes, and implementation-progress updates committed/pushed to Thesis main. This authorization does not launch clouds, spend money, override tool permissions, permit force-pushing shared branches, or change research decisions. Stop at F10's offline gate; paid acceptance remains separate.

Keep code short, explicit and limited to the designed instrument. Add no orchestration service to the framework. Prefer existing contracts and ordinary functions; justify any abstraction by actual duplication. Do not add speculative features, gratuitous tests or arbitrary line-count targets.

## Model handoffs

| Role | Model | Effort | Work |
| --- | --- | --- | --- |
| Coordinator | gpt-6.1-sol | medium | Dispatch, reconcile state, serialize merges, update brain |
| cnb_planner | gpt-6.1-sol | high | Constitution/specify/clarify/plan/tasks/analyze |
| cnb_implementer | gpt-6-luna | medium | Implement tasks, run checks, repair findings/conflicts |
| cnb_converger | gpt-6.1-sol | medium | Spec Kit convergence against current artifacts/code |

Use the project `.codex/agents/*.toml` definitions. If the client exposes native delegation rather than named custom agents, pass the exact model/effort explicitly and supply that role's instructions with minimal fresh context. Do not silently inherit another model; report model unavailability as a blocker. No additional independent-review stage is required.

## Dependencies and ownership

`state.template.json` records the dependency graph. R00 first reconciles governance using `$speckit-constitution`, then F01R migrates and merges the common contracts. F02R/F03/F04/F05/F06/F07/F08/F11 then depend only on merged F01R. F12 follows F08; F09 follows F02R and F03–F08 plus F11; F10 follows F09 and F12. Numeric IDs do not define execution order. Never call an unmerged dependency complete.

Use as many eligible feature stages as available agent slots and non-overlapping ownership allow. Never have two writers in one worktree. There is no fixed wave barrier: start a newly unblocked feature when a slot is free. Close or retire finished agents according to available tools; otherwise reuse an idle role agent with a fresh bounded handoff. Do not busy-poll for slots. Shared-contract changes are serialized; coordinate an explicitly justified contract amendment before allowing dependent work to consume it.

Coordinator owns state, GitHub merges and brain updates. Workers own only their assigned worktree and stage. Create `codex/<feature>-<short-name>` branches from the latest merged dependencies; reuse suitable existing worktrees on recovery. Give every handoff absolute repo/worktree/spec paths, base SHA, feature prompt, owned files, role/model, relevant source references and expected output. Pass `GIT_BRANCH_NAME` and `SPECIFY_FEATURE_DIRECTORY` explicitly; allocate unique new spec directories centrally. Preserve historical F01/F02 artifacts.

The active `.specify/memory/constitution.md` is tracked so R00's governance change reaches dependent worktrees. Preserve that checked-out version. Spec Kit scripts/skills remain ignored local tooling, so a fresh Git worktree will not contain them. Before dispatch, materialize only the installed scripts/templates/presets and skills needed by the workflow (or use a supported installation), verify required files are present, and keep per-worktree `.specify/feature.json` independent. Do not copy another worktree's feature.json, overwrite its constitution, copy secret/cloud state or share active-feature state. If R00 changes a required shared governance template, track that exact template through a narrow ignore exception so later worktrees receive it; do not commit tool installations wholesale. Invoke shell commands with that worktree as cwd and explicit feature context; never depend on a persistent cd or exported variable in another shell call.

## Pipeline for each feature

1. Planner runs specify → clarify → plan → tasks → analyze, reading each actual skill and respecting its artifact contracts. R00 uses constitution plus governance consistency checks instead of inventing an application feature. Use the feature goal from the current Thesis prompt document. Resolve ordinary engineering choices consistent with the design and record assumptions in feature research/spec artifacts. No routine user questions or manual artifact-review gates. Genuine source conflicts or changes to research scope/protocol/budget require user input.
2. Planner resolves actionable consistency findings before handing off. It declares file ownership, public interfaces and exact acceptance checks. Keep calibration-result values provisional; do not fabricate N, study dates or research results.
3. Implementer runs `$speckit-implement` and relevant automated checks. Preserve raw evidence, use credential-free fakes and static Terraform validation, and record actual commands/results. Never treat a fake as proof of cloud behaviour. Report completed tasks, changed files, tested SHA, commands/results, assumptions and next stage.
4. Converger runs `$speckit-converge`. It may append remediation tasks only as that skill allows; it must not fix application code or rewrite spec/plan to make failures disappear. Implementer completes appended tasks and tests again; converger rechecks. Zero actionable findings plus passing relevant checks is the feature gate. A convergence claim alone is insufficient.
5. Coordinator serializes integration with latest main. Merge main into an already-published feature branch rather than force-pushing it. Resolve routine conflicts through the implementer, rerun relevant checks after any code/base change, and repeat convergence when the change affects feature obligations. Record the exact validated head/base; never reuse stale green evidence after changes.
6. Commit/push, create a concise PR to main with problem/behaviour and validation, and attach it to the coordinator chat using the app's artifact tool when available. Check for an existing PR before creating one. Wait for all required GitHub checks and branch protections on the current head. If no required CI exists, require the documented local checks and clean convergence; say CI was absent rather than calling it green. Do not bypass failing checks or required human approval. Merge automatically when eligible, recheck that main actually includes the merge, and only then unblock dependents. If GitHub auto-merge is used, queued is not merged.

Keep working while failures decrease, requirements close or new evidence changes the diagnosis. After three consecutive repair cycles with the same blocker and no measurable progress, mark that feature blocked with evidence and continue unrelated eligible work. Missing authorization, unavailable models and externally required approval are blockers immediately; do not retry them in a loop or substitute models silently. Notify only on a PR merge, meaningful wave completion or required user action.

## Checkpoints and interruption recovery

On first launch copy `state.template.json` to `<coordinator checkout>/.codex/autonomous-state.json`, which is ignored local runtime state. Record that absolute path in the coordinator chat. Only the coordinator writes it, using atomic replacement. Workers return results instead of modifying shared state. Record worktree/spec/branch, agent/model, stage, source/base/head SHAs, exact validation command/exit/log reference, PR/merge identity, assumptions, blocker and next action after every stage and meaningful commit. Put private logs under `.codex/autonomous-evidence/`; commit feature artifacts/code normally. Do not place credentials or cloud state in either file. Local state survives chat/context pauses but is not an off-device backup; Git commits/PRs and saved specs are the recovery evidence.

On every resume reconcile state against actual Git status/commits, worktrees, PR head/merge/check state and feature artifacts before launching work. Do not trust stale agent IDs, checkmarks or cached checks. Reattach existing worktrees and resume the first unfinished stage; do not restart completed features. Never discard dirty work or overwrite concurrent user edits. Verify the previous coordinator is inactive before a replacement takes ownership; a heartbeat must not create a second coordinator/worker for an already active stage. Reconstruct missing local state from Git/PR/spec evidence, marking unknown stages honestly.

The coordinator checkpoint should be small and frequent: abrupt usage exhaustion may prevent a last save. At usage limits, preserve `waiting_usage` and any reset information actually returned; do not guess reset times or spin on retries. The same-thread heartbeat can try again later but cannot bypass limits. Keep computer/app availability and permissions in mind; automatic recovery is best effort, not guaranteed.

## Native heartbeat at launch

Use the app's automation tool to create or reuse one same-chat heartbeat every 30 minutes, named `Framework offline implementation resume`, and save its returned ID in runtime state. Use a heartbeat, not a standalone recurring project job. Set its prompt to the paragraph below; do not write raw automation directives or create a shell/cron workaround. Create it only when KICKOFF is invoked, not while preparing this workflow.

> Resume the authorized offline framework workflow from docs/autonomous/WORKFLOW.md and the coordinator's saved runtime-state path. Reconcile Git, PRs, worktrees and any active agents before acting. Continue the next eligible stage with the configured models and dependencies. Do not duplicate active work or bypass usage limits/permissions. Stay quiet while unchanged, waiting for usage or an external blocker. Notify only on PR merges, meaningful wave completion or required user action. Preserve the user's pause request. Disable this heartbeat after verified F10 completion or when all unfinished work requires external input; do not keep waking a permanently blocked workflow.

If heartbeat creation is unavailable, record that once and provide the resume sentence in KICKOFF.md; do not install another scheduler or claim automatic continuation is active. If the user pauses, halt new dispatch and disable the heartbeat. Re-enable only on explicit resume. A replacement chat must reuse/update the existing heartbeat target through the supported app tools, not create a duplicate.

## Brain updates and finish

After PR merges and meaningful wave completion, serialize factual implementation-progress updates to Thesis `STATUS.md`, `tracking/TASKS.md`, `tracking/M04_implementation.md`, `tracking/index.html` and relevant existing implementation trackers. Commit/push these to Thesis main with current Git state reconciled first. Preserve M02/M03 In review and all research choices; do not edit DECISIONS.md, the design, experiment protocol or thesis prose for routine delivery updates. The user has authorized these cross-repository progress writes; tool permissions still apply.

F10 complete means all prerequisites merged and the specified offline suite passes at the final integrated implementation revision from a clean checkout. State any skipped/unavailable checks as gaps, not success. Save exact revision, commands, results/log references, all PRs, assumptions and remaining paid-acceptance limitations in the existing validation docs and final brain update. Mark complete only with that evidence, disable the heartbeat and provide a concise final handoff. Do not run paid acceptance, calibration, diagnostic traffic or any cloud provisioning.

## Product references

- [Custom agents and inherited permissions](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Same-thread scheduled continuation](https://learn.chatgpt.com/docs/whats-new)

Configuration syntax checked against official documentation on 20 October 2026. These are instructions to Codex, not a deterministic execution engine; validate model access, GitHub rights and tool availability at launch.
