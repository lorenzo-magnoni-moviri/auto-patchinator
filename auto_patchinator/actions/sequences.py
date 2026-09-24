"""Per-role action sequence templates, with per-host overrides.

The Excel plan models "Stop application Group N" and "Start application Group N" as two
*separate* steps, often far apart in time and gated by their own (possibly cross-team)
dependencies - the OS patching itself happens between them, outside this tool. So each
role exposes a STOP half and a START half independently, rather than one bundled
stop->patch->start sequence:

  - stop_pre_group:    once per group, before any node's stop_per_node runs (e.g. cluster-
                       wide captain transfer to the other site).
  - stop_per_node:     per node, triggered by the Excel "Stop application Group N" step.
  - stop_post_group:   once per group, after every node's stop_per_node has finished.
  - start_pre_group / start_per_node / start_post_group: mirror image, triggered by the
                       Excel "Start application Group N" step.

A systemd override (drop-in) file now holds the customizations that used to be edited
directly into the unit, so `splunk enable boot-start -systemd-managed 1 ...` regenerating
the unit template no longer loses anything - no more backup/restore of the unit file
around it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from auto_patchinator.actions.types import Action, ActionKind, ExpectStep, Identity

SPLUNK_BIN = "/opt/splunk/bin/splunk"
SPLUNK_BIN_INDEXER = "/splunkdata/splunk/bin/splunk"

# Shared scratch directory present on every node - used to stash the crontab backup
# made during the stop half, restored during the start half.
APPLHOME_DIR = "/appl/home/splunk"
CRONTAB_BACKUP = f"{APPLHOME_DIR}/crontab.backup"


class NodeRole(str, Enum):
    DEPLOYER = "deployer"
    INDEXER = "indexer"
    FORWARDER = "forwarder"
    SEARCH_HEAD_SIMPLE = "search_head_simple"
    SEARCH_HEAD_STRETCHED = "search_head_stretched"


SPLUNK_BIN_BY_ROLE = {role: SPLUNK_BIN for role in NodeRole} | {NodeRole.INDEXER: SPLUNK_BIN_INDEXER}


@dataclass(frozen=True)
class RoleSequences:
    stop_pre_group: tuple[Action, ...] = field(default_factory=tuple)
    stop_per_node: tuple[Action, ...] = field(default_factory=tuple)
    stop_post_group: tuple[Action, ...] = field(default_factory=tuple)
    start_pre_group: tuple[Action, ...] = field(default_factory=tuple)
    start_per_node: tuple[Action, ...] = field(default_factory=tuple)
    start_post_group: tuple[Action, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Building blocks shared by every role
# ---------------------------------------------------------------------------

def stop_splunk(splunk_bin: str) -> Action:
    return Action(
        name="stop_splunk",
        kind=ActionKind.PLAIN,
        identity=Identity.SPLUNK,
        command=f"sudo {splunk_bin} stop",
        timeout_seconds=900,  # a busy node can take well over the 60s default to shut down
    )


def start_splunk(splunk_bin: str) -> Action:
    return Action(
        name="start_splunk",
        kind=ActionKind.PLAIN,
        identity=Identity.SPLUNK,
        command=f"sudo {splunk_bin} start",
        timeout_seconds=900,  # first start after patching can be slow (migrations, preflight)
    )


def disable_boot_start(splunk_bin: str) -> Action:
    return Action(
        name="disable_boot_start",
        kind=ActionKind.PLAIN,
        identity=Identity.SPLUNK,
        command=f"sudo {splunk_bin} disable boot-start",
        note="If this node's splunk user has no sudoers entry for this, rerun as root instead (no sudo).",
    )


def enable_boot_start(splunk_bin: str) -> Action:
    return Action(
        name="enable_boot_start",
        kind=ActionKind.PLAIN,
        identity=Identity.ROOT,
        command=f"{splunk_bin} enable boot-start -systemd-managed 1 -user splunk -group splunk",
        note="Regenerates the systemd unit file from a template - the systemd override drop-in survives this.",
    )


def daemon_reload() -> Action:
    return Action(
        name="daemon_reload",
        kind=ActionKind.PLAIN,
        identity=Identity.ROOT,
        command="systemctl daemon-reload",
    )


def clean_kvstore(splunk_bin: str) -> Action:
    return Action(
        name="clean_kvstore",
        kind=ActionKind.PLAIN,
        identity=Identity.SPLUNK,
        # --answer-yes: without it, splunk interactively confirms ("This action will
        # permanently drop app key/value-store database ... [y/n]?") and this being a
        # PLAIN (non-interactive) action means nothing ever answers it - the command
        # just hangs until the action's timeout, found live against a real
        # search_head_stretched host (2026-09-09; see TODO.md).
        command=f"{splunk_bin} clean kvstore --local --answer-yes",
        note="--answer-yes skips the interactive drop confirmation - a PLAIN action can't answer it.",
    )


def wait_for_shcluster_member_healthy(splunk_bin: str) -> Action:
    """Poll this search head's own search-head-cluster membership status after it
    restarts, until it reports healthy or the timeout elapses - the search-head half
    of TODO.md's "Cluster status validation via Splunk API" (indexer cluster is out
    of scope here). Executed via `splunk show shcluster-status --verbose -auth
    "<user>:<password>"` over the same SSH session as everything else
    (runner/controller.py's _execute_cluster_wait; parsing in executor/splunk_cli.py)
    - `-auth`, not `-u` (see clean_kvstore-adjacent findings in TODO.md, and
    captain_revert_dynamic's own -auth usage above). Needs Splunk API credentials
    (SPLUNK_API_USER + SPLUNK_API_PASSWORD in .env); forced manual (verify by hand)
    if they aren't configured - see runner/controller.py's is_forced_manual."""
    return Action(
        name="wait_for_shcluster_member_healthy",
        kind=ActionKind.CLUSTER_WAIT,
        identity=Identity.SPLUNK,
        poll_interval_seconds=15,
        timeout_seconds=600,  # cluster rejoin + artifact sync after a restart can take a while
        note=(
            "Confirm this host's own search-head-cluster membership is healthy before "
            "moving on - splunk show shcluster-status --verbose finds it in the "
            "'Members:' section:\n"
            f"   {splunk_bin} show shcluster-status --verbose -auth <user>:<password>\n"
            "Look for THIS host's own entry - confirm status: Up, out_of_sync_node: 0, "
            "restart_required: 0."
        ),
    )


def backup_crontab() -> Action:
    return Action(
        name="backup_crontab",
        kind=ActionKind.PLAIN,
        identity=Identity.SPLUNK,
        command=f"crontab -l > {CRONTAB_BACKUP}",
        note="Save the current crontab BEFORE deleting it - enable_crontab restores from this file.",
    )


def disable_crontab() -> Action:
    return Action(
        name="disable_crontab",
        kind=ActionKind.INTERACTIVE,
        identity=Identity.SPLUNK,
        script=(
            ExpectStep(send="crontab -r", expect="really delete"),
            ExpectStep(send="yes"),
        ),
        note="splunk user's crontab is aliased to 'crontab -i', deletion needs confirmation.",
    )


def enable_crontab() -> Action:
    return Action(
        name="enable_crontab",
        kind=ActionKind.PLAIN,
        identity=Identity.SPLUNK,
        command=f"crontab {CRONTAB_BACKUP}",
        note="Restore the crontab saved by backup_crontab.",
    )


def wait(seconds: int, reason: str) -> Action:
    return Action(name=f"wait_{seconds}s", kind=ActionKind.WAIT, wait_seconds=seconds, note=reason)


def manual_todo(name: str, note: str) -> Action:
    """Placeholder for steps whose exact command is not known yet (flagged in the plan)."""
    return Action(name=name, kind=ActionKind.MANUAL, note=f"TODO: {note}")


# ---------------------------------------------------------------------------
# Role sequences
# ---------------------------------------------------------------------------

def _default_stop(splunk_bin: str) -> tuple[Action, ...]:
    return (stop_splunk(splunk_bin), disable_boot_start(splunk_bin))


def _default_start(splunk_bin: str) -> tuple[Action, ...]:
    return (enable_boot_start(splunk_bin), daemon_reload(), start_splunk(splunk_bin))


def deployer_sequences() -> RoleSequences:
    return RoleSequences(stop_per_node=_default_stop(SPLUNK_BIN), start_per_node=_default_start(SPLUNK_BIN))


def indexer_sequences() -> RoleSequences:
    # S&R factor wait/check after restart is deferred to v2 - see project plan.
    return RoleSequences(
        stop_per_node=_default_stop(SPLUNK_BIN_INDEXER),
        start_per_node=_default_start(SPLUNK_BIN_INDEXER),
    )


def search_head_simple_sequences() -> RoleSequences:
    # No KVStore clean and no captain handling for the 3-node extra cluster - it does
    # have its own SHC captain/election (confirmed live, 2026-09-16 - see TODO.md),
    # just not one this tool coordinates a transfer/revert around.
    return RoleSequences(
        stop_per_node=_default_stop(SPLUNK_BIN),
        start_per_node=(*_default_start(SPLUNK_BIN), wait_for_shcluster_member_healthy(SPLUNK_BIN)),
    )


# Zero live data yet on how long a real cluster-wide election/bootstrap takes to
# settle - starting with the same conservative values as CLUSTER_WAIT (a similar
# "cluster needs to converge" operation) and refining after a live rehearsal, same as
# STREAMSETS_PIPELINE's poll interval/timeout were tuned after seeing real timings.
_CAPTAIN_POLL_INTERVAL_SECONDS = 15
_CAPTAIN_TIMEOUT_SECONDS = 600


def captain_transfer_static(new_captain_host: str, cluster_hostnames: tuple[str, ...]) -> Action:
    """Injected once before the first stretched-SH stop in a wave (pre_group). Sets a
    static captain on new_captain_host - a concrete host on the site NOT being patched
    this wave (Inventory.captain_candidate) - before any per-host stop action runs, so
    it's never touched during this wave's whole stop/patch/start cycle. Then polls
    until the whole cluster (asked via new_captain_host itself) confirms the static
    captain is actually in effect. cluster_hostnames is every stretched SH hostname
    across both sites (Inventory.stretched_sh_hostnames()) - "every other member"
    needs to be pointed at the new captain too, run via splunk identity only (no
    admin API credentials needed - edit shcluster-config is a local config change,
    not a REST-auth'd operation, unlike revert's bootstrap step)."""
    return Action(
        name="transfer_captain_static",
        kind=ActionKind.CAPTAIN_TRANSFER,
        identity=Identity.SPLUNK,
        captain_host=new_captain_host,
        cluster_hostnames=cluster_hostnames,
        poll_interval_seconds=_CAPTAIN_POLL_INTERVAL_SECONDS,
        timeout_seconds=_CAPTAIN_TIMEOUT_SECONDS,
        note=f"Set a static captain on {new_captain_host} (on the site not being "
             "patched this wave) and confirm the whole cluster recognizes it before "
             "proceeding.",
    )


def captain_revert_dynamic(captain_host: str, cluster_hostnames: tuple[str, ...]) -> Action:
    """Injected once after the last stretched-SH start in a wave (post_group).
    Re-enables dynamic election on every member except captain_host (the same host
    captain_transfer_static set), then on captain_host itself, then bootstraps from
    captain_host with the full cluster server list so a real election can pick the
    ongoing captain. The bootstrap step needs Splunk admin API credentials
    (-auth admin:<password>, sent via run_plain_with_secret - never a literal
    command) - is_forced_manual routes this to a manual confirmation if they aren't
    configured."""
    return Action(
        name="revert_captain_dynamic",
        kind=ActionKind.CAPTAIN_REVERT,
        identity=Identity.SPLUNK,
        captain_host=captain_host,
        cluster_hostnames=cluster_hostnames,
        poll_interval_seconds=_CAPTAIN_POLL_INTERVAL_SECONDS,
        timeout_seconds=_CAPTAIN_TIMEOUT_SECONDS,
        note=f"Re-enable dynamic election cluster-wide and bootstrap from {captain_host} "
             "so a real election can pick the ongoing captain.",
    )


def search_head_stretched_sequences() -> RoleSequences:
    # Captain transfer/revert are injected once per wave (first stop / last start)
    # by build_run_plan, not repeated per group. clean_kvstore must run before
    # start_splunk, not after - the KV store is cleaned while splunk is still down.
    # It's only needed when the node was down long enough for its local copy to go
    # stale, so automatic mode asks the operator per host, right before it runs
    # (runner/controller.py's _confirm_kvstore_clean_auto) rather than always forcing it.
    return RoleSequences(
        stop_per_node=_default_stop(SPLUNK_BIN),
        start_per_node=(
            enable_boot_start(SPLUNK_BIN),
            daemon_reload(),
            clean_kvstore(SPLUNK_BIN),
            start_splunk(SPLUNK_BIN),
            wait_for_shcluster_member_healthy(SPLUNK_BIN),
        ),
    )


def forwarder_sequences() -> RoleSequences:
    return RoleSequences(
        stop_per_node=(backup_crontab(), disable_crontab(), *_default_stop(SPLUNK_BIN)),
        start_per_node=(*_default_start(SPLUNK_BIN), enable_crontab()),
    )


# ---------------------------------------------------------------------------
# Per-host overrides - keyed by hostname, take precedence over the role default.
# ---------------------------------------------------------------------------

# poll interval: a real live stop settled to STOPPED in ~4.6s for an idle test
# pipeline (2026-09-22) - 5s keeps polling cheap without being right on top of that.
# timeout: operator-specified, generous margin over the observed settle time for a
# real pipeline under load.
_STREAMSETS_POLL_INTERVAL_SECONDS = 5
_STREAMSETS_TIMEOUT_SECONDS = 90


def stop_streamsets_pipeline(label: str, pipeline_id: str) -> Action:
    """Stop one StreamSets pipeline via the Data Collector REST API and poll until it
    actually reaches STOPPED (the stop call itself only signals the transition - see
    executor/streamsets_api.py). Runs as splunk - the API is reached over localhost,
    no elevated OS privilege needed, matching the rest of this host's sequence."""
    slug = label.lower().replace(" ", "_")
    return Action(
        name=f"stop_streamsets_pipeline_{slug}",
        kind=ActionKind.STREAMSETS_PIPELINE,
        identity=Identity.SPLUNK,
        pipeline_id=pipeline_id,
        pipeline_label=label,
        target_status="STOPPED",
        poll_interval_seconds=_STREAMSETS_POLL_INTERVAL_SECONDS,
        timeout_seconds=_STREAMSETS_TIMEOUT_SECONDS,
        note=f"Stop the {label} StreamSets pipeline via the Data Collector REST API "
             "and confirm it reaches STOPPED.",
    )


def start_streamsets_pipeline(label: str, pipeline_id: str) -> Action:
    """Start one StreamSets pipeline via the Data Collector REST API and poll until it
    actually reaches RUNNING - mirrors stop_streamsets_pipeline (same async-transition
    reasoning: the start call itself only signals STARTING, it doesn't wait for it)."""
    slug = label.lower().replace(" ", "_")
    return Action(
        name=f"start_streamsets_pipeline_{slug}",
        kind=ActionKind.STREAMSETS_PIPELINE,
        identity=Identity.SPLUNK,
        pipeline_id=pipeline_id,
        pipeline_label=label,
        target_status="RUNNING",
        poll_interval_seconds=_STREAMSETS_POLL_INTERVAL_SECONDS,
        timeout_seconds=_STREAMSETS_TIMEOUT_SECONDS,
        note=f"Start the {label} StreamSets pipeline via the Data Collector REST API "
             "and confirm it reaches RUNNING.",
    )


def _prdmilbbspkfw02_sequences(streamsets_pipelines: tuple[tuple[str, str], ...] = ()) -> RoleSequences:
    """streamsets_pipelines: (label, pipeline_id) pairs - loaded from
    inventory/streamsets_pipelines.yaml by cli.py and threaded through
    build_run_plan/get_role_sequences, not hardcoded here. Defaults to empty only for
    callers (mostly tests) that don't care about this host's StreamSets actions at
    all; a real run always passes the real list."""
    return RoleSequences(
        stop_per_node=(
            backup_crontab(),
            disable_crontab(),
            wait(180, "Allow in-flight cron jobs to finish before touching StreamSets."),
            *(stop_streamsets_pipeline(label, pid) for label, pid in streamsets_pipelines),
            *_default_stop(SPLUNK_BIN),
        ),
        start_per_node=(
            *_default_start(SPLUNK_BIN),
            *(start_streamsets_pipeline(label, pid) for label, pid in streamsets_pipelines),
            enable_crontab(),
        ),
    )


HOST_OVERRIDES = {
    "prdmilbbspkfw02": _prdmilbbspkfw02_sequences,
}

# Hosts where the splunk-level identity cannot be used over SSH at all; every action that
# needs Identity.SPLUNK on these hosts is forced to MANUAL by the executor/run controller.
# Previously prdrmlbbspksh01 and prdrmlbbspkdp01 were listed here, but those are now
# reachable via PAS port 10100 (set in inventory as pas_port: 10100) - not manual-only.
MANUAL_ONLY_IDENTITIES: dict[str, tuple[Identity, ...]] = {}


ROLE_SEQUENCES = {
    NodeRole.DEPLOYER: deployer_sequences,
    NodeRole.INDEXER: indexer_sequences,
    NodeRole.FORWARDER: forwarder_sequences,
    NodeRole.SEARCH_HEAD_SIMPLE: search_head_simple_sequences,
}


def splunk_bin_for(role: NodeRole) -> str:
    return SPLUNK_BIN_BY_ROLE[role]


def get_role_sequences(
    hostname: str, role: NodeRole, streamsets_pipelines: tuple[tuple[str, str], ...] = ()
) -> RoleSequences:
    if hostname in HOST_OVERRIDES:
        return HOST_OVERRIDES[hostname](streamsets_pipelines)
    if role == NodeRole.SEARCH_HEAD_STRETCHED:
        return search_head_stretched_sequences()
    return ROLE_SEQUENCES[role]()
