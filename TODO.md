# TODO

Open items, roughly in priority order.

---

## Must-do before first production live run

- [ ] **Verify PAS su flow on prod nodes** — the PTY marker protocol and sudo/su
  password-prompt detection in `executor/ssh.py` have only been tested against the
  test environment (`pas.tst.spk`). The production gateway (`pas.prd.spk`) may behave
  differently (different banner, different prompt timing). Do a `check-connectivity`
  against a few prod nodes before the first live wave.

- [ ] **StreamSets stop/start commands for `prdmilbbspkfw02`** — the
  `disable_streamsets_pipelines` and `enable_streamsets_pipelines` actions in
  `actions/sequences.py → _prdmilbbspkfw02_sequences()` are `manual_todo` placeholders.
  Replace with the exact CLI commands once confirmed.

- [ ] **Root identity connectivity** — `check-connectivity` has only been run with
  `--identity splunk`. Run with `--identity root` against both prod and test to verify
  the `pas.prd.spk.root` / `pas.tst.spk.root` path works end to end.

---

## Short-term improvements

- [ ] **Automate `send_mail`** — every Excel step ends with a manual `send_mail` action.
  Implement SMTP sending (credentials from `.env`, recipients from `inventory/hosts.yaml`
  or a separate config) and change the action kind from `MANUAL` to `PLAIN`.

- [ ] **Indexer S&R factor check** — after `start_splunk` on an indexer, poll
  `splunk show cluster-bundle-status` or `splunk list cluster-peers` until the
  search/replication factor is satisfied before proceeding to the next node.
  Currently skipped (deferred to v2).

- [ ] **Wave 5 / NoProd Excel parsing** — a `Host List NO IT Wave 5 No Prod.xlsx`
  file is present locally. Verify it parses correctly with the current column-alias
  logic (check sheet name pattern and column names match what `wave_mapping.py` expects).

- [ ] **Test wave mockup** — `Vulnerability_Plan_TEST_Mockup.xlsx` exists but has not
  been fully exercised end-to-end with `--environment test`. Run a full dry-run and
  then a live run against the test nodes to validate the complete flow.

- [ ] **`waves/` directory cleanup** — `waves/wave7.yaml` is a leftover from the old
  manual group→host YAML approach (removed in favour of reading directly from the Excel
  host sheet). Safe to delete once confirmed no one is referencing it.

---

## Nice-to-have / v2

- [ ] **GitHub Actions CI** — run `auto-patchinator run --excel <mockup.xlsx>
  --inventory inventory/hosts.example.yaml` in dry-run mode on every push to catch
  import errors and plan-resolution regressions without needing a test suite.

- [ ] **Unit tests** — at minimum: `excel_parser`, `action_mapping`, `dependency`,
  `wave_mapping`, `run_plan`. The SSH layer is harder to test without a real gateway;
  `DryRunConnection` already handles that for integration testing.

- [ ] **Roma nodes in test inventory** — the test cluster currently only has Milano
  nodes. Add Roma test equivalents if/when they exist, so the stretched-SH captain
  transfer path can be tested end-to-end.

- [ ] **`--environment` in report** — the markdown report (`reports/report.py`) does
  not currently record which environment was targeted. Add it to the report header.

- [ ] **Auto-detect PAS gateway from inventory** — `hosts.yaml` already has
  `pas_gateway: pas.sky.local`; wire it into `cli.py` as the default so `--pas-gateway`
  is only needed to override it.

- [ ] **Parallel host actions within a group** — currently hosts within one Excel group
  are processed sequentially. For groups with multiple hosts (e.g. all 5 Roma SHs in
  one step) this could be parallelised with `ThreadPoolExecutor`, gated by a
  configurable concurrency limit.
