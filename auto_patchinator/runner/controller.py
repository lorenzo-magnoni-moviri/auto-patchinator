"""Interactive step navigator: walks the resolved RunStepPlan list, executes/records each
action, and lets the operator drive each major step in one of three modes:

  - automatic:    every action runs back to back, one line per action
                  ("stopping splunk ... DONE"); pauses only for manual
                  confirmations (press ENTER) and failures (retry menu).
  - task-by-task: the operator confirms every action before it runs
                  (run / mark-manual / skip / back / jump / quit).
  - manual guide: nothing is executed - each task is shown one at a time (command,
                  host, user + su command, and optionally why - see show_explanations
                  below); the operator performs it by hand (connecting via WinSSH) and
                  presses ENTER to move to the next one. 'l' lists all of the step's
                  tasks at once.

The mode is asked at the start of every step ([A]/[T]/[M] locks that mode for all
remaining steps; the --full-auto-mode CLI flag skips the question entirely, starting
locked to automatic). show_explanations controls whether manual guide's "why" line is
shown at all - it's asked only once, right after manual guide mode is chosen for the
first time (never asked at all if manual guide is never used that run, and never asked
again afterwards); --verbose pre-decides it and skips that prompt entirely. The
terminal's visible screen is
cleared at the start of every step (scrollback is left intact, so the operator can
still scroll up to earlier steps) so each step starts from a clean screen instead of
scrolling past the previous one's output. Failures show in red with the command output
and a retry-focused menu, identical in all executing modes. All progress is persisted
after every transition so
a crash/Ctrl-C can be resumed later.

Automatic mode additionally does two things task-by-task/manual guide don't, both scoped
to a single step's per-host actions only (never pre/post-group, which stay sequential -
see _run_automatic):
  - Reuses one SSH connection per (host, identity) across that host's whole action list
    for the step, instead of reconnecting (fresh PAS gateway login + su handshake) for
    every single action - see _HostConnections. Never carried into the next step: the
    host may be rebooted by another team's OS-patch action between a "Stop" step and its
    later "Start" step, so nothing is assumed to survive past the step it was opened for.
  - max_parallel_hosts > 1 runs that many hosts' action lists concurrently (a
    ThreadPoolExecutor, one worker per host) instead of one host at a time - default is 1
    (fully sequential, current behavior) since the PAS/CyberArk gateway's tolerance for
    concurrent sessions isn't established; --max-parallel-hosts opts in. Console output
    and state saves are serialized (_console_lock / _state_lock) so concurrent hosts'
    printed lines and failure/manual-confirm prompts never interleave; a quit chosen from
    within one host's prompt stops the others from starting their next action (in-flight
    commands always finish - never killed mid-SSH) via a shared threading.Event.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Protocol

from auto_patchinator.actions.sequences import CRONTAB_BACKUP, splunk_bin_for
from auto_patchinator.actions.types import Action, ActionKind, Identity
from auto_patchinator.config.inventory import Inventory
from auto_patchinator.executor.splunk_cli import find_member, member_health, parse_shcluster_members
from auto_patchinator.executor.ssh import su_command
from auto_patchinator.plan.run_plan import RunStepPlan
from auto_patchinator.state import store
from auto_patchinator.state.models import (
    POST_GROUP_SCOPE,
    PRE_GROUP_SCOPE,
    ActionState,
    ActionStatus,
    RunState,
)
from auto_patchinator.term import bold, clear_screen, cyan, green, progress_line, red, yellow


class Connection(Protocol):
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def run_plain(self, command: str, timeout: float = 60): ...
    def run_interactive(self, script, timeout: float = 60): ...
    def run_plain_with_secret(self, command: str, secret: str, timeout: float = 60): ...


ConnectionFactory = Callable[..., Connection]

_log = logging.getLogger(__name__)

_ANSI_ESC = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07)')

_GROUP_SCOPE_LABEL = {PRE_GROUP_SCOPE: "group", POST_GROUP_SCOPE: "group"}

# Progressive-tense labels for the automatic mode's one-line output.
_AUTO_LABELS = {
    "stop_splunk": "stopping splunk",
    "start_splunk": "starting splunk",
    "disable_boot_start": "disabling boot-start",
    "enable_boot_start": "enabling boot-start",
    "daemon_reload": "reloading systemd daemon",
    "clean_kvstore": "cleaning kvstore",
    "backup_crontab": "backing up crontab",
    "disable_crontab": "disabling crontab",
    "enable_crontab": "restoring crontab",
    "wait_for_shcluster_member_healthy": "waiting for search head cluster membership",
}

_AUTO_LABEL_WIDTH = 50  # pad so the DONE/FAILED column lines up

# Plain-language explanations shown by the manual guide ("why" line per action).
_GUIDE_DESCRIPTIONS = {
    "stop_splunk": "Stop the Splunk process cleanly before the OS is patched.",
    "start_splunk": "Start Splunk again now that the OS has been patched.",
    "disable_boot_start": (
        "Unregister Splunk from systemd boot so the patch reboot comes back up without Splunk."
    ),
    "enable_boot_start": (
        "Re-register Splunk with systemd. This regenerates the unit file from a template, but "
        "the systemd override drop-in holds the customizations so nothing is lost."
    ),
    "daemon_reload": "Make systemd re-read the regenerated unit file.",
    "clean_kvstore": (
        "Clear the local KV store so it resyncs cleanly from the cluster - done while Splunk "
        "is still down, before starting it back up."
    ),
    "backup_crontab": (
        f"Save the splunk user's crontab to {CRONTAB_BACKUP} BEFORE it gets "
        "deleted - it is restored from this file after patching."
    ),
    "disable_crontab": (
        "Delete the splunk user's crontab so no scheduled jobs fire mid-patching. The "
        "crontab command is aliased to 'crontab -i': answer 'yes' at the confirmation."
    ),
    "enable_crontab": f"Restore the crontab from the {CRONTAB_BACKUP} copy.",
    "wait_for_shcluster_member_healthy": (
        "Confirm this host rejoined the search head cluster cleanly after restarting - "
        "polls up to 10 minutes for status=Up, out_of_sync_node=0, restart_required=0."
    ),
}


def _auto_label(action: Action) -> str:
    if action.kind == ActionKind.WAIT:
        return f"waiting {action.wait_seconds}s ({action.note})"
    return _AUTO_LABELS.get(action.name, action.name.replace("_", " "))


def _scope_label(scope: str) -> str:
    return _GROUP_SCOPE_LABEL.get(scope, scope)


class _HostConnections:
    """Caches one open connection per identity for a single host, reused across that
    host's automatic-mode actions within one step. Never used across steps - see the
    module docstring. Only ever touched by the one thread running that host's block, so
    it needs no locking of its own."""

    def __init__(self, factory: ConnectionFactory, hostname: str, role) -> None:
        self._factory = factory
        self._hostname = hostname
        self._role = role
        self._by_identity: dict[Identity, Connection] = {}

    def get(self, identity: Identity) -> Connection:
        conn = self._by_identity.get(identity)
        if conn is None:
            conn = self._factory(hostname=self._hostname, identity=identity, role=self._role)
            conn.connect()
            self._by_identity[identity] = conn
        return conn

    def drop(self, identity: Identity) -> None:
        """Discard a connection that turned out to be stale/dead, so the next attempt
        (e.g. an operator-chosen retry) opens a fresh one instead of reusing a broken
        session."""
        conn = self._by_identity.pop(identity, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - already broken, closing is best-effort
                pass

    def close_all(self) -> None:
        for identity in list(self._by_identity):
            self.drop(identity)


def is_forced_manual(
    inventory: Inventory, scope: str, action: Action, splunk_api_credentials=None
) -> bool:
    if action.kind == ActionKind.MANUAL:
        return True
    if action.kind == ActionKind.CLUSTER_WAIT:
        # Needs Splunk admin credentials (not an SSH identity) - if they're not
        # configured, the operator has to verify cluster health by hand instead.
        return not (
            splunk_api_credentials
            and splunk_api_credentials.username
            and splunk_api_credentials.password
        )
    if scope in (PRE_GROUP_SCOPE, POST_GROUP_SCOPE) or action.identity is None:
        return False
    return inventory.get(scope).is_manual_only(action.identity)


def print_plan_summary(
    run_plan: list[RunStepPlan], inventory: Inventory, splunk_api_credentials=None
) -> None:
    env = inventory.environment.upper()
    if env == "PROD":
        print(red("\n*** PRODUCTION ENVIRONMENT ***"))
    else:
        print(f"\n--- {env} environment ---")
    print("\nResolved plan (in execution order):\n")
    for step_plan in run_plan:
        deps_note = ""
        if step_plan.external_dependencies:
            deps_note = f"  [waits on external step(s) {', '.join(map(str, step_plan.external_dependencies))}]"
        print(f"Step {step_plan.excel_step} - {step_plan.verb.value.upper()} - {step_plan.label}{deps_note}")
        print(f"  groups: {list(step_plan.groups)}  hosts: {list(step_plan.hostnames)}")
        for action in step_plan.pre_group_actions:
            print(f"    [group, before] {action.name} ({action.kind.value})")
        for hostname, actions in step_plan.per_host_actions.items():
            for action in actions:
                ident = action.identity.value if action.identity else "-"
                forced = action.kind != ActionKind.MANUAL and is_forced_manual(
                    inventory, hostname, action, splunk_api_credentials
                )
                manual_note = " [MANUAL ONLY on this host]" if forced else ""
                print(f"    [{hostname}, {ident}] {action.name} ({action.kind.value}){manual_note}")
        for action in step_plan.post_group_actions:
            print(f"    [group, after] {action.name} ({action.kind.value})")
        print()


class RunController:
    def __init__(
        self,
        run_plan: list[RunStepPlan],
        state: RunState,
        state_dir: str,
        connection_factory: ConnectionFactory,
        inventory: Inventory,
        dry_run: bool = False,
        full_auto: bool = False,
        show_explanations: bool | None = False,
        max_parallel_hosts: int = 1,
        splunk_api_credentials=None,
    ) -> None:
        self._plans = {p.excel_step: p for p in run_plan}
        self._order = [p.excel_step for p in run_plan]
        self.state = state
        self._state_dir = state_dir
        self._connection_factory = connection_factory
        self._inventory = inventory
        self._dry_run = dry_run
        # Only consumed by CLUSTER_WAIT actions (post-restart cluster-health polling) -
        # None means those are forced manual instead (see is_forced_manual).
        self._splunk_api_credentials = splunk_api_credentials
        self._locked_mode: str | None = "auto" if full_auto else None
        # None = not yet decided; asked once, the first time manual guide mode is used.
        self._show_explanations: bool | None = show_explanations
        self._jump_target_index: int | None = None
        # >1 runs that many hosts' automatic-mode action lists concurrently (see the
        # module docstring); 1 (the default) is the original fully-sequential behavior.
        self._max_parallel_hosts = max(1, max_parallel_hosts)
        # Guard console output and state saves, respectively, once automatic mode may be
        # running more than one host's actions at a time. Uncontended (near-free) when
        # max_parallel_hosts is 1, since only one thread is ever running then.
        self._console_lock = threading.Lock()
        self._state_lock = threading.Lock()

    def run(self) -> None:
        index = self._start_index()
        while index < len(self._order):
            excel_step = self._order[index]
            self.state.current_step = excel_step
            self._save()
            outcome = self._handle_step(excel_step)
            if outcome == "quit":
                self._handle_quit()
                return
            if outcome == "back":
                index = max(0, index - 1)
                continue
            if outcome == "jump":
                index = self._jump_target_index
                continue
            index += 1
        print(green("\nAll steps complete."))

    def _start_index(self) -> int:
        if self.state.current_step is not None and self.state.current_step in self._order:
            return self._order.index(self.state.current_step)
        return 0

    def _save(self) -> None:
        # Locked so concurrent automatic-mode hosts never interleave writes to the
        # same state file - see the module docstring.
        with self._state_lock:
            store.save(self.state, self._state_dir)

    def _handle_quit(self) -> None:
        try:
            answer = input("\nSave progress for later resume? [Y/n] ").strip().lower()
        except EOFError:
            answer = "y"
        if answer in ("n", "no"):
            store.delete_run(self.state.run_id, self._state_dir)
            _log.info("operator discarded run state %s", self.state.run_id)
            print("Progress discarded.")
        else:
            _log.info("operator saved run state %s", self.state.run_id)
            print("Progress saved — rerun the same command to resume from here.")

    # ------------------------------------------------------------------
    # Step handling
    # ------------------------------------------------------------------

    def _handle_step(self, excel_step: int) -> str:
        step_plan = self._plans[excel_step]
        step_state = self.state.steps[excel_step]

        scoped = [(PRE_GROUP_SCOPE, step_plan.pre_group_actions, step_state.pre_group)]
        for hostname, actions in step_plan.per_host_actions.items():
            scoped.append((hostname, actions, step_state.per_host[hostname]))
        scoped.append((POST_GROUP_SCOPE, step_plan.post_group_actions, step_state.post_group))

        pending = [
            (scope, action, action_state)
            for scope, actions, action_states in scoped
            for action, action_state in zip(actions, action_states)
            if action_state.status not in (ActionStatus.SUCCESS, ActionStatus.SKIPPED)
        ]
        if not pending:
            return "next"

        clear_screen()
        print(bold(f"\n=== Step {excel_step} - {step_plan.verb.value.upper()} - {step_plan.label} ==="))
        _log.info("entering step %s (%s - %s)", excel_step, step_plan.verb.value, step_plan.label)

        mode = self._locked_mode if self._locked_mode is not None else self._ask_step_mode(len(pending))
        if mode == "quit":
            return "quit"
        _log.info("step %s run mode: %s", excel_step, mode)

        if mode == "manual":
            if self._show_explanations is None:
                self._show_explanations = self._ask_show_explanations()
                _log.info("show_explanations set to %s (asked at step %s)", self._show_explanations, excel_step)
            return self._run_manual_guide(pending)

        if mode == "auto":
            return self._run_automatic(pending)

        for scope, action, action_state in pending:
            if action_state.status in (ActionStatus.SUCCESS, ActionStatus.SKIPPED):
                continue  # may have been resolved by a jump/back replay
            outcome = self._handle_action(scope, action, action_state)
            if outcome != "continue":
                return outcome
        return "next"

    # ------------------------------------------------------------------
    # Automatic mode: pre-group (sequential) -> per-host (optionally concurrent,
    # each host's own actions always in order) -> post-group (sequential).
    # ------------------------------------------------------------------

    def _run_automatic(self, pending: list) -> str:
        pre_items: list = []
        post_items: list = []
        host_blocks: dict[str, list] = {}
        host_order: list[str] = []
        for scope, action, action_state in pending:
            if action_state.status in (ActionStatus.SUCCESS, ActionStatus.SKIPPED):
                continue  # may have been resolved by a jump/back replay
            if scope == PRE_GROUP_SCOPE:
                pre_items.append((scope, action, action_state))
            elif scope == POST_GROUP_SCOPE:
                post_items.append((scope, action, action_state))
            else:
                if scope not in host_blocks:
                    host_blocks[scope] = []
                    host_order.append(scope)
                host_blocks[scope].append((scope, action, action_state))

        for scope, action, action_state in pre_items:
            outcome = self._handle_action_auto(scope, action, action_state)
            if outcome != "continue":
                return outcome

        if len(host_order) == 1 or self._max_parallel_hosts <= 1:
            for hostname in host_order:
                if self._run_host_block_auto(hostname, host_blocks[hostname]) == "quit":
                    return "quit"
        elif host_order:
            quit_event = threading.Event()
            with ThreadPoolExecutor(max_workers=self._max_parallel_hosts) as pool:
                futures = {
                    pool.submit(self._run_host_block_auto, hostname, host_blocks[hostname], quit_event): hostname
                    for hostname in host_order
                }
                outcomes = {futures[f]: f.result() for f in as_completed(futures)}
            if "quit" in outcomes.values():
                return "quit"

        for scope, action, action_state in post_items:
            outcome = self._handle_action_auto(scope, action, action_state)
            if outcome != "continue":
                return outcome
        return "next"

    def _run_host_block_auto(
        self, hostname: str, items: list, quit_event: "threading.Event | None" = None
    ) -> str:
        """Run one host's pending automatic-mode actions, in order, against one
        connection per identity reused across the whole list (see _HostConnections).
        Runs in a worker thread when max_parallel_hosts > 1, in the main thread
        otherwise - either way this is the only thread ever touching this host's
        connections or its own action_state entries."""
        role = self._inventory.get(hostname).role
        connections = _HostConnections(self._connection_factory, hostname, role)
        # quit_event is only ever passed (non-None) by the concurrent thread-pool branch
        # of _run_automatic - the single-line dot animation only makes sense when this
        # is truly the only host printing at the moment, i.e. the sequential branch,
        # regardless of what --max-parallel-hosts was set to for the run as a whole.
        animate = quit_event is None
        try:
            for scope, action, action_state in items:
                if action_state.status in (ActionStatus.SUCCESS, ActionStatus.SKIPPED):
                    continue
                if quit_event is not None and quit_event.is_set():
                    return "quit"
                if self._is_forced_manual(scope, action):
                    outcome = self._confirm_manual_auto(scope, action, action_state)
                else:
                    outcome = self._attempt_with_retry(
                        scope, action, action_state, auto=True, connections=connections, animate=animate
                    )
                if outcome == "quit":
                    if quit_event is not None:
                        quit_event.set()
                    return "quit"
            return "continue"
        finally:
            connections.close_all()

    def _ask_step_mode(self, pending_count: int) -> str:
        print(f"    {pending_count} pending action(s). How do you want to run this step?")
        print("      [a] automatic     - run everything, one line per action; pauses only for")
        print("                          manual confirmations and failures")
        print("      [A] automatic for ALL remaining steps (stop asking)")
        print("      [t] task-by-task  - confirm every action before it runs")
        print("      [T] task-by-task for ALL remaining steps (stop asking)")
        print("      [m] manual guide  - execute NOTHING: shows each task one at a time (command,")
        print("                          host, user) and waits for you to do it by hand")
        print("      [M] manual guide for ALL remaining steps (stop asking)")
        print("      [q] quit")
        while True:
            choice = input("    > ").strip()
            if choice == "A":
                self._locked_mode = "auto"
                return "auto"
            if choice == "T":
                self._locked_mode = "task"
                return "task"
            if choice == "M":
                self._locked_mode = "manual"
                return "manual"
            lowered = choice.lower()
            if lowered == "a":
                return "auto"
            if lowered == "t":
                return "task"
            if lowered == "m":
                return "manual"
            if lowered == "q":
                return "quit"
            print("    Please choose one of: a, A, t, T, m, M, q")

    @staticmethod
    def _ask_show_explanations() -> bool:
        print("    Show the reasoning behind each action in manual guide (the 'why' line)?")
        answer = input("    [y/N] > ").strip().lower()
        return answer == "y"

    # ------------------------------------------------------------------
    # Manual guide mode
    # ------------------------------------------------------------------

    def _su_hint(self, hostname: str, identity: Identity) -> str:
        """The su command to become <identity> on <hostname> (connection is via WinSSH)."""
        host = self._inventory.get(hostname)
        if identity == Identity.SPLUNK and host.splunk_su_command:
            return host.splunk_su_command
        return su_command(identity, host.role)

    @staticmethod
    def _guide_what(action: Action) -> list[tuple[str, bool]]:
        """Returns [(line, is_command), ...] - is_command lines get highlighted as
        literal text to type/paste, distinguishing them from surrounding instructions."""
        if action.kind == ActionKind.PLAIN:
            return [(action.command, True)]
        if action.kind == ActionKind.INTERACTIVE:
            lines = []
            for step in action.script:
                expect = f"   (wait for: {step.expect!r})" if step.expect else ""
                lines.append((f"{step.send}{expect}", True))
            return lines
        if action.kind == ActionKind.WAIT:
            return [(f"wait {action.wait_seconds}s - {action.note}", False)]
        # MANUAL: lines indented 3+ spaces in the source note are literal commands
        # (see sequences.py's captain_transfer_static/captain_revert_dynamic); everything
        # else is instructional prose, left unhighlighted.
        result = []
        for line in (action.note or action.name).splitlines():
            if line.startswith("   "):
                result.append((line.strip(), True))
            else:
                result.append((line, False))
        return result

    def _print_guide_scope_header(self, scope: str, actions: list[Action]) -> None:
        if scope in (PRE_GROUP_SCOPE, POST_GROUP_SCOPE):
            when = "before" if scope == PRE_GROUP_SCOPE else "after"
            print(bold(f"\n    Once for the whole group ({when} the per-host work):"))
            return
        host = self._inventory.get(scope)
        print(bold(f"\n    On host {scope} ({host.role.value}, site {host.site}):"))
        for identity in dict.fromkeys(a.identity for a in actions if a.identity is not None):
            forced = "  [CyberArk GUI only - no SSH]" if host.is_manual_only(identity) else ""
            print(f"      become {identity.value} with: {cyan(self._su_hint(scope, identity))}{yellow(forced)}")

    def _print_guide_group_header(self, hostnames: list[str], actions: list[Action]) -> None:
        """Same as _print_guide_scope_header, but for a batch of hosts sharing an
        identical remaining task list - printed once instead of once per host."""
        if len(hostnames) == 1:
            self._print_guide_scope_header(hostnames[0], actions)
            return
        representative = self._inventory.get(hostnames[0])
        sites = sorted({self._inventory.get(h).site for h in hostnames})
        site_note = sites[0] if len(sites) == 1 else "/".join(sites)
        print(bold(
            f"\n    On {len(hostnames)} hosts ({representative.role.value}, site {site_note}): "
            + ", ".join(hostnames)
        ))
        for identity in dict.fromkeys(a.identity for a in actions if a.identity is not None):
            forced = "  [CyberArk GUI only - no SSH]" if representative.is_manual_only(identity) else ""
            print(f"      become {identity.value} with: {cyan(self._su_hint(hostnames[0], identity))}{yellow(forced)}")
        print(yellow(
            f"      Repeat the {len(actions)} task(s) below IDENTICALLY on EACH of these "
            f"{len(hostnames)} hosts."
        ))

    def _print_guide_task(self, number: int, total: int, action: Action) -> None:
        who = action.identity.value if action.identity else "operator"
        kind_note = "  (manual step)" if action.kind == ActionKind.MANUAL else ""
        print(f"\n      task {number}/{total}: {action.name}  [user: {who}]{kind_note}")
        what = self._guide_what(action)
        first_text, first_is_cmd = what[0]
        print(f"         run : {cyan(first_text) if first_is_cmd else first_text}")
        for extra_text, extra_is_cmd in what[1:]:
            print(f"               {cyan(extra_text) if extra_is_cmd else extra_text}")
        why = _GUIDE_DESCRIPTIONS.get(action.name) or (
            action.note if action.kind != ActionKind.MANUAL else None
        )
        if why and self._show_explanations:
            print(f"         why : {why}")

    def _print_guide_overview(self, pending: list) -> None:
        print(yellow("\n    All tasks in this step, in order:"))
        for number, (scope, action, action_state) in enumerate(pending, start=1):
            who = action.identity.value if action.identity else "operator"
            what, what_is_cmd = self._guide_what(action)[0]
            what = cyan(what) if what_is_cmd else what
            marker = {
                ActionStatus.SUCCESS: " [done]",
                ActionStatus.SKIPPED: " [skipped]",
            }.get(action_state.status, "")
            print(f"      {number:>2}. [{_scope_label(scope)}] {action.name} ({who}): {what}{marker}")
        print()

    def _host_profile(self, hostname: str, actions_and_states: list) -> tuple:
        """Signature used to detect hosts whose remaining manual-guide work is
        identical, so they can be presented and confirmed as one batch instead of
        once per host (e.g. 5 identical search-head stops)."""
        host = self._inventory.get(hostname)
        action_sig = tuple((a.name, a.kind, a.command, a.script) for a, _ in actions_and_states)
        identities = dict.fromkeys(a.identity for a, _ in actions_and_states if a.identity is not None)
        su_sig = tuple(
            (identity, self._su_hint(hostname, identity), host.is_manual_only(identity))
            for identity in identities
        )
        return action_sig, su_sig

    def _build_host_groups(self, host_items: list) -> list[tuple[list[str], dict]]:
        """Group per-host pending items by _host_profile, preserving first-seen order
        of both hosts and profiles. Returns [(hostnames, {hostname: [(action, state), ...]})]."""
        per_host: dict[str, list] = {}
        order: list[str] = []
        for scope, action, action_state in host_items:
            if scope not in per_host:
                per_host[scope] = []
                order.append(scope)
            per_host[scope].append((action, action_state))

        groups: list[list[str]] = []
        profile_to_group: dict[tuple, list[str]] = {}
        for hostname in order:
            profile = self._host_profile(hostname, per_host[hostname])
            if profile not in profile_to_group:
                profile_to_group[profile] = []
                groups.append(profile_to_group[profile])
            profile_to_group[profile].append(hostname)

        return [(hostnames, {h: per_host[h] for h in hostnames}) for hostnames in groups]

    def _confirm_task(self, pending: list, scope: str, action: Action, action_state: ActionState) -> str:
        """Show one task and get the operator's answer. Returns 'continue' or 'quit'."""
        while True:
            answer = input(yellow(
                "         press ENTER when done to continue (s=skip, l=list all tasks, q=quit) ... "
            )).strip().lower()
            if answer == "l":
                self._print_guide_overview(pending)
                continue
            break
        _log.info("manual guide: operator answered %r for %s / %s", answer, scope, action.name)

        if answer == "q":
            return "quit"
        if answer == "s":
            action_state.status = ActionStatus.SKIPPED
            self._save()
            print(yellow("         SKIPPED"))
            return "continue"
        action_state.status = ActionStatus.SUCCESS
        action_state.output = "confirmed done manually by operator (manual guide)"
        self._save()
        print(green("         CONFIRMED"))
        return "continue"

    def _guide_single_scope_block(self, pending: list, scope: str, items: list) -> str:
        """Walk a pre/post-group scope's tasks one at a time (these aren't per-host,
        so there's nothing to batch)."""
        if not items:
            return "continue"
        self._print_guide_scope_header(scope, [a for a, _ in items])
        total = len(items)
        for number, (action, action_state) in enumerate(items, start=1):
            self._print_guide_task(number, total, action)
            if self._confirm_task(pending, scope, action, action_state) == "quit":
                return "quit"
        return "continue"

    def _guide_host_group(self, pending: list, hostnames: list[str], per_host_map: dict) -> str:
        """Present one group of hosts sharing an identical task list. Lets the
        operator mark the whole batch done/skipped at once, or fall back to
        confirming host by host if something needs individual handling."""
        representative_actions = [a for a, _ in per_host_map[hostnames[0]]]
        total = len(representative_actions)

        self._print_guide_group_header(hostnames, representative_actions)
        for number, action in enumerate(representative_actions, start=1):
            self._print_guide_task(number, total, action)

        plural = len(hostnames) > 1
        while True:
            if plural:
                prompt = (
                    f"\n    Once you've done the above IDENTICALLY on ALL {len(hostnames)} hosts, "
                    "press ENTER to confirm\n"
                    f"      (or: [i] confirm host-by-host instead  [s] skip all  "
                    "[l] list all tasks  [q] quit)\n    > "
                )
            else:
                prompt = (
                    "\n    Once you've completed the above on this host, press ENTER to confirm\n"
                    "      (or: [i] confirm task-by-task instead  [s] skip  "
                    "[l] list all tasks  [q] quit)\n    > "
                )
            answer = input(yellow(prompt)).strip().lower()
            if answer == "l":
                self._print_guide_overview(pending)
                continue
            break
        _log.info("manual guide: group answer %r for %d host(s), %d task(s) each", answer, len(hostnames), total)

        if answer == "q":
            return "quit"
        if answer == "i":
            return self._guide_host_group_individually(pending, hostnames, per_host_map)
        if answer == "s":
            for host_actions in per_host_map.values():
                for _, action_state in host_actions:
                    action_state.status = ActionStatus.SKIPPED
            self._save()
            print(yellow(f"         SKIPPED for {len(hostnames)} host(s)"))
            return "continue"
        for host_actions in per_host_map.values():
            for _, action_state in host_actions:
                action_state.status = ActionStatus.SUCCESS
                action_state.output = "confirmed done manually by operator (manual guide, batch)"
        self._save()
        print(green(f"         CONFIRMED for {len(hostnames)} host(s)"))
        return "continue"

    def _guide_host_group_individually(self, pending: list, hostnames: list[str], per_host_map: dict) -> str:
        """Fallback from a batched group: confirm each host's tasks one at a time,
        for when something went wrong on one host, or one just needs closer attention."""
        for hostname in hostnames:
            actions_and_states = per_host_map[hostname]
            self._print_guide_scope_header(hostname, [a for a, _ in actions_and_states])
            total = len(actions_and_states)
            for number, (action, action_state) in enumerate(actions_and_states, start=1):
                self._print_guide_task(number, total, action)
                if self._confirm_task(pending, hostname, action, action_state) == "quit":
                    return "quit"
        return "continue"

    def _run_manual_guide(self, pending: list) -> str:
        print(yellow(
            "\n    MANUAL GUIDE - nothing will be executed; identical work across hosts is batched."
        ))

        pre_items = [(a, st) for s, a, st in pending if s == PRE_GROUP_SCOPE]
        post_items = [(a, st) for s, a, st in pending if s == POST_GROUP_SCOPE]
        host_items = [
            (s, a, st) for s, a, st in pending if s not in (PRE_GROUP_SCOPE, POST_GROUP_SCOPE)
        ]

        if self._guide_single_scope_block(pending, PRE_GROUP_SCOPE, pre_items) == "quit":
            return "quit"

        for hostnames, per_host_map in self._build_host_groups(host_items):
            if self._guide_host_group(pending, hostnames, per_host_map) == "quit":
                return "quit"

        if self._guide_single_scope_block(pending, POST_GROUP_SCOPE, post_items) == "quit":
            return "quit"

        return "next"

    # ------------------------------------------------------------------
    # Automatic mode
    # ------------------------------------------------------------------

    def _handle_action_auto(self, scope: str, action: Action, action_state: ActionState) -> str:
        if self._is_forced_manual(scope, action):
            return self._confirm_manual_auto(scope, action, action_state)
        return self._attempt_with_retry(scope, action, action_state, auto=True)

    def _confirm_manual_auto(self, scope: str, action: Action, action_state: ActionState) -> str:
        # Held for the whole prompt (not just the prints) so two hosts hitting a forced-
        # manual action at the same time in a concurrent automatic run can't both read
        # stdin at once - see the module docstring. Other hosts' SSH work keeps running
        # in the background; only their console output queues behind this lock.
        with self._console_lock:
            print(f"\n[{_scope_label(scope)}] {action.name.replace('_', ' ')} - MANUAL STEP")
            detail = action.note or action.command or ""
            if action.kind != ActionKind.MANUAL and action.command:
                # Automatable action forced manual on this host (CyberArk-GUI-only identity):
                # show exactly what to run and as whom.
                ident = action.identity.value if action.identity else "-"
                print(f"    run as {ident}: {action.command}")
            if detail:
                for line in detail.splitlines():
                    print(f"    {line}")
            answer = input(yellow("    press ENTER to confirm it is done (s=skip, q=quit) ... ")).strip().lower()
            _log.info("operator answered %r for manual %s / %s", answer, scope, action.name)
            if answer == "q":
                return "quit"
            if answer == "s":
                action_state.status = ActionStatus.SKIPPED
                self._save()
                print(yellow("    SKIPPED"))
                return "continue"
            action_state.status = ActionStatus.SUCCESS
            action_state.output = "confirmed done manually by operator"
            self._save()
            print(green("    CONFIRMED"))
            return "continue"

    # ------------------------------------------------------------------
    # Shared execution + failure retry menu
    # ------------------------------------------------------------------

    def _attempt_with_retry(
        self,
        scope: str,
        action: Action,
        action_state: ActionState,
        auto: bool,
        connections: "_HostConnections | None" = None,
        animate: bool = True,
    ) -> str:
        prefix = f"[{_scope_label(scope)}] {_auto_label(action)}"
        while True:
            started = time.monotonic()
            if auto:
                if animate:
                    # Only meaningful single-threaded (max_parallel_hosts <= 1) - the
                    # in-place \r redraw assumes it owns the terminal's last line, which
                    # doesn't hold once other hosts may be printing concurrently.
                    with progress_line(f"{prefix:<{_AUTO_LABEL_WIDTH - 4}}"):
                        self._execute(scope, action, action_state, quiet=True, connections=connections)
                else:
                    with self._console_lock:
                        print(f"{prefix} ...")
                    self._execute(scope, action, action_state, quiet=True, connections=connections)
            else:
                self._execute(scope, action, action_state, quiet=False, connections=connections)
            elapsed = time.monotonic() - started
            if action_state.status == ActionStatus.SUCCESS:
                if auto:
                    duration = f"  ({elapsed:.0f}s)" if elapsed >= 2 else ""
                    done = "DONE (dry-run)" if self._dry_run else "DONE"
                    with self._console_lock:
                        if animate:
                            print(green(done) + duration)
                        else:
                            print(f"{prefix} {green(done)}{duration}")
                return "continue"

            # Held for the whole failure block (print + retry-menu input), same reason
            # as _confirm_manual_auto: exactly one host's failure is ever on screen /
            # reading stdin at a time, though other hosts keep running in the background.
            with self._console_lock:
                if auto:
                    print(red("FAILED")) if animate else print(f"{prefix} {red('FAILED')}")
                self._print_failure(scope, action, action_state)
                choice = self._failure_menu()
            _log.info("operator chose %r after failure of %s / %s", choice, scope, action.name)
            if choice == "r":
                continue
            if choice == "d":
                action_state.status = ActionStatus.SUCCESS
                action_state.output = "confirmed done manually by operator"
                self._save()
                return "continue"
            if choice == "s":
                action_state.status = ActionStatus.SKIPPED
                self._save()
                return "continue"
            if choice == "q":
                return "quit"

    def _print_failure(self, scope: str, action: Action, action_state: ActionState) -> None:
        error = action_state.error or "failed"
        if len(error) > 300:
            error = error[:300] + "..."
        print(red(f"\n✖ FAILED: {scope} / {action.name} - {error}"))
        output = _ANSI_ESC.sub("", action_state.output or "").replace("\r", "")
        lines = [line for line in output.splitlines() if line.strip()]
        for line in lines[-8:]:
            print(red(f"│  {line}"))

    @staticmethod
    def _failure_menu() -> str:
        prompt = "Retry? [r] retry  [d] mark as done manually  [s] skip  [q] quit\n> "
        while True:
            choice = input(prompt).strip().lower()
            if choice in ("r", "d", "s", "q"):
                return choice
            print("Please choose one of: r, d, s, q")

    # ------------------------------------------------------------------
    # Task-by-task mode
    # ------------------------------------------------------------------

    def _handle_action(self, scope: str, action: Action, action_state: ActionState) -> str:
        forced_manual = self._is_forced_manual(scope, action)
        while True:
            self._print_action(scope, action, forced_manual)
            options = []
            if not forced_manual and action.kind in (ActionKind.PLAIN, ActionKind.INTERACTIVE, ActionKind.WAIT):
                options.append(("r", "run"))
            options.append(("d", "mark as done manually"))
            options.append(("s", "skip"))
            options.append(("b", "back to previous step"))
            options.append(("j", "jump to a specific step"))
            options.append(("q", "quit"))
            choice = self._prompt_choice(options)
            _log.info("operator chose %r for %s / %s", choice, scope, action.name)

            if choice == "r":
                return self._attempt_with_retry(scope, action, action_state, auto=False)
            if choice == "d":
                action_state.status = ActionStatus.SUCCESS
                action_state.output = "confirmed done manually by operator"
                self._save()
                return "continue"
            if choice == "s":
                action_state.status = ActionStatus.SKIPPED
                self._save()
                return "continue"
            if choice == "b":
                return "back"
            if choice == "j":
                target = self._ask_jump_target()
                if target is None:
                    continue
                self._jump_target_index = self._order.index(target)
                return "jump"
            if choice == "q":
                return "quit"

    # ------------------------------------------------------------------
    # Execution primitive
    # ------------------------------------------------------------------

    def _execute(
        self,
        scope: str,
        action: Action,
        action_state: ActionState,
        quiet: bool = False,
        connections: "_HostConnections | None" = None,
    ) -> None:
        """Run one action. connections is None everywhere except the automatic-mode
        per-host path (_run_host_block_auto), which passes a cache reused across that
        host's whole action list for the step instead of reconnecting every action -
        see _HostConnections and the module docstring."""
        ok_prefix = "OK (dry-run, simulated only)" if self._dry_run else "OK"
        action_state.status = ActionStatus.IN_PROGRESS
        action_state.error = None
        self._save()
        _log.info(
            "executing %s / %s (kind=%s, identity=%s, dry_run=%s) command=%r",
            scope, action.name, action.kind.value,
            action.identity.value if action.identity else "-",
            self._dry_run, action.command,
        )
        try:
            if action.kind == ActionKind.WAIT:
                if not quiet:
                    print(f"Waiting {action.wait_seconds}s ({action.note})...")
                if not self._dry_run:
                    time.sleep(action.wait_seconds)
                action_state.status = ActionStatus.SUCCESS
                action_state.output = f"waited {action.wait_seconds}s"
                if not quiet:
                    print(f"{green(ok_prefix)}: {scope} / {action.name}")
                _log.info("success %s / %s: %s", scope, action.name, action_state.output)
                self._save()
                return

            if action.kind == ActionKind.CLUSTER_WAIT:
                self._execute_cluster_wait(scope, action, action_state, quiet, connections, ok_prefix)
                return

            role = self._inventory.get(scope).role if scope not in (PRE_GROUP_SCOPE, POST_GROUP_SCOPE) else None
            if connections is not None:
                connection = connections.get(action.identity)
                try:
                    if action.kind == ActionKind.PLAIN:
                        result = connection.run_plain(action.command, timeout=action.timeout_seconds)
                    else:
                        result = connection.run_interactive(action.script, timeout=action.timeout_seconds)
                except Exception:
                    # The cached session itself is suspect (not just a failed command) -
                    # drop it so a retry reconnects instead of reusing a dead session.
                    connections.drop(action.identity)
                    raise
            else:
                connection = self._connection_factory(hostname=scope, identity=action.identity, role=role)
                connection.connect()
                try:
                    if action.kind == ActionKind.PLAIN:
                        result = connection.run_plain(action.command, timeout=action.timeout_seconds)
                    else:
                        result = connection.run_interactive(action.script, timeout=action.timeout_seconds)
                finally:
                    connection.close()

            action_state.output = result.output
            if result.success:
                action_state.status = ActionStatus.SUCCESS
                if not quiet:
                    print(f"{green(ok_prefix)}: {scope} / {action.name}")
                    if self._dry_run:
                        print(f"   {result.output}")
                _log.info("success %s / %s (exit 0); output=%r", scope, action.name, result.output)
            else:
                action_state.status = ActionStatus.FAILED
                action_state.error = f"exit code {result.exit_code}"
                _log.error(
                    "failed %s / %s (exit %s); output=%r", scope, action.name, result.exit_code, result.output
                )
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
            action_state.status = ActionStatus.FAILED
            action_state.error = str(exc)
            _log.exception("error running %s / %s", scope, action.name)
        self._save()

    def _execute_cluster_wait(
        self,
        scope: str,
        action: Action,
        action_state: ActionState,
        quiet: bool,
        connections: "_HostConnections | None",
        ok_prefix: str,
    ) -> None:
        """CLUSTER_WAIT: poll `splunk show shcluster-status --verbose -auth ...` for
        `scope`'s own member entry until healthy or action.timeout_seconds elapses.
        Only reached when Splunk API credentials are configured - is_forced_manual
        routes this to a manual confirmation otherwise, so self._splunk_api_credentials
        is never None here. No per-poll console output (quiet or not) - a poll can run
        for minutes, and printing here would fight with _attempt_with_retry's own
        progress_line/animated-dots wrapper around this whole call; each attempt is
        still logged to the DEBUG audit log, and the final health description is
        always in action_state.output/.error for the standard OK/FAILED line after."""
        if self._dry_run:
            action_state.status = ActionStatus.SUCCESS
            action_state.output = "simulated (dry-run) - cluster health not actually polled"
            if not quiet:
                print(f"{green(ok_prefix)}: {scope} / {action.name}")
            _log.info("success %s / %s (dry-run, simulated)", scope, action.name)
            self._save()
            return

        role = self._inventory.get(scope).role
        owns_connection = connections is None
        if connections is not None:
            connection = connections.get(action.identity)
        else:
            connection = self._connection_factory(hostname=scope, identity=action.identity, role=role)
            connection.connect()

        creds = self._splunk_api_credentials
        command = f'{splunk_bin_for(role)} show shcluster-status --verbose -auth "{creds.username}:$AP_SECRET"'
        deadline = time.monotonic() + action.timeout_seconds
        description = "no poll attempt completed"
        try:
            while True:
                result = connection.run_plain_with_secret(command, creds.password, timeout=30)
                fields = find_member(parse_shcluster_members(result.output), scope)
                healthy, description = member_health(fields)
                _log.debug("cluster_wait poll %s / %s: healthy=%s %s", scope, action.name, healthy, description)
                if healthy:
                    action_state.status = ActionStatus.SUCCESS
                    action_state.output = f"member healthy: {description}"
                    break
                if time.monotonic() >= deadline:
                    action_state.status = ActionStatus.FAILED
                    action_state.error = (
                        f"timed out after {action.timeout_seconds}s waiting for a healthy "
                        f"member status (last: {description})"
                    )
                    break
                time.sleep(action.poll_interval_seconds)
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
            if connections is not None:
                connections.drop(action.identity)
            action_state.status = ActionStatus.FAILED
            action_state.error = str(exc)
            _log.exception("error running %s / %s", scope, action.name)
        finally:
            if owns_connection:
                connection.close()

        if action_state.status == ActionStatus.SUCCESS:
            _log.info("success %s / %s: %s", scope, action.name, action_state.output)
            if not quiet:
                print(f"{green(ok_prefix)}: {scope} / {action.name}")
        elif action_state.error:
            _log.error("failed %s / %s: %s", scope, action.name, action_state.error)
        self._save()

    def _is_forced_manual(self, scope: str, action: Action) -> bool:
        return is_forced_manual(self._inventory, scope, action, self._splunk_api_credentials)

    def _ask_jump_target(self) -> int | None:
        raw = input(f"Jump to which step? ({', '.join(map(str, self._order))}, blank to cancel): ").strip()
        if not raw:
            return None
        try:
            target = int(raw)
        except ValueError:
            print("Not a number.")
            return None
        if target not in self._order:
            print(f"Step {target} is not part of this run.")
            return None
        return target

    @staticmethod
    def _print_action(scope: str, action: Action, forced_manual: bool) -> None:
        ident = action.identity.value if action.identity else "-"
        detail = action.command or (action.note or "")
        manual_note = "  [MANUAL ONLY on this host]" if forced_manual and action.kind != ActionKind.MANUAL else ""
        print(f"\n-> [{scope}] {action.name} ({action.kind.value}, identity={ident}){yellow(manual_note)}")
        if detail:
            print(f"   {detail}")

    @staticmethod
    def _prompt_choice(options: list[tuple[str, str]]) -> str:
        menu = "  ".join(f"[{key}] {label}" for key, label in options)
        valid = {key for key, _ in options}
        while True:
            choice = input(f"{menu}\n> ").strip().lower()
            if choice in valid:
                return choice
            print(f"Please choose one of: {', '.join(sorted(valid))}")
