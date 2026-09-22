"""Tests for the pretest orchestration (preflight.py). No real SSH - check_connectivity
and preflight._splunk_show are monkeypatched."""
from auto_patchinator.actions.types import Identity
from auto_patchinator.executor.connectivity import STATUS_FAIL, STATUS_OK, ConnectivityResult
from auto_patchinator.executor.credentials import Credentials, SplunkApiCredentials
from auto_patchinator.plan.action_mapping import map_team_steps
from auto_patchinator.plan.dependency import resolve_order
from auto_patchinator.plan.run_plan import build_run_plan
from auto_patchinator import preflight
from tests.conftest import TEAM, make_raw

# Trimmed but structurally verbatim from a real `splunk show shcluster-status --verbose
# -auth user:pass` response (prdrmlbbspksh01, 2026-09-16, see TODO.md) - a right-aligned
# "key : value" table using the REST content dict's raw (lowercase, underscored) field
# names, not human-prose labels.
SHCLUSTER_STATUS_DYNAMIC = """
 Captain:
		               dynamic_captain : 1
		               elected_captain : Thu Sep 10 18:47:10 2026
		                            id : E60D0AAF-A4A4-44F1-9959-085E83497F12
		              initialized_flag : 1
		                         label : prdmilbbspksh04.sky.local
		                      mgmt_uri : https://prdmilbbspksh04.sky.local:8089
		         min_peers_joined_flag : 1
		            service_ready_flag : 1
		                stable_captain : 1

 Cluster Manager(s):
	https://prdmilbbspkdp01:8089		splunk_version: 9.4.15

 Members:
	prdrmlbbspksh01.sky.local
		                kvstore_status : ready
		                         label : prdrmlbbspksh01.sky.local
		                        status : Up
"""

SHCLUSTER_STATUS_STATIC = SHCLUSTER_STATUS_DYNAMIC.replace("dynamic_captain : 1", "dynamic_captain : 0")

# Trimmed but structurally verbatim from a real `splunk show kvstore-status --verbose`
# response (prdrmlbbspksh01, 2026-09-16, see TODO.md) - one query's "KV store members:"
# section reports every member's replicationStatus, same "ask any member, get the whole
# cluster's picture" pattern as shcluster-status. Deliberately includes the earlier
# "Enabled KV store members:" section too (no replicationStatus field) - a plain
# substring match on "KV store members:" finds that one first and parses nothing
# useful; this was a real live bug (2026-09-16), fixed by anchoring to the exact line.
KVSTORE_STATUS_ALL_HEALTHY = """
 This member:
		                        status : ready

 Enabled KV store members:
	prdrmlbbspksh01.sky.local:8191
		              backupInProgress : 0
		                          guid : 5245C6F2-9B8D-4076-86F4-34D19855511C
		                   hostAndPort : prdrmlbbspksh01.sky.local:8191

 KV store members:
	prdrmlbbspksh01.sky.local:8191
		                 configVersion : 2036
		             replicationStatus : Non-captain KV store member
	prdrmlbbspksh05.sky.local:8191
		                 configVersion : 2036
		             replicationStatus : KV store captain
"""

# prdrmlbbspksh02 really did report this live on 2026-09-16 (see TODO.md) - a member
# down, everything else healthy.
KVSTORE_STATUS_ONE_MEMBER_DOWN = KVSTORE_STATUS_ALL_HEALTHY.replace(
    "\tprdrmlbbspksh01.sky.local:8191\n\t\t                 configVersion : 2036\n"
    "\t\t             replicationStatus : Non-captain KV store member\n",
    "\tprdrmlbbspksh02.sky.local:8191\n\t\t                 configVersion : 1788\n"
    "\t\t             replicationStatus : Down\n",
)


def _plan(inventory, hosts_by_group):
    mapped, _ = map_team_steps([make_raw(2, "Stop application Group 1")], TEAM)
    return build_run_plan(resolve_order(mapped), hosts_by_group, inventory)


def _ok_connectivity(status=STATUS_OK, detail="whoami=ok"):
    """A fake check_connectivity that reports `status` for every (host, identity),
    driving results through on_result exactly like the real one - _run_connectivity_
    block relies on that callback, not the return value."""
    def fake(host_items, identities, credentials, gateway_host, gateway_port, inventory,
              on_result=None, max_workers=1):
        results = []
        for hostname, _host in host_items:
            for identity in identities:
                result = ConnectivityResult(hostname, identity, status, detail)
                results.append(result)
                if on_result:
                    on_result(result)
        return results
    return fake


def _recording_connectivity(calls, status=STATUS_OK, detail="whoami=ok"):
    """Like _ok_connectivity, but also records each call's (hostnames, identities) so
    tests can assert exactly which hosts/identities a given connectivity block
    covered."""
    def fake(host_items, identities, credentials, gateway_host, gateway_port, inventory,
              on_result=None, max_workers=1):
        calls.append(([h for h, _ in host_items], list(identities)))
        results = []
        for hostname, _host in host_items:
            for identity in identities:
                result = ConnectivityResult(hostname, identity, status, detail)
                results.append(result)
                if on_result:
                    on_result(result)
        return results
    return fake


def _fake_splunk_show(responses, calls=None):
    """responses: {(hostname, subcommand): output_text}."""
    def fake(hostname, inventory, credentials, gateway_host, gateway_port, splunk_api_credentials, subcommand):
        if calls is not None:
            calls.append((hostname, subcommand))
        return responses[(hostname, subcommand)]
    return fake


CREDS = SplunkApiCredentials(username="admin", password="hunter2")


def test_plan_hostnames_deduplicates_across_scopes(inventory):
    plan = _plan(inventory, {1: ("dp01", "fw01")})
    assert preflight.plan_hostnames(plan) == ["dp01", "fw01"]


def test_pretest_skips_splunk_checks_when_no_credentials(inventory, monkeypatch, capsys):
    plan = _plan(inventory, {1: ("shx01",)})
    monkeypatch.setattr("auto_patchinator.preflight.check_connectivity", _ok_connectivity())

    def exploding(*a, **kw):
        raise AssertionError("should never run splunk show with no credentials")

    monkeypatch.setattr("auto_patchinator.preflight._splunk_show", exploding)

    ok = preflight.run_pretest(plan, inventory, Credentials("u", "p"), "gw", 22, None)
    assert ok is True
    assert "SKIPPED" in capsys.readouterr().out


def test_pretest_skips_splunk_checks_when_only_a_token_is_configured(inventory, monkeypatch, capsys):
    """`splunk show ... -auth user:pass` needs username+password - a bare token isn't
    enough for these specific checks."""
    plan = _plan(inventory, {1: ("shx01",)})
    monkeypatch.setattr("auto_patchinator.preflight.check_connectivity", _ok_connectivity())

    def exploding(*a, **kw):
        raise AssertionError("should never run splunk show with only a token configured")

    monkeypatch.setattr("auto_patchinator.preflight._splunk_show", exploding)

    ok = preflight.run_pretest(
        plan, inventory, Credentials("u", "p"), "gw", 22, SplunkApiCredentials(token="tok")
    )
    assert ok is True
    assert "SKIPPED" in capsys.readouterr().out


def test_pretest_checks_captain_and_kvstore_once_per_distinct_search_head_role(inventory, monkeypatch):
    """shx01/shx02 (search_head_stretched) and shs01 (search_head_simple) are two
    separate search head clusters - both shcluster-status AND kvstore-status should
    be queried once per role (against that role's first host), not once per host and
    not just once overall - each single query already reports the whole cluster's
    picture from that host's point of view."""
    plan = _plan(inventory, {1: ("shx01", "shx02", "shs01")})
    monkeypatch.setattr("auto_patchinator.preflight.check_connectivity", _ok_connectivity())

    calls = []
    responses = {
        ("shx01", "shcluster-status"): SHCLUSTER_STATUS_DYNAMIC,
        ("shx01", "kvstore-status"): KVSTORE_STATUS_ALL_HEALTHY,
        ("shs01", "shcluster-status"): SHCLUSTER_STATUS_DYNAMIC,
        ("shs01", "kvstore-status"): KVSTORE_STATUS_ALL_HEALTHY,
    }
    monkeypatch.setattr("auto_patchinator.preflight._splunk_show", _fake_splunk_show(responses, calls))

    ok = preflight.run_pretest(plan, inventory, Credentials("u", "p"), "gw", 22, CREDS)
    assert ok is True
    assert sorted(calls) == [("shs01", "kvstore-status"), ("shs01", "shcluster-status"),
                             ("shx01", "kvstore-status"), ("shx01", "shcluster-status")]
    assert not any(h == "shx02" for h, _sub in calls)  # shx02 never queried - shx01 covers the role


def test_static_captain_election_is_flagged_as_a_problem(inventory, monkeypatch, capsys):
    plan = _plan(inventory, {1: ("shx01",)})
    monkeypatch.setattr("auto_patchinator.preflight.check_connectivity", _ok_connectivity())
    responses = {
        ("shx01", "shcluster-status"): SHCLUSTER_STATUS_STATIC,
        ("shx01", "kvstore-status"): KVSTORE_STATUS_ALL_HEALTHY,
    }
    monkeypatch.setattr("auto_patchinator.preflight._splunk_show", _fake_splunk_show(responses))

    ok = preflight.run_pretest(plan, inventory, Credentials("u", "p"), "gw", 22, CREDS)
    assert ok is False
    assert "STATIC" in capsys.readouterr().out


def test_one_down_kvstore_member_is_flagged_without_failing_the_others(inventory, monkeypatch, capsys):
    plan = _plan(inventory, {1: ("shx01",)})
    monkeypatch.setattr("auto_patchinator.preflight.check_connectivity", _ok_connectivity())
    responses = {
        ("shx01", "shcluster-status"): SHCLUSTER_STATUS_DYNAMIC,
        ("shx01", "kvstore-status"): KVSTORE_STATUS_ONE_MEMBER_DOWN,
    }
    monkeypatch.setattr("auto_patchinator.preflight._splunk_show", _fake_splunk_show(responses))

    ok = preflight.run_pretest(plan, inventory, Credentials("u", "p"), "gw", 22, CREDS)
    assert ok is False
    out = capsys.readouterr().out
    assert "prdrmlbbspksh02.sky.local:8191" in out and "Down" in out
    assert "prdrmlbbspksh05.sky.local:8191" in out and "KV store captain" in out


def test_kvstore_query_failure_marks_pretest_unhealthy(inventory, monkeypatch):
    plan = _plan(inventory, {1: ("shx01",)})
    monkeypatch.setattr("auto_patchinator.preflight.check_connectivity", _ok_connectivity())

    def fake_show(hostname, inventory_, credentials, gateway_host, gateway_port, creds, subcommand):
        if subcommand == "shcluster-status":
            return SHCLUSTER_STATUS_DYNAMIC
        raise RuntimeError(f"{hostname}: could not reach management port 8089")

    monkeypatch.setattr("auto_patchinator.preflight._splunk_show", fake_show)

    ok = preflight.run_pretest(plan, inventory, Credentials("u", "p"), "gw", 22, CREDS)
    assert ok is False


def test_connectivity_failure_marks_pretest_unhealthy_even_without_splunk_creds(inventory, monkeypatch):
    plan = _plan(inventory, {1: ("dp01",)})
    monkeypatch.setattr(
        "auto_patchinator.preflight.check_connectivity",
        _ok_connectivity(status=STATUS_FAIL, detail="Authentication failed."),
    )
    ok = preflight.run_pretest(plan, inventory, Credentials("u", "p"), "gw", 22, None)
    assert ok is False


def test_pretest_checks_rest_of_stretched_cluster_splunk_only(inventory, monkeypatch, capsys):
    """Plan only touches shx01 (milano) - shx02 (roma) isn't in the plan, but captain
    transfer/revert already needs to reach it too (whole cluster, both sites), splunk
    identity only - not root."""
    plan = _plan(inventory, {1: ("shx01",)})
    calls = []
    monkeypatch.setattr("auto_patchinator.preflight.check_connectivity", _recording_connectivity(calls))
    monkeypatch.setattr("auto_patchinator.preflight._splunk_show", _fake_splunk_show({
        ("shx01", "shcluster-status"): SHCLUSTER_STATUS_DYNAMIC,
        ("shx01", "kvstore-status"): KVSTORE_STATUS_ALL_HEALTHY,
    }))

    ok = preflight.run_pretest(plan, inventory, Credentials("u", "p"), "gw", 22, CREDS)
    assert ok is True

    # plan-hosts splunk, plan-hosts root, extra-cluster splunk - in that order
    assert len(calls) == 3
    extra_hosts, extra_identities = calls[2]
    assert extra_hosts == ["shx02"]
    assert extra_identities == [Identity.SPLUNK]
    assert "rest of the stretched SH cluster" in capsys.readouterr().out


def test_pretest_skips_extra_cluster_check_when_no_stretched_host_in_plan(inventory, monkeypatch):
    plan = _plan(inventory, {1: ("dp01",)})
    calls = []
    monkeypatch.setattr("auto_patchinator.preflight.check_connectivity", _recording_connectivity(calls))

    ok = preflight.run_pretest(plan, inventory, Credentials("u", "p"), "gw", 22, None)
    assert ok is True
    assert len(calls) == 2  # just the plan-hosts splunk + root blocks
    assert not any("shx02" in hosts for hosts, _identities in calls)


def test_pretest_extra_cluster_check_skipped_when_plan_already_covers_whole_cluster(inventory, monkeypatch):
    plan = _plan(inventory, {1: ("shx01", "shx02")})
    calls = []
    monkeypatch.setattr("auto_patchinator.preflight.check_connectivity", _recording_connectivity(calls))
    monkeypatch.setattr("auto_patchinator.preflight._splunk_show", _fake_splunk_show({
        ("shx01", "shcluster-status"): SHCLUSTER_STATUS_DYNAMIC,
        ("shx01", "kvstore-status"): KVSTORE_STATUS_ALL_HEALTHY,
    }))

    ok = preflight.run_pretest(plan, inventory, Credentials("u", "p"), "gw", 22, CREDS)
    assert ok is True
    assert len(calls) == 2  # nothing "extra" left to check - plan already has both sites


def test_pretest_extra_cluster_connectivity_failure_marks_pretest_unhealthy(inventory, monkeypatch):
    plan = _plan(inventory, {1: ("shx01",)})

    def fake(host_items, identities, credentials, gateway_host, gateway_port, inventory,
              on_result=None, max_workers=1):
        for hostname, _host in host_items:
            for identity in identities:
                status = STATUS_FAIL if hostname == "shx02" else STATUS_OK
                if on_result:
                    on_result(ConnectivityResult(hostname, identity, status, "detail"))

    monkeypatch.setattr("auto_patchinator.preflight.check_connectivity", fake)
    monkeypatch.setattr("auto_patchinator.preflight._splunk_show", _fake_splunk_show({
        ("shx01", "shcluster-status"): SHCLUSTER_STATUS_DYNAMIC,
        ("shx01", "kvstore-status"): KVSTORE_STATUS_ALL_HEALTHY,
    }))

    ok = preflight.run_pretest(plan, inventory, Credentials("u", "p"), "gw", 22, CREDS)
    assert ok is False
