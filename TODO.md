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

- [x] **New: pre-patch "pretest" phase, run automatically before every LIVE `run`
  (skipped for `--dry-run`, which stays "no SSH at all")** (2026-09-16), per operator
  request. Three layers, each gated on the previous being available - see
  `preflight.py`'s module docstring:
  1. SSH connectivity, splunk identity, scoped to exactly the hosts *this plan*
     touches (not the whole inventory) - reuses a newly-extracted shared
     `executor/connectivity.py` (also now used by `check-connectivity` itself, so
     there's one implementation, not two).
  2. Same, root identity. **Root is provisioned per-request, not always-on** (per the
     operator) - a FAIL here is routine on a day root wasn't requested, not
     necessarily a real problem; still shown so the operator remembers to request it
     before the actual patching window if root-identity actions are needed. (Earlier
     in this same session, a FAIL here was momentarily mis-read as a new regression
     before this was clarified - corrected; see the removed entry that used to be
     here.)
  3. If Splunk API credentials are configured (`SPLUNK_API_USER`+`SPLUNK_API_PASSWORD`
     in `.env` - optional, gracefully skipped if absent, and skipped with a clear
     message if only a bare `SPLUNK_API_TOKEN` is set) - for every **distinct
     search-head role** present in the plan (`search_head_stretched` and
     `search_head_simple` are two separate Splunk search head clusters, checked
     separately): who the current SHC captain is and whether election is dynamic or
     static (static outside an active wave is a red flag - likely a prior wave's
     `revert_captain_dynamic` was never run), plus every search-head host's own local
     KV store status. Runs as the **splunk** identity (no root needed) - the Splunk
     admin credentials authorize it, not the OS identity.
  These run via `splunk show shcluster-status --verbose -auth "<user>:<password>"` and
  `splunk show kvstore-status --verbose -auth "<user>:<password>"` over the same SSH
  session used everywhere else (`SSHConnection.run_plain_with_secret`, new - sets the
  password via a redacted shell-variable send first, so it never appears in
  `logs/run-*.log`). New `preflight.py` (orchestration). Never aborts the run itself -
  failures/warnings print clearly and the operator still decides at the existing
  "Proceed with this plan?" prompt.
  **Splunk API credentials were added to `.env` for the first time this session** -
  `load_splunk_api_credentials()` was scaffolding-only before now (nothing consumed
  it). Getting this right took several live-tested iterations against real prod
  `prdrmlbbspksh01`:
  - First built as raw HTTPS REST API calls (`SplunkApiClient`, stdlib `urllib`) -
    guessed field/endpoint names from memory turned out wrong twice (`dynamic_captain`
    on the wrong endpoint, then found the right one - `/services/shcluster/config`).
  - **Operator redirected to the actual `splunk show shcluster-status`/`kvstore-status`
    CLI commands instead** - simpler, matches how the operator actually runs this by
    hand, and needs no root (unlike an OS-level check) since it's the Splunk admin
    credentials that authorize it. Rebuilt on SSH + `run_plain_with_secret`;
    `executor/splunk_api.py` removed (REST approach fully replaced, not kept as a
    second path).
  - First CLI attempt used `-u "user:$AP_SECRET"` (a guess) and got `Your session is
    invalid. Please login.` - NOT a shell/quoting bug (verified separately with a
    dummy value - substitution mechanism works correctly) and NOT a stale cached
    session token (also checked, and ruled out further when the operator hit the same
    error running the command manually on a *different*, completely fresh search
    head). **The actual fix: `-auth`, not `-u`** - confirmed by the operator directly,
    and consistent with `captain_revert_dynamic`'s existing manual `bootstrap
    shcluster-captain -auth admin:<password>` instructions already in
    `actions/sequences.py`, which should have been cross-checked before guessing `-u`
    in the first place.
  - With `-auth`, the real command succeeded and returned genuine cluster data:
    `splunk show shcluster-status --verbose` is a right-aligned `key : value` table
    using the same raw (lowercase, underscored) field names as the REST content dict -
    `dynamic_captain :`, `label :` - not human-prose labels; the parser's regexes were
    adjusted to match and re-verified live. **Real result: captain=
    `prdmilbbspksh04.sky.local`, election=dynamic, all 10 stretched-SH cluster members
    `Up`, all local KV stores `ready`** - a fully healthy cluster, no leftover
    static-election issue from any prior wave.
  Full unit coverage: `tests/test_connectivity.py`, `tests/test_preflight.py` (fixture
  now trimmed-but-verbatim from the real captured output), `tests/test_ssh_helpers.py`
  (`run_plain_with_secret` never sends the secret in a logged/non-sensitive send). Also
  fixed a pre-existing test-isolation bug surfaced by `.env` now having real
  `SPLUNK_API_*` values: `tests/test_credentials.py`'s env-clearing fixture didn't
  stop `_load_dotenv()` from re-populating "cleared" vars straight from the real
  `.env` file.

---

## Must-do before first production live run

- [x] **Verify PAS su flow on prod nodes** — root identity connectivity confirmed
  working on 2026-07-03 (via `check-connectivity --identity root` against a 9-host
  sample, `whoami='root'` on 8/9) **and** by the operator manually testing root SSH
  against the full prod fleet: **the connection works for all the nodes except
  `prdrmlbbspkdp01`.** Confirms the PTY marker protocol and su flow in `executor/ssh.py`
  work correctly against the real `pas.prd.spk.root` gateway.

- [x] **`prdrmlbbspkdp01` root login rejected — fixed and re-verified** (2026-09-17) —
  `Authentication failed` at the PAS gateway itself, the one node out of the entire
  prod fleet where root didn't connect (isolated CyberArk entitlement gap for this
  specific host/identity pair, tracked since 2026-07-03, confirmed still broken as
  recently as 2026-09-16 - separate ticket/issue from the Roma-wide expired-password
  fix below). Re-checked via `check-connectivity --identity root --hosts
  prdrmlbbspkdp01` after the operator's fix landed - **OK, `whoami='root'`.** This was
  the last remaining root-connectivity gap in prod; every prod host's root identity
  now works.

- [x] **Full-fleet prod connectivity scan found 5 more Roma-site hosts with expired
  root passwords beyond `dp01` - now fixed and re-verified** (2026-09-09) — the
  2026-07-03 finding above was from a 9-host sample (1 per role × site) and only caught
  `prdrmlbbspkdp01`. A full 25-host × splunk+root sweep today found **splunk: 25/25 OK**
  (first full confirmation) but **root: 19/25 OK, 6 FAIL** - `prdrmlbbspkdp01` (known,
  outright auth-reject) plus 5 more, all on the **Roma site**, all expired-password (not
  auth-reject): `prdrmlbbspkfw01`, `prdrmlbbspksh02`, `prdrmlbbspksh05`,
  `prdrmlbbspkix02`, `prdrmlbbspkix03`. Milano root was 100% clean - this looks like a
  Roma-site-specific credential rotation gap, not scattered/random. Operator opened a
  CyberArk ticket for the expired-password issue same day; re-tested all 5 after the fix
  landed - **5/5 OK, `whoami='root'`.** `prdrmlbbspkdp01` re-tested separately and
  confirmed still failing (see above - unaffected by this fix, as expected given the
  different failure signature).

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

- [x] **Verified `backup_crontab` restores correctly on a real forwarder** (2026-09-11)
  — tested live against real prod `prdrmlbbspkfw01` (splunk identity only, Splunk itself
  never touched - these 3 actions don't need it stopped). Took an independent read-only
  `crontab -l` capture before touching anything (not just trusting the tool's own
  backup), then ran `backup_crontab` → `disable_crontab` → `enable_crontab` for real:
  backup file content matched the live crontab exactly (and is clean plain text, no
  terminal color codes - those only appear in the interactive `crontab -l` display, not
  the redirected file); `disable_crontab`'s "really delete...yes" confirmation handling
  worked correctly, `crontab -l` right after correctly showed `no crontab for splunk`
  (exit 1 - expected); final independent `crontab -l` capture was **byte-identical** to
  the original. All 3 actions OK.

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

- [x] **Cluster status validation via Splunk API - search head side done** (2026-09-16).
  Indexer cluster (poll `GET /services/cluster/master/peers`/S&R factor) is still open -
  out of scope for this pass, the operator asked for the search-head half specifically.
  - New `ActionKind.CLUSTER_WAIT` (`actions/types.py`, new `Action.poll_interval_seconds`
    field) and `wait_for_shcluster_member_healthy()` (`actions/sequences.py`), appended
    after `start_splunk` in **both** `search_head_stretched_sequences()` and
    `search_head_simple_sequences()`'s start halves - polls this host's own membership
    entry until healthy or `timeout_seconds` (600s) elapses, `poll_interval_seconds`
    (15s) apart.
  - Deliberately **not** the REST API this item originally sketched
    (`GET /services/shcluster/member/peers`) - built on the same `splunk show
    shcluster-status --verbose -auth "<user>:<password>"` CLI pattern the pretest
    already proved reliable (see the `-u` → `-auth` entry above), parsed by a new
    shared module, `executor/splunk_cli.py` (`parse_shcluster_members`, `find_member`,
    `member_health` - pure functions, no I/O). "Healthy" = `status: Up`,
    `out_of_sync_node: 0`, `restart_required: 0` all together - no single field
    observed so far reads as directly "artifact replication complete" (this item's
    original wording); `reported_preexisting_artifacts` looked like the closest
    candidate but its exact semantics aren't confirmed, so it's deliberately not
    relied on - revisit if a real poll run shows a member stuck healthy-looking-but-
    not, or vice versa.
  - Runs as the **splunk** identity, no root needed (Splunk admin credentials
    authorize it, not the OS identity) - reuses `SSHConnection.run_plain_with_secret`
    (the pretest's audit-log-safe password handling) and, in automatic mode, the same
    per-host connection cache as every other action on that host.
  - **Forced manual when Splunk API credentials aren't configured** (`is_forced_manual`
    extended, same pattern as `MANUAL_ONLY_IDENTITIES`) - the operator verifies cluster
    health by hand instead of the check being silently skipped.
  - `--dry-run` support (`DryRunConnection.run_plain_with_secret`, new) - simulates
    success immediately, no real polling, consistent with every other action kind.
  - New tests: `tests/test_splunk_cli.py` (member-section parsing against real
    captured, CRLF-included output - same CRLF bug class as the pretest's kvstore fix,
    guarded against directly), `tests/test_controller_cluster_wait.py` (healthy-
    immediately, unhealthy-then-healthy-after-N-polls, timeout, connection-error,
    dry-run, forced-manual-without-credentials). Live-verified (2026-09-16): the real
    parsing chain (fresh `shcluster-status --verbose` call → `parse_shcluster_members`
    → `find_member` → `member_health`) against `prdrmlbbspksh01` - correctly parsed
    all 10 real cluster members and evaluated the host healthy. **Not yet verified
    against a real restart** (i.e. actually watching the poll loop transition from
    unhealthy to healthy after a live `start_splunk`) - do that deliberately the next
    time a real search head restart happens, rather than assuming the poll-loop
    mechanics are right just because the parsing and the dry-run path are.

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
