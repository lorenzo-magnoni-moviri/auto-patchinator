"""Pre-patch pretest, run from cli.py:cmd_run before the operator is asked to
confirm a plan (LIVE runs only - --dry-run skips this, since dry-run's whole point
is "no SSH at all"; see DOCUMENTATION.md). Three layers, each gated on the previous
being available:

  1. SSH connectivity, splunk identity, scoped to exactly the hosts THIS plan
     touches - not the whole inventory (see plan_hostnames()). Runs up to
     max_parallel_hosts hosts concurrently (reuses --max-parallel-hosts, the same
     knob and PAS-gateway-tolerance caution as automatic-mode patching; default 1,
     sequential) - see executor/connectivity.py.
  2. Same, root identity. Root is provisioned per-request, not always-on (per the
     operator) - a FAIL here is routine on a day root wasn't requested, not
     necessarily a real problem; still worth showing so the operator remembers to
     request it before the actual patching window if root-identity actions are
     needed.
  3. If Splunk API credentials are configured (load_splunk_api_credentials() -
     optional; this tool works without them, same as everywhere else that consumes
     them) - for every distinct search-head role present in the plan (stretched and
     search_head_simple are separate Splunk search head clusters, checked
     separately - see _SEARCH_HEAD_ROLES). Run as the splunk identity over the same
     SSH/PAS path as everything else - no root needed for these two, since the
     Splunk admin credentials (not the OS identity) are what authorizes them:
       a. `splunk show shcluster-status --verbose -auth "<user>:<password>"` - who
          the current SHC captain is, and whether election is dynamic or static.
          Static outside of an active wave's captain-transfer window is a red flag -
          most likely a prior wave's captain-transfer was never reverted
          (revert_captain_dynamic).
       b. `splunk show kvstore-status --verbose -auth "<user>:<password>"` - this
          search head's own local KV store status.
     `-auth`, not `-u` - confirmed live (2026-09-16, see TODO.md): `-u` silently
     doesn't work as CLI-level auth for `splunk show` and falls back to an
     interactive login prompt even with a correct user:password, regardless of a
     cached session file being present or not. `-auth` is also what
     `captain_revert_dynamic`'s existing manual `bootstrap shcluster-captain`
     instructions already use (actions/sequences.py) - should have cross-checked
     that before guessing `-u`.
     The admin password is never sent as literal command text - see
     SSHConnection.run_plain_with_secret (executor/ssh.py), which sets it via a
     redacted shell-variable send first, so it never appears in logs/run-*.log.
     Requires SPLUNK_API_USER + SPLUNK_API_PASSWORD specifically (not a bare
     SPLUNK_API_TOKEN - the `splunk show` CLI commands above take `-auth user:pass`,
     not a token flag); skipped with a clear message if only a token is configured.

Never aborts the run itself - failures/warnings are printed clearly and the
operator still makes the call at the existing "Proceed with this plan?" prompt,
consistent with this tool's existing warn-don't-abort pattern elsewhere (e.g.
_load_team_steps's unmapped-rows warning).
"""
from __future__ import annotations

import re

from auto_patchinator.actions.sequences import SPLUNK_BIN, NodeRole
from auto_patchinator.actions.types import Identity
from auto_patchinator.config.inventory import Inventory
from auto_patchinator.executor.connectivity import STATUS_FAIL, STATUS_OK, STATUS_SKIP, check_connectivity
from auto_patchinator.executor.credentials import Credentials, SplunkApiCredentials
from auto_patchinator.executor.ssh import SSHConnection
from auto_patchinator.plan.run_plan import RunStepPlan
from auto_patchinator.term import green, red, yellow

# search_head_stretched and search_head_simple are two SEPARATE Splunk search head
# clusters in this environment (see DOCUMENTATION.md) - captain status is checked
# once per role present in the plan, not just once overall.
_SEARCH_HEAD_ROLES = (NodeRole.SEARCH_HEAD_STRETCHED, NodeRole.SEARCH_HEAD_SIMPLE)


def plan_hostnames(run_plan: list[RunStepPlan]) -> list[str]:
    """Every distinct hostname this plan actually touches, in first-seen order."""
    seen: dict[str, None] = {}
    for step in run_plan:
        for hostname in step.per_host_actions:
            seen.setdefault(hostname, None)
    return list(seen)


def run_pretest(
    run_plan: list[RunStepPlan],
    inventory: Inventory,
    credentials: Credentials,
    gateway_host: str | None,
    gateway_port: int,
    splunk_api_credentials: SplunkApiCredentials | None,
    max_parallel_hosts: int = 1,
) -> bool:
    """Returns True if every check came back clean, False if anything needs the
    operator's attention - never raises, never aborts (see module docstring).

    max_parallel_hosts > 1 checks that many hosts' connectivity concurrently (reuses
    --max-parallel-hosts - the same "how many hosts at once" knob and the same
    PAS-gateway-tolerance caution as automatic-mode patching). Only the connectivity
    checks are parallelized; the Splunk API checks already query only one host per
    search-head role (see _check_captain/_check_kvstore), so there's nothing to gain
    from threading those too."""
    hostnames = plan_hostnames(run_plan)
    host_items = [(h, inventory.get(h)) for h in hostnames]
    all_ok = True

    print(f"\n=== Pretest: connectivity, splunk identity ({len(host_items)} host(s)) ===")
    all_ok &= _run_connectivity_block(
        host_items, [Identity.SPLUNK], credentials, gateway_host, gateway_port, inventory, max_parallel_hosts
    )

    print(f"\n=== Pretest: connectivity, root identity ({len(host_items)} host(s)) ===")
    all_ok &= _run_connectivity_block(
        host_items, [Identity.ROOT], credentials, gateway_host, gateway_port, inventory, max_parallel_hosts
    )

    print("\n=== Pretest: Splunk API checks (captain/election, KV store) ===")
    if splunk_api_credentials is None:
        print(yellow(
            "SKIPPED - no Splunk API credentials configured (SPLUNK_API_USER + "
            "SPLUNK_API_PASSWORD in .env). These checks are optional."
        ))
    elif not (splunk_api_credentials.username and splunk_api_credentials.password):
        print(yellow(
            "SKIPPED - these checks run `splunk show ... -auth user:password` over SSH, which "
            "needs SPLUNK_API_USER + SPLUNK_API_PASSWORD specifically; only a "
            "SPLUNK_API_TOKEN is configured."
        ))
    else:
        by_role: dict[NodeRole, list[str]] = {}
        for hostname in hostnames:
            role = inventory.get(hostname).role
            if role in _SEARCH_HEAD_ROLES:
                by_role.setdefault(role, []).append(hostname)

        if not by_role:
            print("No search-head-role hosts in this plan - nothing to check.")
        else:
            for role, sh_hosts in by_role.items():
                print(f"\n  -- {role.value} ({len(sh_hosts)} host(s)) --")
                all_ok &= _check_captain(
                    sh_hosts[0], inventory, credentials, gateway_host, gateway_port, splunk_api_credentials
                )
                all_ok &= _check_kvstore(
                    sh_hosts[0], inventory, credentials, gateway_host, gateway_port, splunk_api_credentials
                )

    return all_ok


def _run_connectivity_block(
    host_items, identities, credentials, gateway_host, gateway_port, inventory, max_parallel_hosts=1
) -> bool:
    col_w = max((len(h) for h, _ in host_items), default=20)
    ok = True

    def on_result(result) -> None:
        nonlocal ok
        color = {STATUS_OK: green, STATUS_FAIL: red, STATUS_SKIP: yellow}[result.status]
        print(f"  {result.hostname:<{col_w}}  [{result.identity.value:<6}]  {color(result.status):<4}  {result.detail}")
        if result.status == STATUS_FAIL:
            ok = False

    check_connectivity(
        host_items, identities, credentials, gateway_host, gateway_port, inventory,
        on_result=on_result, max_workers=max_parallel_hosts,
    )
    return ok


def _splunk_show(
    hostname: str,
    inventory: Inventory,
    credentials: Credentials,
    gateway_host: str | None,
    gateway_port: int,
    splunk_api_credentials: SplunkApiCredentials,
    subcommand: str,
) -> str:
    """Runs `splunk show <subcommand> --verbose -auth "<user>:<password>"` as the
    splunk identity on `hostname` and returns its raw output, with CRLF line endings
    normalized to bare \\n (the PTY-captured output uses \\r\\n throughout - confirmed
    live, 2026-09-16, see TODO.md; a regex expecting a line boundary right after some
    captured text silently fails to match if there's a stray \\r in between, which is
    why this is normalized once here rather than patched into every regex downstream).
    The admin password is never sent as literal text - see run_plain_with_secret."""
    host = inventory.get(hostname)
    conn = SSHConnection(
        hostname, Identity.SPLUNK, host.role, credentials,
        pas_gateway=gateway_host,
        port=gateway_port,
        pas_domain_suffix=host.effective_pas_domain_suffix(inventory.pas_domain_suffix),
        pas_port=host.effective_pas_port(inventory.pas_port),
        splunk_su_command=host.splunk_su_command,
        pas_suffixes=inventory.pas_suffixes,
    )
    conn.connect()
    try:
        command = f'{SPLUNK_BIN} show {subcommand} --verbose -auth "{splunk_api_credentials.username}:$AP_SECRET"'
        result = conn.run_plain_with_secret(command, splunk_api_credentials.password, timeout=30)
        if not result.success:
            raise RuntimeError(f"{hostname}: `splunk show {subcommand}` exited {result.exit_code}")
        return result.output.replace("\r\n", "\n")
    finally:
        conn.close()


def _check_captain(
    hostname: str, inventory, credentials, gateway_host, gateway_port, splunk_api_credentials
) -> bool:
    try:
        output = _splunk_show(
            hostname, inventory, credentials, gateway_host, gateway_port, splunk_api_credentials,
            "shcluster-status",
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
        print(red(f"  FAIL  {exc}"))
        return False

    # Best-effort parse of `splunk show shcluster-status --verbose` text output -
    # scoped to the "Captain:" section only, so a "label :" line for some other
    # member further down isn't mistaken for the captain's. Field names/format
    # confirmed live (2026-09-16): a right-aligned "key : value" table using the
    # same raw field names as the REST content dict (lowercase, underscored) - not
    # human-prose labels like "Dynamic captain:".
    captain_section = re.search(r"Captain:\s*\n(.*?)(?:\n\s*\n|\nCluster Manager|\nMembers:|\Z)", output, re.DOTALL)
    section_text = captain_section.group(1) if captain_section else output
    label_match = re.search(r"\blabel\s*:\s*(\S+)", section_text)
    dynamic_match = re.search(r"\bdynamic_captain\s*:\s*(\d|true|false)", section_text, re.IGNORECASE)

    captain_label = label_match.group(1) if label_match else "?"
    if not dynamic_match:
        print(yellow(f"  ?     captain={captain_label}  could not find a 'dynamic_captain :' line "
                     "in the output - verify shcluster-status's text format against this Splunk "
                     "version (see the raw output below)."))
        print(f"  --- raw output ---\n{output}\n  --- end raw output ---")
        return True  # don't fail the pretest on an unrecognized-but-present response, just flag it

    dynamic = dynamic_match.group(1).lower() in ("1", "true")
    if dynamic:
        print(green(f"  OK    captain={captain_label}  election=dynamic"))
        return True
    print(red(
        f"  WARNING  captain={captain_label}  election=STATIC - dynamic election is "
        "disabled. If no wave is currently mid-patch, this most likely means a prior "
        "wave's captain-transfer was never reverted (revert_captain_dynamic) - "
        "investigate before proceeding."
    ))
    return False


def _check_kvstore(
    hostname: str, inventory, credentials, gateway_host, gateway_port, splunk_api_credentials
) -> bool:
    """One query against a single representative host - `kvstore-status --verbose`'s
    "KV store members:" section already reports every member's replicationStatus
    from that host's own view of the replica set, same "ask any member, get the
    whole cluster's picture" pattern as _check_captain. Confirmed live (2026-09-16,
    see TODO.md) - this is also how a real, live replication problem on a different
    member was first caught (a member reporting "Down" while every healthy member
    reports "Non-captain KV store member" or "KV store captain")."""
    try:
        output = _splunk_show(
            hostname, inventory, credentials, gateway_host, gateway_port, splunk_api_credentials,
            "kvstore-status",
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
        print(red(f"  FAIL  {exc}"))
        return False

    # Anchored to the exact line (not just the substring) - the output also has an
    # "Enabled KV store members:" section earlier, which contains "KV store members:"
    # as a substring but none of these hosts' replicationStatus (only backupInProgress/
    # guid/hostAndPort) - a plain \b-bounded search matches that one first and finds
    # nothing useful. Found live (2026-09-16, see TODO.md).
    members_section = re.search(r"^ KV store members:\s*\n(.*?)\Z", output, re.DOTALL | re.MULTILINE)
    if not members_section:
        print(yellow("  ?     could not find a 'KV store members:' section in the output - "
                     "verify kvstore-status's text format against this Splunk version."))
        print(f"  --- raw output ---\n{output}\n  --- end raw output ---")
        return True  # don't fail the pretest on an unrecognized-but-present response, just flag it

    # Each member is a tab-indented "host:port" line followed by its own indented
    # key:value block - split on that line pattern to get one block per member.
    blocks = re.split(r"\n\t(\S+:\d+)\n", "\n" + members_section.group(1))
    members = {blocks[i]: blocks[i + 1] for i in range(1, len(blocks) - 1, 2)}
    if not members:
        print(yellow("  ?     'KV store members:' section was present but empty/unparseable - "
                     "verify kvstore-status's text format against this Splunk version."))
        print(f"  --- raw output ---\n{output}\n  --- end raw output ---")
        return True

    ok = True
    col_w = max(len(m) for m in members)
    for member, block in members.items():
        status_match = re.search(r"replicationStatus\s*:\s*(.+)", block)
        status = status_match.group(1).strip() if status_match else "?"
        # Both observed-healthy values ("Non-captain KV store member", "KV store
        # captain") contain "captain" - anything else (e.g. "Down") doesn't.
        healthy = "captain" in status.lower()
        color = green if healthy else red
        print(color(f"  {member:<{col_w}}  {'OK' if healthy else 'FAIL':<4}  replicationStatus={status!r}"))
        if not healthy:
            ok = False
    return ok
