# TODO

Open items, roughly in priority order.

---

## Recently done

- [x] Unit test suite (`tests/`, 61 tests) covering excel parsing, action mapping,
  dependency ordering, wave/host mapping, role sequences, inventory, PAS login string
  building, and state persistence/resume. Run with `python -m pytest` (install with
  `pip install -e ".[test]"`).
- [x] Auto-detect PAS gateway from inventory — `pas_gateway` in `hosts.yaml` is now the
  default; `--pas-gateway` only needed to override.
- [x] `run` defaults to LIVE execution; `--dry-run` is the opt-in simulate-only flag
  (previously the reverse).
- [x] Three per-step operator modes: automatic (`a`/`A`, or `--full-auto-mode`),
  task-by-task (`t`), and manual guide (`m` — prints each task's command/host/user/why
  one at a time, nothing executed, no ssh commands since the team connects via WinSSH).
- [x] Failures now show in red with a dedicated retry menu instead of silently
  re-prompting; automatic mode shows an animated "..." while an action runs.
- [x] Full DEBUG audit logging to `logs/run-<id>.log` (SSH send/receive, operator
  choices, passwords redacted).
- [x] Fixed: crontab was deleted with no backup taken first (see "Verify
  `backup_crontab`..." below for the remaining verification step).
- [x] **Default inventory path** — `--inventory` now defaults to `inventory/hosts.yaml`
  if omitted; a clear `SystemExit` error names the expected path if it's missing.
- [x] **Interactive Excel prompt** — if `--excel` is omitted, the tool looks for `.xlsx`
  files in a dedicated `plans/` directory first (most recent first), falls back to the
  current directory, and lets the operator pick a number or type a path; prompts for a
  path directly if none are found anywhere.
- [x] **`logs/` rotation** — `logging_setup.prune_old_logs` keeps only the 3 most
  recently modified `*.log` files, called automatically every time a run sets up
  logging.
- [x] **State pruning** — `store.prune_other_states` deletes every other `run-*.json`
  once the active run's state is saved, so `state/` only ever holds the current run.
- [x] **Splunk API credentials (scaffolding only)** — `SPLUNK_API_TOKEN` /
  `SPLUNK_API_USER` / `SPLUNK_API_PASSWORD` added to `.env.example` and loadable via
  `credentials.load_splunk_api_credentials()`. Nothing consumes them yet — see the
  StreamSets/captain-transfer/cluster-status items below, which now depend on this
  instead of needing their own credential plumbing.
- [x] **GitHub Actions CI** — `.github/workflows/ci.yml` runs `python -m pytest` plus a
  dry-run plan-resolution smoke test (`scripts/gen_ci_fixture.py` builds a synthetic,
  non-sensitive plan Excel referencing `inventory/hosts.example.yaml` hosts, since real
  wave Excels are gitignored) on every push and pull request.
- [x] **Branch protection on `main`** — PR + passing `test` status check now required
  before merge, configured in GitHub repo settings.
- [x] **Manual guide batches identical hosts** — patching a whole group (e.g. 5 search
  heads) used to show and confirm the same task list once per host. Hosts sharing an
  identical remaining task list + `su` hint are now shown once, with a "repeat on all
  N hosts" note, and confirmed together in one go; `i` falls back to per-host
  confirmation if one needs individual handling.
- [x] **Captain transfer/revert instructions readability** — the manual-guide text
  packed a numbered procedure and two long `shcluster-config` commands into one
  run-on paragraph. Restructured with explicit line breaks so each command sits on
  its own indented line, separate from the surrounding prose.
- [x] **Capital-letter "lock in" for task-by-task and manual guide too** — previously
  only `A` locked automatic mode for the rest of the run (`--full-auto-mode` did the
  same from the start). `T` and `M` now do the same for task-by-task and manual
  guide respectively (`RunController._locked_mode` generalizes the old `_full_auto`
  boolean to hold any of the three modes).
- [x] **Concrete captain-transfer hostnames instead of `<placeholder>`s** —
  `Inventory.captain_candidate(site)` picks the lowest-numbered stretched-SH host on
  the site not being patched (e.g. `prdrmlbbspksh01`), used consistently in both the
  transfer and revert manual instructions; the revert's `-servers_list` is now the
  actual full set of stretched-SH hostnames across both sites
  (`Inventory.stretched_sh_hostnames()`), not `<host1>,...`.
- [x] **Highlight literal commands in manual guide** — `su` hints, each task's `run:`
  line, and any 3+-space-indented line inside a `MANUAL` action's note (the captain
  transfer/revert commands) are now printed in bold cyan (`term.py`'s new `cyan`
  helper), so it's obvious at a glance what to type/paste vs. what's just explanation.
- [x] **Clear screen at the start of each step** — every step used to just keep
  scrolling past the previous one's output, making it hard to tell where the current
  step's output starts. `term.clear_screen()` clears the terminal's **visible** screen
  only (`\033[H\033[2J`, not `\033[3J`) at the start of every step - scrollback is
  untouched, so the operator can still scroll up to review earlier steps. No-op when
  stdout isn't a real terminal.
- [x] **Verified + documented native Windows support** — audited the codebase for
  POSIX-only calls (`pty`/`fcntl`/`termios`/`os.fork`/etc.) - none exist; every SSH
  command is a string addressed to the remote Linux hosts, irrelevant to the local OS.
  Added a "Running on native Windows" section to `DOCUMENTATION.md` (setup command
  differences, `.env` permissions without `chmod`, the `NO_COLOR` fallback for older
  `cmd.exe` consoles) — README just links to it now — plus a caution in CLAUDE.md to
  keep the codebase POSIX-call-free going forward.
- [x] **README trimmed to a lean quick-start** — Requirements/Files-you-maintain/full
  Commands+flags/Interactive-run-modes tables/Output-files/Architecture/Action
  sequences/Captain transfer/Known limitations were all duplicated (in more depth) in
  `DOCUMENTATION.md` already; README now just covers setup, a handful of example
  commands, and a pointer to `DOCUMENTATION.md` for everything else.
- [x] **Manual guide "why" line made optional** — `--verbose` or an interactive prompt,
  asked once right after manual guide mode is first chosen (never asked at all if it's
  never used that run), controls whether the reasoning behind each action is shown.
- [x] **Dropped unit-file backup/restore, reordered stretched-SH KV clean** (2026-07-23)
  — a systemd override (drop-in) file now holds each node's customizations, so
  `enable_boot_start` regenerating the unit from a template no longer loses anything;
  `backup_systemd_unit`/`restore_systemd_unit` are gone from every role's stop/start
  sequence. Search-head-stretched's start sequence now cleans the KV store *before*
  `start_splunk`, not after, since the store must be cleaned while Splunk is down.
- [x] **Fixed a real dry-run failure against this month's ("23on24") wave file**
  (2026-07-23) — three issues stacked: (1) the exact-name `"Plan"` sheet lookup crashed
  on this wave's incidental `"Plan "` (trailing space) naming — `excel_parser.py` now
  tolerates whitespace-only differences without matching another team's `"Plan IT
  ..."` sheet; (2) the host-sheet substring fallback in `wave_mapping.py` would have
  silently picked a leftover `"..._old"` sheet over the current one when both matched
  — it now deprioritizes `_old`-marked sheets and raises instead of guessing if still
  ambiguous; (3) this wave labels our rows `Gruppo_referente == "Splunk Broadband"`
  (no `"AOM"` prefix, confirmed to be a legitimate recurring label, not a typo) — added
  to `DEFAULT_TEAM_FILTERS`, and `_load_team_steps` now warns (listing every label
  actually found) instead of silently resolving to an empty plan if a future wave uses
  yet another label none of the filters match.

- [x] **Automatic mode was reconnecting per action; now reuses one connection per
  (host, identity) per step, plus optional `--max-parallel-hosts`** (2026-09-09) —
  raised by the operator as "the automatic version is too slow." Two findings/fixes,
  same area of code:
  - **Per-action reconnect** (found while investigating): `RunController._execute` was
    opening a brand-new SSH+PAS-gateway login and re-running the full `su` handshake for
    *every single action*, not once per host as CLAUDE.md's architecture notes already
    claimed - a role like `search_head_stretched`'s start sequence (4 actions, 2
    identities) paid 4 full PAS logins + 4 su handshakes per host. Fixed: automatic mode
    now opens one connection per (host, identity) and reuses it across that host's whole
    action list for the step (`_HostConnections`), closing it once that host's block
    finishes. Deliberately **not** carried across steps - the host may be rebooted by
    another team's OS-patch action between a "Stop" step and its later "Start" step, so
    nothing survives past the step it was opened for. A retry after a failed command
    drops and reopens that identity's connection rather than reusing a possibly-dead one.
    Task-by-task and manual guide are unaffected (still one-shot per action - see below).
  - **`--max-parallel-hosts N`** (the operator's actual ask): automatic mode can now run
    up to N hosts' action lists concurrently within a step (`ThreadPoolExecutor`,
    `_run_automatic`/`_run_host_block_auto`) instead of one host at a time. Pre-/post-
    group actions stay sequential barriers before/after, unaffected. Default is **1**
    (fully sequential, unchanged behavior) - opt-in, since the PAS/CyberArk gateway's
    tolerance for concurrent sessions isn't established (see the connectivity findings
    above: the gateway's own retry-delay comment already calls out rate-limiting
    sensitivity, and repeated test-environment failures are believed to have triggered
    an account-lockout counter). Console output and state saves are serialized so
    concurrent hosts' printed lines and failure/manual-confirm prompts never interleave;
    a quit chosen from one host's prompt stops the others from starting their *next*
    action (an in-flight command always finishes - never killed mid-SSH) via a shared
    `threading.Event`, checked before each action. Scoped to automatic mode only -
    task-by-task and manual guide are operator-paced already, not runtime-bound, and
    task-by-task's back/jump navigation doesn't fit the "contiguous per-host block"
    assumption this relies on.
  - Once the operator has run a live wave with `--max-parallel-hosts` > 1 without
    gateway-side issues (rate-limiting, unexpected auth failures), consider raising the
    default above 1.

- [x] **Fixed: `clean_kvstore` hung indefinitely - found live against real prod
  `prdrmlbbspksh01`** (2026-09-09) — a single-host live stop/start test of the full
  `search_head_stretched` sequence (operator-requested, explicit double-check done
  first: role/site confirmed, Splunk's actual running status verified read-only before
  and after, captain status acknowledged as unverifiable without Splunk admin creds and
  accepted as a 10-node-cluster self-heals-from-1-node risk). Stop half and
  `enable_boot_start`/`daemon_reload` all succeeded; `clean_kvstore` then hung and timed
  out - `splunk clean kvstore --local` interactively confirms ("This action will
  permanently drop app key/value-store database ... [y/n]?") and, being a `PLAIN`
  (non-interactive) action, nothing ever answered it. Nothing was actually dropped (no
  "y" was ever sent), but `start_splunk` was never reached, leaving Splunk down on a
  production search head until `start_splunk` was run directly to recover (confirmed
  back up immediately after, read-only). Fixed: `clean_kvstore`'s command now includes
  `--answer-yes`, splunk's documented flag for skipping exactly this confirmation.
  **This role/action combination has no test-environment equivalent** - every
  `search_head_stretched` host in the inventory is prod (test only has
  `search_head_simple`, which doesn't use `clean_kvstore` at all) - so this bug had
  never been exercised against a real host by anything, tool or operator, before now.
  Unit-tested (`--answer-yes` present in the command) and **re-verified live the same
  day**, same host, full `search_head_stretched` sequence repeated end to end: all 6
  actions OK, `clean_kvstore` auto-answered the confirmation cleanly ("Successfully
  removed contents of /opt/splunk/var/lib/splunk/kvstore.", exit 0) and `start_splunk`
  followed immediately - no manual fallback needed this time. Splunk confirmed running
  (fresh PID) immediately after.

---

## Must-do before first production live run

- [x] **Verify PAS su flow on prod nodes** — root identity connectivity confirmed
  working on 2026-07-03 (via `check-connectivity --identity root` against a 9-host
  sample, `whoami='root'` on 8/9) **and** by the operator manually testing root SSH
  against the full prod fleet: **the connection works for all the nodes except
  `prdrmlbbspkdp01`.** Confirms the PTY marker protocol and su flow in `executor/ssh.py`
  work correctly against the real `pas.prd.spk.root` gateway.

- [ ] **`prdrmlbbspkdp01` root login rejected** — `Authentication failed` at the PAS
  gateway itself (same signature as the broken test hosts below) — the one node out of
  the entire prod fleet where root doesn't connect. Looks like an isolated CyberArk
  entitlement gap for this specific host/identity pair, not a systemic prod issue —
  flag it to whoever manages the CyberArk safes.

- [ ] **BLOCKED: Root identity connectivity is broken in test** — ran
  `check-connectivity --identity root --environment test` (2026-07-03) against all 7
  test hosts: **0 OK, 7 FAIL.** Two distinct failure modes, both on the PAS/CyberArk
  side, not in this tool's code:
  - `tstmilbbspkdp01`, `fw01`, `spksh01-03` (5 hosts): PAS gateway login itself rejects
    the root identity outright (`Authentication failed.` before any shell is reached) —
    looks like the root PAS account isn't entitled/provisioned for these hosts, or uses
    a different password than the splunk identity (`.env` only has one AP_PASSWORD).
  - `tstmilbbspkix01`, `ix02` (the 2 indexers): gateway login succeeds, but the root
    account's password has **expired** on the target host, forcing an interactive
    `passwd`-style prompt (`Changing password for user pas.tst.spk.root. Current
    password:`). Also surfaced: `ix01`'s login banner showed "2 failed login attempts
    since the last successful login" — i.e. failed attempts are being counted, so
    repeated testing risks an account lockout.
  - Test's root credential looks broadly broken/unmaintained compared to prod's (which
    worked cleanly on 8/9 sampled hosts) — get it fixed/rotated via CyberArk, then retest.
  - Fixed regardless (2026-07-03): `ssh.py`'s `connect()` now detects the forced
    password-change prompt and raises a clear `PasswordExpiredError` instead of an
    opaque 30s timeout, and no longer leaks the socket when the shell/su setup fails
    after paramiko auth succeeds (`ssh.py` + `cli.py`'s `check-connectivity` loop).

- [ ] **Full end-to-end test on test environment** — run a complete live wave against
  the `tst*` nodes using `Vulnerability_Plan_TEST_Mockup.xlsx` (dry-run first, then
  live) to validate the full pipeline — Excel parsing, SSH sessions, action sequences,
  state persistence, resume, and report — before touching production. First live
  attempt this session surfaced a missing `pas_gateway` config and the crontab-backup
  bug below (both fixed); a full run still hasn't completed without operator
  intervention.

- [ ] **Verify `backup_crontab` restores correctly on a real forwarder** — fixed a bug
  this session where `disable_crontab` deleted the splunk user's crontab with nothing
  backing it up first (`enable_crontab` restored from a file that was never written).
  `backup_crontab` (`crontab -l > /appl/home/splunk/crontab.backup` — path updated
  2026-07-03 to the shared `/appl/home/splunk` scratch dir used on every node) now runs
  first on both
  the forwarder role and the `prdmilbbspkfw02` override, and it's unit-tested that the
  sequencing and filename match — but not yet verified against a real node's actual
  crontab.

---

## Short-term improvements

- [ ] **Automate StreamSets stop/start for `prdmilbbspkfw02`** — replace the current
  `manual_todo` placeholders in `actions/sequences.py → _prdmilbbspkfw02_sequences()`
  with StreamSets REST API calls:
  - Stop all pipelines before the Splunk stop sequence.
  - After the Splunk start sequence, poll the StreamSets API until all pipelines report
    `RUNNING` and metrics confirm data is flowing again.
  - Temporarily scale the ODP Preprocessing pipeline worker threads from 5 → 8 before
    restart (to absorb the backlog) and revert to 5 once throughput normalises.
  StreamSets API base URL and credentials should come from `.env` (a separate var, not
  the Splunk API credentials above).

- [ ] **Automate SH captain transfer / revert** — replace the
  `transfer_captain_static` and `revert_captain_dynamic` manual steps with Splunk REST
  API calls, using `credentials.load_splunk_api_credentials()` (now available, see
  "Recently done"):
  - Transfer: `POST /services/shcluster/captain/transfer` on the current captain, or
    `edit shcluster-config -mode captain` on the target.
  - Revert: re-enable dynamic election on all members, then bootstrap from the captain.
  Keep both as manual fallbacks if the API call fails.

- [ ] **Cluster status validation via Splunk API** — add automated checks at key
  points in the sequence using the Splunk REST API (via `load_splunk_api_credentials()`):
  - **Indexer cluster**: after each indexer restarts, poll
    `GET /services/cluster/master/peers` until the peer is `Up` and S&R factor is met.
  - **Search head cluster**: after each SH restarts, poll
    `GET /services/shcluster/member/peers` until the member is `Up` and artifact
    replication is complete.
  - Implement as a `WAIT`-kind action with a configurable timeout and a live progress
    line ("waiting for SH cluster... 3/5 members up").

- [ ] **Automate `send_mail`** — every Excel step ends with a manual `send_mail` action.
  Implement SMTP sending (server / credentials from `.env`, recipients configurable per
  step or globally) and change the action kind from `MANUAL` to `PLAIN`.

- [ ] **Indexer S&R factor check** — basic post-restart search/replication factor
  verification, via `load_splunk_api_credentials()`. Deferred to v2 but the credential
  dependency is now resolved, not implementation complexity.

- [x] **`waves/` directory cleanup** — the leftover `waves/wave7.yaml` from the old
  manual group→host YAML approach is gone; confirmed nothing in code or docs references
  `waves/` anymore.

---

## Nice-to-have / v2

- [ ] **Windows CI job** — `.github/workflows/ci.yml` only runs on `ubuntu-latest`
  today. Windows support is code-reviewed and documented (see "Recently done" above)
  but not automatically tested; add a `windows-latest` entry to the job matrix if it's
  ever worth the extra CI minutes.

- [ ] **Containerize the app** — package the tool as a Docker image so it can run
  without a local Python/venv setup. Things to work out:
  - Base image with Python 3.10+ and the pinned deps (openpyxl, PyYAML, paramiko,
    python-dotenv) installed via `pip install .`.
  - The tool is *interactive* (prompts throughout a run) — needs `docker run -it` and
    a documented invocation, not a fire-and-forget entrypoint.
  - `inventory/`, `plans/`, `state/`, `logs/`, `reports/`, and `.env` all need to be
    bind-mounted volumes (or a single mounted project dir) so they persist across
    container runs and stay off the image — none of that sensitive data belongs baked
    into a layer.
  - Credentials (`AP_USERNAME`/`AP_PASSWORD`, future `SPLUNK_API_*`) should be passed
    via `--env-file .env` or `-e`, never `ARG`/`ENV` in the Dockerfile.
  - Decide whether `check-connectivity` and `run` need real outbound SSH from inside
    the container (they do) — confirm the corporate network/VPN path to the PAS
    gateway is reachable from wherever the container runs.

- [ ] **`--environment` in report** — the markdown report (`reports/report.py`) does
  not currently record which environment was targeted. Add it to the report header.

---

## Dreams

- [ ] **Mail-triggered step progression** — parse incoming confirmation emails (from
  other teams signalling their step is complete) to automatically unblock external
  dependencies, eliminating the manual "confirm the other team finished" prompt.

- [ ] **Automatic step-completion notifications** — send a structured email at the end
  of each step automatically (the `send_mail` action above), so downstream teams and
  the change record are updated without operator intervention.
