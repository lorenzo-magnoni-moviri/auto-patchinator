"""Entrypoint: `python -m auto_patchinator run ...` / `python -m auto_patchinator check-connectivity ...`"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from auto_patchinator.actions.types import Identity
from auto_patchinator.config.inventory import load_inventory
from auto_patchinator.executor.connectivity import STATUS_FAIL, STATUS_OK, STATUS_SKIP, ConnectivityResult, check_connectivity
from auto_patchinator.executor.credentials import load_splunk_api_credentials, prompt_credentials
from auto_patchinator.executor.ssh import DryRunConnection, SSHConnection
from auto_patchinator.logging_setup import setup_run_logging
from auto_patchinator.preflight import run_pretest
from auto_patchinator.term import green, red, yellow
from auto_patchinator.plan.action_mapping import map_team_steps
from auto_patchinator.plan.dependency import resolve_order
from auto_patchinator.plan.excel_parser import load_plan_sheet
from auto_patchinator.plan.run_plan import build_run_plan
from auto_patchinator.plan.wave_mapping import HOST_SHEET_NAME, load_wave_mapping_from_excel
from auto_patchinator.reports.report import write_report
from auto_patchinator.runner.controller import RunController, print_plan_summary
from auto_patchinator.state import store

DEFAULT_TEAM_FILTERS = ["AOM Sky CSO", "AOM Splunk Broadband", "Splunk Broadband"]
DEFAULT_INVENTORY_PATH = "inventory/hosts.yaml"
PLANS_DIR = "plans"


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-patchinator",
        epilog="For a subcommand's full option list, run: auto-patchinator <command> --help "
               "(e.g. auto-patchinator run --help)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Resolve and walk through the patch plan")
    run_parser.add_argument(
        "--excel",
        default=None,
        help=f"Path to the wave Vulnerability_Plan .xlsx. If omitted, looks for .xlsx files "
             f"in '{PLANS_DIR}/' (then the current directory) and prompts you to pick one.",
    )
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
        "--verbose",
        action="store_true",
        help="Show the reasoning behind each action in manual guide mode (the 'why' line), "
             "not just the command. Skips the interactive prompt for this (normally asked "
             "once, the first time manual guide mode is used).",
    )
    run_parser.add_argument(
        "--max-parallel-hosts",
        type=int,
        default=3,
        metavar="N",
        help="In automatic mode, run this many hosts' action sequences concurrently within "
             "each step instead of one at a time; also used by the pre-patch pretest's "
             "connectivity checks (default: 3; pass 1 for the old fully-sequential behavior). "
             "Only affects automatic mode and the pretest's connectivity checks; task-by-task, "
             "manual guide, and the pretest's Splunk API checks are always sequential. Has "
             "no effect on a step/check with only one host.",
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


def _resolve_pas_gateway(inventory) -> tuple[str | None, int]:
    """Parse 'pas_gateway' from the inventory YAML (optional ':port')."""
    value = inventory.pas_gateway
    if not value:
        return None, 22
    host, sep, port = str(value).partition(":")
    return host, int(port) if sep else 22


def _excel_format_error(exc: ValueError) -> None:
    """Every sheet lookup (plan sheet, host sheet) raises ValueError rather than
    guessing when the workbook doesn't match what this script expects - re-raise as a
    SystemExit with that guidance spelled out, instead of a bare traceback."""
    raise SystemExit(
        f"{exc}\n\n"
        "The wave Excel must follow the expected format for this script to understand "
        "it: a 'Plan' sheet with the step rows, and a host->group sheet named "
        "'List Host NO IT' (small naming variations, e.g. incidental whitespace or a "
        "'NO IT' substring, are tolerated automatically - but an ambiguous or missing "
        "sheet is never guessed at). Fix the workbook and rerun."
    )


def _load_team_steps(excel: str, team_filter: list[str]):
    try:
        raw_steps = load_plan_sheet(excel)
    except ValueError as exc:
        _excel_format_error(exc)
    mapped, unmapped = map_team_steps(raw_steps, team_filter)
    if not mapped:
        labels = sorted({r.gruppo_referente for r in raw_steps if r.gruppo_referente})
        print(
            f"\nWARNING: no rows matched team filter {team_filter} - the resolved plan will "
            f"be empty. Gruppo_referente values found in this sheet: {labels}"
        )
    if unmapped:
        print(f"\nWARNING: {len(unmapped)} row(s) matched team filter but could not be "
              "parsed into a stop/start + group action - review manually:")
        for row in unmapped:
            print(f"  step {row.step}: {row.nome!r}")
    return mapped


def cmd_run(args: argparse.Namespace) -> None:
    if args.max_parallel_hosts < 1:
        raise SystemExit(f"--max-parallel-hosts must be at least 1, got {args.max_parallel_hosts}")
    args.excel = args.excel or _prompt_for_excel_path()
    args.inventory = _resolve_inventory_path(args.inventory)

    mapped = _load_team_steps(args.excel, args.team_filter)
    ordered_steps = resolve_order(mapped)
    inventory = load_inventory(args.inventory, args.environment)
    try:
        wave_mapping = load_wave_mapping_from_excel(args.excel, inventory)
    except ValueError as exc:
        _excel_format_error(exc)
    if not wave_mapping:
        print(
            f"WARNING: no hosts found in '{HOST_SHEET_NAME}' sheet that match the inventory — "
            "check that the Excel host sheet and hosts.yaml are consistent."
        )
    run_plan = build_run_plan(ordered_steps, wave_mapping, inventory)

    gateway_host, gateway_port = _resolve_pas_gateway(inventory)
    # Just an env/`.env` read, never prompts - safe to resolve once, early, and reuse
    # for the plan summary, the pretest, and the controller (CLUSTER_WAIT actions).
    splunk_api_credentials = load_splunk_api_credentials()

    print_plan_summary(run_plan, inventory, splunk_api_credentials)
    if args.dry_run:
        print(green("MODE: DRY-RUN - every action will only be simulated, nothing runs on any host."))
        credentials = None
    else:
        print(red("MODE: LIVE - actions WILL be executed on the target hosts (use --dry-run to simulate)."))
        if gateway_host:
            print(f"PAS gateway: {gateway_host}:{gateway_port}")
        else:
            print(yellow("WARNING: no PAS gateway configured ('pas_gateway' in the inventory) - "
                         "will SSH directly to each node, which PAS-fronted nodes refuse."))
        credentials = prompt_credentials()
        # Pretest is LIVE-only - --dry-run's whole point is "no SSH at all" (see
        # DOCUMENTATION.md), which a connectivity check would violate. Never aborts
        # by itself - see preflight.py's module docstring; the operator still decides
        # at the "Proceed with this plan?" prompt below, now informed by the results.
        # Own log file (like check-connectivity) rather than the eventual run's -
        # state.run_id isn't known yet at this point (resume/state setup happens
        # after the confirm prompt below), and this stays a single, low-risk addition
        # instead of reordering that flow. setup_run_logging's file handler stays
        # attached afterward, so the run's own log (once set up) picks up everything
        # from here on too - some overlap between the two files, nothing lost.
        pretest_log_path = setup_run_logging(args.logs_dir, f"pretest-{datetime.now():%Y%m%dT%H%M%S}")
        print(f"Logging pretest to {pretest_log_path}")
        pretest_ok = run_pretest(
            run_plan, inventory, credentials, gateway_host, gateway_port, splunk_api_credentials,
            max_parallel_hosts=args.max_parallel_hosts,
        )
        if not pretest_ok:
            print(red("\nOne or more pretest checks need attention - review the output above."))
    if input("\nProceed with this plan? [y/N] ").strip().lower() != "y":
        print("Aborted, nothing was done.")
        return

    # None = not yet decided; the controller asks once, the first time manual guide mode
    # is actually used, instead of upfront here where it may turn out to be irrelevant.
    show_explanations = True if args.verbose else None

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
        "run %s | mode=%s | full_auto=%s | verbose=%s | max_parallel_hosts=%s | environment=%s | "
        "excel=%s | host_sheet=%s | team_filter=%s | pas_gateway=%s:%s",
        state.run_id, "dry-run" if args.dry_run else "LIVE", args.full_auto_mode, args.verbose,
        args.max_parallel_hosts, args.environment, args.excel, HOST_SHEET_NAME, args.team_filter,
        gateway_host, gateway_port,
    )
    for p in run_plan:
        log.info("plan: step %s %s groups=%s hosts=%s", p.excel_step, p.verb.value, list(p.groups), list(p.hostnames))

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
        dry_run=args.dry_run, full_auto=args.full_auto_mode, show_explanations=show_explanations,
        max_parallel_hosts=args.max_parallel_hosts, splunk_api_credentials=splunk_api_credentials,
    )
    controller.run()

    report_path = write_report(state, run_plan, args.reports_dir)
    print(f"\nReport written to {report_path}")


def cmd_check_connectivity(args: argparse.Namespace) -> None:
    args.inventory = _resolve_inventory_path(args.inventory)
    log_path = setup_run_logging(args.logs_dir, f"check-connectivity-{datetime.now():%Y%m%dT%H%M%S}")
    print(f"Logging to {log_path}")
    inventory = load_inventory(args.inventory, args.environment)
    gateway_host, gateway_port = _resolve_pas_gateway(inventory)
    if gateway_host:
        print(f"PAS gateway: {gateway_host}:{gateway_port}")
    else:
        print("WARNING: no PAS gateway configured ('pas_gateway' in the inventory) - will SSH "
              "directly to each node, which PAS-fronted nodes refuse.")
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

    def on_result(result: ConnectivityResult) -> None:
        status = f"{result.status:<4}"
        print(f"  {result.hostname:<{col_w}}  [{result.identity.value:<6}]  {status}  {result.detail}")

    results = check_connectivity(
        host_items, identities, credentials, gateway_host, gateway_port, inventory, on_result=on_result
    )

    ok   = sum(1 for r in results if r.status == STATUS_OK)
    fail = sum(1 for r in results if r.status == STATUS_FAIL)
    skip = sum(1 for r in results if r.status == STATUS_SKIP)
    print(f"\n{ok} OK  {fail} FAIL  {skip} SKIP")
    if fail:
        print("\nFailed hosts:")
        for r in results:
            if r.status == STATUS_FAIL:
                print(f"  {r.hostname}  [{r.identity.value}]  {r.detail}")


def _new_run_state(args: argparse.Namespace, run_plan):
    run_id = f"{Path(args.excel).stem}-{datetime.now():%Y%m%dT%H%M%S}"
    return store.build_initial_state(run_id, args.excel, HOST_SHEET_NAME, run_plan)


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.command == "run":
        cmd_run(args)
    elif args.command == "check-connectivity":
        cmd_check_connectivity(args)


if __name__ == "__main__":
    main()
