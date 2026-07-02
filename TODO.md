# TODO

Open items, roughly in priority order.

---

## Must-do before first production live run

- [ ] **Verify PAS su flow on prod nodes** — the PTY marker protocol and sudo/su
  password-prompt detection in `executor/ssh.py` have only been tested against the
  test environment (`pas.tst.spk`). The production gateway (`pas.prd.spk`) may behave
  differently (different banner, different prompt timing). Do a `check-connectivity`
  against a few prod nodes before the first live wave.

- [ ] **Root identity connectivity** — `check-connectivity` has only been run with
  `--identity splunk`. Run with `--identity root` against both prod and test to verify
  the `pas.prd.spk.root` / `pas.tst.spk.root` path works end to end.

- [ ] **Full end-to-end test on test environment** — run a complete live wave against
  the `tst*` nodes using `Vulnerability_Plan_TEST_Mockup.xlsx` (dry-run first, then
  live) to validate the full pipeline — Excel parsing, SSH sessions, action sequences,
  state persistence, resume, and report — before touching production.

---

## Short-term improvements

- [ ] **Default inventory path** — avoid having to type `--inventory inventory/hosts.yaml`
  every run. Fall back to `inventory/hosts.yaml` relative to the working directory if
  `--inventory` is omitted, and error clearly if the file is not found.

- [ ] **Interactive Excel prompt** — if `--excel` is not provided on the command line,
  instead of exiting with a usage error, prompt the operator to enter a file path
  (or pick from `.xlsx` files found in the current directory). Useful during live runs
  where the operator may not remember the exact filename.

- [ ] **Splunk API credentials** — several upcoming automations (captain transfer,
  cluster status checks) require a Splunk admin token or username/password. Add
  `SPLUNK_API_USER` and `SPLUNK_API_PASSWORD` (or a token `SPLUNK_API_TOKEN`) to
  `.env.example` and load them via the existing `credentials.py` / dotenv mechanism.
  **Never hardcode these values** — in particular, the `bootstrap shcluster-captain`
  admin password must never appear in source or git history.

- [ ] **Automate StreamSets stop/start for `prdmilbbspkfw02`** — replace the current
  `manual_todo` placeholders in `actions/sequences.py → _prdmilbbspkfw02_sequences()`
  with StreamSets REST API calls:
  - Stop all pipelines before the Splunk stop sequence.
  - After the Splunk start sequence, poll the StreamSets API until all pipelines report
    `RUNNING` and metrics confirm data is flowing again.
  - Temporarily scale the ODP Preprocessing pipeline worker threads from 5 → 8 before
    restart (to absorb the backlog) and revert to 5 once throughput normalises.
  StreamSets API base URL and credentials should come from `.env`.

- [ ] **Automate SH captain transfer / revert** — replace the
  `transfer_captain_static` and `revert_captain_dynamic` manual steps with Splunk REST
  API calls (requires the Splunk API credentials above):
  - Transfer: `POST /services/shcluster/captain/transfer` on the current captain, or
    `edit shcluster-config -mode captain` on the target.
  - Revert: re-enable dynamic election on all members, then bootstrap from the captain.
  Keep both as manual fallbacks if the API call fails.

- [ ] **Cluster status validation via Splunk API** — add automated checks at key
  points in the sequence using the Splunk REST API (requires API credentials above):
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
  verification (blocked on the Splunk API credentials above). Deferred to v2 but
  dependency is now the API auth item, not implementation complexity.

- [ ] **`waves/` directory cleanup** — `waves/wave7.yaml` is a leftover from the old
  manual group→host YAML approach (removed in favour of reading directly from the Excel
  host sheet). Safe to delete once confirmed no one is referencing it.

---

## Nice-to-have / v2

- [ ] **Auto-detect PAS gateway from inventory** — `hosts.yaml` already has
  `pas_gateway: pas.sky.local`; wire it into `cli.py` as the default so `--pas-gateway`
  is only needed to override it.

- [ ] **`--environment` in report** — the markdown report (`reports/report.py`) does
  not currently record which environment was targeted. Add it to the report header.

- [ ] **Roma nodes in test inventory** — the test cluster currently only has Milano
  nodes. Add Roma test equivalents if/when they exist, so the stretched-SH captain
  transfer path can be tested end-to-end.

- [ ] **GitHub Actions CI** — run `auto-patchinator run --excel <mockup.xlsx>
  --inventory inventory/hosts.example.yaml` in dry-run mode on every push to catch
  import errors and plan-resolution regressions without needing a test suite.

- [ ] **Unit tests** — at minimum: `excel_parser`, `action_mapping`, `dependency`,
  `wave_mapping`, `run_plan`. The SSH layer is harder to test without a real gateway;
  `DryRunConnection` already handles that for integration testing.

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
