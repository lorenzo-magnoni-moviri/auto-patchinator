"""Tests for the SCHEDULER_WAIT action (wait_for_scheduling_resumed) - polling
splunk list shcluster-member-info after a search head restarts, to confirm the
scheduler's delegation pool actually accepted it back (not just basic shcluster
membership health, which is what CLUSTER_WAIT checks - see test_controller_cluster_wait.py
and the 2026-10-22 incident noted in actions/sequences.py). No real SSH."""
from auto_patchinator.executor.credentials import SplunkApiCredentials
from auto_patchinator.executor.ssh import CommandResult
from auto_patchinator.plan.action_mapping import map_team_steps
from auto_patchinator.plan.dependency import resolve_order
from auto_patchinator.plan.run_plan import build_run_plan
from auto_patchinator.runner.controller import RunController, is_forced_manual
from auto_patchinator.state import store
from auto_patchinator.state.models import ActionStatus
from tests.conftest import TEAM, make_raw

# Real captured `splunk list shcluster-member-info` output shape (2026-10-22) - tight
# "key:value", no padding, unlike show shcluster-status's right-aligned "key : value".
ACTIVE_OUTPUT = (
    "\t\tactive_historical_search_count:7\r\n"
    "\t\tactive_realtime_search_count:0\r\n"
)
INACTIVE_OUTPUT = ACTIVE_OUTPUT.replace("active_historical_search_count:7", "active_historical_search_count:0")


class FakeConnection:
    def __init__(self, hostname, identity, role, **kw):
        self.hostname = hostname
        self.closed = False

    def connect(self):
        pass

    def close(self):
        self.closed = True

    def run_plain(self, command, timeout=60):
        return CommandResult(exit_code=0, output="")

    def run_plain_with_secret(self, command, secret, timeout=60):
        raise NotImplementedError  # overridden per-test


def _plan_and_state(inventory, hostname="shx01"):
    mapped, _ = map_team_steps([make_raw(4, "Start application Group 1", deps=())], TEAM)
    plan = build_run_plan(resolve_order(mapped), {1: (hostname,)}, inventory)
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    return plan, state


def _scheduler_wait_action(plan, hostname="shx01"):
    actions = plan[0].per_host_actions[hostname]
    action = next(a for a in actions if a.name == "wait_for_scheduling_resumed")
    return action, actions.index(action)


def test_is_forced_manual_without_splunk_api_credentials(inventory):
    plan, _ = _plan_and_state(inventory)
    action, _ = _scheduler_wait_action(plan)
    assert is_forced_manual(inventory, "shx01", action, None) is True
    assert is_forced_manual(inventory, "shx01", action, SplunkApiCredentials(token="tok")) is True  # no user+pass


def test_is_forced_manual_with_splunk_api_credentials(inventory):
    plan, _ = _plan_and_state(inventory)
    action, _ = _scheduler_wait_action(plan)
    creds = SplunkApiCredentials(username="admin", password="hunter2")
    assert is_forced_manual(inventory, "shx01", action, creds) is False


def _controller(inventory, plan, state, connection_factory, poll_interval=0, timeout=5):
    ctrl = RunController(
        plan, state, "unused-state-dir", connection_factory, inventory,
        splunk_api_credentials=SplunkApiCredentials(username="admin", password="hunter2"),
    )
    ctrl._save = lambda: None  # no real state dir in these tests
    action, index = _scheduler_wait_action(plan)
    object.__setattr__(action, "poll_interval_seconds", poll_interval)
    object.__setattr__(action, "timeout_seconds", timeout)
    action_state = state.steps[4].per_host["shx01"][index]
    return ctrl, action, action_state


def test_active_on_first_poll(inventory):
    plan, state = _plan_and_state(inventory)

    class Active(FakeConnection):
        def run_plain_with_secret(self, command, secret, timeout=60):
            return CommandResult(exit_code=0, output=ACTIVE_OUTPUT)

    ctrl, action, action_state = _controller(inventory, plan, state, lambda **kw: Active(**kw))
    ctrl._execute("shx01", action, action_state)
    assert action_state.status == ActionStatus.SUCCESS
    assert "active_historical_search_count=7" in action_state.output


def test_inactive_then_active_after_a_few_polls(inventory):
    plan, state = _plan_and_state(inventory)
    attempts = {"n": 0}

    class EventuallyActive(FakeConnection):
        def run_plain_with_secret(self, command, secret, timeout=60):
            attempts["n"] += 1
            return CommandResult(exit_code=0, output=ACTIVE_OUTPUT if attempts["n"] >= 3 else INACTIVE_OUTPUT)

    ctrl, action, action_state = _controller(inventory, plan, state, lambda **kw: EventuallyActive(**kw))
    ctrl._execute("shx01", action, action_state)
    assert action_state.status == ActionStatus.SUCCESS
    assert attempts["n"] == 3


def test_never_active_times_out_with_remediation_suggestion(inventory):
    plan, state = _plan_and_state(inventory)

    class NeverActive(FakeConnection):
        def run_plain_with_secret(self, command, secret, timeout=60):
            return CommandResult(exit_code=0, output=INACTIVE_OUTPUT)

    ctrl, action, action_state = _controller(inventory, plan, state, lambda **kw: NeverActive(**kw), timeout=0)
    ctrl._execute("shx01", action, action_state)
    assert action_state.status == ActionStatus.FAILED
    assert "timed out" in action_state.error
    # The operator-requested failure guidance (2026-10-22): wait longer first, restart
    # only if it's still not scheduling after that.
    assert "wait a few more minutes" in action_state.error
    assert "restart" in action_state.error


def test_connection_error_marks_failed(inventory):
    plan, state = _plan_and_state(inventory)

    class Exploding(FakeConnection):
        def run_plain_with_secret(self, command, secret, timeout=60):
            raise RuntimeError("connection reset")

    ctrl, action, action_state = _controller(inventory, plan, state, lambda **kw: Exploding(**kw))
    ctrl._execute("shx01", action, action_state)
    assert action_state.status == ActionStatus.FAILED
    assert "connection reset" in action_state.error


def test_dry_run_never_connects(inventory):
    plan, state = _plan_and_state(inventory)

    def exploding_factory(**kw):
        raise AssertionError("dry-run should never open a real connection")

    ctrl = RunController(
        plan, state, "unused-state-dir", exploding_factory, inventory,
        dry_run=True, splunk_api_credentials=SplunkApiCredentials(username="admin", password="hunter2"),
    )
    ctrl._save = lambda: None
    action, index = _scheduler_wait_action(plan)
    action_state = state.steps[4].per_host["shx01"][index]
    ctrl._execute("shx01", action, action_state)
    assert action_state.status == ActionStatus.SUCCESS
    assert "dry-run" in action_state.output
