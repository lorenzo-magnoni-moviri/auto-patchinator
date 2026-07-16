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

- [ ] **Parallel host actions within a group** — currently hosts within one Excel group
  are processed sequentially. For groups with multiple hosts (e.g. all 5 Roma SHs in
  one step) this could be parallelised with `ThreadPoolExecutor`, gated by a
  configurable concurrency limit.

---

## Dreams

- [ ] **Mail-triggered step progression** — parse incoming confirmation emails (from
  other teams signalling their step is complete) to automatically unblock external
  dependencies, eliminating the manual "confirm the other team finished" prompt.

- [ ] **Automatic step-completion notifications** — send a structured email at the end
  of each step automatically (the `send_mail` action above), so downstream teams and
  the change record are updated without operator intervention.
