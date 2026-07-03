# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An interactive CLI that reads a monthly Splunk patch-wave Excel plan, extracts the Splunk BB team's steps (rows where `Gruppo_referente == "AOM Sky CSO"`) in dependency order, resolves them to per-node stop/disable/enable/start action sequences, and walks an operator through confirming/running/skipping each one over SSH (via a PAS/CyberArk gateway). Python 3.10+, deps: openpyxl, PyYAML, paramiko.

## Commands

```bash
# Setup (venv at .venv already exists)
source .venv/bin/activate
pip install -e .

# Run - LIVE by default (real SSH, prompts for credentials, shows MODE banner).
# Group->host mapping is read directly from the Excel's 'List Host NO IT' sheet,
# cross-referenced against the inventory to filter to our team's hosts.
auto-patchinator run --excel <plan.xlsx> --inventory inventory/hosts.yaml --pas-gateway <gateway-host>

# Simulate only (no SSH) - for testing plans end to end
auto-patchinator run ... --dry-run

# Verify SSH connectivity to all inventory nodes
auto-patchinator check-connectivity --inventory inventory/hosts.yaml --pas-gateway <gateway-host>
```

```bash
# Tests (pytest; install test deps with: pip install -e ".[test]")
python -m pytest            # whole suite
python -m pytest tests/test_run_plan.py -k captain   # single file / test
```

No linter is configured. The unit tests cover the plan pipeline and pure helpers; the interactive flow and SSH layer are exercised via dry-run mode end to end.

## Architecture

Data flows through a strict pipeline, assembled in `cli.py:cmd_run`:

1. **`plan/excel_parser.py`** — reads the Excel "Plan" sheet into `RawStep` rows (header names come from the sheet verbatim, including the "Dependancy" typo).
2. **`plan/action_mapping.py`** — filters to team rows and regex-parses the verb (stop/start) and group numbers out of the free-text `Nome` column (the `Tipo` column is empty for Splunk rows). Rows it can't confidently parse are returned separately and flagged for manual review — never silently guessed.
3. **`plan/dependency.py`** — Kahn's-algorithm ordering. Dependencies pointing outside the team's own step set (other teams' steps) become `external_dependencies`, which surface as a manual "confirm the other team finished" action at run time.
4. **`plan/wave_mapping.py` + `config/inventory.py`** — `wave_mapping.py` reads the group→hostname mapping directly from the Excel's "List Host NO IT" sheet and cross-references it with the static inventory (`hosts.yaml`) to filter to our team's hosts. The inventory (hostname→role/site/manual-identity exceptions) doesn't change month to month.
5. **`plan/run_plan.py`** — combines the above with `actions/sequences.py` into `RunStepPlan`s: `pre_group_actions` / `per_host_actions` / `post_group_actions`. Group-level actions are deduped per role (or per overridden host), not by sequence equality.
6. **`runner/controller.py`** — the interactive loop. Each step is run in one of three operator-chosen modes (asked per step; `A` or `--full-auto-mode` locks in automatic): *automatic* (one line per action, pauses only on manual confirmations and failures), *task-by-task* (run / mark-done-manually / skip / back / jump / quit per action), or *manual guide* (`m`: executes nothing — prints every command with host, user, exact PAS ssh/su line, and rationale, then the operator marks the work done). Failures show a red block + retry menu in all executing modes (`term.py` has the ANSI color helpers, auto-disabled when not a tty). Persists state after every transition.
7. **`state/`** — JSON run state under `state/`; on startup an incomplete run is detected and offered for resume. **`reports/report.py`** writes a markdown report at the end.

### Action model (`actions/`)

- `types.py`: `Action` has a kind (`PLAIN` shell command, `INTERACTIVE` expect-script, `MANUAL` operator step, `WAIT`) and an identity (`splunk` or `root`). Validation is in `__post_init__`.
- `sequences.py` is the domain knowledge core. Each role (deployer, indexer, forwarder, search_head_simple, search_head_stretched) exposes **separate STOP and START halves** — the Excel models "Stop Group N" and "Start Group N" as independent steps because OS patching happens between them, outside this tool. Each half has pre-group / per-node / post-group phases.
- `HOST_OVERRIDES` (keyed by hostname) replaces the role sequence entirely for special hosts (e.g. `prdmilbbspkfw02` with StreamSets). `MANUAL_ONLY_IDENTITIES` lists hosts where an identity can't be used over SSH (CyberArk-GUI-only); the controller forces those actions to manual.
- Indexers use a different Splunk binary path (`/splunkdata/splunk/bin/splunk` vs `/opt/splunk/bin/splunk`) and a different su command.

### SSH layer (`executor/ssh.py`)

Everything runs in one persistent PTY shell session per (host, identity) — not one-shot `exec_command` — because the `sudo su -` step and some commands prompt interactively. The PAS gateway login encodes the target identity in the username (`<user>@pas.prd.spk[.root]@<host>`); after login it still `su`s into splunk/root with the same password. Command completion/exit codes are detected via a `PS1` marker + `AP_EXIT_CODE` echo protocol. `DryRunConnection` implements the same interface and is what `--dry-run` runs use. Every run appends a DEBUG audit log (actions, operator choices, raw SSH sends/reads with passwords redacted) to `logs/run-<run_id>.log` via `logging_setup.py`.

## Invariants and cautions

- **Never hardcode the Splunk admin password** (used by `bootstrap shcluster-captain -auth admin:<password>`) anywhere in this repo. Search-head-captain transfer/revert are *intentionally* manual steps — don't automate them (cluster-wide scope, host choice, and credential handling are why).
- `*.xlsx` files are gitignored because the plan contains sensitive data; so are `state/` and `reports/`. Don't commit or echo their contents.
- The start sequence order is deliberate: `enable boot-start` regenerates the systemd unit from a template, so it must be enable → restore the edited unit copy (`cat >`, not rm+cp — replacing the inode breaks systemd's cached state) → daemon-reload → start.
- The sudo/su password-prompt regex and PTY marker protocol in `ssh.py` are untested against the real PAS gateway (flagged in README) — expect that login flow to need adjustment with lab access.
- Actions are meant to be idempotent; the controller re-presents non-SUCCESS/SKIPPED actions on resume.
