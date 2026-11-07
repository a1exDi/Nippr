# Universal Agent Bridge

A local coordination ledger for Claude Code, Codex, Grok, Muse Code and Antigravity.
Any agent can coordinate; tasks and worker reports survive coordinator handoffs.

## Components

- `universal-bridge/bridge.py`: SQLite ledger, coordinator leases, checkpoints, scoped worker tokens, file reservations, atomic allocation plans, readiness checks and backups.
- `herdr-launcher.py`: terminal project/team/coordinator picker and Herdr startup.
- `usage-monitor/`: Herdr plugin with a quota dashboard and a live team board.
- `tests/`: coordination, allocation, recovery, worktree, readiness and adapter tests.

## Install

Requires Python 3.9+ and Git on macOS or Linux. No third-party Python packages.
Herdr and authenticated agent CLIs are required for terminal delivery and launchpad.

```sh
python3 install.py
~/.local/bin/agent-bridge --help
~/.local/bin/herdr-start
```

The installer creates commands in `~/.local/bin` without overwriting unrelated commands.
It links the local Herdr plugin if Herdr is available. Keep this checkout in place.
Provider support and default project discovery are primarily tested on macOS.

## First project

```sh
agent-bridge --project /path/to/project init --goal "Your approved objective"
agent-bridge --project /path/to/project claim --agent codex
agent-bridge --project /path/to/project status
```

Claim returns a coordinator token. Pass it privately through `BRIDGE_TOKEN` or the
global `--token` option before the subcommand. Read `context`, inspect actual files
and processes, then `reconcile --revision REV --evidence "Checks performed"` before
assigning work. See [the agent protocol](universal-bridge/AGENT_BRIDGE.md).

## Allocation and handoff

`allocate --plan FILE` validates a complete wave; `--send` commits reservations and
delivers instructions. `dispatch` handles one task. One active assignment per pane;
overlapping file/directory reservations are rejected. Workers independently send
`worker-report` with running, blocked or review status. The coordinator validates
results before marking done.

`handoff` transfers ownership; `switch` is an explicit local operator override.
`recover` works after the lease expires. Executors retain their tasks and scoped
tokens across handoffs. A successor reconciles state before new assignments.

Herdr-bound grants require a successful readiness probe no older than five minutes:
`health --session SESSION --probe`. Probes can consume a small amount of model quota.

## Storage and backups

For Git projects: `<git-common-dir>/agent-bridge/`, shared by all linked worktrees.
For non-Git directories: `.agent-bridge/`. Use `where` to locate it. State contains
private coordination and worker tokens; never commit or publish it.
`backup` uses SQLite's consistent online backup API. Launchpad backs up at startup.
`migrate --offline` relocates legacy ledgers only after all writers are stopped.

## Validation

```sh
python3 tests/run.py
```

Unit/integration fixtures cover the local control plane; provider calls are mocked
where noted. An extended five-agent production workload and automatic crash recovery
have not been validated. This project is experimental.

## Boundaries

- File reservations constrain Bridge assignments, not OS filesystem access.
- No automatic full-chat transfer, quota-triggered failover or reboot recovery.
- Ledger data is only as current as agent reports; direct chat work is not intercepted.
- Herdr delivery acknowledgment does not prove execution or completion.
- Multiple hosts must not synchronize a live SQLite database as a distributed lock.
- Quota adapters depend on installed CLI and service response formats.
- Grok quota reading uses the existing local CLI credential in memory against its
  billing endpoint; Codex uses its app server; Muse/Antigravity use their CLIs;
  Claude quotas require an explicitly configured statusline collector.
- Installation does not modify Claude settings or import browser credentials.

No user task ledgers, account snapshots, credentials or diagnostic transcripts are
included in this repository. No license has been selected yet.
