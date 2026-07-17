# auto-patchinator

Interactive CLI for the Sky Splunk Broadband team's monthly OS patch wave. Reads the
wave Vulnerability Plan Excel, resolves our team's steps into per-node stop/start
action sequences, and walks an operator through executing or confirming each one over
SSH via the PAS/CyberArk gateway.

**Full documentation:** see [`DOCUMENTATION.md`](DOCUMENTATION.md) for architecture,
configuration reference, every CLI flag, the three run modes in detail, native Windows
setup, troubleshooting, and known issues. [`TODO.md`](TODO.md) has the live backlog.

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env   # fill in AP_USERNAME / AP_PASSWORD, then: chmod 600 .env
```

Native Windows (no WSL)? See `DOCUMENTATION.md`'s Installation section.

Copy `inventory/hosts.example.yaml` to `inventory/hosts.yaml` and fill in your hosts if
you haven't already — see `DOCUMENTATION.md` for the field reference.

---

## Running

```bash
auto-patchinator run                       # LIVE, prod (the default)
auto-patchinator run --dry-run             # simulate only, no SSH - safe any time
auto-patchinator run --environment test    # target the test estate instead
auto-patchinator check-connectivity        # verify SSH to every host before a live run
```

`--excel` and `--inventory` are both optional: drop each month's wave file in a
`plans/` directory and the tool will find it; the inventory defaults to
`inventory/hosts.yaml`. `--pas-gateway` also isn't usually needed once it's set in
`hosts.yaml`.

At the start of each step, choose how to run it: `a` automatic, `t` task-by-task, `m`
manual guide (executes nothing, walks you through it by hand — no SSH command shown
since the team connects via WinSSH). Capital `A`/`T`/`M` locks that mode for the rest
of the run. An interrupted run can always be resumed by rerunning the same command.

---

## Tests

```bash
pip install -e ".[test]"
python -m pytest
```

`main` is branch-protected — a passing `test` CI check is required before merging.
