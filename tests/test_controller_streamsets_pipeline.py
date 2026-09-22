"""Tests for the STREAMSETS_PIPELINE action (stop_streamsets_pipeline /
start_streamsets_pipeline) - POST stop or start via the StreamSets Data Collector REST
API, then poll status until the target status is reached. No real SSH.

scope="fw01" (an ordinary fixture host, not prdmilbbspkfw02) is used throughout -
_execute_streamsets_pipeline only needs *some* valid inventory host to resolve a role
for the connection factory; which host isn't relevant to this action's own logic
(pipeline id/target status come from the Action itself, built directly via
stop_streamsets_pipeline()/start_streamsets_pipeline() rather than through the real
HOST_OVERRIDES plan-building path, which is exercised separately in
tests/test_sequences.py). Most scenarios below are only exercised for stop - the
controller logic is identical for start (same method, branching only on
action.target_status), so test_started_after_start_call_and_one_status_poll below
exists mainly to confirm the verb selection (POST .../start, not .../stop) actually
works, not to re-prove every failure mode a second time.
"""
from auto_patchinator.actions.sequences import start_streamsets_pipeline, stop_streamsets_pipeline
from auto_patchinator.executor.credentials import StreamSetsApiCredentials
from auto_patchinator.executor.ssh import CommandResult
from auto_patchinator.runner.controller import RunController, is_forced_manual
from auto_patchinator.state import store
from auto_patchinator.state.models import ActionState, ActionStatus

PIPELINE_ID = "testpipeldd81d72c-ef3b-4ef8-9099-2816551a7633"

STARTING_OUTPUT = '{"status" : "STARTING"}\nHTTPSTATUS:200'
RUNNING_OUTPUT = '{"status" : "RUNNING"}\nHTTPSTATUS:200'
STOPPING_OUTPUT = '{"status" : "STOPPING"}\nHTTPSTATUS:200'
STOPPED_OUTPUT = '{"status" : "STOPPED"}\nHTTPSTATUS:200'
UNAUTHORIZED_OUTPUT = "Unauthorized\nHTTPSTATUS:401"


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


def _action(poll_interval=0, timeout=5, start=False):
    factory = start_streamsets_pipeline if start else stop_streamsets_pipeline
    action = factory("RDK", PIPELINE_ID)
    object.__setattr__(action, "poll_interval_seconds", poll_interval)
    object.__setattr__(action, "timeout_seconds", timeout)
    return action


def test_is_forced_manual_without_streamsets_credentials(inventory):
    action = _action()
    assert is_forced_manual(inventory, "fw01", action, None, None) is True
    # username without password isn't configured either
    creds = StreamSetsApiCredentials(username="admin")
    assert is_forced_manual(inventory, "fw01", action, None, creds) is True


def test_is_forced_manual_with_streamsets_credentials(inventory):
    action = _action()
    creds = StreamSetsApiCredentials(username="admin", password="hunter2")
    assert is_forced_manual(inventory, "fw01", action, None, creds) is False


def _controller(inventory, connection_factory, dry_run=False):
    ctrl = RunController(
        [], store.build_initial_state("t", "p.xlsx", "s", []), "unused-state-dir",
        connection_factory, inventory, dry_run=dry_run,
        streamsets_api_credentials=StreamSetsApiCredentials(username="admin", password="hunter2"),
    )
    ctrl._save = lambda: None  # no real state dir in these tests
    return ctrl


def test_stopped_after_stop_call_and_one_status_poll(inventory):
    """Stop call returns STOPPING (transitional); the very next status poll already
    shows STOPPED."""
    calls = []

    class EventuallyStopped(FakeConnection):
        def run_plain_with_secret(self, command, secret, timeout=60):
            calls.append(command)
            if "/stop?" in command:
                return CommandResult(exit_code=0, output=STOPPING_OUTPUT)
            return CommandResult(exit_code=0, output=STOPPED_OUTPUT)

    ctrl = _controller(inventory, lambda **kw: EventuallyStopped(**kw))
    action = _action()
    action_state = ActionState(name=action.name)
    ctrl._execute("fw01", action, action_state)

    assert action_state.status == ActionStatus.SUCCESS
    assert "RDK" in action_state.output and "STOPPED" in action_state.output
    assert any("/stop?" in c for c in calls)
    assert any("/status?" in c for c in calls)


def test_started_after_start_call_and_one_status_poll(inventory):
    """Mirror of the stop test above, confirming the verb selection for target_status
    RUNNING actually issues POST .../start (not .../stop)."""
    calls = []

    class EventuallyRunning(FakeConnection):
        def run_plain_with_secret(self, command, secret, timeout=60):
            calls.append(command)
            if "/start?" in command:
                return CommandResult(exit_code=0, output=STARTING_OUTPUT)
            return CommandResult(exit_code=0, output=RUNNING_OUTPUT)

    ctrl = _controller(inventory, lambda **kw: EventuallyRunning(**kw))
    action = _action(start=True)
    action_state = ActionState(name=action.name)
    ctrl._execute("fw01", action, action_state)

    assert action_state.status == ActionStatus.SUCCESS
    assert "RDK" in action_state.output and "RUNNING" in action_state.output
    assert any("/start?" in c for c in calls)
    assert not any("/stop?" in c for c in calls)


def test_still_stopping_across_several_polls_then_settles(inventory):
    attempts = {"n": 0}

    class SlowToStop(FakeConnection):
        def run_plain_with_secret(self, command, secret, timeout=60):
            if "/stop?" in command:
                return CommandResult(exit_code=0, output=STOPPING_OUTPUT)
            attempts["n"] += 1
            return CommandResult(exit_code=0, output=STOPPED_OUTPUT if attempts["n"] >= 3 else STOPPING_OUTPUT)

    ctrl = _controller(inventory, lambda **kw: SlowToStop(**kw))
    action = _action()
    action_state = ActionState(name=action.name)
    ctrl._execute("fw01", action, action_state)

    assert action_state.status == ActionStatus.SUCCESS
    assert attempts["n"] == 3


def test_never_settles_times_out(inventory):
    class NeverStops(FakeConnection):
        def run_plain_with_secret(self, command, secret, timeout=60):
            return CommandResult(exit_code=0, output=STOPPING_OUTPUT)

    ctrl = _controller(inventory, lambda **kw: NeverStops(**kw))
    action = _action(timeout=0)
    action_state = ActionState(name=action.name)
    ctrl._execute("fw01", action, action_state)

    assert action_state.status == ActionStatus.FAILED
    assert "timed out" in action_state.error
    assert "RDK" in action_state.error


def test_stop_call_http_error_fails_without_polling(inventory):
    calls = []

    class Unauthorized(FakeConnection):
        def run_plain_with_secret(self, command, secret, timeout=60):
            calls.append(command)
            return CommandResult(exit_code=0, output=UNAUTHORIZED_OUTPUT)

    ctrl = _controller(inventory, lambda **kw: Unauthorized(**kw))
    action = _action()
    action_state = ActionState(name=action.name)
    ctrl._execute("fw01", action, action_state)

    assert action_state.status == ActionStatus.FAILED
    assert "HTTP 200" in action_state.error
    assert len(calls) == 1  # never polled status after a failed trigger call


def test_connection_error_marks_failed(inventory):
    class Exploding(FakeConnection):
        def run_plain_with_secret(self, command, secret, timeout=60):
            raise RuntimeError("connection reset")

    ctrl = _controller(inventory, lambda **kw: Exploding(**kw))
    action = _action()
    action_state = ActionState(name=action.name)
    ctrl._execute("fw01", action, action_state)

    assert action_state.status == ActionStatus.FAILED
    assert "connection reset" in action_state.error


def test_dry_run_never_connects(inventory):
    def exploding_factory(**kw):
        raise AssertionError("dry-run should never open a real connection")

    ctrl = _controller(inventory, exploding_factory, dry_run=True)
    action = _action()
    action_state = ActionState(name=action.name)
    ctrl._execute("fw01", action, action_state)

    assert action_state.status == ActionStatus.SUCCESS
    assert "dry-run" in action_state.output
