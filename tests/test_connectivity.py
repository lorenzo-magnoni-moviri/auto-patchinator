"""Tests for the shared check_connectivity() primitive (no real SSH)."""
import threading

from auto_patchinator.actions.types import Identity
from auto_patchinator.config.inventory import Host, Inventory
from auto_patchinator.executor.connectivity import (
    STATUS_FAIL,
    STATUS_OK,
    STATUS_SKIP,
    check_connectivity,
    extract_whoami,
)
from auto_patchinator.executor.credentials import Credentials
from auto_patchinator.executor.ssh import EXIT_MARKER, PROMPT_MARKER


class _FakeResult:
    def __init__(self, success: bool, exit_code: int = 0):
        self.success = success
        self.exit_code = exit_code
        self.output = f"whoami; echo {EXIT_MARKER}:{exit_code}\nsplunk\n{PROMPT_MARKER}"


class _FakeConnection:
    def __init__(self, hostname, identity, role, outcome):
        self.hostname = hostname
        self._outcome = outcome  # "ok" | "fail" | "raise"

    def connect(self):
        if self._outcome == "raise":
            raise RuntimeError("Authentication failed.")

    def close(self):
        pass

    def run_plain(self, command, timeout=60):
        return _FakeResult(success=self._outcome == "ok")


def _factory(outcomes):
    def factory(hostname, identity, role, credentials, **kwargs):
        return _FakeConnection(hostname, identity, role, outcomes[hostname])
    return factory


def _inventory(hosts_and_outcomes):
    from auto_patchinator.actions.sequences import NodeRole
    hosts = {h: Host(hostname=h, role=NodeRole.DEPLOYER, site="milano") for h in hosts_and_outcomes}
    return Inventory(hosts=hosts, environment="prod")


def test_extract_whoami_pulls_the_username_line():
    raw = f"whoami; echo {EXIT_MARKER}:0\nsplunk\n{PROMPT_MARKER}"
    assert extract_whoami(raw) == "splunk"


def test_ok_fail_and_exception_are_reported_distinctly(monkeypatch):
    outcomes = {"h1": "ok", "h2": "fail", "h3": "raise"}
    inventory = _inventory(outcomes)
    monkeypatch.setattr(
        "auto_patchinator.executor.connectivity.SSHConnection",
        lambda hostname, identity, role, credentials, **kw: _FakeConnection(hostname, identity, role, outcomes[hostname]),
    )
    host_items = [(h, inventory.get(h)) for h in outcomes]
    results = check_connectivity(
        host_items, [Identity.SPLUNK], Credentials("u", "p"), "gw", 22, inventory
    )
    by_host = {r.hostname: r for r in results}
    assert by_host["h1"].status == STATUS_OK
    assert "whoami=" in by_host["h1"].detail
    assert by_host["h2"].status == STATUS_FAIL
    assert by_host["h3"].status == STATUS_FAIL
    assert "Authentication failed" in by_host["h3"].detail


def test_manual_only_identity_is_skipped_without_connecting(monkeypatch):
    from auto_patchinator.actions.sequences import NodeRole
    host = Host(hostname="h1", role=NodeRole.DEPLOYER, site="milano", manual_identities=(Identity.ROOT,))
    inventory = Inventory(hosts={"h1": host}, environment="prod")

    def exploding_factory(*a, **kw):
        raise AssertionError("should never attempt to connect for a manual-only identity")

    monkeypatch.setattr("auto_patchinator.executor.connectivity.SSHConnection", exploding_factory)
    results = check_connectivity(
        [("h1", host)], [Identity.ROOT], Credentials("u", "p"), "gw", 22, inventory
    )
    assert results[0].status == STATUS_SKIP


def test_on_result_callback_fires_per_result(monkeypatch):
    outcomes = {"h1": "ok"}
    inventory = _inventory(outcomes)
    monkeypatch.setattr(
        "auto_patchinator.executor.connectivity.SSHConnection",
        lambda hostname, identity, role, credentials, **kw: _FakeConnection(hostname, identity, role, "ok"),
    )
    seen = []
    check_connectivity(
        [("h1", inventory.get("h1"))], [Identity.SPLUNK], Credentials("u", "p"), "gw", 22, inventory,
        on_result=seen.append,
    )
    assert len(seen) == 1
    assert seen[0].hostname == "h1"


def test_max_workers_runs_hosts_concurrently(monkeypatch):
    """With max_workers=2, two hosts must genuinely overlap: both block on a 2-party
    barrier, which only releases if both threads reached it at the same time. If the
    implementation regressed to sequential execution, the first host would block
    until the barrier's timeout and its connect() would raise."""
    barrier = threading.Barrier(2, timeout=5)
    hit_barrier = {"h1": False, "h2": False}

    class BarrierConnection:
        def __init__(self, hostname, identity, role, credentials, **kw):
            self.hostname = hostname

        def connect(self):
            if not hit_barrier[self.hostname]:
                hit_barrier[self.hostname] = True
                barrier.wait()  # only returns once BOTH hosts have called connect()

        def close(self):
            pass

        def run_plain(self, command, timeout=60):
            return _FakeResult(success=True)

    monkeypatch.setattr("auto_patchinator.executor.connectivity.SSHConnection", BarrierConnection)
    inventory = _inventory({"h1": "ok", "h2": "ok"})
    host_items = [("h1", inventory.get("h1")), ("h2", inventory.get("h2"))]

    results = check_connectivity(
        host_items, [Identity.SPLUNK], Credentials("u", "p"), "gw", 22, inventory, max_workers=2
    )
    assert all(r.status == STATUS_OK for r in results)


def test_max_workers_preserves_host_order_in_the_returned_list(monkeypatch):
    """Concurrent hosts can finish in any order - the returned list should still
    reflect the original host_items order, not completion order."""
    release = {"h1": threading.Event(), "h2": threading.Event()}
    release["h2"].set()  # h2 finishes immediately; h1 waits, so h1 completes LAST

    class OrderedConnection:
        def __init__(self, hostname, identity, role, credentials, **kw):
            self.hostname = hostname

        def connect(self):
            assert release[self.hostname].wait(timeout=5)

        def close(self):
            pass

        def run_plain(self, command, timeout=60):
            return _FakeResult(success=True)

    monkeypatch.setattr("auto_patchinator.executor.connectivity.SSHConnection", OrderedConnection)
    inventory = _inventory({"h1": "ok", "h2": "ok"})
    host_items = [("h1", inventory.get("h1")), ("h2", inventory.get("h2"))]

    timer = threading.Timer(0.05, release["h1"].set)
    timer.start()
    results = check_connectivity(
        host_items, [Identity.SPLUNK], Credentials("u", "p"), "gw", 22, inventory, max_workers=2
    )
    timer.cancel()
    assert [r.hostname for r in results] == ["h1", "h2"]  # input order, not completion order


def test_max_workers_1_stays_single_threaded_by_default(monkeypatch):
    """Default is sequential - no thread pool involved, same as before this feature."""
    outcomes = {"h1": "ok", "h2": "ok"}
    inventory = _inventory(outcomes)
    monkeypatch.setattr(
        "auto_patchinator.executor.connectivity.SSHConnection",
        lambda hostname, identity, role, credentials, **kw: _FakeConnection(hostname, identity, role, "ok"),
    )
    host_items = [(h, inventory.get(h)) for h in outcomes]
    results = check_connectivity(host_items, [Identity.SPLUNK], Credentials("u", "p"), "gw", 22, inventory)
    assert [r.hostname for r in results] == ["h1", "h2"]
