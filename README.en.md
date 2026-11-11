# Nippr 🧭

**Nippr is a teamwork and workflow add-on for Herdr: a launchpad, live team board, usage dashboard and evidence-based task acceptance for AI coding agents.**

[🇷🇺 Русский](README.md) | **🇬🇧 English**

## 🎯 Why use it?

Herdr provides the terminal workspace and agent sessions. Nippr adds a project-level workflow around those sessions: choose a team, distribute bounded assignments, see progress and quotas, transfer coordination, and require a verifiable result before calling a task complete.

Use it when several agents work on one repository, when a long task outlives one coordinator session, or when you need to distinguish “the agent says it is finished” from “the result was checked and accepted.” Supported role names: **Claude Code, Codex, Grok, Muse Code and Antigravity**. Any supported agent can coordinate.

| Component | What it does | When to open it |
|---|---|---|
| 🚀 Launchpad | Recent Herdr sessions; project, team and coordinator selection; startup | Starting or resuming work |
| 🧭 Bridge Board — Live Team | Team, assignments, worker reports, coordinator activity/checkpoints, evidence receipts | Seeing who is doing what |
| 📊 Usage Monitor | Available quota, token and context snapshots with sources/timestamps | Checking capacity and limits |
| 🔗 Universal Bridge | Local SQLite ledger, leases, scoped worker capabilities, reservations, handoff and acceptance | Coordinating and reviewing work |

## 🛠 Installation

Requires **Python 3.9+ and Git**, on macOS or Linux. No third-party Python packages. Install Herdr and authenticate the agent CLIs separately for launchpad and terminal delivery. The bundled plugin declares Herdr **0.9.5+**. Provider adapters and project discovery are primarily tested on macOS.

**1. Keep the source checkout in a stable location.** For example, clone into `~/Projects/Nippr`:

```sh
mkdir -p "$HOME/Projects"
git clone https://github.com/a1exDi/Nippr.git "$HOME/Projects/Nippr"
cd "$HOME/Projects/Nippr"
```

If already downloaded, enter the checkout with `cd "/path/to/Nippr"`. Keep it in place after installation: the command wrappers and plugin link point to files inside it. If you move it, reconfigure the commands and plugin link for the new path; the installer preserves existing different wrappers instead of replacing them automatically.

**2. Run the installer from the Nippr directory:**

```sh
python3 install.py
```

The installer creates four commands in `~/.local/bin` and links the plugin when Herdr is available. Existing different commands are preserved. If an existing wrapper points to another checkout, inspect it before assuming this version was installed.

**3. Add `~/.local/bin` to PATH.** For the current terminal window:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

To keep it for new terminal windows, add the line to your shell configuration **once**. For zsh, the default shell on macOS:

```sh
printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"
source "$HOME/.zshrc"
```

For interactive bash instead:

```sh
printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.bashrc"
source "$HOME/.bashrc"
```

Bash login shells may use `~/.bash_profile` instead of `~/.bashrc`; add the line there if needed. Open a new terminal and check the installation:

```sh
command -v agent-bridge
command -v herdr-start
command -v bridge-board
command -v herdr-usage
agent-bridge --help
herdr-start
```

`command -v` shows the resolved command path. If a command is missing, check PATH and the installer output. If it points to a different directory, inspect the matching wrapper in `~/.local/bin` and use the intended installed checkout.

## 🚀 Start and resume projects

`herdr-start` first lists up to 20 recent sessions: project, server state, saved-layout time and session ID. Search filters these fields; Enter attaches to the selected session without creating a new team or resending bootstrap instructions. “New session” opens project/team/coordinator selection. Esc from project selection returns to recent sessions.

A running server does not prove that a model is working. Restoring a stopped session restores its saved layout; process continuation is not guaranteed. List errors are shown instead of silently starting a session.

```sh
herdr-start --list-projects
```

Configure discovery roots in `~/.config/herdr-launcher/roots.txt`, one directory per line.

## 🖥 The two panels and everyday commands

| Command | Result |
|---|---|
| `herdr-start` | Project/team launchpad and recent sessions |
| `herdr-start --list-projects` | List discovered projects |
| `bridge-board --project /path/to/project` | Open live team board directly in this terminal |
| `bridge-board --project /path/to/project --json` | Read-only team/ledger snapshot as JSON |
| `herdr-usage` | Select a running Herdr session and open Usage Monitor |
| `herdr-usage SESSION` | Open Usage Monitor in a specific session |
| `agent-bridge --project /path/to/project status` | Structured task, ownership, report and evidence state |
| `agent-bridge --project /path/to/project context` | Markdown continuation context for a successor |
| `agent-bridge --help` | All Bridge subcommands |
| `agent-bridge COMMAND --help` | Exact flags for one subcommand |

Open the plugin tabs explicitly:

```sh
herdr --session SESSION plugin pane open --plugin local.usage-monitor --entrypoint bridge --placement tab --focus --env BRIDGE_PROJECT=/path/to/project
herdr --session SESSION plugin pane open --plugin local.usage-monitor --entrypoint dashboard --placement tab --focus
```

From this checkout, `python3 usage-monitor/monitor.py --json` reads usage snapshots; `python3 usage-monitor/monitor.py --refresh` explicitly refreshes provider data. Dashboard `R` refreshes; `Q` exits. Missing data is unknown, not zero usage. Claude quota data needs an explicitly configured statusline collector. Adapter availability depends on installed CLIs and service formats.

## 👥 Roles, ownership and task flow

The human authorizes scope and permissions. One coordinator holds a renewable lease (900 seconds by default). Executors receive task-scoped capabilities and file/directory reservations. They can report `running`, `blocked` or `review`; they cannot accept or finish tasks.

```text
queued → grant → running / blocked → review + artifacts
                                      ↓
                            coordinator-pinned check
                                      ↓
                              task-verify passes
                                      ↓
                         coordinator inspects + accepts
                                      ↓
                                     done
```

Reservations reject overlapping active assignments and duplicate occupied panes. They are ledger rules, not OS filesystem locks. Delivery acknowledgment, a model's confident report and coordinator activity `progress --state done` do not establish task completion.

## ✅ Mandatory evidence, verification and acceptance

A review requires at least one **existing, nonempty regular file within the task's reserved resources**. Repeat `--artifact` for the full deliverable; include output directories in reservations. No symlinks, parent traversal, Git/Bridge internals or environment files. File bytes are recorded with SHA-256 and size.

The coordinator pins a trusted validation command, Bridge runs it independently of the worker report, and only the current coordinator may accept its latest passing receipt. Changes to artifacts, logs, submission or check plan prevent reuse of stale acceptance. A coordinator successor must accept again after reconciliation.

Example after task creation/grant; `TASK` and `VERIFICATION_ID` are returned IDs. Coordinator commands below use a privately configured `BRIDGE_TOKEN`; the worker uses the assignment-loading `report` command.

```sh
agent-bridge --project /project report TASK --status review --artifact src/result.py --result "Proposal ready" --next "Coordinator review"
agent-bridge --project /project task-check TASK --command-json '["python3","-m","unittest","discover"]' --timeout 60
agent-bridge --project /project task-verify TASK
# Inspect the artifacts, diff and actual verification log before accepting.
agent-bridge --project /project task-accept TASK --verification VERIFICATION_ID --note "Reviewed result and check output"
agent-bridge --project /project task-update TASK --status done --result "Accepted result"
```

The runner records combined stdout/stderr, exit status, duration and log hash in private state. It executes an argv array without an implicit shell in the selected project; timeout is 1–300 seconds. A failed command, missing executable, timeout or changed evidence cannot be accepted. The ledger write lock is released while checks run.

**What this proves:** the selected command ran against the submitted version and the coordinator recorded acceptance. **What still needs judgment:** completeness of the artifact list, adequacy of the checks and correctness of the result. This is not a third-party model review or an OS security sandbox. Remote links and worker-written test reports alone cannot satisfy acceptance. Historical `done` rows remain historical, without invented verification receipts.

## 📚 Complete Bridge command reference

Global `--project PATH` and optional private `--token` precede the subcommand. `BRIDGE_TOKEN` is the alternative for coordinator commands. Do not print capabilities, commit them or put them into prompts/checkpoints. Prefer `report` for dispatched workers; it reads the assignment internally.

| Command | Purpose / main options |
|---|---|
| `init` | Initialize project with `--goal` |
| `status` | JSON snapshot including evidence receipts; no capability values |
| `context` | Markdown continuation summary |
| `claim` | Claim a free/reserved role: `--agent`, optional `--ttl` |
| `recover` | Claim after expiry: `--agent`, `--reason` |
| `reconcile` | Acknowledge inspected state: `--revision`, `--evidence` |
| `heartbeat` | Renew active lease: optional `--ttl` |
| `progress` | Coordinator activity: `--agent`, `--current`, `--done`, `--next`, `--checks`, `--state` |
| `checkpoint` | Durable checkpoint: `--summary`, `--next`, optional `--checks`, `--limitations` |
| `task-add` | Queue a task: `--title`, optional `--assignee` |
| `task-grant ID` | Grant queued task: `--resources`, optional `--session`, `--pane` |
| `dispatch` | Create, reserve and deliver one task: `--title`, `--assignee`, `--resources`, `--session`, `--pane`, `--instructions-file` |
| `allocate` | Validate a whole wave: `--plan FILE`; `--send` commits and delivers |
| `report ID` | Assignment-loading worker report: `--status`, `--result`, `--next`, repeated `--artifact` for review |
| `worker-report ID` | Low-level task-token report with the same report flags |
| `task-check ID` | Pin trusted check: `--command-json`, optional `--timeout` |
| `task-verify ID` | Run pinned check and return verification ID, status and receipt |
| `task-accept ID` | Coordinator acceptance: `--verification`, `--note` |
| `task-update ID` | Coordinator state decision: `--status`, `--result`, optional `--assignee`; done requires acceptance |
| `handoff` | Transfer and reserve ownership: `--to`, `--summary`, `--next`, optional `--ttl`, `--session` |
| `switch` | Explicit local operator override: `--to`, `--reason`; optional expected-owner/generation guards |
| `release` | Release coordinator role: `--summary` |
| `register-session` | Bind Herdr session: `--session`, `--panes` JSON, optional `--coordinator` |
| `health` | Read readiness; `--session`; `--probe` requests a small model response and can consume quota |
| `permissions` | Handle recognized scoped permission dialogs: `--session`, optional worker `--task` |
| `trust-workspace` | Explicit workspace-trust handling: `--session`, `--remember` authorizes this exact path |
| `sidebar-sync` | Synchronize registered pane labels |
| `where` | Show canonical ledger directory |
| `backup` | Create consistent SQLite backup |
| `migrate --offline` | Relocate legacy storage after all writers stop |

## 🔄 First coordinator and handoff

```sh
agent-bridge --project /project init --goal "User-approved objective"
agent-bridge --project /project claim --agent codex
agent-bridge --project /project status
agent-bridge --project /project context
# Inspect actual files/processes; privately configure the returned capability.
agent-bridge --project /project reconcile --revision REV --evidence "What was inspected"
```

Read current revision before reconciling; concurrent reports make an old revision stale. `handoff` revokes the old capability and reserves the target for 300 seconds by default. `recover` cannot override a live lease; `switch` is an explicit operator override. Worker assignments survive transfers. New coordination requires reconciliation; existing acceptance requires the successor's new acceptance.

## 💾 Storage, privacy and backups

Git projects share `<git-common-dir>/agent-bridge/` across linked worktrees. Non-Git projects use `.agent-bridge/`. `where` identifies the actual location. Private state contains capabilities, task history and verification logs; do not publish it. Dispatched assignment files under `.nippr-runtime/` are private too.

`backup` uses SQLite's online backup API; Launchpad also backs up at startup. Verification logs are separate files and must be retained with the ledger for receipts to remain usable; a SQLite backup alone does not copy them. Do not synchronize a live SQLite database between hosts as a distributed lock.

## 🧪 Development and checks

```sh
python3 tests/run.py
```

Tests cover control-plane behavior, allocation, handoff, readiness, worktrees, UI adapters and evidence acceptance. Provider calls are mocked where noted. Acceptance tests also run real local subprocess checks, failures, timeouts and concurrent artifact changes. An extended five-agent production workload and automatic crash recovery have not been validated.

Source layout: `universal-bridge/` (ledger/protocol/acceptance), `herdr-launcher.py` (launchpad), `usage-monitor/` (both panels), `tests/` (validation), `install.py` (local wrappers).

## ⚠️ Current boundaries

No automatic full-chat transfer, quota-triggered failover, reboot recovery or auto-publication. Unreported chat work is not intercepted. Leases cannot stop old processes. Identity labels are local roles, not authenticated model identities. Trusted validation commands run with the user's OS permissions. Installation does not alter Claude settings or import browser credentials. Grok's quota adapter uses the existing local CLI credential in memory for its billing endpoint; other adapters use their documented local interfaces.

This project is experimental. No user task ledgers, credentials or account snapshots are included. No license has been selected yet.

## 🔌 Codex connection errors

For `Automatic reconnect ...` in a Codex pane, first check `herdr --session SESSION status server`, then `codex doctor --summary`. A live Herdr server does not establish Codex app-server connectivity. Save an editable draft before quitting. Nippr suppresses reminders when the visible surface shows a reconnect error, an existing reminder, or cannot be confirmed. Automatic Codex connection repair is not implemented.
