"""Tests for CAPTAIN_TRANSFER (captain_transfer_static) and CAPTAIN_REVERT
(captain_revert_dynamic) - setting/reverting a static SH-cluster captain across the
whole cluster (both sites), not just this wave's own hosts. No real SSH.

CAPTAIN = "shx01" (a real fixture host, milano). OTHER_MEMBERS use dp01/fw01 as
stand-ins for the rest of the cluster - their actual role doesn't affect anything
here (the splunk binary/commands are derived from CAPTAIN's role only, and the fake
connections don't care what role they were opened with). shx02, the fixture's other
search_head_stretched host, is deliberately manual_identities=(SPLUNK,) and is used
specifically for the is_forced_manual test that exercises that path - never as a
plain stand-in member.
"""
import threading

from auto_patchinator.actions.sequences import captain_revert_dynamic, captain_transfer_static
from auto_patchinator.executor.credentials import SplunkApiCredentials
from auto_patchinator.executor.ssh import CommandResult
from auto_patchinator.runner.controller import RunController, is_forced_manual
from auto_patchinator.state import store
from auto_patchinator.state.models import ActionState, ActionStatus

CAPTAIN = "shx01"
OTHER_MEMBERS = ("dp01", "fw01")
CLUSTER = (CAPTAIN,) + OTHER_MEMBERS

CREDS = SplunkApiCredentials(username="admin", password="hunter2")

STATIC_OUTPUT = (
    ' Captain:\n\t\t               dynamic_captain : 0\n'
    '\t\t                         label : shx01.sky.local\n'
)
DYNAMIC_OUTPUT = (
    ' Captain:\n\t\t               dynamic_captain : 1\n'
    '\t\t                         label : shx01.sky.local\n'
)
WRONG_CAPTAIN_STATIC_OUTPUT = STATIC_OUTPUT.replace("shx01", "dp01")


def _transfer_action(poll_interval=0, timeout=5):
    action = captain_transfer_static(CAPTAIN, CLUSTER)
    object.__setattr__(action, "poll_interval_seconds", poll_interval)
    object.__setattr__(action, "timeout_seconds", timeout)
    return action


def _revert_action(poll_interval=0, timeout=5):
    action = captain_revert_dynamic(CAPTAIN, CLUSTER)
    object.__setattr__(action, "poll_interval_seconds", poll_interval)
    object.__setattr__(action, "timeout_seconds", timeout)
    return action


def test_is_forced_manual_without_splunk_api_credentials(inventory):
    assert is_forced_manual(inventory, "__pre_group__", _transfer_action(), None) is True
    assert is_forced_manual(inventory, "__post_group__", _revert_action(), None) is True


def test_is_forced_manual_with_splunk_api_credentials(inventory):
    assert is_forced_manual(inventory, "__pre_group__", _transfer_action(), CREDS) is False
    assert is_forced_manual(inventory, "__post_group__", _revert_action(), CREDS) is False


def test_is_forced_manual_when_a_cluster_host_is_manual_only_identity(inventory):
    """shx02 is manual_identities=(SPLUNK,) in the fixture - can't automate 'touch
    every member' if one member can't be reached via SSH at all, even with credentials."""
    action = captain_transfer_static("shx01", ("shx01", "shx02"))
    object.__setattr__(action, "poll_interval_seconds", 0)
    assert is_forced_manual(inventory, "__pre_group__", action, CREDS) is True


class _CallLog:
    """Thread-safe call recorder - the "other members" step genuinely runs
    concurrently (ThreadPoolExecutor)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls: list[tuple] = []

    def add(self, *entry):
        with self._lock:
            self.calls.append(entry)

    def hostnames_touched(self):
        with self._lock:
            return {c[0] for c in self.calls}


def _make_factory(log, *, fail_hosts=(), poll_outputs=None, fail_bootstrap=False):
    """poll_outputs: iterator of raw shcluster-status outputs, one per
    run_plain_with_secret status-poll call on the captain connection (StopIteration
    if polled more times than provided - fails the test loudly, matching the
    kvstore-confirm tests' "exhausted feed" pattern)."""
    poll_iter = iter(poll_outputs or [])

    class Fake:
        def __init__(self, hostname, identity, role, **kw):
            self.hostname = hostname
            self.identity = identity
            self.closed = False

        def connect(self):
            log.add(self.hostname, "connect")

        def close(self):
            self.closed = True
            log.add(self.hostname, "close")

        def run_plain(self, command, timeout=60):
            log.add(self.hostname, "run_plain", command)
            if self.hostname in fail_hosts:
                return CommandResult(exit_code=1, output=f"{self.hostname}: simulated failure")
            return CommandResult(exit_code=0, output="ok")

        def run_plain_with_secret(self, command, secret, timeout=60):
            # Every command now goes through here (edit shcluster-config included, at
            # the operator's request - see TODO.md), not just bootstrap/status-poll.
            log.add(self.hostname, "run_plain_with_secret", command)
            if "bootstrap" in command:
                if fail_bootstrap:
                    return CommandResult(exit_code=1, output="bootstrap failed")
                return CommandResult(exit_code=0, output="ok")
            if "show shcluster-status" in command:
                return CommandResult(exit_code=0, output=next(poll_iter))
            # an edit shcluster-config call (become captain/member, or re-enable election)
            if self.hostname in fail_hosts:
                return CommandResult(exit_code=1, output=f"{self.hostname}: simulated failure")
            return CommandResult(exit_code=0, output="ok")

    return lambda **kw: Fake(**kw)


def _controller(inventory, connection_factory):
    ctrl = RunController(
        [], store.build_initial_state("t", "p.xlsx", "s", []), "unused-state-dir",
        connection_factory, inventory, splunk_api_credentials=CREDS,
    )
    ctrl._save = lambda: None
    return ctrl


# ---------------------------------------------------------------------------
# CAPTAIN_TRANSFER
# ---------------------------------------------------------------------------

def test_transfer_success_on_first_poll(inventory):
    log = _CallLog()
    factory = _make_factory(log, poll_outputs=[STATIC_OUTPUT])
    ctrl = _controller(inventory, factory)
    action = _transfer_action()
    action_state = ActionState(name=action.name)

    ctrl._execute("__pre_group__", action, action_state)

    assert action_state.status == ActionStatus.SUCCESS
    assert log.hostnames_touched() == set(CLUSTER)
    become_captain_calls = [c for c in log.calls if c[0] == CAPTAIN and c[1] == "run_plain_with_secret"]
    assert any("mode captain" in c[2] for c in become_captain_calls)
    for host in OTHER_MEMBERS:
        member_calls = [c for c in log.calls if c[0] == host and c[1] == "run_plain_with_secret"]
        assert any("mode member" in c[2] for c in member_calls)


def test_transfer_fails_when_become_captain_command_fails(inventory):
    log = _CallLog()
    factory = _make_factory(log, fail_hosts={CAPTAIN})
    ctrl = _controller(inventory, factory)
    action = _transfer_action()
    action_state = ActionState(name=action.name)

    ctrl._execute("__pre_group__", action, action_state)

    assert action_state.status == ActionStatus.FAILED
    assert CAPTAIN in action_state.error
    # never touched the other members - fails fast before pointing anyone at a
    # captain that never actually got set
    assert log.hostnames_touched() == {CAPTAIN}


def test_transfer_fails_when_a_member_fails(inventory):
    log = _CallLog()
    factory = _make_factory(log, fail_hosts={"dp01"})
    ctrl = _controller(inventory, factory)
    action = _transfer_action()
    action_state = ActionState(name=action.name)

    ctrl._execute("__pre_group__", action, action_state)

    assert action_state.status == ActionStatus.FAILED
    assert "dp01" in action_state.error
    # never polled for verification (the "become captain" call to CAPTAIN is expected -
    # it's the "every other member" step, run after it, that failed)
    assert not any("show shcluster-status" in c[2] for c in log.calls if c[1] == "run_plain_with_secret")


def test_transfer_times_out_if_never_confirmed(inventory):
    log = _CallLog()
    # every poll shows the WRONG captain - never matches, must time out rather than loop forever
    factory = _make_factory(log, poll_outputs=[WRONG_CAPTAIN_STATIC_OUTPUT])
    ctrl = _controller(inventory, factory)
    action = _transfer_action(timeout=0)
    action_state = ActionState(name=action.name)

    ctrl._execute("__pre_group__", action, action_state)

    assert action_state.status == ActionStatus.FAILED
    assert "timed out" in action_state.error


def test_transfer_dry_run_never_connects(inventory):
    def exploding_factory(**kw):
        raise AssertionError("dry-run should never open a real connection")

    ctrl = RunController(
        [], store.build_initial_state("t", "p.xlsx", "s", []), "unused-state-dir",
        exploding_factory, inventory, dry_run=True, splunk_api_credentials=CREDS,
    )
    ctrl._save = lambda: None
    action = _transfer_action()
    action_state = ActionState(name=action.name)
    ctrl._execute("__pre_group__", action, action_state)
    assert action_state.status == ActionStatus.SUCCESS
    assert "dry-run" in action_state.output


# ---------------------------------------------------------------------------
# CAPTAIN_REVERT
# ---------------------------------------------------------------------------

def test_revert_success_on_first_poll(inventory):
    log = _CallLog()
    factory = _make_factory(log, poll_outputs=[DYNAMIC_OUTPUT])
    ctrl = _controller(inventory, factory)
    action = _revert_action()
    action_state = ActionState(name=action.name)

    ctrl._execute("__post_group__", action, action_state)

    assert action_state.status == ActionStatus.SUCCESS
    for host in OTHER_MEMBERS + (CAPTAIN,):
        election_calls = [c for c in log.calls if c[0] == host and c[1] == "run_plain_with_secret"]
        assert any("election true" in c[2] and f"https://{host}." in c[2] for c in election_calls), host
    bootstrap_calls = [c for c in log.calls if c[0] == CAPTAIN and c[1] == "run_plain_with_secret"]
    assert any("bootstrap" in c[2] for c in bootstrap_calls)


def test_revert_fails_when_a_member_fails(inventory):
    log = _CallLog()
    factory = _make_factory(log, fail_hosts={"fw01"})
    ctrl = _controller(inventory, factory)
    action = _revert_action()
    action_state = ActionState(name=action.name)

    ctrl._execute("__post_group__", action, action_state)

    assert action_state.status == ActionStatus.FAILED
    assert "fw01" in action_state.error
    # never even connected to the captain - fails before touching it or bootstrapping
    assert CAPTAIN not in log.hostnames_touched()


def test_revert_fails_when_captains_own_election_true_fails(inventory):
    log = _CallLog()
    factory = _make_factory(log, fail_hosts={CAPTAIN})
    ctrl = _controller(inventory, factory)
    action = _revert_action()
    action_state = ActionState(name=action.name)

    ctrl._execute("__post_group__", action, action_state)

    assert action_state.status == ActionStatus.FAILED
    assert CAPTAIN in action_state.error
    assert not any("bootstrap" in c[2] for c in log.calls if c[1] == "run_plain_with_secret")


def test_revert_fails_when_bootstrap_fails(inventory):
    log = _CallLog()
    factory = _make_factory(log, fail_bootstrap=True)
    ctrl = _controller(inventory, factory)
    action = _revert_action()
    action_state = ActionState(name=action.name)

    ctrl._execute("__post_group__", action, action_state)

    assert action_state.status == ActionStatus.FAILED
    assert "bootstrap" in action_state.error.lower()


def test_revert_bootstrap_command_never_contains_the_literal_password(inventory):
    log = _CallLog()
    factory = _make_factory(log, poll_outputs=[DYNAMIC_OUTPUT])
    ctrl = _controller(inventory, factory)
    action = _revert_action()
    action_state = ActionState(name=action.name)

    ctrl._execute("__post_group__", action, action_state)

    bootstrap_calls = [c for c in log.calls if c[1] == "run_plain_with_secret" and "bootstrap" in c[2]]
    assert bootstrap_calls
    for call in bootstrap_calls:
        assert "hunter2" not in call[2]  # only $AP_SECRET, never the literal password
        assert "$AP_SECRET" in call[2]


def test_revert_times_out_if_never_confirmed(inventory):
    log = _CallLog()
    factory = _make_factory(log, poll_outputs=[STATIC_OUTPUT])  # still static, never dynamic
    ctrl = _controller(inventory, factory)
    action = _revert_action(timeout=0)
    action_state = ActionState(name=action.name)

    ctrl._execute("__post_group__", action, action_state)

    assert action_state.status == ActionStatus.FAILED
    assert "timed out" in action_state.error


def test_revert_dry_run_never_connects(inventory):
    def exploding_factory(**kw):
        raise AssertionError("dry-run should never open a real connection")

    ctrl = RunController(
        [], store.build_initial_state("t", "p.xlsx", "s", []), "unused-state-dir",
        exploding_factory, inventory, dry_run=True, splunk_api_credentials=CREDS,
    )
    ctrl._save = lambda: None
    action = _revert_action()
    action_state = ActionState(name=action.name)
    ctrl._execute("__post_group__", action, action_state)
    assert action_state.status == ActionStatus.SUCCESS
    assert "dry-run" in action_state.output
