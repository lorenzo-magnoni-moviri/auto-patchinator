# TODO

Open items, roughly in priority order.

---

## Recently done

- [x] Unit test suite (`tests/`, 61 tests) covering excel parsing, action mapping,
  dependency ordering, wave/host mapping, role sequences, inventory, PAS login string
  building, and state persistence/resume. Run with `python -m pytest` (install with
  `pip install -e ".[test]"`).
- [x] Auto-detect PAS gateway from inventory — `pas_gateway` in `hosts.yaml` is the only
  source (2026-09-21: the `--pas-gateway` CLI override was removed entirely, along with
  `--plan-sheet`/`--host-sheet` — a workbook that doesn't have a `Plan` sheet and a
  `List Host NO IT`-ish host sheet now fails with a clear "must follow the expected
  format" error instead of offering a per-run override).
- [x] `run` defaults to LIVE execution; `--dry-run` is the opt-in simulate-only flag
  (previously the reverse).
- [x] Three per-step operator modes: automatic (`a`/`A`, or `--full-auto-mode`),
  task-by-task (`t`), and manual guide (`m` — prints each task's command/host/user/why
  one at a time, nothing executed, no ssh commands since the team connects via WinSSH).
- [x] Failures now show in red with a dedicated retry menu instead of silently
  re-prompting; automatic mode shows an animated "..." while an action runs.
- [x] Full DEBUG audit logging to `logs/run-<id>.log` (SSH send/receive, operator
  choices, passwords redacted).
- [x] **Verbose per-host progress for `CAPTAIN_TRANSFER`/`CAPTAIN_REVERT` in automatic
  mode** (2026-09-24, operator feedback after the live transfer test: the terse
  `"[group] transfer captain static ... DONE"` line every other automatic-mode action
  gets wasn't enough for a multi-minute, multi-host conf change - especially coming
  right after the earlier live-test confusion over what looked like a stalled/
  incomplete transfer). Both actions now print their own progress as they go:
  `_print_captain_progress` (new small helper, `_console_lock`-held) prints a line for
  each phase - setting the captain, re-enabling election, bootstrapping - and
  `_run_captain_config_command` (shared by both actions' "every other member" step)
  now prints a per-host `[hostname] OK`/`FAILED` line as each one completes, from
  inside its worker thread. The poll loop for both actions now prints every attempt
  (elapsed time + the raw `label`/`dynamic_captain` fields) instead of only logging it
  at DEBUG level - directly answers "is this actually still working or stuck?" without
  needing to tail the log file. `_handle_action_auto` forces `animate=False` for these
  two action kinds specifically, since the extra print lines would otherwise collide
  with the single-line dot-spinner's in-place `\r` redraw (same static-line/heartbeat
  path concurrent per-host actions already use). Verified via existing
  `tests/test_controller_captain.py` fixtures run with `pytest -s` (output capture
  off) to see the actual formatting - no test changes needed, output didn't affect any
  assertion.
- [x] **Fixed: CI's `--dry-run` smoke test crashed with `EOFError`** (2026-09-24, found
  from the actual CI failure notification for commit `54c8c42`). Root cause: the `.env`
  completeness check (`ensure_env_credentials_complete()`, added earlier the same day -
  see below) ran unconditionally at the very top of `cmd_run`, before the `--dry-run`
  branch even existed. CI's fresh checkout has no `.env` and pipes a single `n` to
  stdin for the "Proceed with this plan?" prompt; the credentials check consumed that
  `n` as its first field's answer, then called `input()` again for the next field with
  stdin already exhausted, raising an unhandled `EOFError`. Two fixes, both belt-and-
  suspenders: (1) `cli.py:cmd_run` now only calls `ensure_env_credentials_complete()`
  when `not args.dry_run` - matches every other credential-touching step
  (`prompt_credentials()`, the pretest), since `--dry-run`'s whole point is "no SSH at
  all" and it shouldn't need or prompt for credentials either; (2)
  `ensure_env_credentials_complete()` itself now checks `sys.stdin.isatty()` and skips
  silently (no prompt, no crash) whenever stdin isn't interactive, so *any* future
  non-interactive invocation degrades gracefully instead of hitting the same `EOFError`
  class of bug. Reproduced locally first (temporarily moving `.env` aside and clearing
  the credential env vars, then running the exact CI command) to confirm the crash,
  then again after the fix to confirm a clean exit. 1 new test in
  `tests/test_credentials.py` (20 total); `.github/workflows/ci.yml` itself needed no
  change.
- [x] **Dropped `inventory/hosts.example.yaml`** (2026-09-24, operator feedback: "host
  will always be the same there's no need for an hosts.example.yaml") — unlike `.env`,
  there's only ever one real inventory for this team's fixed estate, and `hosts.yaml`
  itself is already tracked in git (no sensitive personal data in it) and present from
  a clone, so a separate "template to copy from" was pure redundancy - and had already
  drifted stale (old `idx`→`ix` hostname rename, missing the `test` environment
  entries, missing the top-level `environments:` block). Removed the file and every
  reference to it: `cli.py`'s missing-inventory error message and its test
  (`tests/test_cli_defaults.py`), `scripts/gen_ci_fixture.py`'s docstring, and
  `.github/workflows/ci.yml`'s `--inventory` flag - **which was still pointing at the
  file being deleted**, so this would have silently broken CI if missed. `README.md`/
  `DOCUMENTATION.md` updated to match (inventory setup is now "already there, nothing
  to do" rather than a copy step).
- [x] **Windows installation instructions expanded into a full step-by-step walkthrough**
  (2026-09-24) — `DOCUMENTATION.md`'s §3 Windows subsection (renamed "Installing on
  native Windows") used to explain *why* the tool works identically on Windows without
  actually walking through getting it installed there (assumed Python/git already
  present, jumped straight to the venv commands). Now numbered end to end: installing
  Python 3.10+ (with the "Add to PATH" gotcha called out), getting the code
  (`git clone` or a GitHub ZIP download for operators without git), venv setup, install,
  credentials (now points at the new `.env` completeness prompt from the previous
  entry instead of a manual `copy`/edit step), inventory setup, and verifying with
  `check-connectivity`/`--dry-run` before a live run. `README.md`'s Windows quick-start
  trimmed to match and pointed at the full walkthrough.
- [x] **Stronger SSH connect retry: exponential backoff + jitter, fail-fast on auth
  errors** (2026-09-24) — found live: frequent `Error reading SSH protocol
  banner...[Errno 104] Connection reset by peer` errors, most likely the PAS gateway
  resetting some handshakes when several land at once (`--max-parallel-hosts` defaults
  to 3, and `CAPTAIN_TRANSFER`/`CAPTAIN_REVERT` open several ad-hoc connections
  concurrently on top of that). The existing retry in `SSHConnection.connect()`
  (`executor/ssh.py`) was only 2 attempts with a flat 3s delay - not enough headroom,
  and with no jitter, hosts that failed together in the same burst would retry in
  lockstep and could hit the gateway together again. Now: `_CONNECT_RETRIES` = 5,
  `_connect_retry_delay()` gives exponential backoff (2s/4s/8s/16s, capped at 20s)
  with ±30% jitter so concurrent hosts desynchronize on retry. Also narrower than
  before: `_is_transient_connect_error()` only retries connection-level flakiness
  (`paramiko.SSHException`/`OSError`/`EOFError`) - a wrong password or rejected host
  key (`AuthenticationException`/`BadHostKeyException`) fails on the first attempt
  instead of burning the whole backoff schedule before telling the operator their
  credentials are wrong. 6 new tests in `tests/test_ssh_helpers.py`.
- [x] **`.env` completeness check, run before any plan/Excel work** (2026-09-24) — found
  live: `.env` had gone missing from this machine entirely (not a code change - just
  discovered mid-session), and today's pretest log
  (`logs/run-pretest-20260924T150526.log`) had real `Authentication failed` errors for
  several search heads as a result, since credential gaps used to only surface lazily,
  wherever the first feature that needed them happened to hit the gap. `cli.py:cmd_run`
  now calls `executor/credentials.py:ensure_env_credentials_complete()` first thing,
  before `--excel`/`--inventory` resolution: checks `AP_USERNAME`/`AP_PASSWORD` plus the
  four Splunk-API/StreamSets-API fields (`ENV_CREDENTIAL_FIELDS` — deliberately excludes
  the alternative `SPLUNK_API_TOKEN`, since nothing in this codebase's real command
  paths consumes a bare token, only `-auth user:password`), prompts for whatever's
  missing (blank input skips a field - AP_USERNAME/AP_PASSWORD are the only ones this
  tool can't run without, still enforced downstream by the existing
  `prompt_credentials()`/`load_*_credentials()` fallbacks, not duplicated here), and
  writes answers back into `.env` (`_write_env_values` - updates an existing `KEY=...`
  line in place, appends one for a key not already present, leaves every other line
  untouched; builds a fresh `.env` from `.env.example`'s structure, comments and all, if
  `.env` doesn't exist yet at all; `chmod 600`s the file afterwards, best-effort). Same
  change also switched the password prompt in `prompt_credentials()` from
  `getpass.getpass()` to a plain `input()` — **terminal input is no longer masked
  anywhere in this tool**, operator's explicit preference (easier to verify what was
  typed/pasted than a blind prompt; `.env` itself already isn't visible to anyone
  without shell access to this machine). Two fields also get per-field guidance in the
  prompt itself (`_FIELD_HINTS`/`_FIELD_DEFAULTS`/`_prompt_text`): `AP_USERNAME` is
  hinted to use the operator's own day-to-day Splunk login (no sensible default);
  `SPLUNK_API_USER` is hinted to use the Splunk admin account and defaults to `"admin"`
  on blank input (shown as `[admin]` in the prompt - deliberately not an actually
  editable pre-filled field, which would need the `readline` module and break native
  Windows support). 10 new tests in `tests/test_credentials.py` (19 total).
- [x] **Fixed: `run_plain_with_secret` leaked the Splunk admin password into
  `logs/run-*.log` in cleartext** (found live, 2026-09-22, mid real wave-9 run — the
  password showed up ~194 times across the run log and both pretest logs from that
  session). Root cause: `send(..., sensitive=True)` redacted the *outbound* line, but a
  PTY locally echoes back whatever was just typed before the shell even processes it,
  and the `read_until()` immediately following that send logged that raw echoed buffer
  unconditionally - the redaction never covered the read side. Fixed by adding a
  `sensitive` flag to `read_until()` too, threaded through `run_plain_with_secret`'s
  read and (defensively, though not observed to actually leak - real password prompts
  suppress local echo) the `su`/`sudo` password read in `connect()`. Any log file
  written before this fix (this environment's `logs/run-Vulnerability_Plan_Wave9_*.log`
  and `logs/run-pretest-*.log` from 2026-09-21/22) still has the real password in
  cleartext on disk - rotate the Splunk admin credential and treat those specific files
  as sensitive until scrubbed/removed. Code fix only protects *future* runs - it can't
  retroactively fix a process already running with the old code loaded in memory.
- [x] **Fixed: a slow action in concurrent automatic mode (`--max-parallel-hosts` > 1)
  printed nothing at all between its `...` line and its result** (found live,
  2026-09-22, same session as above - a search-head-cluster captain's `stop_splunk`
  took ~4m45s vs ~30s for its non-captain peers, with total silence in between,
  indistinguishable from a hang; prompted a risky manual `splunk stop` on the real
  host from a separate session). The in-place animated-dots spinner only works
  single-threaded (it assumes it owns the terminal's last line), so it's deliberately
  off whenever more than one host may be printing at once - which left that path with
  no progress indication at all. `RunController._heartbeat` (a context manager
  wrapping that branch of `_attempt_with_retry`) now prints a new `still running (Ns)`
  line every 30s while a concurrent-mode action is still in flight.
- [x] **`clean_kvstore` confirmation asked once per run, not once per search head**
  (2026-09-22, operator feedback). Automatic mode used to ask "Also clean the KV
  store?" separately for every search head being started together (e.g. 3+ times for
  a 3-node group) - no reason for that, the answer is always the same decision for
  the whole run. `RunController._kvstore_clean_decision` (`None` until the first
  `clean_kvstore` action is reached, then locked in) is now checked before asking -
  the same `_console_lock`-held ask-if-needed pattern already used elsewhere also
  means concurrent hosts (`--max-parallel-hosts` > 1) can't race to ask twice.
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
  non-sensitive plan Excel referencing `inventory/hosts.yaml` hosts, since real wave
  Excels are gitignored) on every push and pull request. (2026-09-24: originally
  pointed at a separate `inventory/hosts.example.yaml` - removed, see "Dropped
  `inventory/hosts.example.yaml`" above - now points at the real inventory directly.)
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

- [x] **Automate StreamSets stop/start for `prdmilbbspkfw02`** (2026-09-22, both
  halves). New `ActionKind.STREAMSETS_PIPELINE` (`actions/types.py`) +
  `executor/streamsets_api.py` (command building, status extraction, HTTP-status
  check) + `RunController._execute_streamsets_pipeline` (`runner/controller.py`) -
  POST `.../{stop,start}?rev=0`, then poll `.../status?rev=0` until the pipeline
  actually reaches the target status (`STOPPED`/`RUNNING`) - the stop/start call
  itself only signals the transition, a real live test showed it stays
  `STOPPING`/`STARTING` for a few seconds first. Credentials via
  `STREAMSETS_API_USER`/`STREAMSETS_API_PASSWORD` in `.env`
  (`executor/credentials.py:load_streamsets_api_credentials`) - forced manual when
  unset, same pattern as CLUSTER_WAIT. The 5 real pipeline (label, id) pairs (RDK,
  ODP, ODP PODS, RDKV, ATD) replace both the old `disable_streamsets_pipelines` and
  `enable_streamsets_pipelines` manual_todo placeholders in `_prdmilbbspkfw02_sequences()`.
  Both directions verified live end to end against all 5 real production pipelines
  (stop then restart all 5, via the actual wired-in `RunController` code path) - all
  10 actions succeeded; real pipelines settled slower than the idle test pipeline
  (14-25s to stop, ~9.5s to start) but well within the timeout. 90s timeout / 5s poll
  interval, per the operator.
  - **2026-09-22, follow-up (operator feedback)**: the pipeline list was originally
    hardcoded as `STREAMSETS_PIPELINES` in `actions/sequences.py` - moved to
    `inventory/streamsets_pipelines.yaml` (same directory/pattern as `hosts.yaml`),
    loaded by `config/streamsets_pipelines.py:load_streamsets_pipelines` and threaded
    explicitly through `cli.py` → `build_run_plan` → `get_role_sequences` →
    `_prdmilbbspkfw02_sequences(streamsets_pipelines)`, matching how credentials/
    inventory are already threaded elsewhere rather than loaded lazily inside
    `sequences.py` (which would also risk a circular import with
    `config/inventory.py`). Editing the list no longer needs a code change/PR.
  **Still open**: the "scale ODP Preprocessing pipeline worker threads 5→8 before
  restart, revert after" detail from the original sketch (never confirmed with the
  operator, not built).

- [x] **Automate SH captain transfer / revert - transfer half live-verified,
  revert still pending** (2026-09-22, transfer live-tested 2026-09-24).
  `transfer_captain_static`/`revert_captain_dynamic`
  (`actions/sequences.py`) now build real `ActionKind.CAPTAIN_TRANSFER`/
  `CAPTAIN_REVERT` actions instead of `manual_todo` placeholders, executed by
  `RunController._execute_captain_transfer`/`_execute_captain_revert`
  (`runner/controller.py`):
  - **Transfer** (pre_group, before any per-host stop): `edit shcluster-config -mode
    captain` on the new captain (`Inventory.captain_candidate` on the site NOT being
    patched this wave - never touched during this wave's whole cycle, so the earlier
    "no live peer" concern below doesn't apply here), then `-mode member` on every
    other stretched-SH host across both sites **concurrently**
    (`--max-parallel-hosts` workers), then polls `shcluster-status --verbose` until
    the whole cluster confirms the static captain. No `-auth` needed for the
    `edit shcluster-config` calls themselves (confirmed from the original manual
    instructions), but the verification poll needs it, same as `CLUSTER_WAIT`.
  - **Revert** (post_group, after the last per-host start): `edit shcluster-config
    -election true` on every other member concurrently, then on the captain itself,
    then `bootstrap shcluster-captain -servers_list "..." -auth` from the captain
    (password via `run_plain_with_secret`, never literal), then polls until a
    dynamic captain is confirmed.
  - Both forced manual (`is_forced_manual`) when Splunk API credentials aren't
    configured, or when any cluster host has the splunk identity marked
    CyberArk-GUI-only (can't automate "touch every member" otherwise) - same
    manual-fallback guarantee CLAUDE.md requires.
  - `executor/splunk_cli.py` gained `parse_captain()` (the "Captain:" section's
    `label`/`dynamic_captain`/etc. fields), extracted from `preflight.py`'s
    previously-private regex so the pretest's read-only check and this verification
    share one implementation.
  - Prerequisite (2026-09-22, done first): the pretest now checks splunk-identity
    connectivity to the *whole* stretched-SH cluster (both sites), not just this
    wave's own site - see `preflight.py`'s "rest of the stretched SH cluster" block.
  - The earlier "no live peer to hand off captaincy to" concern (stretched-SH groups
    stop concurrently) turned out not to apply: the temporary captain lives on the
    untouched site the whole time, so it's never part of the concurrent stop. That
    finding was really about the unrelated Milano `mso` cluster incident, which has
    no transfer mechanism at all and is out of scope here.
  - Dry-run verified against the real prod wave: resolves to the correct temporary
    captain and the correct 10-host cluster list.
  - **Transfer live-tested against real production 2026-09-24**: ran the actual
    `captain_transfer_static` action (through the real `RunController._execute` path,
    same code a real wave uses) against the live 10-host stretched-SH cluster.
    Reported `SUCCESS` - `prdmilbbspksh01` (Milano, untouched site) confirmed as
    static captain with `dynamic_captain=0`. Follow-up read-only checks (per-host
    `shcluster-status`, not just the captain's own poll) found every one of the 10
    hosts individually confirmed pointing at the new captain immediately - but the
    *captain's own aggregated `Members:` list* only showed 8/10, then 9/10 a couple
    minutes later, before reaching 10/10 - real heartbeat/check-in lag after a
    captain change, not a failure; worth remembering if a future transfer looks
    "incomplete" right after running; give it a minute and re-check by aggregated
    member count, not just the captain-confirmation poll's own success flag (which
    only checks the captain host's own view, not full membership). One host
    (`prdmilbbspksh05`) briefly showed `service_ready_flag=0` when the others showed
    `1` - not investigated further since it self-resolved, but worth watching for on
    a future run. **Revert not yet tested** - explicit operator decision (2026-09-24)
    to leave the cluster on the static captain and hold off on reverting for now
    ("don't proceed to dynamic captain"), not a technical blocker. `.env` and the
    credentials needed are still in place, so `captain_revert_live.py`
    (not checked into git - ad hoc scratch script) is ready to run whenever the
    operator gives the go-ahead; nothing else needs preparing.

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
