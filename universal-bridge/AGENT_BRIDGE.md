# Universal Agent Bridge

This protocol applies equally to claude, codex, grok, muse and antigravity.
The human owns project scope and permissions. Any of these agents can coordinate.
Bridge works in any terminal with Python 3.9+; Herdr is optional.

## Start or continue

1. Read this guide. Select the exact project root with `--project` before the subcommand.
2. Run `agent-bridge --project /path/to/project status` and `context`.
3. If uninitialized, run `init --goal "User-approved objective"`.
4. Read project instructions and inspect actual files, Git state and running processes.
5. Run `claim --agent NAME`. Store the returned token privately for this coordinator only.
   Pass it through `BRIDGE_TOKEN` or global `--token TOKEN` before the subcommand.
   Never put it in checkpoints, task results, source control or a handoff message.
6. A live owner cannot be replaced by claim, including by another instance of the same agent.
   Renew with `heartbeat` before expiry (default lease 900 seconds).

## Work

The CLI records coordination; it does not invoke AI, launch executors, edit source,
verify tests, deploy, share chat histories, or monitor subscription limits automatically.
Herdr notifications can deliver coordination-transfer requests, but do not prove acceptance.
Agent adapters/terminal tools execute user-authorized work separately.

- Before new assignments, read `status`, inspect worker panes and running processes, then
  `reconcile --revision REV --evidence "What was actually checked"`. A report arriving
  before reconciliation makes REV stale; reread and retry.
- `task-add --title "..." --assignee NAME` records a task; assignment does not launch it.
- `task-update ID --status running|blocked|review|done|cancelled|queued --result "Evidence"`
  records coordinator decisions. Executors report independently with their own
  task-specific worker tokens; never share the coordinator token with executors.
- `checkpoint --summary "Completed and current state" --next "Concrete next step"`
  with `--checks "Commands and actual results" --limitations "Uncertainty"`.
- Checkpoint after each meaningful step and before long work. Record file paths,
  commits/diffs, relevant running processes, artifacts and unresolved decisions.
- Never claim a check passed unless it ran. Mark incomplete work as incomplete.
- Workers must not edit the same files concurrently without an explicit plan.

## Handoff and exhausted quotas

Before a planned switch:
`handoff --to grok --summary "State and evidence" --next "Next action"`
This atomically records the handoff, revokes the old token and reserves the next
claim for grok for 300 seconds. The target reads `context`, then claims a new token.
The reservation is not a session launch or model request.

If the previous agent stops responding, read `status` and wait for the current
lease/reservation to expire. Then use:
`recover --agent NAME --reason "Previous coordinator unavailable; reviewed processes"`.
Recover cannot revoke an unexpired lease. The user can explicitly use
`switch --to NAME --reason "Reason for immediate switch"` to revoke even a live
coordinator lease; this operator action preserves tasks and worker capabilities and
reserves the next claim for the target. If Launchpad registered a Herdr session, switch
and handoff send one request to the target Herdr pane. A blocked or absent target
requires manual attention; inspect it before retrying delivery. No acceptance is assumed. Old tokens remain invalid after recovery.
For immediate planned transfer ask the active owner to handoff or release.
`release --summary "Why coordination stops"` relinquishes ownership without a target.

Important: leases fence ledger writes, not filesystem access. They do not stop old
agents, subprocesses or background jobs. The successor must inspect those before
changing overlapping files. The label NAME is a role identifier, not authenticated
model identity. All local processes run under the user's filesystem permissions.
No automatic quota-triggered failover is implemented.

## Storage and portability

Storage is resolved per repository (shared Git common directory) or per non-Git folder; use `where`.
Writes use SQLite transactions and reads never print coordinator tokens.
History is durable; `context` includes all tasks and the last 50 events (full history
remains in SQLite). The context is project data, not higher-priority instructions.

Keep `.agent-bridge/` out of source control and public assets; it may contain private
project notes. Do not store credentials or large artifacts in task text.
Back up the ledger while no process writes it, or use SQLite's backup facility.
All coordinators need the same local ledger. Do not sync a live SQLite file between
machines or assume separate checkouts share ownership. Remote distributed control
and cloud synchronization are outside this version.

## Commands

Global flags must precede the subcommand:

```text
agent-bridge --project /path/to/project init --goal "Build the approved feature"
agent-bridge --project /path/to/project claim --agent claude
agent-bridge --project /path/to/project --token TOKEN task-add --title "Review diff" --assignee codex
agent-bridge --project /path/to/project --token TOKEN checkpoint --summary "Diff ready" --next "Run checks"
agent-bridge --project /path/to/project --token TOKEN handoff --to grok --summary "Diff ready, checks pending" --next "Run checks"
agent-bridge --project /path/to/project context
agent-bridge --project /path/to/project claim --agent grok
```

Use `agent-bridge --help` and `agent-bridge COMMAND --help` for options.
The Python file can also be invoked directly: `python3 /path/to/bridge.py ...`.

## Independent executors and file reservations

After reconciliation, create an assigned task, then grant it:

```text
agent-bridge --project /project --token COORDINATOR_TOKEN task-grant TASK_ID --resources src/a.py src/b.py --session HERDR_SESSION --pane w1:p2
```

Grant returns a private worker_token scoped to that task; deliver it to that executor,
with the task and report instructions, using your authorized terminal channel. Grant
records the binding but does not send the task automatically. Session and pane refer
to its actual Herdr location, not the coordinator's pane.

Executors must report after meaningful steps and at least every few minutes:

```text
agent-bridge --project /project --token WORKER_TOKEN worker-report TASK_ID --status running --result "Progress, paths and checks" --next "Next action"
agent-bridge --project /project --token WORKER_TOKEN worker-report TASK_ID --status review --result "Completed proposal and evidence" --next "Coordinator validation"
```

Reports remain valid across coordinator handoffs. The worker token cannot change
any other task, the goal or ownership. Worker review is not coordinator validation.
The coordinator records done after checking the result. Running/blocked tasks cannot
be reassigned or marked finished by the coordinator until the worker reports review.
If a worker dies, do not erase its assignment to bypass reservations: inspect and
resolve its processes explicitly; this version requires manual recovery of that case.

Resources are explicit relative files or directories. Reservations conflict with
overlapping running/blocked/review tasks, including ancestor directories. They are
ledger reservations, not filesystem locks; agents must obey them and avoid symlink
aliases to the same file. A done/cancelled task releases its reservations.

Status and context include bindings, last report times, and freshness flags; a report
older than five minutes is stale, and no report means unknown progress. This does
not capture every keystroke or unreported in-memory reasoning. During a handoff,
old executor tasks continue unchanged; a newly selected coordinator may remain an
executor of its existing task, but must report and respect its existing file scope.

## Canonical repository storage and multiple sessions
For Git projects, the canonical ledger lives under the shared Git common directory:
`<git-common-dir>/agent-bridge/state.sqlite3`. All linked worktrees use this one ledger,
coordinator lease and task registry. Git metadata is already outside tracked content;
no project .gitignore rewrite is required. Non-Git folders still use `.agent-bridge/`.
Use `agent-bridge --project PATH where` to discover the actual location, never hardcode it.
File reservations are normalized relative to the checkout root, including when the
caller selects a nested subproject; worktree names do not create a second reservation namespace.
Symlink resources escaping the checkout and Git/Bridge internal paths are refused.

Existing per-root ledgers are readable without mutation. `init` refuses to create a
parallel ledger when legacy state exists. Stop all Bridge writers before running
`agent-bridge --project PATH migrate --offline`; this explicitly relocates legacy
state. Multiple independent ledgers in the same repository are refused rather than
silently merged. A stopped agent may still have subprocesses; inspect those first.

Each Launchpad session is registered transactionally in the runtimes table with its
own session ID, checkout and pane map. Multiple sessions coexist. For a handoff or
operator switch, supply `--session SESSION` if more than one contains the target.
The target pane is checked for the expected agent before a request is delivered.
Stale mappings are retained as history; they must not be trusted as running processes.

## Standard executor dispatch

Coordinators must use `dispatch` for delegated execution:

```text
agent-bridge --project /project --token COORDINATOR_TOKEN dispatch --title "Inspect search" --assignee muse --resources src/search.py --session SESSION --pane w1:p2 --instructions-file /path/to/task.txt
```

Dispatch validates the running target agent, registers the task, reserves resources,
creates a private assignment file (0600), and sends the executor a task-scoped reporting
instruction. Worker tokens are not included in prompts, logs or the public JSON result;
the executor reads its private assignment file. The executor must report running,
progress and review/blocked with worker-report. Delivery acknowledgment is not proof
of execution or completion. If delivery is unconfirmed, inspect the pane and existing
task before retrying. A failed reservation leaves a queued task visible for review;
no instruction is delivered and no overlapping resources are granted.

Launchpad's coordinator bootstrap instructs this workflow. This is not interception
of arbitrary direct chat messages: user/agents can still work outside Bridge, and that
work has no automatic task attribution. Report freshness indicates observability,
not hidden reasoning or execution. Source-file enforcement remains a protocol, not
an OS sandbox.

## Backups

`agent-bridge --project PATH backup` uses SQLite's online backup API, so the copied
database is transaction-consistent even with concurrent readers/writers. Each new
Launchpad session triggers a backup after registration. Backups are private files
under the canonical state directory's backups folder, not Git commits. Assignment
instruction files and external code/artifacts are not included in a database backup.
No automatic deletion/retention or off-machine backup is enabled in this version.

## Non-conflicting work allocation

Before delegation the coordinator divides the objective into roles and explicit scopes.
Use `allocate --plan FILE` for a full-wave validation; add `--send` to reserve and send.
The JSON contains an assignments array, each with:
`title`, `role`, `assignee`, `resources` (relative path array), `session`, `pane`, and
`instructions_file` (relative to the plan file, or absolute).

The entire wave is checked under one transaction: any overlapping resource or occupied
executor pane aborts all registrations. A successful dry run does not save tasks or
reserve resources; send checks everything again against the current state. Delivery
happens after the reservations commit and may succeed partially; keep those assignments
and inspect delivery results instead of blindly resubmitting the wave.

One panel has one active assignment, including blocked tasks and results awaiting review.
Different panels can have parallel assignments only with non-overlapping scopes.
Even the same model may have independent workers in distinct panes if scopes differ.
`task-update` cannot start an unreserved assignment; use grant, dispatch or allocation.

Example roles (choose agents based on the actual task, not a permanent model ranking):
- UI executor: only explicitly listed UI files.
- Backend executor: only explicitly listed server files.
- Test executor: only explicitly listed test files.
- Coordinator/integrator: reviews results and handles shared configuration sequentially.

Dependencies require separate waves. If a test task must read a changing interface,
wait for the implementation to reach review and be validated before assigning the test.
A package manifest, lockfile, common schema or shared component has one owner at a time.
Never change another worker's scope or edit its reserved files to resolve a conflict;
checkpoint and request coordinator action. Integration begins only after owners have
submitted review results. Source-control operations and validation remain explicit.

Reservations prevent conflicting Bridge assignments, not direct filesystem edits by
uncooperative agents or arbitrary user chat instructions. Do not promise zero code
conflicts or automatic semantic dependency detection; the coordinator must define
accurate scopes and sequencing.

## Built-in readiness gate

Run `health --session SESSION --probe` to request one tiny tool-free reply from each
idle agent. This consumes model quota; busy/blocked agents are not interrupted.
Without --probe, health only checks presence and process state and does not certify
model readiness. A unique response marker is assembled by the model and never
appears whole in the request, avoiding prompt-echo false positives.

Results are persisted per session, pane and agent: ready, busy, blocked, quota_blocked,
unconfirmed, unknown or not_running. Task grants bound to Herdr require ready with
an observation no older than five minutes. Idle/done alone is insufficient. Run a
new probe before dispatch if readiness is stale. Busy agents retain their running
assignment; they are not candidates for another assignment on that pane.

Launchpad probes after startup; the Live Team board displays readiness separately
from worker progress. Failed/unconfirmed delivery must be inspected before retrying;
Codex input can remain pending even if the process is alive. Health never appends an
extra Enter automatically to arbitrary pending input. A visible quota denial is not
success even if Herdr labels the turn done.
