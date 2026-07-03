"""Entrypoint: `python -m auto_patchinator run ...` / `python -m auto_patchinator check-connectivity ...`"""
from __future__ import annotations

import argparse
import logging
import re
from datetime import datetime
from pathlib import Path

from auto_patchinator.actions.types import Identity
from auto_patchinator.config.inventory import load_inventory
from auto_patchinator.executor.credentials import prompt_credentials
from auto_patchinator.executor.ssh import EXIT_MARKER, PROMPT_MARKER, DryRunConnection, SSHConnection
from auto_patchinator.logging_setup import setup_run_logging
from auto_patchinator.term import green, red, yellow
from auto_patchinator.plan.action_mapping import map_team_steps
from auto_patchinator.plan.dependency import resolve_order
from auto_patchinator.plan.excel_parser import load_plan_sheet
from auto_patchinator.plan.run_plan import build_run_plan
from auto_patchinator.plan.wave_mapping import HOST_SHEET_NAME, load_wave_mapping_from_excel
from auto_patchinator.reports.report import write_report
from auto_patchinator.runner.controller import RunController, print_plan_summary
from auto_patchinator.state import store

DEFAULT_TEAM_FILTERS = ["AOM Sky CSO", "AOM Splunk Broadband"]
DEFAULT_INVENTORY_PATH = "inventory/hosts.yaml"
PLANS_DIR = "plans"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auto-patchinator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Resolve and walk through the patch plan")
    run_parser.add_argument(
        "--excel",
        default=None,
        help=f"Path to the wave Vulnerability_Plan .xlsx. If omitted, looks for .xlsx files "
             f"in '{PLANS_DIR}/' (then the current directory) and prompts you to pick one.",
    )
    run_parser.add_argument("--plan-sheet", default="Plan")
    run_parser.add_argument("--host-sheet", default=HOST_SHEET_NAME,
                            help=f"Sheet name for host→group mapping (default: '{HOST_SHEET_NAME}')")
    run_parser.add_argument(
        "--team-filter",
        nargs="+",
        default=DEFAULT_TEAM_FILTERS,
        metavar="FILTER",
        help=(
            "One or more Gruppo_referente values to match (case-insensitive). "
            f"Default: {DEFAULT_TEAM_FILTERS}"
        ),
    )
    run_parser.add_argument(
        "--inventory",
        default=None,
        help=f"Path to hosts.yaml (default: '{DEFAULT_INVENTORY_PATH}')",
    )
    run_parser.add_argument("--environment", default="prod", choices=["prod", "test"],
                            help="Which environment to target (default: prod)")
    run_parser.add_argument("--state-dir", default="state")
    run_parser.add_argument("--reports-dir", default="reports")
    run_parser.add_argument("--logs-dir", default="logs")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only simulate every action (no SSH) - for testing the plan. Default is LIVE execution.",
    )
    run_parser.add_argument(
        "--full-auto-mode",
        action="store_true",
        help="Run every step in automatic mode (one line per action) without asking per step; "
             "pauses only for manual confirmations and failures.",
    )
    run_parser.add_argument(
        "--pas-gateway",
        default=None,
        help="PAS/CyberArk SSH gateway, as 'host' or 'host:port' (default port 22). "
             "Falls back to 'pas_gateway' in the inventory YAML.",
    )

    conn_parser = subparsers.add_parser(
        "check-connectivity",
        help="SSH into every host in the inventory and verify the su step works",
    )
    conn_parser.add_argument(
        "--inventory",
        default=None,
        help=f"Path to hosts.yaml (default: '{DEFAULT_INVENTORY_PATH}')",
    )
    conn_parser.add_argument("--environment", default="prod", choices=["prod", "test"],
                             help="Which environment to target (default: prod)")
    conn_parser.add_argument(
        "--pas-gateway",
        default=None,
        help="PAS/CyberArk SSH gateway, as 'host' or 'host:port' (default port 22). "
             "Falls back to 'pas_gateway' in the inventory YAML.",
    )
    conn_parser.add_argument("--logs-dir", default="logs")
    conn_parser.add_argument(
        "--identity",
        choices=["splunk", "root", "all"],
        default="splunk",
        help="Which identity to test (default: splunk)",
    )
    conn_parser.add_argument(
        "--hosts",
        nargs="+",
        metavar="HOSTNAME",
        default=None,
        help="Test only these hosts (default: all hosts in inventory)",
    )

    return parser


def _resolve_inventory_path(explicit: str | None) -> str:
    """Fall back to DEFAULT_INVENTORY_PATH when --inventory is omitted."""
    path = explicit or DEFAULT_INVENTORY_PATH
    if not Path(path).exists():
        if explicit:
            raise SystemExit(f"Inventory file not found: {path}")
        raise SystemExit(
            f"No --inventory given and the default '{DEFAULT_INVENTORY_PATH}' does not "
            f"exist. Pass --inventory explicitly, or create '{DEFAULT_INVENTORY_PATH}' "
            "(copy from inventory/hosts.example.yaml)."
        )
    return path


def _discover_excel_candidates() -> list[Path]:
    """Look for .xlsx files, preferring the dedicated PLANS_DIR over the current directory."""
    for directory in (Path(PLANS_DIR), Path(".")):
        if directory.is_dir():
            candidates = sorted(
                directory.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            if candidates:
                return candidates
    return []


def _prompt_for_excel_path() -> str:
    """Interactively resolve --excel when it wasn't given on the command line."""
    candidates = _discover_excel_candidates()
    if not candidates:
        raw = input(
            f"No --excel given and no .xlsx files found in '{PLANS_DIR}/' or the current "
            "directory. Enter a path to the wave Excel: "
        ).strip()
        if not raw:
            raise SystemExit("No Excel file provided.")
        return raw

    print(f"No --excel given. Found {len(candidates)} .xlsx file(s), most recent first:")
    for i, candidate in enumerate(candidates, start=1):
        print(f"  [{i}] {candidate}")
    raw = input("Pick a number, or type a path (blank to cancel): ").strip()
    if not raw:
        raise SystemExit("No Excel file selected.")
    if raw.isdigit() and 1 <= int(raw) <= len(candidates):
        return str(candidates[int(raw) - 1])
    return raw


def _resolve_pas_gateway(cli_value: str | None, inventory) -> tuple[str | None, int]:
    """Pick the gateway from --pas-gateway or the inventory; parse optional ':port'."""
    value = cli_value or inventory.pas_gateway
    if not value:
        return None, 22
    host, sep, port = str(value).partition(":")
    return host, int(port) if sep else 22


def _load_team_steps(excel: str, plan_sheet: str, team_filter: list[str]):
    raw_steps = load_plan_sheet(excel, plan_sheet)
    mapped, unmapped = map_team_steps(raw_steps, team_filter)
    if unmapped:
        print(f"\nWARNING: {len(unmapped)} row(s) matched team filter but could not be "
              "parsed into a stop/start + group action - review manually:")
        for row in unmapped:
            print(f"  step {row.step}: {row.nome!r}")
    return mapped


def cmd_run(args: argparse.Namespace) -> None:
    args.excel = args.excel or _prompt_for_excel_path()
    args.inventory = _resolve_inventory_path(args.inventory)

    mapped = _load_team_steps(args.excel, args.plan_sheet, args.team_filter)
    ordered_steps = resolve_order(mapped)
    inventory = load_inventory(args.inventory, args.environment)
    wave_mapping = load_wave_mapping_from_excel(args.excel, inventory, sheet_name=args.host_sheet)
    if not wave_mapping:
        print(
            f"WARNING: no hosts found in '{args.host_sheet}' sheet that match the inventory — "
            "check that the Excel host sheet and hosts.yaml are consistent."
        )
    run_plan = build_run_plan(ordered_steps, wave_mapping, inventory)

    gateway_host, gateway_port = _resolve_pas_gateway(args.pas_gateway, inventory)

    print_plan_summary(run_plan, inventory)
    if args.dry_run:
        print(green("MODE: DRY-RUN - every action will only be simulated, nothing runs on any host."))
    else:
        print(red("MODE: LIVE - actions WILL be executed on the target hosts (use --dry-run to simulate)."))
        if gateway_host:
            print(f"PAS gateway: {gateway_host}:{gateway_port}")
        else:
            print(yellow("WARNING: no PAS gateway configured (--pas-gateway or 'pas_gateway' in the "
                         "inventory) - will SSH directly to each node, which PAS-fronted nodes refuse."))
    if input("Proceed with this plan? [y/N] ").strip().lower() != "y":
        print("Aborted, nothing was done.")
        return

    resumable = store.find_incomplete_run(args.state_dir)
    if resumable is not None:
        existing = store.load(resumable)
        if input(f"Resume incomplete run {existing.run_id} from {existing.updated_at}? [y/N] ").strip().lower() == "y":
            state = existing
        else:
            state = _new_run_state(args, run_plan)
    else:
        state = _new_run_state(args, run_plan)
    store.save(state, args.state_dir)
    store.prune_other_states(args.state_dir, state.run_id)

    log_path = setup_run_logging(args.logs_dir, state.run_id)
    print(f"Logging to {log_path}")
    log = logging.getLogger(__name__)
    log.info(
        "run %s | mode=%s | full_auto=%s | environment=%s | excel=%s | host_sheet=%s | team_filter=%s "
        "| pas_gateway=%s:%s",
        state.run_id, "dry-run" if args.dry_run else "LIVE", args.full_auto_mode, args.environment,
        args.excel, args.host_sheet, args.team_filter, gateway_host, gateway_port,
    )
    for p in run_plan:
        log.info("plan: step %s %s groups=%s hosts=%s", p.excel_step, p.verb.value, list(p.groups), list(p.hostnames))

    credentials = None if args.dry_run else prompt_credentials()

    def connection_factory(hostname: str, identity, role):
        if args.dry_run:
            return DryRunConnection(hostname, identity, role)
        host = inventory.get(hostname)
        return SSHConnection(
            hostname, identity, role, credentials,
            pas_gateway=gateway_host,
            port=gateway_port,
            pas_domain_suffix=host.effective_pas_domain_suffix(inventory.pas_domain_suffix),
            pas_port=host.effective_pas_port(inventory.pas_port),
            splunk_su_command=host.splunk_su_command,
            pas_suffixes=inventory.pas_suffixes,
        )

    controller = RunController(
        run_plan, state, args.state_dir, connection_factory, inventory,
        dry_run=args.dry_run, full_auto=args.full_auto_mode,
    )
    controller.run()

    report_path = write_report(state, run_plan, args.reports_dir)
    print(f"\nReport written to {report_path}")


_ANSI_ESC = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07)')


def _extract_whoami(raw: str) -> str:
    """Pull the username out of a raw `whoami` command result buffer."""
    clean = _ANSI_ESC.sub("", raw)
    for line in clean.splitlines():
        line = line.strip()
        if (line
                and line not in ("whoami",)
                and EXIT_MARKER not in line
                and PROMPT_MARKER not in line
                and not line.startswith("whoami;")):
            return line
    return "?"


def cmd_check_connectivity(args: argparse.Namespace) -> None:
    args.inventory = _resolve_inventory_path(args.inventory)
    log_path = setup_run_logging(args.logs_dir, f"check-connectivity-{datetime.now():%Y%m%dT%H%M%S}")
    print(f"Logging to {log_path}")
    inventory = load_inventory(args.inventory, args.environment)
    gateway_host, gateway_port = _resolve_pas_gateway(args.pas_gateway, inventory)
    if gateway_host:
        print(f"PAS gateway: {gateway_host}:{gateway_port}")
    else:
        print("WARNING: no PAS gateway configured (--pas-gateway or 'pas_gateway' in the "
              "inventory) - will SSH directly to each node, which PAS-fronted nodes refuse.")
    credentials = prompt_credentials()

    identities: list[Identity] = []
    if args.identity in ("splunk", "all"):
        identities.append(Identity.SPLUNK)
    if args.identity in ("root", "all"):
        identities.append(Identity.ROOT)

    host_items = list(inventory.hosts.items())
    if args.hosts:
        unknown = set(args.hosts) - inventory.hosts.keys()
        if unknown:
            print(f"WARNING: host(s) not in inventory, skipping: {', '.join(sorted(unknown))}")
        host_items = [(h, inventory.hosts[h]) for h in args.hosts if h in inventory.hosts]

    col_w = max((len(h) for h, _ in host_items), default=20)
    print(f"\nTesting {len(host_items)} host(s) with identity={args.identity} ...\n")

    _STATUS_OK   = "OK  "
    _STATUS_FAIL = "FAIL"
    _STATUS_SKIP = "SKIP"

    results: list[tuple[str, Identity, str, str]] = []

    for hostname, host in host_items:
        for identity in identities:
            if host.is_manual_only(identity):
                line = f"  {hostname:<{col_w}}  [{identity.value:<6}]  {_STATUS_SKIP}  manual-only identity"
                print(line)
                results.append((hostname, identity, _STATUS_SKIP, "manual-only identity"))
                continue

            print(f"  {hostname:<{col_w}}  [{identity.value:<6}]  ...  ", end="", flush=True)
            conn = SSHConnection(
                hostname, identity, host.role, credentials,
                pas_gateway=gateway_host,
                port=gateway_port,
                pas_domain_suffix=host.effective_pas_domain_suffix(inventory.pas_domain_suffix),
                pas_port=host.effective_pas_port(inventory.pas_port),
                splunk_su_command=host.splunk_su_command,
                pas_suffixes=inventory.pas_suffixes,
            )
            try:
                conn.connect()
                try:
                    result = conn.run_plain("whoami", timeout=15)
                finally:
                    conn.close()
                who = _extract_whoami(result.output)
                if result.success:
                    print(f"\r  {hostname:<{col_w}}  [{identity.value:<6}]  {_STATUS_OK}  whoami={who!r}")
                    results.append((hostname, identity, _STATUS_OK, f"whoami={who!r}"))
                else:
                    print(f"\r  {hostname:<{col_w}}  [{identity.value:<6}]  {_STATUS_FAIL}  exit {result.exit_code}")
                    results.append((hostname, identity, _STATUS_FAIL, f"exit {result.exit_code}"))
            except Exception as exc:
                msg = str(exc).splitlines()[0]  # first line only - keeps table readable
                print(f"\r  {hostname:<{col_w}}  [{identity.value:<6}]  {_STATUS_FAIL}  {msg}")
                results.append((hostname, identity, _STATUS_FAIL, msg))

    ok   = sum(1 for *_, s, _ in results if s == _STATUS_OK)
    fail = sum(1 for *_, s, _ in results if s == _STATUS_FAIL)
    skip = sum(1 for *_, s, _ in results if s == _STATUS_SKIP)
    print(f"\n{ok} OK  {fail} FAIL  {skip} SKIP")
    if fail:
        print("\nFailed hosts:")
        for hostname, identity, status, detail in results:
            if status == _STATUS_FAIL:
                print(f"  {hostname}  [{identity.value}]  {detail}")


def _new_run_state(args: argparse.Namespace, run_plan):
    run_id = f"{Path(args.excel).stem}-{datetime.now():%Y%m%dT%H%M%S}"
    return store.build_initial_state(run_id, args.excel, args.host_sheet, run_plan)


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.command == "run":
        cmd_run(args)
    elif args.command == "check-connectivity":
        cmd_check_connectivity(args)


if __name__ == "__main__":
    main()
