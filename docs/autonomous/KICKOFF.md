# Start the coordinator

Recommended: a new local Codex desktop chat in `/Users/katya/Desktop/University/Masters/cloud-network-benchmark`, after the workflow setup is on main. Select **GPT-6.1 Sol, Medium** for the coordinator. Custom agent settings govern workers; the kickoff does not change your global model settings. Use the existing permissions that permit this repository, managed worktrees, GitHub delivery and authorized Thesis progress updates; instructions do not expand sandbox permissions.

Paste this once:

```text
Start the autonomous offline implementation workflow in docs/autonomous/WORKFLOW.md.
I authorize subagents, the full dependency-ordered R00 through F10 pipeline,
automatic PR creation and merging when the documented gates pass, and factual
implementation-progress commits/pushes to the Thesis brain main branch.
Use GPT-6.1 Sol High for planning/analysis, GPT-6 Luna Medium for implementation
and repairs, and GPT-6.1 Sol Medium for Spec Kit convergence. Use the maximum
available safe concurrency across eligible feature worktrees. Do not add a
separate independent-review stage or pause for routine artifact approvals.
Decide engineering details consistently with the current design and record
assumptions. Preserve research decisions; resolve genuine source conflicts
with me. Keep the code small and limited to the designed framework.
Initialize or reconcile the durable local progress state and create/reuse the
same-chat resume heartbeat described in WORKFLOW.md. Start with R00 and respect
all feature dependencies. Continue until F10 is proved offline or genuine
external input is required. No cloud launches, paid experiments or permission
bypasses. Notify me on PR merges, meaningful wave completion or required input.
```

The coordinator first checks source/tool/model availability and current Git/PR/worktree state. It then starts R00; existing F01/F02 are migrated rather than reimplemented from scratch. Planning/convergence use Sol; code and repairs use Luna. Each worker receives a bounded stage, not the whole project conversation.

If automatic continuation is unavailable, or you want to resume manually in the same chat:

```text
Resume docs/autonomous/WORKFLOW.md. Reconcile the saved state with actual
worktrees, Git commits, PRs and active agents, then continue the first unfinished
eligible stage with the configured models. Preserve completed and dirty work.
```

For a replacement coordinator, include the previous coordinator chat ID and absolute `.codex/autonomous-state.json` path. Transfer ownership only after the previous coordinator is inactive, and reuse its heartbeat. The workflow does not assume a five-hour uninterrupted run or automatic execution while the desktop environment is unavailable.
