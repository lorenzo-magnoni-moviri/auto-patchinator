# auto-patchinator

Interactive CLI for the Sky Splunk Broadband team's monthly OS patch wave.

Reads the wave Vulnerability Plan Excel, extracts our team's steps in dependency order,
resolves each step into per-node stop / disable / enable / start action sequences, and
walks an operator through executing or confirming each one over SSH via the PAS/CyberArk
gateway.

---

## Requirements

- Python 3.10+
- Access to the PAS/CyberArk SSH gateway (`pas.sky.local`)
- The wave Vulnerability Plan `.xlsx` file
- `inventory/hosts.yaml` filled in (see below)

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Credentials

A live run prompts for username and password at startup. To skip the prompt, copy
`.env.example` to `.env`, fill it in, and restrict its permissions:

```bash
cp .env.example .env
# edit .env: set AP_USERNAME and AP_PASSWORD
chmod 600 .env
```

`.env` is gitignored. **Never commit credentials.**

`.env.example` also has placeholders for `SPLUNK_API_TOKEN` / `SPLUNK_API_USER` /
`SPLUNK_API_PASSWORD` — reserved for future automations (captain transfer, cluster
status polling; see `TODO.md`). Nothing in the tool reads them yet.

---

## Files you maintain

### `inventory/hosts.yaml`

Single source of truth for all nodes — both production and test. Does not change
month to month. Contains per-host role, site, PAS connection details, and SSH
quirks. Copy from `inventory/hosts.example.yaml` to get started.

`--inventory` defaults to `inventory/hosts.yaml` if omitted — pass it explicitly only
to use a different file.

Key top-level fields:

```yaml
stretched_sh_sites: [milano, roma]   # sites of the stretched SH cluster
pas_port: 10100                      # encoded in every PAS login username
pas_gateway: pas.sky.local           # default PAS gateway (overridable via --pas-gateway)

environments:
  prod:
    pas_splunk_suffix: pas.prd.spk
    pas_root_suffix:   pas.prd.spk.root
  test:
    pas_splunk_suffix: pas.tst.spk
    pas_root_suffix:   pas.tst.spk.root
```

Per-host fields:

| Field | Description |
|---|---|
| `role` | `deployer` \| `indexer` \| `forwarder` \| `search_head_simple` \| `search_head_stretched` |
| `site` | `milano` \| `roma` |
| `environment` | `prod` (default) \| `test` |
| `pas_domain_suffix` | `.sky.local` for Roma nodes; omit for Milano |
| `splunk_su_command` | Override su command (e.g. `sudo su - splunk` for `ix01-03`) |
| `manual_identities` | List identities that must be done via CyberArk GUI, not SSH |

### The wave Excel file

The Vulnerability Plan `.xlsx` is gitignored (contains sensitive scheduling data).
The tool reads two sheets from it automatically:

- **Plan** — step order, dependencies, team filter (`Gruppo_referente`)
- **List Host NO IT** (or similar name) — group → hostname mapping, cross-referenced
  against the inventory to select only our nodes

No manual YAML mapping is needed between waves.

**`--excel` is optional.** Drop each month's file into a `plans/` directory at the
project root (create it if it doesn't exist — it's gitignored like any other `.xlsx`).
If `--excel` is omitted, the tool lists `.xlsx` files there, most recent first, and lets
you pick one by number or type a path; if `plans/` is empty or missing, it falls back to
the current directory, and if that's empty too, it just prompts for a path.

---

## Commands

`--excel` and `--inventory` are both optional — see "Files you maintain" above for the
fallback rules (plans/ directory picker, default `inventory/hosts.yaml`). `--pas-gateway`
is also optional and normally never needed: it falls back to `pas_gateway` in
`hosts.yaml`, which doesn't change month to month. Only pass it explicitly to override
that default for a one-off run (e.g. testing against a different gateway).

### Live run (the default)

```bash
auto-patchinator run
```

No `.xlsx` files found where the tool looks? You'll be prompted for a path. Prompts once
for credentials (or reads from `.env`), shows a `PRODUCTION ENVIRONMENT` banner, asks
for confirmation, then walks through the plan interactively.

### Dry-run (simulate only — no SSH, safe to run any time)

```bash
auto-patchinator run --dry-run
```

Prints the full resolved plan and simulates every action, no credentials needed. Use
this to verify a plan (or a new wave's Excel) before a live run.

### Test environment

```bash
auto-patchinator run --environment test
```

Filters to `tst*` nodes and uses the `test` PAS suffixes from `hosts.yaml` automatically.

### Connectivity check

Verify SSH access to every node before a live run:

```bash
# Prod
auto-patchinator check-connectivity

# Test
auto-patchinator check-connectivity --environment test

# Root identity only
auto-patchinator check-connectivity --identity root

# Specific hosts
auto-patchinator check-connectivity --hosts prdmilbbspksh01 prdmilbbspksh02
```

---

## Interactive run modes

At the start of each Excel step the operator chooses how to run it:

| Key | Mode |
|---|---|
| `a` | **Automatic** — actions run back to back, one line each; pauses only for manual steps and failures |
| `A` | Automatic for **all remaining steps** |
| `t` | **Task-by-task** — confirm each action before it runs |
| `m` | **Manual guide** — executes **nothing**: shows the command, which user to become and how, and why, then waits for ENTER after you have done it by hand (connection via WinSSH). Hosts sharing an identical role/task list (e.g. 5 search heads) are shown **once** with a "repeat on all N hosts" note, and confirmed **together** in one go — `i` falls back to confirming them one host at a time if one needs individual handling; `l` lists every task at once, `s` skips, `q` quits |

Within task-by-task mode each action offers:

`[r]un` / `[d] mark done manually` / `[s]kip` / `[b]ack` / `[j]ump <step>` / `[q]uit`

Failures (in any executing mode) show the command output in red and offer:

`[r]etry` / `[d] mark done manually` / `[s]kip` / `[q]uit`

**Resuming an interrupted run:** if a run is interrupted (Ctrl-C, network drop, crash),
rerunning the same command detects the incomplete state file under `state/` and offers
to resume from where it left off.

---

## Tests

```bash
pip install -e ".[test]"
python -m pytest
```

Unit tests cover the whole plan pipeline (Excel parsing, team-step mapping, dependency
ordering, host mapping, role sequences, inventory, PAS login strings, state persistence)
plus the controller's manual-guide mode. Run them before merging any change; dry-run mode
remains the way to exercise the interactive flow end to end.

### CI

`.github/workflows/ci.yml` runs on every push and pull request: the full unit test
suite, plus a dry-run plan-resolution smoke test against a synthetic Excel generated by
`scripts/gen_ci_fixture.py` (real wave Excels are gitignored, so CI can't use one
directly — the fixture references hosts from the checked-in
`inventory/hosts.example.yaml` instead). This catches import errors and plan-resolution
regressions that a unit test importing internal functions directly might miss.

`main` is branch-protected: a pull request and a passing `test` status check are both
required before a merge is allowed.

---

## Output files

All are gitignored.

| Path | Contents |
|---|---|
| `state/run-<id>.json` | Live run state; updated after every action; used for resume. Only **one** state file is kept at a time — starting or resuming a run deletes any other leftover run state. |
| `reports/run-<id>.md` | Markdown report written at end of run (or on quit) — not pruned |
| `logs/run-<id>.log` | Full DEBUG log: plan resolution, every SSH send/receive, operator choices. Passwords logged as `<redacted>`. Only the **3** most recently modified log files are kept; older ones are deleted automatically. |

---

## Architecture overview

```
Excel (.xlsx)
  └── plan/excel_parser.py      → RawStep list (Plan sheet, alias-tolerant headers)
  └── plan/wave_mapping.py      → group→host map (host sheet, cross-ref with inventory)

plan/action_mapping.py          → TeamStep list  (filter by Gruppo_referente, parse verb+group)
plan/dependency.py              → OrderedStep list (Kahn's algorithm, flags external deps)
config/inventory.py             → Inventory (filtered to --environment)
actions/sequences.py            → per-role STOP / START action sequences
plan/run_plan.py                → RunStepPlan list (combines all above)

runner/controller.py            → interactive loop (auto / task-by-task)
executor/ssh.py                 → PTY shell session per (host, identity) via PAS gateway
state/store.py                  → JSON persistence / resume detection
reports/report.py               → markdown report
```

### PAS login username format

```
<username>@<pas_suffix>@<hostname>[.sky.local][#10100]
```

Examples:
- `lmm992@pas.prd.spk@prdmilbbspksh01#10100`
- `lmm992@pas.prd.spk.root@prdrmlbbspkdp01.sky.local#10100`
- `lmm992@pas.tst.spk@tstmilbbspksh01#10100`

### Action sequence per node (stop half)

1. `stop_splunk` — `sudo <splunk_bin> stop`
2. `backup_systemd_unit` — `cp /etc/systemd/system/Splunkd.service /appl/home/splunk/Splunkd.service.copy`
3. `disable_boot_start` — `sudo <splunk_bin> disable boot-start`

Forwarders also back up the crontab first (`crontab -l > /appl/home/splunk/crontab.backup`)
before `disable_crontab` deletes it.

### Action sequence per node (start half)

1. `enable_boot_start` — regenerates the systemd unit from template (runs as root)
2. `restore_systemd_unit` — overwrites the generated unit with the backed-up edited copy (`cat >`, not `cp` — replacing the inode breaks systemd's file watch)
3. `daemon_reload` — `systemctl daemon-reload`
4. `start_splunk` — `sudo <splunk_bin> start`
5. `clean_kvstore` — search heads only: `splunk clean kvstore --local`

### Captain transfer (stretched SH cluster)

Injected **once per wave** — not per group:
- **Before the first SH stop**: manual step to transfer captain to the site NOT being
  patched (Milano → Roma, or Roma → Milano — whichever site the wave isn't touching)
- **After the last SH start**: manual step to revert captain election to dynamic

The admin password required by `bootstrap shcluster-captain` is **never stored in this
repo** — type it only at the live terminal.

---

## Known limitations / placeholders

- **StreamSets on `prdmilbbspkfw02`**: stop/start of StreamSets pipelines is a
  `manual_todo` placeholder pending the exact CLI commands.
- **Root identity**: verified 2026-07-03 — works across the prod fleet **except
  `prdrmlbbspkdp01`** (rejected at the PAS gateway, looks like an isolated CyberArk
  entitlement gap, not a code issue). **Root is currently broken in test** (all 7 hosts
  fail: 5 outright auth failures, 2 indexers hit an expired PAS password mid-login) —
  needs a CyberArk-side fix before further test-environment root testing. See `TODO.md`
  for the full findings.
- **Indexer S&R factor check**: post-restart search/replication factor wait-and-verify
  is deferred to v2.
- **`send_mail` step**: currently a manual placeholder at the end of every Excel step;
  will be automated in a future version.
- **PAS su password prompt regex**: verified working against the real prod gateway
  (root identity, 2026-07-03); the expired-password variant is now detected explicitly
  (`PasswordExpiredError`) instead of timing out.
