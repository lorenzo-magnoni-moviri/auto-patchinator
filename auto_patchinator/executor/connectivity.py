"""Shared SSH connectivity-check primitive: opens a connection and runs `whoami`,
per (host, identity). Used by both the standalone `check-connectivity` subcommand
(cli.py) and the pre-patch pretest (preflight.py) - extracted here so the two don't
duplicate the same loop.
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from auto_patchinator.actions.types import Identity
from auto_patchinator.config.inventory import Host, Inventory
from auto_patchinator.executor.credentials import Credentials
from auto_patchinator.executor.ssh import EXIT_MARKER, PROMPT_MARKER, SSHConnection

_ANSI_ESC = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07)')

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"


@dataclass(frozen=True)
class ConnectivityResult:
    hostname: str
    identity: Identity
    status: str  # STATUS_OK | STATUS_FAIL | STATUS_SKIP
    detail: str


def extract_whoami(raw: str) -> str:
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


def _check_one(
    hostname: str,
    host: Host,
    identities: list[Identity],
    credentials: Credentials,
    gateway_host: str | None,
    gateway_port: int,
    inventory: Inventory,
) -> list[ConnectivityResult]:
    """All of one host's identity checks, in order. Only ever called by the one
    thread handling this host - no shared state touched here."""
    results = []
    for identity in identities:
        if host.is_manual_only(identity):
            results.append(ConnectivityResult(hostname, identity, STATUS_SKIP, "manual-only identity"))
            continue

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
                cmd_result = conn.run_plain("whoami", timeout=15)
            finally:
                conn.close()
            who = extract_whoami(cmd_result.output)
            if cmd_result.success:
                result = ConnectivityResult(hostname, identity, STATUS_OK, f"whoami={who!r}")
            else:
                result = ConnectivityResult(hostname, identity, STATUS_FAIL, f"exit {cmd_result.exit_code}")
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
            msg = str(exc).splitlines()[0]  # first line only - keeps output readable
            result = ConnectivityResult(hostname, identity, STATUS_FAIL, msg)

        results.append(result)
    return results


def check_connectivity(
    host_items: list[tuple[str, Host]],
    identities: list[Identity],
    credentials: Credentials,
    gateway_host: str | None,
    gateway_port: int,
    inventory: Inventory,
    on_result=None,
    max_workers: int = 1,
) -> list[ConnectivityResult]:
    """Try (host, identity) pairs, in order within each host. `on_result`, if given,
    is called with each ConnectivityResult as soon as it's known - lets a caller
    print live progress instead of waiting for the whole batch.

    max_workers > 1 checks that many hosts concurrently (one thread per host; each
    host's own identities still run in order within its own thread) - default 1
    (sequential, unchanged behavior) for the same reason --max-parallel-hosts
    defaults to 1 for automatic-mode patching: the PAS/CyberArk gateway's tolerance
    for concurrent sessions from one account isn't established. `on_result` calls
    are serialized regardless of max_workers, so concurrent hosts' printed lines
    never interleave mid-line."""
    if max_workers <= 1 or len(host_items) <= 1:
        results: list[ConnectivityResult] = []
        for hostname, host in host_items:
            for result in _check_one(hostname, host, identities, credentials, gateway_host, gateway_port, inventory):
                results.append(result)
                if on_result:
                    on_result(result)
        return results

    console_lock = threading.Lock()

    def check_one_and_report(hostname: str, host: Host) -> list[ConnectivityResult]:
        host_results = _check_one(hostname, host, identities, credentials, gateway_host, gateway_port, inventory)
        if on_result:
            for result in host_results:
                with console_lock:
                    on_result(result)
        return host_results

    ordered_results: list[list[ConnectivityResult] | None] = [None] * len(host_items)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(check_one_and_report, hostname, host): index
            for index, (hostname, host) in enumerate(host_items)
        }
        for future, index in futures.items():
            ordered_results[index] = future.result()

    return [result for host_results in ordered_results for result in host_results]
