"""Read-only, structured health checks for the `health-check` subcommand - entirely
new, additive code. Deliberately does NOT import preflight.py's private helpers
(_splunk_show/_check_kvstore) even though captain_switch.py (an uncommitted personal
script) already does - this module is committed, tested code, so it stays
self-contained instead of coupling to another module's internals. It only reuses the
stable, already-multiply-used PUBLIC parsing helpers in executor/splunk_cli.py
(parse_captain, parse_shcluster_members, member_health, find_member,
parse_member_info, scheduling_active - the exact same functions CLUSTER_WAIT,
SCHEDULER_WAIT, CAPTAIN_TRANSFER/REVERT and the pretest already use) and
executor/connectivity.py's check_connectivity, unmodified.

Each check function returns a list[CheckResult] - plain, formatting-agnostic data.
Two renderers (terminal, markdown - see report.py) consume the same results, so a new
check never needs to know anything about how it's displayed.

Adding a new check for an existing group is exactly one line: append to that group's
*_CHECKS tuple. Each entry is (name, fn) where fn(ctx) -> list[CheckResult].
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from auto_patchinator.actions.sequences import NodeRole, splunk_bin_for
from auto_patchinator.actions.types import Identity
from auto_patchinator.config.inventory import Inventory
from auto_patchinator.executor.connectivity import STATUS_OK, check_connectivity
from auto_patchinator.executor.credentials import Credentials, SplunkApiCredentials
from auto_patchinator.executor.splunk_cli import (
    find_member,
    member_health,
    normalize_line_endings,
    parse_captain,
    parse_member_info,
    parse_shcluster_members,
    scheduling_active,
)
from auto_patchinator.executor.ssh import SSHConnection

GROUP_SEARCH_HEADS = "search_heads"
GROUP_FORWARDERS = "forwarders"
GROUP_INDEXERS = "indexers"


@dataclass(frozen=True)
class CheckResult:
    group: str
    check: str
    scope: str  # a hostname, or a cluster-wide label like "stretched cluster (via sh01)"
    passed: bool | None  # None = couldn't determine (error reaching the host/command)
    detail: str


@dataclass(frozen=True)
class CheckContext:
    inventory: Inventory
    credentials: Credentials
    gateway_host: str | None
    gateway_port: int
    splunk_api_credentials: SplunkApiCredentials | None
    max_parallel_hosts: int = 3


def _run_splunk_command(ctx: CheckContext, hostname: str, command_suffix: str) -> str:
    """Runs `<splunk_bin> <command_suffix> -auth "<user>:<password>"` as the splunk
    identity on hostname, returns raw output with CRLF normalized. Self-contained -
    see module docstring for why this isn't reused from preflight.py."""
    host = ctx.inventory.get(hostname)
    conn = SSHConnection(
        hostname, Identity.SPLUNK, host.role, ctx.credentials,
        pas_gateway=ctx.gateway_host,
        port=ctx.gateway_port,
        pas_domain_suffix=host.effective_pas_domain_suffix(ctx.inventory.pas_domain_suffix),
        pas_port=host.effective_pas_port(ctx.inventory.pas_port),
        splunk_su_command=host.splunk_su_command,
        pas_suffixes=ctx.inventory.pas_suffixes,
    )
    conn.connect()
    try:
        creds = ctx.splunk_api_credentials
        splunk_bin = splunk_bin_for(host.role)
        command = f'{splunk_bin} {command_suffix} -auth "{creds.username}:$AP_SECRET"'
        result = conn.run_plain_with_secret(command, creds.password, timeout=30)
        return normalize_line_endings(result.output)
    finally:
        conn.close()


def _needs_splunk_api_credentials(ctx: CheckContext, group: str, check: str, scope: str) -> CheckResult | None:
    """Every check below needs Splunk admin API credentials (-auth). Returns a single
    explanatory CheckResult if they're not configured, None if they are - mirrors
    is_forced_manual's same gate elsewhere in this codebase, but as data instead of
    blocking the whole run."""
    creds = ctx.splunk_api_credentials
    if creds and creds.username and creds.password:
        return None
    return CheckResult(
        group, check, scope, None,
        "SPLUNK_API_USER/SPLUNK_API_PASSWORD not configured in .env - skipped",
    )


def _hostnames_by_role(ctx: CheckContext, role: NodeRole) -> list[str]:
    return sorted(h for h, host in ctx.inventory.hosts.items() if host.role == role)


# ---------------------------------------------------------------------------
# Search heads
# ---------------------------------------------------------------------------

def _shc_membership(ctx: CheckContext, hostnames: list[str]) -> list[CheckResult]:
    results = []
    for hostname in hostnames:
        guard = _needs_splunk_api_credentials(ctx, GROUP_SEARCH_HEADS, "cluster_membership", hostname)
        if guard:
            results.append(guard)
            continue
        try:
            output = _run_splunk_command(ctx, hostname, "show shcluster-status --verbose")
            fields = find_member(parse_shcluster_members(output), hostname)
            healthy, detail = member_health(fields)
            results.append(CheckResult(GROUP_SEARCH_HEADS, "cluster_membership", hostname, healthy, detail))
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
            results.append(CheckResult(
                GROUP_SEARCH_HEADS, "cluster_membership", hostname, None,
                str(exc).splitlines()[0] if str(exc) else type(exc).__name__,
            ))
    return results


def _shc_captain_and_election(ctx: CheckContext, cluster_label: str, hostnames: list[str]) -> list[CheckResult]:
    """Captain/election is cluster-wide info - same answer regardless of which member
    you ask (same pattern preflight.py's captain check and CAPTAIN_TRANSFER/REVERT's
    verification poll already rely on) - so this asks just one reachable member."""
    scope = f"{cluster_label} (asked via first reachable member)"
    guard = _needs_splunk_api_credentials(ctx, GROUP_SEARCH_HEADS, "captain_and_election", scope)
    if guard:
        return [guard]
    last_error = "no host in this cluster answered"
    for hostname in hostnames:
        try:
            output = _run_splunk_command(ctx, hostname, "show shcluster-status --verbose")
            fields = parse_captain(output)
            label = fields.get("label")
            dynamic_raw = fields.get("dynamic_captain")
            if not label or dynamic_raw is None:
                last_error = f"no 'Captain:' section found (via {hostname})"
                continue
            mode = "dynamic" if dynamic_raw.lower() in ("1", "true") else "STATIC"
            detail = f"captain={label}  election={mode}  (asked via {hostname})"
            # A static captain is reported as informational, not a failure - transfer/
            # revert deliberately leave the cluster on a static captain for a while
            # during a wave; only a cluster with NO readable captain is a real failure.
            return [CheckResult(GROUP_SEARCH_HEADS, "captain_and_election", f"{cluster_label}", True, detail)]
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return [CheckResult(GROUP_SEARCH_HEADS, "captain_and_election", f"{cluster_label}", None, last_error)]


def _shc_scheduling(ctx: CheckContext, hostnames: list[str]) -> list[CheckResult]:
    results = []
    for hostname in hostnames:
        guard = _needs_splunk_api_credentials(ctx, GROUP_SEARCH_HEADS, "scheduling", hostname)
        if guard:
            results.append(guard)
            continue
        try:
            output = _run_splunk_command(ctx, hostname, "list shcluster-member-info")
            active, detail = scheduling_active(parse_member_info(output))
            results.append(CheckResult(GROUP_SEARCH_HEADS, "scheduling", hostname, active, detail))
        except Exception as exc:  # noqa: BLE001
            results.append(CheckResult(
                GROUP_SEARCH_HEADS, "scheduling", hostname, None,
                str(exc).splitlines()[0] if str(exc) else type(exc).__name__,
            ))
    return results


def _parse_kvstore_members(output: str) -> dict[str, dict[str, str]]:
    """Parse `splunk show kvstore-status --verbose`'s "KV store members:" section -
    same algorithm as preflight.py's _check_kvstore (reimplemented here rather than
    imported - see module docstring), returns {host:port: {field: value}}."""
    section = re.search(r"^ KV store members:\s*\n(.*?)\Z", output, re.DOTALL | re.MULTILINE)
    if not section:
        return {}
    blocks = re.split(r"\n\t(\S+:\d+)\n", "\n" + section.group(1))
    members: dict[str, dict[str, str]] = {}
    for i in range(1, len(blocks) - 1, 2):
        member_key, block = blocks[i], blocks[i + 1]
        fields = dict(re.findall(r"^\s+(\S+)\s*:\s*(.*)$", block, re.MULTILINE))
        members[member_key] = fields
    return members


def _shc_kvstore(ctx: CheckContext, cluster_label: str, hostnames: list[str]) -> list[CheckResult]:
    """KV store replica set is cluster-wide info too - ask any one reachable member,
    same pattern as the captain check above."""
    guard = _needs_splunk_api_credentials(ctx, GROUP_SEARCH_HEADS, "kvstore_sync", cluster_label)
    if guard:
        return [guard]
    last_error = "no host in this cluster answered"
    for hostname in hostnames:
        try:
            output = _run_splunk_command(ctx, hostname, "show kvstore-status --verbose")
            members = _parse_kvstore_members(output)
            if not members:
                last_error = f"no 'KV store members:' section found (via {hostname}) - unrecognized output format"
                continue
            unhealthy = []
            for member_key, fields in members.items():
                status = fields.get("replicationStatus", "?")
                # Both healthy values ("KV store captain", "Non-captain KV store
                # member") contain "captain" - anything else (e.g. "Down") doesn't -
                # same rule preflight.py's _check_kvstore already validated live.
                if "captain" not in status.lower():
                    unhealthy.append(f"{member_key}={status!r}")
            if unhealthy:
                detail = f"out of sync: {', '.join(unhealthy)}  (asked via {hostname})"
                return [CheckResult(GROUP_SEARCH_HEADS, "kvstore_sync", cluster_label, False, detail)]
            detail = f"{len(members)} member(s) all in sync  (asked via {hostname})"
            return [CheckResult(GROUP_SEARCH_HEADS, "kvstore_sync", cluster_label, True, detail)]
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return [CheckResult(GROUP_SEARCH_HEADS, "kvstore_sync", cluster_label, None, last_error)]


def _shc_cluster_membership_check(ctx: CheckContext) -> list[CheckResult]:
    stretched = _hostnames_by_role(ctx, NodeRole.SEARCH_HEAD_STRETCHED)
    simple = _hostnames_by_role(ctx, NodeRole.SEARCH_HEAD_SIMPLE)
    return _shc_membership(ctx, stretched + simple)


def _shc_captain_and_election_check(ctx: CheckContext) -> list[CheckResult]:
    results: list[CheckResult] = []
    stretched = _hostnames_by_role(ctx, NodeRole.SEARCH_HEAD_STRETCHED)
    simple = _hostnames_by_role(ctx, NodeRole.SEARCH_HEAD_SIMPLE)
    if stretched:
        results += _shc_captain_and_election(ctx, "stretched cluster", stretched)
    if simple:
        results += _shc_captain_and_election(ctx, "simple cluster", simple)
    return results


def _shc_scheduling_check(ctx: CheckContext) -> list[CheckResult]:
    stretched = _hostnames_by_role(ctx, NodeRole.SEARCH_HEAD_STRETCHED)
    simple = _hostnames_by_role(ctx, NodeRole.SEARCH_HEAD_SIMPLE)
    return _shc_scheduling(ctx, stretched + simple)


def _shc_kvstore_check(ctx: CheckContext) -> list[CheckResult]:
    results: list[CheckResult] = []
    stretched = _hostnames_by_role(ctx, NodeRole.SEARCH_HEAD_STRETCHED)
    simple = _hostnames_by_role(ctx, NodeRole.SEARCH_HEAD_SIMPLE)
    if stretched:
        results += _shc_kvstore(ctx, "stretched cluster", stretched)
    if simple:
        results += _shc_kvstore(ctx, "simple cluster", simple)
    return results


def _fw_up_check(ctx: CheckContext) -> list[CheckResult]:
    hostnames = _hostnames_by_role(ctx, NodeRole.FORWARDER)
    host_items = [(h, ctx.inventory.get(h)) for h in hostnames]
    conn_results = check_connectivity(
        host_items, [Identity.SPLUNK], ctx.credentials, ctx.gateway_host, ctx.gateway_port,
        ctx.inventory, max_workers=ctx.max_parallel_hosts,
    )
    return [
        CheckResult(GROUP_FORWARDERS, "up", r.hostname, r.status == STATUS_OK, r.detail)
        for r in conn_results
    ]


# ---------------------------------------------------------------------------
# Indexers
# ---------------------------------------------------------------------------

# Co-located with the Milano deployer role; confirmed live against real production
# 2026-10-28. This fleet runs exactly one multi-site indexer cluster (site1=milano,
# site2=roma per `show cluster-status --verbose`'s own peer listing - a single query
# to this one host reports on every indexer on both sites), unlike the two separate
# search-head clusters above - so there's one cluster manager to query, not one per
# role/site.
INDEXER_CLUSTER_MANAGER_HOSTNAME = "prdmilbbspkdp01"

_IX_ANSI_ESC = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07)')
_IX_GUID = r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}'
_IX_PEER_HEADER = re.compile(rf'^\s*(\S+)\s+({_IX_GUID})\s+(\S+)\s*$')
_IX_PEER_FIELD = re.compile(r'^\s*(Searchable|Status)\s+(\S+)\s*$')
# Dotted "<label> .... YES|NO" lines in the leading "Pre-flight check" block - tolerant
# of the box-drawing prefix (e.g. "├──────") and the ANSI color wrapping the value.
_IX_PREFLIGHT_LINE = re.compile(r'^[^A-Za-z]*([A-Za-z].*?)\s*\.{2,}\s*(YES|NO)\s*$')


def _ix_query_cluster_status(ctx: CheckContext) -> str:
    """Runs `splunk show cluster-status --verbose -auth` once against the indexer
    cluster manager - there's only one, so unlike the SHC checks above there's no
    "ask whichever member answers first" fallback; a failure here just means this one
    host (recorded in the CheckResult's scope) couldn't be reached."""
    return _run_splunk_command(ctx, INDEXER_CLUSTER_MANAGER_HOSTNAME, "show cluster-status --verbose")


def _ix_parse_peers(output: str) -> dict[str, dict[str, str]]:
    """Parse `splunk show cluster-status --verbose`'s per-peer blocks (hostname, GUID,
    site, then indented Searchable/Status/Bucket Count/Splunk Version fields) -
    distinguished from the per-index bucket-count blocks further down the same output
    by the GUID on the header line, which only peer blocks have. Returns
    {hostname: {field: value}}."""
    clean = _IX_ANSI_ESC.sub("", normalize_line_endings(output))
    peers: dict[str, dict[str, str]] = {}
    for block in re.split(r'\n\s*\n', clean):
        lines = block.strip("\n").splitlines()
        if not lines:
            continue
        header = _IX_PEER_HEADER.match(lines[0])
        if not header:
            continue
        hostname, _guid, site = header.groups()
        fields = {"site": site}
        for line in lines[1:]:
            field = _IX_PEER_FIELD.match(line)
            if field:
                fields[field.group(1).lower()] = field.group(2)
        peers[hostname] = fields
    return peers


def _ix_parse_preflight_fields(output: str) -> dict[str, str]:
    """Parse the leading "Pre-flight check successful / ├── ... / └── ..." block into
    {lowercased label: 'YES'|'NO'}."""
    clean = _IX_ANSI_ESC.sub("", normalize_line_endings(output))
    fields = {}
    for line in clean.splitlines():
        m = _IX_PREFLIGHT_LINE.match(line)
        if m:
            fields[m.group(1).strip().lower()] = m.group(2)
    return fields


def _ix_cluster_membership_check(ctx: CheckContext) -> list[CheckResult]:
    guard = _needs_splunk_api_credentials(ctx, GROUP_INDEXERS, "cluster_membership", INDEXER_CLUSTER_MANAGER_HOSTNAME)
    if guard:
        return [guard]
    try:
        output = _ix_query_cluster_status(ctx)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return [CheckResult(GROUP_INDEXERS, "cluster_membership", INDEXER_CLUSTER_MANAGER_HOSTNAME, None, detail)]
    peers = _ix_parse_peers(output)
    if not peers:
        return [CheckResult(
            GROUP_INDEXERS, "cluster_membership", INDEXER_CLUSTER_MANAGER_HOSTNAME, None,
            "no peer entries found in cluster-status output - unrecognized output format",
        )]
    results = []
    for hostname, fields in sorted(peers.items()):
        status = fields.get("status", "?")
        searchable = fields.get("searchable", "?")
        healthy = status.lower() == "up"
        detail = f"status={status!r}  searchable={searchable!r}  site={fields.get('site', '?')}"
        results.append(CheckResult(GROUP_INDEXERS, "cluster_membership", hostname, healthy, detail))
    return results


def _ix_factors_check(ctx: CheckContext) -> list[CheckResult]:
    guard = _needs_splunk_api_credentials(ctx, GROUP_INDEXERS, "search_replication_factor", INDEXER_CLUSTER_MANAGER_HOSTNAME)
    if guard:
        return [guard]
    try:
        output = _ix_query_cluster_status(ctx)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return [CheckResult(GROUP_INDEXERS, "search_replication_factor", INDEXER_CLUSTER_MANAGER_HOSTNAME, None, detail)]
    fields = _ix_parse_preflight_fields(output)
    if not fields:
        return [CheckResult(
            GROUP_INDEXERS, "search_replication_factor", INDEXER_CLUSTER_MANAGER_HOSTNAME, None,
            "no pre-flight check fields found in cluster-status output - unrecognized output format",
        )]
    results = []
    # "site replication/search factor met" on a multi-site cluster (this fleet's
    # shape, confirmed live); matched by substring so a differently-worded
    # single-site "replication/search factor met" would still be picked up.
    for label, needle in (("replication factor", "replication factor met"), ("search factor", "search factor met")):
        match = next((v for k, v in fields.items() if needle in k), None)
        if match is None:
            results.append(CheckResult(
                GROUP_INDEXERS, "search_replication_factor", label, None, f"'{needle}' not found in output",
            ))
        else:
            results.append(CheckResult(
                GROUP_INDEXERS, "search_replication_factor", label, match == "YES", f"{needle}={match}",
            ))
    return results


# ---------------------------------------------------------------------------
# Registry - the one place a new check gets wired in. Each entry is
# (check_name, fn), fn(ctx) -> list[CheckResult]. Adding a check for an existing
# group is exactly one line: append a tuple to that group's list below - the CLI,
# the terminal renderer and the markdown report all walk this registry generically
# and need no changes. A brand new group (e.g. indexers, once ready) is the same:
# one new tuple in GROUPS, pointing at that group's own list of (name, fn) checks.
# ---------------------------------------------------------------------------

SEARCH_HEAD_CHECKS: tuple[tuple[str, object], ...] = (
    ("cluster_membership", _shc_cluster_membership_check),
    ("captain_and_election", _shc_captain_and_election_check),
    ("scheduling", _shc_scheduling_check),
    ("kvstore_sync", _shc_kvstore_check),
)

FORWARDER_CHECKS: tuple[tuple[str, object], ...] = (
    ("up", _fw_up_check),
)

INDEXER_CHECKS: tuple[tuple[str, object], ...] = (
    ("cluster_membership", _ix_cluster_membership_check),
    ("search_replication_factor", _ix_factors_check),
)

GROUPS: tuple[tuple[str, str, tuple[tuple[str, object], ...]], ...] = (
    (GROUP_SEARCH_HEADS, "Search heads", SEARCH_HEAD_CHECKS),
    (GROUP_FORWARDERS, "Forwarders", FORWARDER_CHECKS),
    (GROUP_INDEXERS, "Indexers", INDEXER_CHECKS),
)


def run_group_checks(ctx: CheckContext, group_checks: tuple[tuple[str, object], ...]) -> list[CheckResult]:
    """Runs every (name, fn) in group_checks and flattens the results - the one
    generic driver every group/check goes through, so a new check never needs its
    own wiring beyond the registry entry above."""
    results: list[CheckResult] = []
    for _name, fn in group_checks:
        results += fn(ctx)
    return results
