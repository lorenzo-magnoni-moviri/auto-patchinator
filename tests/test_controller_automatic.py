"""Automatic-mode tests: per-host connection reuse and --max-parallel-hosts.

No real SSH - a small fake Connection records connect()/close() calls (guarded by a
lock, since these tests deliberately run hosts concurrently) and returns scripted
results, so behavior is asserted precisely instead of just "the run finished".
"""
import threading

from auto_patchinator.actions.types import Identity
from auto_patchinator.executor.ssh import CommandResult
from auto_patchinator.plan.action_mapping import map_team_steps
from auto_patchinator.plan.dependency import resolve_order
from auto_patchinator.plan.run_plan import build_run_plan
from auto_patchinator.runner.controller import RunController
from auto_patchinator.state import store
from auto_patchinator.state.models import ActionStatus
from tests.conftest import TEAM, make_raw


class FakeConnection:
    def __init__(self, hostname, identity, role, log, log_lock, run_plain_hook=None):
        self.hostname = hostname
        self.identity = identity
        self.role = role
        self._log = log
        self._lock = log_lock
        self._run_plain_hook = run_plain_hook

    def connect(self):
        with self._lock:
            self._log.append(("connect", self.hostname, self.identity))

    def close(self):
        with self._lock:
            self._log.append(("close", self.hostname, self.identity))

    def run_plain(self, command, timeout=60):
        with self._lock:
            self._log.append(("run", self.hostname, self.identity, command))
        if self._run_plain_hook:
            return self._run_plain_hook(self.hostname, command)
        return CommandResult(exit_code=0, output="ok")

    def run_interactive(self, script, timeout=60):
        return self.run_plain(script[0].send if script else "", timeout)


class RecordingConnectionFactory:
    """Records every connect()/close()/run_plain() call, thread-safely."""

    def __init__(self, run_plain_hook=None):
        self.log: list[tuple] = []
        self._lock = threading.Lock()
        self._run_plain_hook = run_plain_hook

    def __call__(self, hostname, identity, role):
        return FakeConnection(hostname, identity, role, self.log, self._lock, self._run_plain_hook)

    def calls(self, kind):
        with self._lock:
            return [entry for entry in self.log if entry[0] == kind]


def _start_step_plan(inventory, hosts_by_group):
    """A single 'Start application Group 1' step - exercises both identities
    (enable_boot_start/daemon_reload as root, start_splunk as splunk)."""
    mapped, _ = map_team_steps([make_raw(2, "Start application Group 1")], TEAM)
    return build_run_plan(resolve_order(mapped), hosts_by_group, inventory)


def test_automatic_mode_reuses_one_connection_per_host_per_identity(tmp_path, inventory, monkeypatch):
    """dp01's start sequence is enable_boot_start[root] -> daemon_reload[root] ->
    start_splunk[splunk]: 3 actions, 2 identities. Automatic mode must open exactly one
    connection per identity (2 total), not one per action (3)."""
    plan = _start_step_plan(inventory, {1: ("dp01",)})
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    factory = RecordingConnectionFactory()
    feed = iter(["a"])  # step mode: automatic (send_mail's manual confirm never reached - ENTER not needed)
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl = RunController(plan, state, str(tmp_path), factory, inventory)

    # send_mail (post-group, MANUAL) would otherwise prompt for ENTER - answer it too.
    feed_full = iter(["a", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(feed_full))
    ctrl.run()

    assert ctrl.state.is_complete()
    connects = factory.calls("connect")
    assert len(connects) == 2, connects
    assert ("connect", "dp01", Identity.ROOT) in connects
    assert ("connect", "dp01", Identity.SPLUNK) in connects
    assert len(factory.calls("run")) == 3
    assert len(factory.calls("close")) == 2  # closed once each at end of dp01's block


def test_clean_kvstore_declined_in_automatic_mode_is_skipped_not_run(tmp_path, inventory, monkeypatch):
    """clean_kvstore is only meaningful on search_head_stretched. In automatic mode the
    operator is asked per host, right before it runs, whether to actually clean it - 'n'
    skips it (and never touches the connection) without blocking the rest of the host's
    sequence (start_splunk still runs)."""
    plan = _start_step_plan(inventory, {1: ("shx01",)})
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    factory = RecordingConnectionFactory()
    # mode=a, decline the kvstore-clean prompt, then ENTER for the two remaining forced-
    # manual confirmations (wait_for_shcluster_member_healthy - no Splunk API credentials
    # configured - and revert_captain_dynamic/send_mail).
    feed = iter(["a", "n", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl = RunController(plan, state, str(tmp_path), factory, inventory)
    ctrl.run()

    assert ctrl.state.is_complete()
    action_states = {a.name: a for _, a in ctrl.state.steps[2].all_action_states()}
    assert action_states["clean_kvstore"].status == ActionStatus.SKIPPED
    assert action_states["start_splunk"].status == ActionStatus.SUCCESS
    ran_commands = [call[3] for call in factory.calls("run")]
    assert not any("clean kvstore" in cmd for cmd in ran_commands)


def test_clean_kvstore_confirmed_in_automatic_mode_runs_it(tmp_path, inventory, monkeypatch):
    """Blank input (the default, matching a plain ENTER) keeps today's always-clean
    behavior - the prompt is opt-out, not opt-in."""
    plan = _start_step_plan(inventory, {1: ("shx01",)})
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    factory = RecordingConnectionFactory()
    feed = iter(["a", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl = RunController(plan, state, str(tmp_path), factory, inventory)
    ctrl.run()

    assert ctrl.state.is_complete()
    action_states = {a.name: a for _, a in ctrl.state.steps[2].all_action_states()}
    assert action_states["clean_kvstore"].status == ActionStatus.SUCCESS
    ran_commands = [call[3] for call in factory.calls("run")]
    assert any("clean kvstore" in cmd for cmd in ran_commands)


def test_automatic_mode_sequential_by_default_still_completes_all_hosts(tmp_path, inventory, monkeypatch):
    """max_parallel_hosts defaults to 1 - dp01 and fw01 both run, one at a time, with
    each host's own connections opened/closed independently."""
    plan = _start_step_plan(inventory, {1: ("dp01", "fw01")})
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    factory = RecordingConnectionFactory()
    feed = iter(["a", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl = RunController(plan, state, str(tmp_path), factory, inventory)
    ctrl.run()

    assert ctrl.state.is_complete()
    dp01_connects = [c for c in factory.calls("connect") if c[1] == "dp01"]
    fw01_connects = [c for c in factory.calls("connect") if c[1] == "fw01"]
    assert len(dp01_connects) == 2   # root, splunk
    assert len(fw01_connects) == 2   # root, splunk (fw01's start is enable/daemon_reload/start/enable_crontab)


def test_max_parallel_hosts_runs_hosts_concurrently(tmp_path, inventory, monkeypatch):
    """With max_parallel_hosts=2, dp01 and fw01's first actions must genuinely overlap:
    both block on a 2-party barrier, which only releases if both threads reached it at
    the same time. If the implementation regressed to sequential execution, the first
    host to run would block until the barrier's timeout and its action would fail."""
    barrier = threading.Barrier(2, timeout=5)
    hit_barrier = {"dp01": False, "fw01": False}

    def hook(hostname, command):
        if not hit_barrier[hostname]:
            hit_barrier[hostname] = True
            barrier.wait()  # only returns once BOTH hosts have called run_plain
        return CommandResult(exit_code=0, output="ok")

    plan = _start_step_plan(inventory, {1: ("dp01", "fw01")})
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    factory = RecordingConnectionFactory(run_plain_hook=hook)
    feed = iter(["a", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl = RunController(
        plan, state, str(tmp_path), factory, inventory, max_parallel_hosts=2,
    )
    ctrl.run()

    assert ctrl.state.is_complete()
    for _, action_state in ctrl.state.steps[2].all_action_states():
        assert action_state.status == ActionStatus.SUCCESS, action_state.error


def test_single_host_step_never_spawns_a_thread_pool_even_with_max_parallel_hosts(
    tmp_path, inventory, monkeypatch
):
    """A step with only one host has nothing to parallelize - _run_automatic should
    take the sequential branch regardless of max_parallel_hosts (cheap to verify via
    the same connection-count assertion; a real regression here would only show up as
    unnecessary thread-pool overhead, not incorrect results, so this mostly documents
    the intent)."""
    plan = _start_step_plan(inventory, {1: ("dp01",)})
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    factory = RecordingConnectionFactory()
    feed = iter(["a", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl = RunController(plan, state, str(tmp_path), factory, inventory, max_parallel_hosts=4)
    ctrl.run()
    assert ctrl.state.is_complete()
    assert len(factory.calls("connect")) == 2


def test_quit_stops_a_host_before_its_next_action_but_never_mid_action(tmp_path, inventory, monkeypatch):
    """Unit-tests _run_host_block_auto directly (not the full thread pool, to keep this
    deterministic): if quit_event is already set by the time a host's worker checks
    before its NEXT action, that action is left PENDING - but an action already in
    flight when quit fires always finishes and is recorded normally."""
    plan = _start_step_plan(inventory, {1: ("fw01",)})
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    action1_started = threading.Event()
    proceed = threading.Event()

    def hook(hostname, command):
        if not action1_started.is_set():
            action1_started.set()
            assert proceed.wait(timeout=5), "test setup bug: was never released"
        return CommandResult(exit_code=0, output="ok")

    factory = RecordingConnectionFactory(run_plain_hook=hook)
    ctrl = RunController(plan, state, str(tmp_path), factory, inventory)

    step_state = ctrl.state.steps[2]
    items = [
        ("fw01", action, action_state)
        for action, action_state in zip(
            ctrl._plans[2].per_host_actions["fw01"], step_state.per_host["fw01"]
        )
    ]
    quit_event = threading.Event()
    result = {}

    def run_block():
        result["outcome"] = ctrl._run_host_block_auto("fw01", items, quit_event)

    thread = threading.Thread(target=run_block)
    thread.start()
    assert action1_started.wait(timeout=5)  # action 1 is now in flight (already past its pre-check)
    quit_event.set()                        # simulate another host having just quit
    proceed.set()                           # let action 1 finish
    thread.join(timeout=5)

    assert result["outcome"] == "quit"
    statuses = [a.status for _, _, a in items]
    assert statuses[0] == ActionStatus.SUCCESS   # in flight when quit fired - always finishes
    assert all(s == ActionStatus.PENDING for s in statuses[1:])  # never started
