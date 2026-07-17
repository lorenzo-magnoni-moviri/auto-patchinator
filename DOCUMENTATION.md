# auto-patchinator — Full Documentation

This is the in-depth reference for the tool: what it does, how every piece works, how to
configure and operate it, and what is known to be broken or incomplete. For a quick
command cheat-sheet see `README.md`; for guidance aimed at Claude Code / AI assistants
working in this repo see `CLAUDE.md`; for the live backlog and the exact findings from
recent operational testing see `TODO.md`.

## Table of contents

1. [Purpose and context](#1-purpose-and-context)
2. [Glossary](#2-glossary)
3. [Installation](#3-installation)
4. [Configuration](#4-configuration)
5. [CLI reference](#5-cli-reference)
6. [Architecture: the pipeline](#6-architecture-the-pipeline)
7. [Data model reference](#7-data-model-reference)
8. [Action sequences per role](#8-action-sequences-per-role)
9. [The three run modes](#9-the-three-run-modes)
10. [The SSH/PAS layer in depth](#10-the-sshpas-layer-in-depth)
11. [State, logging, and reports](#11-state-logging-and-reports)
12. [Testing and CI](#12-testing-and-ci)
13. [Known issues and operational findings](#13-known-issues-and-operational-findings)
14. [Troubleshooting](#14-troubleshooting)
15. [Repository map](#15-repository-map)
16. [Roadmap](#16-roadmap)

---

## 1. Purpose and context

Sky ITA's Broadband & Talk monitoring platform runs on Splunk. Every month, a
company-wide OS patching wave touches every server in the estate — including the
Splunk nodes owned by the "AOM Sky CSO" / "AOM Splunk Broadband" team (referred to
throughout the code and this doc as simply "our team" or "the Splunk BB team").

Each wave is described by a **Vulnerability Plan Excel file** produced by IT-SA (the
security/patching coordination team). It lists, in dependency order, every step of the
wave across *every* team involved — Splunk is just one of many rows. Our team's rows
say, in free text, things like "Stop application: ... Group 3" or "Start application: ...
Group 1" — these correspond to stopping/starting Splunk on a specific subset ("group")
of our nodes, timed around the OS patch reboot that IT-SA performs *outside* this tool.

Before this tool existed, an operator had to manually:
1. Read the Excel, find our rows, work out the order and which hosts are in which group.
2. SSH to each host (through a PAS/CyberArk gateway — direct SSH isn't allowed).
3. Run the right stop/start commands, in the right order, as the right user (`splunk`
   or `root`), remembering several host-specific quirks (different Splunk binary path
   on indexers, crontab handling on forwarders, systemd unit regeneration quirks, a
   StreamSets pipeline on one node, a stretched search-head cluster's captain election...).
4. Track progress by hand and improvise if something failed mid-wave.

`auto-patchinator` automates steps 1–3 (parsing, ordering, and — when live — executing)
and structures step 4 (progress tracking, resume-after-crash, reporting). It does **not**
replace the operator: every run is interactive, walks through the plan step by step, and
several inherently risky/cluster-wide actions are deliberately left as manual steps (see
[§13](#13-known-issues-and-operational-findings)).

**Who this is for:** whoever is running the monthly Splunk BB patch wave, and whoever
maintains this tool between waves. Written assuming you know what Splunk and SSH are,
but not assuming familiarity with PAS/CyberArk or this specific Sky infrastructure.

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Wave** | One monthly patching cycle, described by one Vulnerability Plan Excel file. |
| **Group** | A named subset of our team's hosts within a wave (e.g. "Group 1"), stopped/started together. Group membership is read from the Excel's host sheet, not fixed month to month. |
| **Step** (Excel) | One row of the Plan sheet — could belong to any team, not just ours. Has a numeric `Step` id and zero or more `Dependancy` step ids it waits on. |
| **PAS / CyberArk** | The company's Privileged Access Security gateway. All SSH to production/test Splunk nodes is proxied through it; you never SSH directly to a node. See [§10](#10-the-sshpas-layer-in-depth). |
| **Identity** | Which user you become on the target host after logging in via PAS: `splunk` or `root`. Encoded directly in the PAS login username. |
| **su** | After the PAS login, the tool still runs `sudo su - splunk` (or `root`, or an indexer-specific variant) to actually become that user on the target host. |
| **shcluster / captain** | Splunk's search-head clustering feature. The stretched search-head cluster spans two physical sites (Milano and Roma) with one node acting as "captain" at a time. Captain transfer/revert around a wave is handled as a manual step — see [§8](#8-action-sequences-per-role). |
| **KV store** | Splunk's built-in key-value store, cleaned locally on stretched search heads after a restart so it resyncs cleanly from the cluster. |
| **boot-start** | Splunk's systemd integration (`splunk enable/disable boot-start`). Regenerates a systemd unit file from a template, which is why the tool has to back up and restore a hand-edited copy around it — see [§8](#8-action-sequences-per-role). |
| **Manual guide mode** | One of the three run modes; executes nothing, just tells the operator what to do and waits. See [§9](#9-the-three-run-modes). |
| **Dry-run** | `--dry-run`: simulates every action (no SSH at all) so you can sanity-check a resolved plan safely. |
| **Role** | One of `deployer`, `indexer`, `forwarder`, `search_head_simple`, `search_head_stretched` — determines which action sequence a host gets. Configured per host in `inventory/hosts.yaml`. |

---

## 3. Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .          # installs the auto-patchinator console script
pip install -e ".[test]"   # also installs pytest, for running the test suite
```

Requires Python 3.10+. Runtime dependencies: `openpyxl` (Excel), `PyYAML` (inventory),
`paramiko` (SSH), `python-dotenv` (`.env` loading). All pinned in `pyproject.toml`.

Works from any Linux host or WSL that has network access to the PAS gateway — no agent
needs to be installed on the target Splunk nodes themselves.

### Running on native Windows (no WSL)

If you're on **WSL**, ignore this — it's a real Linux userspace, so everything above
already works as written.

For native Windows (PowerShell/cmd): the tool is pure Python with cross-platform
dependencies — there is no POSIX-only code anywhere in it (no `pty`/`fcntl`/`termios`,
no shelling out to Unix tools). Every SSH command it sends is a *string* addressed to
the remote Linux Splunk hosts (`/opt/splunk/bin/splunk stop`, `sudo su - splunk`, etc.)
— those paths live on the target servers, not on your machine, so they don't care which
OS is running the tool. **Functionally, everything behaves identically on native
Windows.** Only the setup commands differ:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

If PowerShell blocks the activation script with an execution-policy error, either run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first, or activate via
`.venv\Scripts\activate.bat` from `cmd.exe` instead.

**Credentials:** `copy .env.example .env`, then edit it the same way. Windows has no
`chmod` — NTFS permissions work differently — so there's no direct equivalent of
`chmod 600`; at minimum keep `.env` under your own user profile (not a shared/network
drive) and rely on the default per-user NTFS permissions there.

**Colors and the animated `...` progress indicator** both auto-disable when stdout
isn't a real terminal, same as on Linux. On a real terminal, **Windows Terminal** and
**PowerShell 7+** render everything correctly out of the box. The legacy `cmd.exe`
console on older Windows builds may not have virtual-terminal processing on by default,
which would show raw escape codes (e.g. `\x1b[32m`) instead of actual color — if that
happens, switch to Windows Terminal (free, Microsoft Store) or set the `NO_COLOR`
environment variable to fall back to plain text.

**WinSSH vs. this tool's own SSH — not the same thing:** manual guide mode never prints
an `ssh` command because the team connects to hosts by hand via **WinSSH** (a
GUI/PuTTY-style client), not a terminal `ssh` invocation. That's separate from *this
tool's* own connection: a live (non-`--dry-run`) run uses `paramiko` (a pure-Python SSH
library) internally to reach the PAS gateway — it never shells out to `ssh.exe`, WinSSH,
or PuTTY, so nothing extra needs installing; `pip install -e .` is all either OS needs.

Everything else — `run`, `--dry-run`, `check-connectivity`, all three run modes, the
state/log/report files, `pytest` — behaves identically on Windows. The one thing not
yet verified there specifically is the CI workflow, which only runs on `ubuntu-latest`
today (see `TODO.md` if a Windows CI job ever becomes worth adding).

---

## 4. Configuration

Three things need to be in place before a run: the **inventory** (who your hosts are),
**credentials** (how you authenticate), and the **wave Excel** (what to do this month).

### 4.1 `inventory/hosts.yaml`

The static, team-maintained source of truth for every host — production *and* test,
in one file, distinguished by an `environment:` field per host. This file does **not**
change month to month; only the wave Excel does. Copy `inventory/hosts.example.yaml` to
get started (the example has the same shape with placeholder/no `pas_gateway`).

`--inventory` defaults to `inventory/hosts.yaml` if you don't pass it explicitly.

**Top-level fields:**

```yaml
stretched_sh_sites: [milano, roma]   # the two sites of the stretched SH cluster
pas_port: 10100                      # appended as #10100 to every PAS login username
pas_gateway: pas.sky.local           # default gateway; --pas-gateway overrides per-run

environments:
  prod:
    pas_splunk_suffix: pas.prd.spk       # PAS identity suffix for the splunk user, prod
    pas_root_suffix:   pas.prd.spk.root  # ...for root, prod
  test:
    pas_splunk_suffix: pas.tst.spk
    pas_root_suffix:   pas.tst.spk.root
```

`environments.<env>.*` overrides the corresponding top-level key when
`--environment <env>` is active — this is how prod and test get different PAS suffixes
without duplicating the whole file. `pas_domain_suffix` and `pas_port` can also be
overridden per environment or per host this way.

**Per-host fields** (under `hosts:`, keyed by hostname):

| Field | Required | Meaning |
|---|---|---|
| `role` | yes | `deployer` \| `indexer` \| `forwarder` \| `search_head_simple` \| `search_head_stretched` |
| `site` | yes | `milano` \| `roma` — used for stretched-cluster captain transfer logic |
| `environment` | no (default `prod`) | `prod` \| `test` — `--environment` at the CLI filters to matching hosts |
| `pas_domain_suffix` | no | FQDN suffix appended to the hostname in the PAS login username, e.g. `.sky.local`. Roma nodes currently need this; Milano nodes don't. Set to `""` explicitly to suppress a *global* suffix for one host. |
| `pas_port` | no | Overrides the global `pas_port` for this host only. |
| `splunk_su_command` | no | Overrides the default `su` command for the `splunk` identity on this host (the indexers use `sudo su - splunk`, differing from the tool's normal indexer default of `sudo /bin/su - splunk -s /bin/bash`). |
| `manual_identities` | no | List of `splunk`/`root` — identities that **cannot** be used over SSH on this host at all (CyberArk-GUI-only). The controller forces any action needing that identity to manual on that host. Currently empty for every host in prod/test (see [§13](#13-known-issues-and-operational-findings) — two hosts used to be listed here but are now reachable via `pas_port: 10100`). |

### 4.2 Credentials (`.env`)

```bash
cp .env.example .env
chmod 600 .env
```

- `AP_USERNAME` / `AP_PASSWORD` — your own PAS login credentials. If both are set, a
  live run skips the interactive prompt entirely.
- `SPLUNK_API_TOKEN` / `SPLUNK_API_USER` / `SPLUNK_API_PASSWORD` — **reserved for future
  automations** (captain transfer, cluster status polling — see [§16](#16-roadmap)).
  Loadable via `credentials.load_splunk_api_credentials()`, but nothing in the tool
  consumes them yet.

`.env` is gitignored. **Never commit real credentials, and never hardcode the Splunk
admin password used by `bootstrap shcluster-captain -auth admin:<password>` anywhere in
this repo** — that command is intentionally left as a manual step precisely so the
password only ever gets typed at the live terminal.

### 4.3 The wave Excel file

Gitignored (`*.xlsx`), because it contains real hostnames, phone numbers, and email
addresses. The tool reads two sheets automatically, tolerating naming/layout variation
across waves (see [§6.1](#61-excel_parserpy) and [§6.4](#64-wave_mappingpy) for the exact
alias rules):

- **Plan** (or `Plan`-like) — the full multi-team step list.
- **List Host NO IT** (or similar — matched by substring `"NO IT"`) — group→hostname
  mapping across every team; the tool cross-references this with `hosts.yaml` and keeps
  only the hostnames that are ours.

`--excel` is optional: drop each month's file into a `plans/` directory at the project
root (create it if missing — it's gitignored, same as any `.xlsx`). If `--excel` is
omitted, the tool lists `.xlsx` files there (most recent first) and lets you pick a
number or type a path; if `plans/` is empty, it falls back to the current directory, and
if that's empty too, it just prompts for a path directly (`cli.py:_prompt_for_excel_path`).

---

## 5. CLI reference

Two subcommands: `run` and `check-connectivity`.

### `auto-patchinator run`

| Flag | Default | Notes |
|---|---|---|
| `--excel PATH` | prompts interactively | See [§4.3](#43-the-wave-excel-file). |
| `--inventory PATH` | `inventory/hosts.yaml` | Errors clearly if the default doesn't exist and nothing was passed. |
| `--plan-sheet NAME` | `Plan` | Rarely needs changing. |
| `--host-sheet NAME` | `List Host NO IT` | Override if a wave's host sheet is named unusually and the substring match fails. |
| `--team-filter FILTER [FILTER ...]` | `AOM Sky CSO`, `AOM Splunk Broadband` | `Gruppo_referente` values to match, case-insensitive. |
| `--environment {prod,test}` | `prod` | Filters `hosts.yaml` and selects the matching PAS suffixes. |
| `--pas-gateway HOST[:PORT]` | from `hosts.yaml`'s `pas_gateway` | Only needed to override the inventory default for one run. |
| `--dry-run` | off (i.e. **live** by default) | Simulates every action, no SSH, no credentials prompt. |
| `--full-auto-mode` | off | Skips the per-step mode question; every step runs in automatic mode. |
| `--state-dir DIR` | `state` | |
| `--reports-dir DIR` | `reports` | |
| `--logs-dir DIR` | `logs` | |

**Note the polarity:** a bare `auto-patchinator run` is a **live** run against
**production**. Add `--dry-run` to simulate, `--environment test` to target test nodes.

### `auto-patchinator check-connectivity`

Logs in and runs `whoami` on every host (or a chosen subset), without touching Splunk at
all. The right first thing to run before any live wave, and after any credential
rotation.

| Flag | Default | Notes |
|---|---|---|
| `--inventory PATH` | `inventory/hosts.yaml` | Same fallback as `run`. |
| `--environment {prod,test}` | `prod` | |
| `--pas-gateway HOST[:PORT]` | from `hosts.yaml` | |
| `--identity {splunk,root,all}` | `splunk` | |
| `--hosts HOSTNAME [HOSTNAME ...]` | all hosts in scope | Test a subset — useful to avoid hammering the whole fleet, see [§13](#13-known-issues-and-operational-findings). |
| `--logs-dir DIR` | `logs` | |

---

## 6. Architecture: the pipeline

```
Excel (.xlsx)
  │
  ├─► plan/excel_parser.py   ──► list[RawStep]            (whole multi-team Plan sheet)
  │
  ├─► plan/action_mapping.py ──► list[TeamStep], unmapped  (filtered to our team, verb+group parsed)
  │
  ├─► plan/dependency.py     ──► list[OrderedStep]         (Kahn's-algorithm order + external deps flagged)
  │
  └─► plan/wave_mapping.py   ──► {group: (hostnames,)}     (host sheet ∩ inventory)

config/inventory.py          ──► Inventory                 (hosts.yaml, filtered to --environment)
actions/sequences.py          ──► RoleSequences per host    (stop/start halves, per role)

plan/run_plan.py: build_run_plan(ordered_steps, wave_mapping, inventory)
  ──► list[RunStepPlan]   (pre_group_actions / per_host_actions / post_group_actions per Excel step,
                            captain-transfer/revert injected once, send_mail appended to every step)

runner/controller.py: RunController.run()
  ──► walks RunStepPlans in order, one of 3 modes per step (§9), executing via:

executor/ssh.py: SSHConnection / DryRunConnection
  ──► one PTY session per (host, identity) through the PAS gateway (§10)

state/store.py    ──► JSON persistence + resume detection + pruning (§11)
reports/report.py ──► markdown report at the end of a run (§11)
```

Everything is assembled and wired together in `cli.py:cmd_run`. Each stage below is a
separate module with a narrow, testable responsibility.

### 6.1 `excel_parser.py`

Reads the Plan sheet into a flat `list[RawStep]`. Column names vary across waves
(English vs Italian, suffix-numbered variants like a second "Dependancy" column) —
handled by an alias table (`_COLUMN_ALIASES`) rather than hardcoding one header name.
Notably `Dependancy` is deliberately spelled that way (matches the sheet's own typo),
and also accepts `Dipendenza`/`Dipendenza2`. Rows whose `Step` cell isn't a plain
integer (section labels, "Pre task 1", blank spacer rows) are silently skipped — this is
deliberate, not a bug: those rows carry no step data.

### 6.2 `action_mapping.py`

Filters `RawStep`s down to rows whose `Gruppo_referente` matches the team filter
(case-insensitive), then regex-parses the **verb** (stop/shutdown → `STOP`;
start/restart → `START`) and the **group number(s)** out of the free-text `Nome` column
— because the dedicated `Tipo` column is empty for every Splunk row ever seen. A row
matching the team filter but where the verb or group can't be confidently parsed is
returned separately as "unmapped" and printed as a warning by the CLI — **never silently
guessed**. This is one of the tool's core safety properties: if it isn't sure, it says so
loudly instead of taking a guess that could be wrong.

### 6.3 `dependency.py`

Orders `TeamStep`s using Kahn's algorithm, releasing exactly **one** ready step at a
time (not a whole batch), in ascending step-number order among ties. This detail matters:
the Excel typically models a *rolling* wave — stop group 1 → (OS patch, another team's
step) → start group 1 → stop group 2 → ... — where each start step's only *internal*
(our-team) dependency is a **later** stop step, while its *immediate* real-world
predecessor is an *external* step (someone else's OS-patching row). If dependency
resolution released all "ready" steps in one batch, steps whose only prerequisite is
external would all float to the top together, destroying the intended one-group-at-a-time
rollout and producing a plan that stops/starts every group back to back instead of
rolling through them one at a time waiting on the patch each time. Releasing one step at
a time (lowest Excel step number first) preserves the sequential intent encoded in the
Excel numbering.

Dependencies pointing at steps outside our own team's set become `external_dependencies`
on the resulting `OrderedStep` — these surface later as a mandatory manual "confirm the
other team's step finished" action before the group's own work begins.

### 6.4 `wave_mapping.py`

Reads the group→hostname mapping **directly from the Excel's own host-listing sheet**,
not from a separately-maintained YAML — a design decision made because that sheet
already lists every host across every team for the wave, and duplicating it by hand
every month would just be another place for stale data to hide. Sheet/column naming
varies across waves, handled the same alias-table way as the Plan sheet:

- Sheet found by exact name (`List Host NO IT`) or, failing that, a case-insensitive
  substring match on `"NO IT"`/`"No IT"`.
- Row 0 is a `SUBTOTAL` count formula cell, not data — the real header is row 1.
- Group column: `Group` or `Groups`.
- Hostname: prefers a bare `Hostname` column; falls back to stripping the domain off a
  `Computer` (FQDN) column if `Hostname` isn't present.
- Any host in the sheet that **isn't** also in `hosts.yaml` is silently dropped — the
  sheet lists every team's hosts, and we only want ours.

### 6.5 `config/inventory.py`

Loads and filters `hosts.yaml` to the active `--environment`, resolving the
global/per-environment/per-host override chain for PAS suffixes, gateway, port, and
domain suffix described in [§4.1](#41-inventoryhostsyaml). `Inventory.other_site(site)`
is what powers the stretched-cluster captain-transfer logic — given the site being
patched, it returns the *other* one.

### 6.6 `run_plan.py`

The assembly stage: for every `OrderedStep`, resolves its hosts (`wave_mapping.py`),
looks up each host's role-appropriate action sequence (`sequences.py`), and combines
them into a `RunStepPlan`. Two cross-cutting behaviors are applied afterward across the
*whole* plan, not per step:

- **Captain transfer/revert injection** (`_inject_captain_actions`): if any stretched
  search-head host appears anywhere in the wave, a manual "transfer captain to the
  other site" action is inserted before the **first** stretched-SH stop step in the
  whole wave, and a manual "revert captain to dynamic election" action is appended after
  the **last** stretched-SH start step — once per wave, not once per group, since the
  captain election is cluster-wide, not per-group.
- **`send_mail` injection** (`_append_send_mail`): every single `RunStepPlan` gets a
  manual "send completion e-mail for this step" action appended as its very last
  post-group action.

Group-level pre/post actions are deduplicated **per role** (or per specifically
overridden host) using a `(kind, role_or_hostname)` key — not by deep-equality of the
generated action sequence, which could differ in incidental ways (e.g. wording) while
still representing "the same role-level thing," so equality-based dedup would
under-deduplicate.

### 6.7 `actions/sequences.py`

The domain-knowledge core — see [§8](#8-action-sequences-per-role) for the full
per-role breakdown. Key structural facts:

- Every role exposes **independent STOP and START halves** (`RoleSequences.stop_*` /
  `start_*`), each with `pre_group` / `per_node` / `post_group` phases. The Excel models
  "Stop Group N" and "Start Group N" as two separate steps, often hours apart, because
  the OS patch/reboot happens *between* them, outside this tool's control — so the code
  mirrors that: there is no single bundled "patch this host" function anywhere.
- `HOST_OVERRIDES` (keyed by hostname) replaces a role's sequence **entirely** for
  special-cased hosts — currently just `prdmilbbspkfw02`, which additionally runs
  StreamSets pipeline stop/start around the normal forwarder sequence.
- `MANUAL_ONLY_IDENTITIES` is a hook for hosts where a given identity can never be used
  over SSH at all (CyberArk-GUI-only) — currently **empty**; two hosts used to be listed
  here but turned out to just need `pas_port: 10100` in the inventory instead of being
  genuinely unreachable.

### 6.8 `runner/controller.py`

The interactive loop — see [§9](#9-the-three-run-modes) for the full walkthrough of its
three operating modes. It owns:
- The main step loop (`RunController.run`) and its back/jump/quit navigation.
- Failure handling: a red `✖ FAILED` block with the last lines of output, and a
  `[r]etry [d]one-manually [s]kip [q]uit` menu — identical across all three
  executing modes.
- The forced-manual check (`is_forced_manual`): an action becomes manual if it's
  already `MANUAL` kind, or if the target host lists that action's identity in
  `manual_identities`.
- Delegating actual execution to whatever `Connection` the CLI wired up (`SSHConnection`
  for live runs, `DryRunConnection` for `--dry-run`) — the controller itself has no idea
  whether it's really touching a host or not.

### 6.9 `executor/ssh.py`

See [§10](#10-the-sshpas-layer-in-depth) for the full write-up — this is the layer with
the most infrastructure-specific, hard-won detail in the codebase.

### 6.10 `state/store.py`

JSON persistence of a `RunState` (see [§7](#7-data-model-reference)), one file per run
under `state/`. `find_incomplete_run` lets the CLI detect and offer to resume an
interrupted run on startup. `prune_other_states` deletes every state file except the
currently-active one every time a run starts (see [§11](#11-state-logging-and-reports)).

### 6.11 `logging_setup.py`

Attaches a DEBUG-level file handler per run to `logs/run-<id>.log`, capturing plan
resolution, every action attempt (with full command/output), every operator menu choice,
and the *raw* SSH send/receive traffic (with password sends redacted). `prune_old_logs`
keeps only the 3 most recently modified log files, called automatically every time
logging is set up.

### 6.12 `reports/report.py`

Renders a per-run markdown report (`reports/report-<id>.md`) from the same `RunState`
used for resume — a step-by-step record of what happened, with a DONE/FAILED/INCOMPLETE
status per step. Not pruned (unlike state/logs) — kept as a durable record.

### 6.13 `term.py`

ANSI color helpers (`green`/`red`/`yellow`/`bold`) and the animated "..." progress
indicator shown during automatic-mode actions. Both auto-disable when stdout isn't a
real terminal (piped output, `NO_COLOR` env var set) — so logs and captured output stay
clean plain text.

---

## 7. Data model reference

| Dataclass | Module | Purpose |
|---|---|---|
| `RawStep` | `excel_parser.py` | One Plan-sheet row, verbatim. |
| `TeamStep` | `action_mapping.py` | A `RawStep` confirmed to be ours, with parsed `verb` (`ActionVerb.STOP`/`START`) and `groups`. |
| `OrderedStep` | `dependency.py` | A `TeamStep` plus its resolved `external_dependencies`. |
| `Host` | `config/inventory.py` | One inventory entry: role, site, environment, PAS overrides, manual identities. |
| `Inventory` | `config/inventory.py` | The full filtered host set plus global PAS/gateway config; `.get(hostname)`, `.other_site(site)`. |
| `Action` | `actions/types.py` | One idempotent unit of work: `kind` (`PLAIN`/`INTERACTIVE`/`MANUAL`/`WAIT`), `identity`, `command` or `script`, `timeout_seconds` (default 60s; splunk stop/start use 900s). |
| `ExpectStep` | `actions/types.py` | One send/expect pair inside an `INTERACTIVE` action's `script`. |
| `RoleSequences` | `actions/sequences.py` | The six action tuples (`stop_pre_group`/`stop_per_node`/`stop_post_group`/`start_*`) for one role. |
| `RunStepPlan` | `plan/run_plan.py` | One fully-resolved Excel step: verb, hosts, external deps, and the three actions tuples/dict ready to execute. |
| `Credentials` | `executor/credentials.py` | PAS username/password, held in memory only. |
| `SplunkApiCredentials` | `executor/credentials.py` | Reserved token/user/password for future Splunk-API automations; unused today. |
| `CommandResult` | `executor/ssh.py` | `exit_code` + raw `output` from one executed command; `.success` is `exit_code == 0`. |
| `ActionState` | `state/models.py` | Persisted status (`pending`/`in_progress`/`success`/`failed`/`skipped`) + output/error for one action. |
| `StepState` | `state/models.py` | The `ActionState`s for one Excel step, keyed by scope (`__pre_group__`, hostname, or `__post_group__`). |
| `RunState` | `state/models.py` | The whole run: `run_id`, `excel_path`, `host_source`, step order, and every `StepState`. |

---

## 8. Action sequences per role

Every role's **stop half** and **start half** are independent (see
[§6.7](#67-actionssequencespy)). All timeouts are 60s except `stop_splunk`/`start_splunk`
(900s — a busy node can take a while to shut down, and a first start after patching can
be slow).

### Common building blocks

| Action | Identity | Command | Notes |
|---|---|---|---|
| `stop_splunk` | splunk | `sudo <bin> stop` | |
| `backup_systemd_unit` | root | `cp /etc/systemd/system/Splunkd.service /appl/home/splunk/Splunkd.service.copy` | Must happen **before** `disable_boot_start` — preserves the hand-edited unit file. |
| `disable_boot_start` | splunk | `sudo <bin> disable boot-start` | If splunk has no sudoers entry for this on a given node, rerun as root (no sudo) instead. |
| `enable_boot_start` | root | `<bin> enable boot-start -systemd-managed 1 -user splunk -group splunk` | **Regenerates** the unit file from a template — the edited copy must be restored right after. |
| `restore_systemd_unit` | root | `cat /appl/home/splunk/Splunkd.service.copy > /etc/systemd/system/Splunkd.service` | Deliberately `cat >`, not `rm`+`cp` — replacing the inode breaks systemd's cached unit state/file watch. |
| `daemon_reload` | root | `systemctl daemon-reload` | |
| `start_splunk` | splunk | `sudo <bin> start` | |
| `clean_kvstore` | splunk | `<bin> clean kvstore --local` | Stretched search heads only. |
| `backup_crontab` | splunk | `crontab -l > /appl/home/splunk/crontab.backup` | Forwarders only; must precede `disable_crontab`. |
| `disable_crontab` | splunk | interactive: `crontab -r` → expect `"really delete"` → send `yes` | The splunk user's `crontab` is aliased to `crontab -i`, which asks for confirmation before deleting. |
| `enable_crontab` | splunk | `crontab /appl/home/splunk/crontab.backup` | |

`<bin>` is `/opt/splunk/bin/splunk` for every role except indexers, which use
`/splunkdata/splunk/bin/splunk`.

### Per role

| Role | Stop sequence | Start sequence |
|---|---|---|
| `deployer` | stop → backup unit → disable boot-start | enable boot-start → restore unit → daemon-reload → start |
| `indexer` | same as deployer, indexer `<bin>` | same as deployer, indexer `<bin>` (S&R factor wait-and-check deferred to v2, see [§16](#16-roadmap)) |
| `forwarder` | **backup crontab** → disable crontab → stop → backup unit → disable boot-start | enable boot-start → restore unit → daemon-reload → start → **restore crontab** |
| `search_head_simple` | same as deployer | same as deployer (no KV-store clean, no captain handling — this is the separate 3-node cluster, not the stretched one) |
| `search_head_stretched` | same as deployer | same as deployer, **+ clean_kvstore** at the end |

`prdmilbbspkfw02` (host override, replaces the forwarder sequence entirely):
1. `backup_crontab`
2. `disable_crontab`
3. `wait(180s)` — "allow in-flight cron jobs to finish before touching StreamSets"
4. `manual_todo: disable_streamsets_pipelines` — placeholder, exact command not yet known
5. stop → backup unit → disable boot-start

Start half mirrors it, ending with `manual_todo: enable_streamsets_pipelines` then
`enable_crontab`.

### Captain transfer (stretched search-head cluster)

Injected **once per wave**, not per group (see [§6.6](#66-run_planpy)). The instructions
name a **concrete** host, not a `<placeholder>`: `Inventory.captain_candidate(site)`
picks the lowest-numbered stretched-SH hostname on the site *not* being patched (e.g.
`prdrmlbbspksh01`), and the same host is referenced consistently in both the transfer
and the revert message so the operator doesn't have to track it themselves:

- **Before the first stretched-SH stop in the wave** — manual step: on the chosen new
  captain (the concrete host above), run
  `<bin> edit shcluster-config -mode captain -captain_uri https://<that-host>.sky.local:8089 -election false`;
  then on every *other* search head in the whole cluster (both sites), run the member
  variant of the same command.
- **After the last stretched-SH start in the wave** — manual step: re-enable dynamic
  election on every member except the current captain (the same concrete host), then
  the captain itself; then from that host,
  `bootstrap shcluster-captain -servers_list "https://host1.sky.local:8089,..." -auth admin:<password>`
  — the server list is the actual full set of stretched-SH hostnames across both sites
  (`Inventory.stretched_sh_hostnames()`), not a placeholder either.
  **The admin password is never stored in this repo — type it only at the live
  terminal when performing this step.**

These are cluster-wide, touch every search head (not just the ones in scope for the
current Excel step), require choosing which host becomes captain, and need a Splunk
admin credential — which is exactly why they're deliberately manual rather than
automated. The tool shows the exact command templates; it does not run them.

---

## 9. The three run modes

At the start of every step, `term.clear_screen()` clears the terminal's **visible**
screen (`\033[H\033[2J` — deliberately not `\033[3J`, which would also wipe the
scrollback buffer) so the new step starts on a clean screen instead of scrolling past
the previous step's output; the operator can still scroll up normally to review earlier
steps. A no-op when stdout isn't a real terminal (piped output, tests).

The mode is then asked once at the start of every Excel step (unless `--full-auto-mode`
is set, which locks in automatic for the whole run from the start):

```
    9 pending action(s). How do you want to run this step?
      [a] automatic     - run everything, one line per action; pauses only for
                          manual confirmations and failures
      [A] automatic for ALL remaining steps (stop asking)
      [t] task-by-task  - confirm every action before it runs
      [T] task-by-task for ALL remaining steps (stop asking)
      [m] manual guide  - execute NOTHING: shows each task one at a time (command,
                          host, user, and why) and waits for you to do it by hand
      [M] manual guide for ALL remaining steps (stop asking)
      [q] quit
```

Each mode's capital variant (`A`/`T`/`M`) works the same way: it locks that mode into
`RunController._locked_mode`, so the question is skipped for every subsequent step in
the run — useful once you're confident the rest of the wave can run the same way. The
lowercase variant only applies to the current step; you'll be asked again next time.

### Automatic (`a` / `A`)

Runs every action back to back, one line each, with an animated `...` while an SSH
command is in flight:

```
[tstmilbbspksh01] stopping splunk ...                DONE (12s)
[tstmilbbspksh01] backing up systemd unit ...        DONE
[tstmilbbspksh01] disabling boot-start ...            DONE
```

Pauses only for:
- **Manual confirmations** (external-dependency checks, `send_mail`, captain
  transfer/revert, any action on a `manual_identities` host, StreamSets placeholders) —
  shown with the exact command/instructions and a `press ENTER when done` prompt.
- **Failures** — a red block plus retry menu (see below).

### Task-by-task (`t` / `T`)

Confirms every single action before running it:
`[r]un [d] mark done manually [s]kip [b]ack [j]ump <step> [q]uit`.
Useful when you want to inspect each command before it fires, or step through slowly
during a first live run.

### Manual guide (`m` / `M`)

**Executes nothing.** Presents the pending tasks for a single host as before, but
**hosts sharing an identical remaining task list and `su` hint are batched**: shown
once, with a note to repeat the sequence on every host in the batch, and confirmed
together in one go — no need to page through the same 3-command sequence 5 times for
5 search heads:

```
On 5 hosts (search_head_stretched, site milano): tstmilbbspksh01, tstmilbbspksh02,
tstmilbbspksh03, tstmilbbspksh04, tstmilbbspksh05
  become splunk with: sudo su - splunk
  become root with: sudo su - root
  Repeat the 3 task(s) below IDENTICALLY on EACH of these 5 hosts.

  task 1/3: stop_splunk  [user: splunk]
     run : sudo /opt/splunk/bin/splunk stop
     why : Stop the Splunk process cleanly before the OS is patched.

  task 2/3: backup_systemd_unit  [user: root]
     run : cp /etc/systemd/system/Splunkd.service /appl/home/splunk/Splunkd.service.copy
     why : ...

  task 3/3: disable_boot_start  [user: splunk]
     run : sudo /opt/splunk/bin/splunk disable boot-start
     why : ...

    Once you've done the above IDENTICALLY on ALL 5 hosts, press ENTER to confirm
      (or: [i] confirm host-by-host instead  [s] skip all  [l] list all tasks  [q] quit)
```

Every literal command the operator needs to type or paste — the `become <identity>
with: ...` `su` line, each task's `run :` line, and any command embedded in a manual
step's instructions (e.g. the `shcluster-config`/`bootstrap` commands in the captain
transfer/revert text) — is printed in **bold cyan** (`term.py`'s `cyan` helper,
auto-disabled on a non-tty same as the other colors) so it stands out from the
surrounding prose/rationale text at a glance. For `MANUAL`-kind actions, `_guide_what`
treats any note line indented 3+ spaces as a literal command to highlight and
everything else as plain instructional text.

The grouping (`RunController._host_profile` / `_build_host_groups`) compares each host's
exact remaining action sequence (name/kind/command/script — identical for same-role
hosts, since neither the Splunk binary path nor any command text depends on the
hostname) *and* its resolved `su` hint (which **can** differ per host via
`splunk_su_command` overrides, or a `manual_identities` CyberArk-GUI-only flag) — only
hosts matching on both are batched. `i` falls back to confirming the same hosts one at a
time (e.g. if one of them had a problem and needs individual handling); a single host is
shown exactly as before, just with singular wording. `l` lists every task for every host
at once (with `[done]`/`[skipped]` markers), unaffected by batching; `s` skips, `q` quits
(progress saved). No `ssh` command is ever shown, since the team connects via WinSSH, not
the raw `ssh` CLI — only the `su` step is shown.

### Failures (all executing modes)

```
✖ FAILED: tstmilbbspkfw01 / stop_splunk - exit code 1
│  Stopping splunkd...
│  some error: permission denied
Retry? [r] retry  [d] mark as done manually  [s] skip  [q] quit
```

Retrying re-attempts the exact same action; the underlying `SSHConnection` retries the
initial connect up to twice with a delay (handles PAS rate-limiting) before this menu is
even shown.

---

## 10. The SSH/PAS layer in depth

Nothing SSHes to a Splunk node directly — everything goes through the shared PAS
gateway, and the **target identity is encoded in the SSH username itself**:

```
<username>@<pas_suffix>@<hostname>[<pas_domain_suffix>][#<pas_port>]
```

Examples:
- `lmm992@pas.prd.spk@prdmilbbspksh01#10100` (splunk identity, prod)
- `lmm992@pas.prd.spk.root@prdrmlbbspkdp01.sky.local#10100` (root identity, prod, Roma
  host needing the `.sky.local` suffix)
- `lmm992@pas.tst.spk@tstmilbbspksh01#10100` (splunk identity, test)

After PAS lets you in, the tool still runs `sudo su - splunk` / `sudo su - root` (or the
indexer-specific `sudo /bin/su - splunk -s /bin/bash`, or a per-host
`splunk_su_command` override) to actually become that user on the box.

### Why one persistent PTY session, not one-shot commands

The `su` step — and some commands, like the splunk user's aliased `crontab -i` — can
prompt interactively. So every command for a given (host, identity) runs inside **one**
persistent PTY shell channel (`paramiko.SSHClient.invoke_shell()`), not a fresh
`exec_command` per command. A unique marker protocol detects when a command has
finished and recovers its exit code:

- `PROMPT_MARKER = "<<AP_READY>>"` — the tool sets `PS1='<<AP_READY>>'` right after
  login, so it can reliably detect "the shell is idle and ready for the next command"
  by scanning for this exact string, regardless of whatever the real prompt would have
  looked like.
- `EXIT_MARKER = "AP_EXIT_CODE"` — every command is sent as
  `<command>; echo AP_EXIT_CODE:$?`, and the exit code is parsed back out of the output
  with a regex.

### Connection lifecycle (`SSHConnection.connect()`)

1. Build the PAS login username as above.
2. Attempt the paramiko-level connection, retrying once (`_CONNECT_RETRIES = 2`, 3s
   delay) — handles PAS-side rate limiting. `_paramiko_connect` tries password auth
   first, falls back to keyboard-interactive if the gateway demands it (answering every
   challenge with the same password).
3. Once authenticated, `invoke_shell()` and read until either a normal shell prompt
   (`[#$>]\s*$`) **or** a forced password-change banner appears (`PASSWORD_EXPIRED_PATTERN`
   — matches `"password has expired"` / `"changing password for"`). If the latter, raise
   `PasswordExpiredError` immediately with a clear message, instead of the 30s timeout
   that used to be the only outcome (fixed 2026-07-03, see [§13](#13-known-issues-and-operational-findings)).
4. Send the `su` command; if it prompts for a password (`PASSWORD_PROMPT_PATTERN`,
   matches anything ending `...assword...:`), send the same PAS password (logged as
   `<redacted>`, never the real text).
5. Set the `PS1` marker.
6. **Only now** is `self._client`/`self._session` actually assigned. If *anything* in
   steps 3–5 raises, the already-open paramiko client is explicitly closed before
   re-raising — this was a real leak until 2026-07-03 (see [§13](#13-known-issues-and-operational-findings)):
   previously, a failure at any of these steps left the underlying socket open until
   Python's garbage collector eventually got to it.

`DryRunConnection` implements the exact same interface (`connect`/`close`/`run_plain`/
`run_interactive`) but never touches paramiko at all — it just records what *would* have
run. This is what makes `--dry-run` safe to run anywhere, anytime, with no network
dependency (paramiko is imported lazily inside `connect()` specifically so dry-run mode
never even needs it installed).

---

## 11. State, logging, and reports

### State (`state/run-<id>.json`)

Every action's status is persisted after every single transition — so a crash, Ctrl-C,
or network drop loses at most the in-flight action. On startup, `cli.py` looks for an
incomplete run (`store.find_incomplete_run`, newest-modified-first) and offers to resume
it. **Only one run's state file is kept on disk at a time**: once a run starts (whether
fresh or resumed), `store.prune_other_states` deletes every other `run-*.json` in
`state/`. Skipped actions count as "complete" for resume/completion purposes, same as
successful ones.

### Logs (`logs/run-<id>.log` or `logs/run-check-connectivity-<timestamp>.log`)

DEBUG-level: plan resolution, every action attempt (command, exit code, output),
every operator menu choice, and the *raw* SSH conversation (every line sent and every
buffer received) — invaluable for diagnosing PAS/CyberArk login-flow issues, since that
part of the stack is the least predictable (see [§13](#13-known-issues-and-operational-findings)).
Password sends are marked sensitive and logged as `<redacted>`. Only the **3** most
recently modified log files survive; older ones are deleted automatically every time a
run sets up logging (`logging_setup.prune_old_logs`).

### Reports (`reports/report-<id>.md`)

A human-readable markdown summary written at the end of a run (or right after quitting),
generated from the same `RunState` used for resume: one section per Excel step, its
overall status (`DONE`/`FAILED`/`INCOMPLETE`), and every action's final status. **Not**
pruned — kept as a durable record of what happened each wave.

---

## 12. Testing and CI

```bash
pip install -e ".[test]"
python -m pytest                                    # whole suite
python -m pytest tests/test_run_plan.py -k captain   # one file / one test
```

The suite (`tests/`) covers every pure-logic module: Excel parsing (column aliases,
malformed rows), team-step mapping (verb/group parsing, unmapped-row flagging),
dependency ordering (**including** the rolling-wave external-deps ordering case from
[§6.3](#63-dependencypy)), host-sheet mapping, role sequences (timeouts, crontab
backup-before-delete invariant, systemd unit ordering), inventory environment
filtering, PAS login-string building, state persistence/resume/pruning, and the
controller's manual-guide mode (using a connection factory that fails the test if
anything ever tries to actually SSH). Deliberately does **not** attempt to test the real
SSH/PAS layer end-to-end — that's what `--dry-run` and `check-connectivity` are for.

**CI** (`.github/workflows/ci.yml`) runs the whole suite plus a dry-run
plan-resolution smoke test on every push and pull request: `scripts/gen_ci_fixture.py`
builds a small, fully-synthetic plan Excel referencing hosts already in the tracked
`inventory/hosts.example.yaml` (real wave Excels are gitignored, so CI can't use one
directly), then `auto-patchinator run --dry-run` is invoked against it and immediately
aborted at the confirmation prompt — enough to exercise the whole pipeline
(parsing → mapping → ordering → host cross-reference → plan building → summary
printing) and catch import errors or plan-resolution regressions that a unit test
importing internal functions directly might miss.

`main` is branch-protected: a pull request and a passing `test` status check are both
required before a merge is allowed.

---

## 13. Known issues and operational findings

These are current, verified facts about the state of the system as of **2026-07-03** —
check `TODO.md` for anything more recent, since this list will drift.

- **Root identity connectivity, prod: works, with one exception.** Verified both by
  `check-connectivity --identity root` against a 9-host sample (one per role × site,
  8/9 returned a confirmed `whoami='root'`) and independently by the operator manually
  testing the full prod fleet. **`prdrmlbbspkdp01` is the sole exception** — rejected
  outright at the PAS gateway (`Authentication failed`, before any shell is reached).
  Looks like an isolated CyberArk entitlement gap for this one host/identity pair, not
  a systemic prod issue. Needs following up with whoever manages the CyberArk safes.

- **Root identity connectivity, test: broken across the board.** All 7 test hosts
  failed `check-connectivity --identity root --environment test` (0 OK, 7 FAIL), in two
  distinct ways:
  - 5 hosts (`dp01`, `fw01`, `spksh01-03`): outright `Authentication failed` at the PAS
    gateway — same signature as prod's one exception.
  - The 2 indexers (`ix01`, `ix02`): gateway login *succeeds*, but the root account's
    password has **expired** on the target host, forcing an interactive password-change
    prompt. `ix01`'s banner also showed "2 failed login attempts since the last
    successful login" — i.e. attempts are being counted, so repeated testing risks an
    account lockout. **Do not hammer this further without a CyberArk-side fix first.**

  This is entirely a CyberArk/credential-side issue, not a code bug — except that the
  tool used to handle the expired-password case very badly (an opaque 30s timeout
  instead of a clear error), which **is** fixed now: `ssh.py` detects the forced
  password-change banner immediately and raises `PasswordExpiredError` with a clear
  message. A related bug — the underlying socket was never closed when the shell/su
  setup failed after paramiko-level auth had already succeeded — is also fixed (both in
  `ssh.py`'s `connect()` and in `cli.py`'s `check-connectivity` loop).

- **`splunk` identity connectivity** has not had a dedicated `check-connectivity` run
  against prod specifically (it *has* been exercised live in test, successfully, during
  earlier dry-run/live testing of the mockup wave). Since it shares the exact same
  `connect()`/`su` code path just verified for root, this is low-risk, but hasn't been
  independently confirmed.

- **StreamSets pipeline stop/start on `prdmilbbspkfw02`** is still a `manual_todo`
  placeholder — the exact CLI/API commands aren't known yet.

- **`send_mail`** is a manual placeholder appended to every single Excel step. Not yet
  automated (see [§16](#16-roadmap)).

- **Search-head captain transfer/revert** are *intentionally* manual, not a gap to be
  closed casually — see [§8](#8-action-sequences-per-role) for why.

- **Indexer post-restart Search/Replication Factor check** is deferred to v2 — no
  automated wait-and-verify that the cluster is healthy again after an indexer restarts.

- **A full, unattended live wave against the test environment has not yet completed
  end to end.** Earlier attempts surfaced (and led to fixing) a missing `pas_gateway`
  inventory entry and the crontab-backup-missing bug; a complete run without operator
  intervention is still outstanding.

---

## 14. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `WARNING: no PAS gateway configured` | Neither `--pas-gateway` nor `hosts.yaml`'s `pas_gateway` is set | Fill in `pas_gateway` in `hosts.yaml` (it shouldn't change month to month) |
| `Authentication failed` at connect | Wrong/expired PAS password for that identity, or the identity isn't entitled on that specific host | Check `.env`'s `AP_PASSWORD`; if only one specific host fails, it's likely a CyberArk entitlement gap for that host (see [§13](#13-known-issues-and-operational-findings)) — not a code bug |
| `PasswordExpiredError` | The PAS/CyberArk password for that identity has expired on the target host, triggering a forced password-change prompt the tool won't perform for you | Rotate the credential via CyberArk, then retry — do **not** repeatedly retry, each failure typically increments a lockout counter |
| `TimeoutReadingShell` | Something appeared in the shell buffer the tool's regexes don't recognize (unexpected banner text, a prompt phrased differently than expected) — the login flow is now verified against the real prod gateway (see [§13](#13-known-issues-and-operational-findings)), but a new environment/banner variant could still surface this | Check `logs/run-<id>.log` at DEBUG level for the raw buffer content the regex failed to match |
| Step re-asks the same question after a failure (didn't just move on) | This is by design — see [§9](#9-the-three-run-modes) | Use the retry menu: `[r]etry [d]one-manually [s]kip [q]uit` |
| `group N has no hosts in the wave mapping` | The Excel host sheet doesn't list any host for that group that also exists in `hosts.yaml` | Check the wave's host sheet and `hosts.yaml` are consistent — a new host may need adding to the inventory |
| `Could not find the host sheet` | The wave's host-listing sheet name doesn't contain `"NO IT"` | Pass `--host-sheet <exact name>` explicitly |
| Resumed run keeps re-showing steps you thought were done | Actions are only "done" once marked `SUCCESS` or `SKIPPED` — a step interrupted mid-action stays pending | Expected; the point of resume is exactly this — nothing is assumed done that wasn't recorded as such |
| Inventory file not found | `--inventory` omitted and `inventory/hosts.yaml` doesn't exist yet | Copy from `inventory/hosts.example.yaml` |

---

## 15. Repository map

```
auto_patchinator/
  cli.py                    entrypoint: `run` and `check-connectivity` subcommands
  logging_setup.py           per-run DEBUG log file + rotation
  term.py                    ANSI colors + animated progress indicator
  plan/
    excel_parser.py          Plan sheet → RawStep
    action_mapping.py        RawStep → TeamStep (team filter, verb+group parsing)
    dependency.py            TeamStep → OrderedStep (Kahn's algorithm)
    wave_mapping.py           host sheet → {group: hostnames}, cross-ref with inventory
    run_plan.py               ties it all together → RunStepPlan (+ captain/send_mail injection)
  actions/
    types.py                  Action / ActionKind / Identity / ExpectStep
    sequences.py               per-role stop/start sequences, host overrides, captain transfer text
  config/
    inventory.py               hosts.yaml loading + environment/PAS-suffix resolution
  executor/
    ssh.py                     PAS/PTY SSH layer + DryRunConnection
    credentials.py             AP_USERNAME/PASSWORD + reserved Splunk API creds
  runner/
    controller.py              the interactive loop, all 3 run modes, failure retry menu
  state/
    models.py                  RunState / StepState / ActionState
    store.py                   JSON persistence, resume detection, pruning
  reports/
    report.py                  markdown report generation

inventory/
  hosts.yaml                   real inventory (tracked in git — no sensitive personal data)
  hosts.example.yaml           template to copy from

scripts/
  gen_ci_fixture.py            generates the synthetic Excel used by CI

tests/                         pytest suite, one file per module + integration/controller tests
.github/workflows/ci.yml       pytest + dry-run smoke test on every push/PR

plans/        (gitignored, create yourself)   monthly wave .xlsx files go here
state/        (gitignored)                    live run state, pruned to 1 file
logs/         (gitignored)                    DEBUG audit logs, pruned to 3 files
reports/      (gitignored)                    markdown run reports, not pruned
.env          (gitignored)                    real credentials — copy from .env.example

README.md            quick-start / command cheat-sheet
CLAUDE.md             guidance for AI assistants working in this repo
TODO.md               live backlog + exact findings from operational testing
DOCUMENTATION.md      this file
```

---

## 16. Roadmap

`TODO.md` is the authoritative, continuously-updated backlog — read it for anything more
recent than this document. In summary, the main open threads are:

1. **Resolve the CyberArk-side root-identity issues** found in [§13](#13-known-issues-and-operational-findings)
   (test environment broadly broken; `prdrmlbbspkdp01` in prod) — blocking further
   root-identity testing/use.
2. **Complete one full, unattended live wave against test** end to end.
3. **Automate what's currently manual**, in roughly this order of value: `send_mail`
   (SMTP), StreamSets stop/start on the fw02 override, then (bigger lift, needs the
   already-scaffolded Splunk API credentials) captain transfer/revert and
   cluster-health polling after restarts.
4. **Nice-to-have infrastructure**: containerizing the app, recording the target
   `--environment` in the report header, parallelizing per-host actions within a group.
