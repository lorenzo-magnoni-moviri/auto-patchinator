"""Interactive step navigator: walks the resolved RunStepPlan list, executes/records each
action, and lets the operator drive each major step in one of three modes:

  - automatic:    every action runs back to back, one line per action
                  ("stopping splunk ... DONE"); pauses only for manual
                  confirmations (press ENTER) and failures (retry menu).
  - task-by-task: the operator confirms every action before it runs
                  (run / mark-manual / skip / back / jump / quit).
  - manual guide: nothing is executed - prints the full ordered list of commands
                  with, per action, where to run it, as which user (including the
                  exact PAS login string and su command), and why. The operator
                  performs everything by hand and then records the result.

The mode is asked at the start of every step ([A] applies automatic to all remaining
steps; the --full-auto-mode CLI flag skips the question entirely). Failures show in red
with the command output and a retry-focused menu, identical in all executing modes. All
progress is persisted after every transition so a crash/Ctrl-C can be resumed later.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Callable, Protocol

from auto_patchinator.actions.types import Action, ActionKind, Identity
from auto_patchinator.config.inventory import Inventory
from auto_patchinator.executor.ssh import login_username, su_command
from auto_patchinator.plan.run_plan import RunStepPlan
from auto_patchinator.state import store
from auto_patchinator.state.models import (
    POST_GROUP_SCOPE,
    PRE_GROUP_SCOPE,
    ActionState,
    ActionStatus,
    RunState,
)
from auto_patchinator.term import bold, green, progress_line, red, yellow


class Connection(Protocol):
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def run_plain(self, command: str, timeout: float = 60): ...
    def run_interactive(self, script, timeout: float = 60): ...


ConnectionFactory = Callable[..., Connection]

_log = logging.getLogger(__name__)

_ANSI_ESC = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07)')

_GROUP_SCOPE_LABEL = {PRE_GROUP_SCOPE: "group", POST_GROUP_SCOPE: "group"}

# Progressive-tense labels for the automatic mode's one-line output.
_AUTO_LABELS = {
    "stop_splunk": "stopping splunk",
    "start_splunk": "starting splunk",
    "backup_systemd_unit": "backing up systemd unit",
    "restore_systemd_unit": "restoring systemd unit",
    "disable_boot_start": "disabling boot-start",
    "enable_boot_start": "enabling boot-start",
    "daemon_reload": "reloading systemd daemon",
    "clean_kvstore": "cleaning kvstore",
    "disable_crontab": "disabling crontab",
    "enable_crontab": "restoring crontab",
}

_AUTO_LABEL_WIDTH = 50  # pad so the DONE/FAILED column lines up

# Plain-language explanations shown by the manual guide ("why" line per action).
_GUIDE_DESCRIPTIONS = {
    "stop_splunk": "Stop the Splunk process cleanly before the OS is patched.",
    "start_splunk": "Start Splunk again now that the OS has been patched.",
    "backup_systemd_unit": (
        "Save a copy of the hand-edited systemd unit file - 'enable boot-start' later "
        "regenerates it from a template and would lose the edits."
    ),
    "disable_boot_start": (
        "Unregister Splunk from systemd boot so the patch reboot comes back up without Splunk."
    ),
    "enable_boot_start": (
        "Re-register Splunk with systemd. This REGENERATES the unit file from a template - "
        "the next action restores the edited copy over it."
    ),
    "restore_systemd_unit": (
        "Overwrite the regenerated unit file with the backed-up edited copy. Overwrite in "
        "place (cat >) - do NOT rm+cp, replacing the inode breaks systemd's cached state."
    ),
    "daemon_reload": "Make systemd re-read the restored unit file.",
    "clean_kvstore": "Clear the local KV store so it resyncs cleanly from the cluster.",
    "disable_crontab": (
        "Delete the splunk user's crontab so no scheduled jobs fire mid-patching. The "
        "crontab command is aliased to 'crontab -i': answer 'yes' at the confirmation."
    ),
    "enable_crontab": "Restore the crontab from the /home/splunk/crontab.backup copy.",
}


def _auto_label(action: Action) -> str:
    if action.kind == ActionKind.WAIT:
        return f"waiting {action.wait_seconds}s ({action.note})"
    return _AUTO_LABELS.get(action.name, action.name.replace("_", " "))


def _scope_label(scope: str) -> str:
    return _GROUP_SCOPE_LABEL.get(scope, scope)


def is_forced_manual(inventory: Inventory, scope: str, action: Action) -> bool:
    if action.kind == ActionKind.MANUAL:
        return True
    if scope in (PRE_GROUP_SCOPE, POST_GROUP_SCOPE) or action.identity is None:
        return False
    return inventory.get(scope).is_manual_only(action.identity)


def print_plan_summary(run_plan: list[RunStepPlan], inventory: Inventory) -> None:
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
                forced = action.kind != ActionKind.MANUAL and is_forced_manual(inventory, hostname, action)
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
        gateway: tuple[str | None, int] = (None, 22),
    ) -> None:
        self._plans = {p.excel_step: p for p in run_plan}
        self._order = [p.excel_step for p in run_plan]
        self.state = state
        self._state_dir = state_dir
        self._connection_factory = connection_factory
        self._inventory = inventory
        self._dry_run = dry_run
        self._full_auto = full_auto
        self._gateway = gateway
        self._jump_target_index: int | None = None

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

        print(bold(f"\n=== Step {excel_step} - {step_plan.verb.value.upper()} - {step_plan.label} ==="))
        _log.info("entering step %s (%s - %s)", excel_step, step_plan.verb.value, step_plan.label)

        mode = "auto" if self._full_auto else self._ask_step_mode(len(pending))
        if mode == "quit":
            return "quit"
        _log.info("step %s run mode: %s", excel_step, mode)

        if mode == "manual":
            self._print_manual_guide(pending)
            choice = self._guide_followup()
            _log.info("manual guide follow-up for step %s: %r", excel_step, choice)
            if choice == "q":
                return "quit"
            if choice == "d":
                for _, _, action_state in pending:
                    action_state.status = ActionStatus.SUCCESS
                    action_state.output = "confirmed done manually by operator (manual guide)"
                self._save()
                print(green(f"All {len(pending)} action(s) marked as done."))
                return "next"
            mode = "task"  # choice == "t": record them one by one

        for scope, action, action_state in pending:
            if action_state.status in (ActionStatus.SUCCESS, ActionStatus.SKIPPED):
                continue  # may have been resolved by a jump/back replay
            if mode == "auto":
                outcome = self._handle_action_auto(scope, action, action_state)
            else:
                outcome = self._handle_action(scope, action, action_state)
            if outcome != "continue":
                return outcome
        return "next"

    def _ask_step_mode(self, pending_count: int) -> str:
        print(f"    {pending_count} pending action(s). How do you want to run this step?")
        print("      [a] automatic     - run everything, one line per action; pauses only for")
        print("                          manual confirmations and failures")
        print("      [A] automatic for ALL remaining steps (stop asking)")
        print("      [t] task-by-task  - confirm every action before it runs")
        print("      [m] manual guide  - execute NOTHING: print every command with where to")
        print("                          run it, as which user, and why - you do it all by hand")
        print("      [q] quit")
        while True:
            choice = input("    > ").strip()
            if choice == "A":
                self._full_auto = True
                return "auto"
            if choice.lower() == "a":
                return "auto"
            if choice.lower() == "t":
                return "task"
            if choice.lower() == "m":
                return "manual"
            if choice.lower() == "q":
                return "quit"
            print("    Please choose one of: a, A, t, m, q")

    # ------------------------------------------------------------------
    # Manual guide mode
    # ------------------------------------------------------------------

    def _ssh_hint(self, hostname: str, identity: Identity) -> str:
        """The exact ssh command + su step to reach <identity> on <hostname> by hand."""
        host = self._inventory.get(hostname)
        gw_host, gw_port = self._gateway
        if gw_host:
            login = login_username(
                "<your-user>", identity, hostname,
                pas_domain_suffix=host.effective_pas_domain_suffix(self._inventory.pas_domain_suffix),
                pas_port=host.effective_pas_port(self._inventory.pas_port),
                pas_suffixes=self._inventory.pas_suffixes,
            )
            port = f" -p {gw_port}" if gw_port != 22 else ""
            ssh = f"ssh{port} '{login}'@{gw_host}"
        else:
            ssh = f"ssh <your-user>@{hostname}  (no PAS gateway configured)"
        if identity == Identity.SPLUNK and host.splunk_su_command:
            su = host.splunk_su_command
        else:
            su = su_command(identity, host.role)
        return f"{ssh}   then: {su}"

    @staticmethod
    def _guide_what(action: Action) -> list[str]:
        if action.kind == ActionKind.PLAIN:
            return [action.command]
        if action.kind == ActionKind.INTERACTIVE:
            lines = []
            for step in action.script:
                expect = f"   (wait for: {step.expect!r})" if step.expect else ""
                lines.append(f"{step.send}{expect}")
            return lines
        if action.kind == ActionKind.WAIT:
            return [f"wait {action.wait_seconds}s - {action.note}"]
        return (action.note or action.name).splitlines()

    def _print_manual_guide(self, pending: list) -> None:
        print(yellow("\n    MANUAL GUIDE - nothing will be executed. Do the following by hand, in order:"))
        by_scope: dict[str, list[Action]] = {}
        for scope, action, _ in pending:
            by_scope.setdefault(scope, []).append(action)

        number = 0
        for scope, actions in by_scope.items():
            if scope in (PRE_GROUP_SCOPE, POST_GROUP_SCOPE):
                when = "before" if scope == PRE_GROUP_SCOPE else "after"
                print(bold(f"\n    Once for the whole group ({when} the per-host work):"))
            else:
                host = self._inventory.get(scope)
                print(bold(f"\n    On host {scope} ({host.role.value}, site {host.site}):"))
                identities = dict.fromkeys(a.identity for a in actions if a.identity is not None)
                for identity in identities:
                    forced = " [CyberArk GUI only - no SSH]" if host.is_manual_only(identity) else ""
                    print(f"      connect as {identity.value}{yellow(forced)}:")
                    print(f"        {self._ssh_hint(scope, identity)}")

            for action in actions:
                number += 1
                who = action.identity.value if action.identity else "operator"
                kind_note = "  (manual step)" if action.kind == ActionKind.MANUAL else ""
                print(f"\n      {number}. {action.name}  [user: {who}]{kind_note}")
                what = self._guide_what(action)
                print(f"         run : {what[0]}")
                for extra in what[1:]:
                    print(f"               {extra}")
                why = _GUIDE_DESCRIPTIONS.get(action.name) or (
                    action.note if action.kind != ActionKind.MANUAL else None
                )
                if why:
                    print(f"         why : {why}")
        print()

    @staticmethod
    def _guide_followup() -> str:
        prompt = (
            "    When you have performed the steps above:\n"
            "      [d] mark ALL of them as done  [t] record them one by one (task-by-task)  [q] quit\n"
            "    > "
        )
        while True:
            choice = input(prompt).strip().lower()
            if choice in ("d", "t", "q"):
                return choice
            print("    Please choose one of: d, t, q")

    # ------------------------------------------------------------------
    # Automatic mode
    # ------------------------------------------------------------------

    def _handle_action_auto(self, scope: str, action: Action, action_state: ActionState) -> str:
        if self._is_forced_manual(scope, action):
            return self._confirm_manual_auto(scope, action, action_state)
        return self._attempt_with_retry(scope, action, action_state, auto=True)

    def _confirm_manual_auto(self, scope: str, action: Action, action_state: ActionState) -> str:
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

    def _attempt_with_retry(self, scope: str, action: Action, action_state: ActionState, auto: bool) -> str:
        while True:
            started = time.monotonic()
            if auto:
                prefix = f"[{_scope_label(scope)}] {_auto_label(action)}"
                with progress_line(f"{prefix:<{_AUTO_LABEL_WIDTH - 4}}"):
                    self._execute(scope, action, action_state, quiet=True)
            else:
                self._execute(scope, action, action_state, quiet=False)
            elapsed = time.monotonic() - started
            if action_state.status == ActionStatus.SUCCESS:
                if auto:
                    duration = f"  ({elapsed:.0f}s)" if elapsed >= 2 else ""
                    done = "DONE (dry-run)" if self._dry_run else "DONE"
                    print(green(done) + duration)
                return "continue"

            if auto:
                print(red("FAILED"))
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

    def _execute(self, scope: str, action: Action, action_state: ActionState, quiet: bool = False) -> None:
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

            role = self._inventory.get(scope).role if scope not in (PRE_GROUP_SCOPE, POST_GROUP_SCOPE) else None
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

    def _is_forced_manual(self, scope: str, action: Action) -> bool:
        return is_forced_manual(self._inventory, scope, action)

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
