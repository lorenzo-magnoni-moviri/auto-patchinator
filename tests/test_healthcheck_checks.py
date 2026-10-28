"""Tests for the `health-check` subcommand's checks (auto_patchinator/healthcheck/
checks.py). No real SSH - the module's one seam, _run_splunk_command, and
executor.connectivity.check_connectivity are monkeypatched, same pattern as
test_preflight.py's _splunk_show monkeypatching."""
from auto_patchinator.executor.connectivity import STATUS_FAIL, STATUS_OK, ConnectivityResult
from auto_patchinator.executor.credentials import Credentials, SplunkApiCredentials
from auto_patchinator.healthcheck import checks as hc

# Trimmed but structurally verbatim from test_preflight.py's own real-capture samples
# (same shcluster-status/kvstore-status shape, reused here instead of re-deriving it).
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
	shx01
		                kvstore_status : ready
		                         label : shx01
		                        status : Up
		              out_of_sync_node : 0
		              restart_required : 0
"""

SHCLUSTER_STATUS_STATIC = SHCLUSTER_STATUS_DYNAMIC.replace("dynamic_captain : 1", "dynamic_captain : 0")

MEMBER_INFO_ACTIVE = "active_historical_search_count : 3\nis_registered : 1\n"
MEMBER_INFO_INACTIVE = "active_historical_search_count : 0\nis_registered : 1\n"

KVSTORE_STATUS_ALL_HEALTHY = """
 KV store members:
	shx01:8191
		             replicationStatus : Non-captain KV store member
	shx02:8191
		             replicationStatus : KV store captain
"""

KVSTORE_STATUS_ONE_DOWN = """
 KV store members:
	shx01:8191
		             replicationStatus : Down
	shx02:8191
		             replicationStatus : KV store captain
"""

# Trimmed but structurally verbatim from a real `splunk show cluster-status --verbose`
# response (prdmilbbspkdp01, 2026-10-28) - two peers plus one index/bucket-count block,
# which exercises the "distinguish peer blocks from index blocks by the GUID" logic.
CLUSTER_STATUS_SAMPLE = """
 Pre-flight check successful .................. \x1b[31mNO\x1b[0m
 ├────── Site replication factor met .......... \x1b[31mNO\x1b[0m
 ├────── Site search factor met ............... \x1b[32mYES\x1b[0m
 └────── All peers are up ..................... \x1b[31mNO\x1b[0m

 ix01 \t 3C7753AF-799D-4B39-B782-6DF9AFDCEC5B\t site1
\t Searchable \x1b[31mNO\x1b[0m
\t Status  \x1b[31mStopped\x1b[0m
\t Bucket Count=0

 ix02.sky.local \t BB98C638-F6D3-4EBF-8F2B-2D41D9216680\t site2
\t Searchable \x1b[32mYES\x1b[0m
\t Status  \x1b[32mUp\x1b[0m
\t Bucket Count=241891

 _audit
\t Number of non-site aware buckets=0
\t Size=254118854916
\t Searchable  \x1b[32mYES\x1b[0m
"""


def _ctx(inventory, configured=True, max_parallel_hosts=3):
    splunk_api_creds = (
        SplunkApiCredentials(username="admin", password="secret") if configured
        else None
    )
    return hc.CheckContext(
        inventory=inventory,
        credentials=Credentials(username="op", password="oppass"),
        gateway_host="pas.sky.local",
        gateway_port=22,
        splunk_api_credentials=splunk_api_creds,
        max_parallel_hosts=max_parallel_hosts,
    )


def _without_role(inventory, role):
    from auto_patchinator.config.inventory import Inventory
    hosts = {h: host for h, host in inventory.hosts.items() if host.role != role}
    return Inventory(hosts=hosts, environment="test", stretched_sh_sites=inventory.stretched_sh_sites)


def _fake_run_splunk_command(responses, calls=None):
    """responses: {hostname: str | Exception} for a single response regardless of
    which command is requested, or {hostname: {command_substring: str | Exception}}
    when a test needs a host to answer differently depending on which command it
    was asked (e.g. the scheduling check's captain lookup and its own per-host
    member-info query can both target the same host). Records (hostname,
    command_suffix)."""
    def fake(ctx, hostname, command_suffix):
        if calls is not None:
            calls.append((hostname, command_suffix))
        entry = responses[hostname]
        if isinstance(entry, dict):
            for key, value in entry.items():
                if key in command_suffix:
                    result = value
                    break
            else:
                raise KeyError(f"no matching command stub for {hostname} / {command_suffix!r}")
        else:
            result = entry
        if isinstance(result, Exception):
            raise result
        return result
    return fake


# ---------------------------------------------------------------------------
# _needs_splunk_api_credentials
# ---------------------------------------------------------------------------

def test_needs_splunk_api_credentials_passes_when_configured(inventory):
    ctx = _ctx(inventory, configured=True)
    assert hc._needs_splunk_api_credentials(ctx, "g", "c", "scope") is None


def test_needs_splunk_api_credentials_skips_when_not_configured(inventory):
    ctx = _ctx(inventory, configured=False)
    result = hc._needs_splunk_api_credentials(ctx, "g", "c", "scope")
    assert result.passed is None
    assert result.group == "g" and result.check == "c" and result.scope == "scope"
    assert "not configured" in result.detail


def test_needs_splunk_api_credentials_skips_on_bare_token_only(inventory):
    """A bare SPLUNK_API_TOKEN (no username/password) is deliberately NOT enough -
    every check here authenticates via `-auth user:pass`, which a token alone can't
    satisfy (same exclusion the module docstring and credentials.py call out)."""
    ctx = hc.CheckContext(
        inventory=inventory,
        credentials=Credentials(username="op", password="oppass"),
        gateway_host="pas.sky.local",
        gateway_port=22,
        splunk_api_credentials=SplunkApiCredentials(token="tok-only"),
    )
    result = hc._needs_splunk_api_credentials(ctx, "g", "c", "scope")
    assert result is not None and result.passed is None


# ---------------------------------------------------------------------------
# _hostnames_by_role
# ---------------------------------------------------------------------------

def test_hostnames_by_role_sorted(inventory):
    from auto_patchinator.actions.sequences import NodeRole
    assert hc._hostnames_by_role(_ctx(inventory), NodeRole.INDEXER) == ["ix01", "ix02"]


# ---------------------------------------------------------------------------
# Search heads: cluster_membership
# ---------------------------------------------------------------------------

def test_shc_cluster_membership_check_marks_up_member_healthy(inventory, monkeypatch):
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shs01": SHCLUSTER_STATUS_DYNAMIC.replace("shx01", "shs01"),
        "shx01": SHCLUSTER_STATUS_DYNAMIC,
        "shx02": SHCLUSTER_STATUS_DYNAMIC.replace("shx01", "shx02"),
    }))
    results = hc._shc_cluster_membership_check(_ctx(inventory))
    by_scope = {r.scope: r for r in results}
    assert by_scope["shx01"].passed is True
    assert by_scope["shx02"].passed is True
    assert by_scope["shs01"].passed is True


def test_shc_cluster_membership_check_skips_without_credentials(inventory, monkeypatch):
    def exploding(ctx, hostname, command_suffix):
        raise AssertionError("should not be called when creds aren't configured")
    monkeypatch.setattr(hc, "_run_splunk_command", exploding)
    results = hc._shc_cluster_membership_check(_ctx(inventory, configured=False))
    assert results and all(r.passed is None for r in results)


def test_shc_cluster_membership_check_reports_connection_error(inventory, monkeypatch):
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shs01": SHCLUSTER_STATUS_DYNAMIC.replace("shx01", "shs01"),
        "shx01": ConnectionError("no route to host"),
        "shx02": SHCLUSTER_STATUS_DYNAMIC.replace("shx01", "shx02"),
    }))
    results = hc._shc_cluster_membership_check(_ctx(inventory))
    by_scope = {r.scope: r for r in results}
    assert by_scope["shx01"].passed is None
    assert "no route to host" in by_scope["shx01"].detail


# ---------------------------------------------------------------------------
# Search heads: captain_and_election
# ---------------------------------------------------------------------------

def test_shc_captain_and_election_falls_back_to_next_reachable_member(inventory, monkeypatch):
    from auto_patchinator.actions.sequences import NodeRole
    calls = []
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shx01": ConnectionError("down"),
        "shx02": SHCLUSTER_STATUS_DYNAMIC,
    }, calls))
    stretched_only = _without_role(inventory, NodeRole.SEARCH_HEAD_SIMPLE)
    results = hc._shc_captain_and_election_check(_ctx(stretched_only))
    stretched = next(r for r in results if r.scope == "stretched cluster")
    assert stretched.passed is True
    assert "captain=prdmilbbspksh04.sky.local" in stretched.detail
    assert "election=dynamic" in stretched.detail
    assert "(asked via shx02)" in stretched.detail
    assert [c[0] for c in calls] == ["shx01", "shx02"]


def test_shc_captain_and_election_static_is_not_a_failure(inventory, monkeypatch):
    from auto_patchinator.actions.sequences import NodeRole
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shx01": SHCLUSTER_STATUS_STATIC,
        "shx02": SHCLUSTER_STATUS_STATIC,
    }))
    stretched_only = _without_role(inventory, NodeRole.SEARCH_HEAD_SIMPLE)
    results = hc._shc_captain_and_election_check(_ctx(stretched_only))
    stretched = next(r for r in results if r.scope == "stretched cluster")
    assert stretched.passed is True
    assert "election=STATIC" in stretched.detail


def test_shc_captain_and_election_skips_cluster_with_no_members(inventory):
    """An inventory with no search-head hosts at all (of either role) should produce
    no results rather than an error - no host to even attempt a query against."""
    from auto_patchinator.actions.sequences import NodeRole
    no_simple = _without_role(inventory, NodeRole.SEARCH_HEAD_SIMPLE)
    no_sh = _without_role(no_simple, NodeRole.SEARCH_HEAD_STRETCHED)
    results = hc._shc_captain_and_election_check(_ctx(no_sh))
    assert results == []


def test_shc_captain_and_election_reports_error_when_nothing_answers(inventory, monkeypatch):
    from auto_patchinator.actions.sequences import NodeRole
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shx01": ConnectionError("down"),
        "shx02": ConnectionError("also down"),
    }))
    stretched_only = _without_role(inventory, NodeRole.SEARCH_HEAD_SIMPLE)
    results = hc._shc_captain_and_election_check(_ctx(stretched_only))
    stretched = next(r for r in results if r.scope == "stretched cluster")
    assert stretched.passed is None
    assert "also down" in stretched.detail


# ---------------------------------------------------------------------------
# Search heads: scheduling
# ---------------------------------------------------------------------------

def test_shc_scheduling_check_maps_active_and_inactive(inventory, monkeypatch):
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shs01": MEMBER_INFO_INACTIVE,
        "shx01": MEMBER_INFO_ACTIVE,
        "shx02": MEMBER_INFO_INACTIVE,
    }))
    results = hc._shc_scheduling_check(_ctx(inventory))
    by_scope = {r.scope: r for r in results}
    assert by_scope["shx01"].passed is True
    assert by_scope["shx02"].passed is False
    assert by_scope["shs01"].passed is False


def test_shc_scheduling_zero_count_on_the_captain_is_not_a_failure(inventory, monkeypatch):
    """shx02 is both the stretched cluster's captain and inactive (0 count) - expected,
    not a FAIL, confirmed live 2026-10-28 (prdmilbbmsosh01, the simple cluster's static
    captain, showed exactly this)."""
    captain_status = SHCLUSTER_STATUS_DYNAMIC.replace("prdmilbbspksh04.sky.local", "shx02.sky.local")
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shx01": {"shcluster-status": captain_status, "member-info": MEMBER_INFO_ACTIVE},
        "shx02": {"shcluster-status": captain_status, "member-info": MEMBER_INFO_INACTIVE},
    }))
    from auto_patchinator.actions.sequences import NodeRole
    stretched_only = _without_role(inventory, NodeRole.SEARCH_HEAD_SIMPLE)
    results = hc._shc_scheduling_check(_ctx(stretched_only))
    by_scope = {r.scope: r for r in results}
    assert by_scope["shx01"].passed is True
    assert by_scope["shx02"].passed is True
    assert "current captain" in by_scope["shx02"].detail


def test_shc_scheduling_zero_count_on_a_non_captain_is_still_a_failure(inventory, monkeypatch):
    captain_status = SHCLUSTER_STATUS_DYNAMIC.replace("prdmilbbspksh04.sky.local", "shx01.sky.local")
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shx01": {"shcluster-status": captain_status, "member-info": MEMBER_INFO_ACTIVE},
        "shx02": {"shcluster-status": captain_status, "member-info": MEMBER_INFO_INACTIVE},
    }))
    from auto_patchinator.actions.sequences import NodeRole
    stretched_only = _without_role(inventory, NodeRole.SEARCH_HEAD_SIMPLE)
    results = hc._shc_scheduling_check(_ctx(stretched_only))
    by_scope = {r.scope: r for r in results}
    assert by_scope["shx02"].passed is False


def test_shc_scheduling_skips_captain_lookup_without_credentials(inventory, monkeypatch):
    calls = []
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({}, calls))
    results = hc._shc_scheduling_check(_ctx(inventory, configured=False))
    assert calls == []
    assert results and all(r.passed is None for r in results)


# ---------------------------------------------------------------------------
# Search heads: kvstore_sync
# ---------------------------------------------------------------------------

def test_shc_kvstore_check_all_healthy(inventory, monkeypatch):
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shs01": KVSTORE_STATUS_ALL_HEALTHY,
        "shx01": KVSTORE_STATUS_ALL_HEALTHY,
    }))
    results = hc._shc_kvstore_check(_ctx(inventory))
    stretched = next(r for r in results if r.scope == "stretched cluster")
    assert stretched.passed is True
    assert "all in sync" in stretched.detail


def test_shc_kvstore_check_flags_down_member(inventory, monkeypatch):
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shs01": KVSTORE_STATUS_ALL_HEALTHY,
        "shx01": KVSTORE_STATUS_ONE_DOWN,
    }))
    results = hc._shc_kvstore_check(_ctx(inventory))
    stretched = next(r for r in results if r.scope == "stretched cluster")
    assert stretched.passed is False
    assert "shx01:8191='Down'" in stretched.detail


def test_shc_kvstore_check_unrecognized_format(inventory, monkeypatch):
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        "shs01": "garbage, no KV store members section here",
        "shx01": "garbage, no KV store members section here",
        "shx02": "garbage, no KV store members section here",
    }))
    results = hc._shc_kvstore_check(_ctx(inventory))
    stretched = next(r for r in results if r.scope == "stretched cluster")
    assert stretched.passed is None
    assert "unrecognized output format" in stretched.detail


# ---------------------------------------------------------------------------
# Forwarders
# ---------------------------------------------------------------------------

def test_fw_up_check_maps_connectivity_results(inventory, monkeypatch):
    def fake_check_connectivity(host_items, identities, credentials, gateway_host, gateway_port, inv, max_workers):
        return [
            ConnectivityResult(hostname=h, identity=identities[0], status=STATUS_OK, detail="whoami=splunk")
            for h, _host in host_items
        ]
    monkeypatch.setattr(hc, "check_connectivity", fake_check_connectivity)
    results = hc._fw_up_check(_ctx(inventory))
    assert len(results) == 1
    assert results[0].group == hc.GROUP_FORWARDERS
    assert results[0].passed is True


def test_fw_up_check_reports_failures(inventory, monkeypatch):
    def fake_check_connectivity(host_items, identities, credentials, gateway_host, gateway_port, inv, max_workers):
        return [
            ConnectivityResult(hostname=h, identity=identities[0], status=STATUS_FAIL, detail="auth failed")
            for h, _host in host_items
        ]
    monkeypatch.setattr(hc, "check_connectivity", fake_check_connectivity)
    results = hc._fw_up_check(_ctx(inventory))
    assert results[0].passed is False
    assert results[0].detail == "auth failed"


# ---------------------------------------------------------------------------
# Indexers: parsing helpers
# ---------------------------------------------------------------------------

def test_ix_parse_peers_finds_peer_blocks_and_skips_index_blocks():
    peers = hc._ix_parse_peers(CLUSTER_STATUS_SAMPLE)
    assert set(peers) == {"ix01", "ix02.sky.local"}
    assert peers["ix01"] == {"site": "site1", "searchable": "NO", "status": "Stopped"}
    assert peers["ix02.sky.local"] == {"site": "site2", "searchable": "YES", "status": "Up"}


def test_ix_parse_preflight_fields():
    fields = hc._ix_parse_preflight_fields(CLUSTER_STATUS_SAMPLE)
    assert fields["pre-flight check successful"] == "NO"
    assert fields["site replication factor met"] == "NO"
    assert fields["site search factor met"] == "YES"
    assert fields["all peers are up"] == "NO"


def test_ix_parse_peers_empty_on_unrecognized_output():
    assert hc._ix_parse_peers("nothing useful here") == {}


def test_ix_parse_preflight_fields_empty_on_unrecognized_output():
    assert hc._ix_parse_preflight_fields("nothing useful here") == {}


# ---------------------------------------------------------------------------
# Indexers: check functions
# ---------------------------------------------------------------------------

def test_ix_cluster_membership_check_maps_up_and_stopped(inventory, monkeypatch):
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        hc.INDEXER_CLUSTER_MANAGER_HOSTNAME: CLUSTER_STATUS_SAMPLE,
    }))
    results = hc._ix_cluster_membership_check(_ctx(inventory))
    by_scope = {r.scope: r for r in results}
    assert by_scope["ix01"].passed is False
    assert by_scope["ix02.sky.local"].passed is True


def test_ix_cluster_membership_check_skips_without_credentials(inventory, monkeypatch):
    def exploding(ctx, hostname, command_suffix):
        raise AssertionError("should not be called when creds aren't configured")
    monkeypatch.setattr(hc, "_run_splunk_command", exploding)
    results = hc._ix_cluster_membership_check(_ctx(inventory, configured=False))
    assert results and all(r.passed is None for r in results)


def test_ix_cluster_membership_check_reports_connection_error(inventory, monkeypatch):
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        hc.INDEXER_CLUSTER_MANAGER_HOSTNAME: ConnectionError("no route to host"),
    }))
    results = hc._ix_cluster_membership_check(_ctx(inventory))
    assert len(results) == 1
    assert results[0].passed is None
    assert "no route to host" in results[0].detail


def test_ix_cluster_membership_check_unrecognized_format(inventory, monkeypatch):
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        hc.INDEXER_CLUSTER_MANAGER_HOSTNAME: "garbage output",
    }))
    results = hc._ix_cluster_membership_check(_ctx(inventory))
    assert len(results) == 1
    assert results[0].passed is None
    assert "unrecognized output format" in results[0].detail


def test_ix_factors_check_maps_met_and_not_met(inventory, monkeypatch):
    monkeypatch.setattr(hc, "_run_splunk_command", _fake_run_splunk_command({
        hc.INDEXER_CLUSTER_MANAGER_HOSTNAME: CLUSTER_STATUS_SAMPLE,
    }))
    results = hc._ix_factors_check(_ctx(inventory))
    by_scope = {r.scope: r for r in results}
    assert by_scope["replication factor"].passed is False
    assert by_scope["search factor"].passed is True


def test_ix_factors_check_skips_without_credentials(inventory, monkeypatch):
    def exploding(ctx, hostname, command_suffix):
        raise AssertionError("should not be called when creds aren't configured")
    monkeypatch.setattr(hc, "_run_splunk_command", exploding)
    results = hc._ix_factors_check(_ctx(inventory, configured=False))
    assert results and all(r.passed is None for r in results)


# ---------------------------------------------------------------------------
# _run_per_host concurrency helper
# ---------------------------------------------------------------------------

def test_run_per_host_runs_concurrently_and_preserves_order(inventory):
    """A barrier that needs all 3 calls in flight at once proves genuine
    concurrency - a sequential implementation would block on the first call and
    time out, since the other two would never get a chance to run."""
    import threading
    barrier = threading.Barrier(3, timeout=2)

    def fn(hostname):
        barrier.wait()
        return hc.CheckResult("g", "c", hostname, True, "ok")

    ctx = _ctx(inventory, max_parallel_hosts=3)
    results = hc._run_per_host(ctx, ["a", "b", "c"], fn)
    assert [r.scope for r in results] == ["a", "b", "c"]


def test_run_per_host_sequential_when_max_parallel_hosts_is_one(inventory):
    calls = []

    def fn(hostname):
        calls.append(hostname)
        return hc.CheckResult("g", "c", hostname, True, "ok")

    ctx = _ctx(inventory, max_parallel_hosts=1)
    results = hc._run_per_host(ctx, ["a", "b"], fn)
    assert calls == ["a", "b"]
    assert [r.scope for r in results] == ["a", "b"]


def test_run_per_host_empty_hostnames(inventory):
    assert hc._run_per_host(_ctx(inventory), [], lambda h: None) == []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_groups_registry_is_well_formed():
    group_keys = {key for key, _label, _checks in hc.GROUPS}
    assert group_keys == {hc.GROUP_SEARCH_HEADS, hc.GROUP_FORWARDERS, hc.GROUP_INDEXERS}
    for _key, label, checks in hc.GROUPS:
        assert isinstance(label, str) and label
        for name, fn in checks:
            assert isinstance(name, str) and name
            assert callable(fn)


def test_run_group_checks_flattens_results_in_order():
    def fn_a(ctx):
        return [hc.CheckResult("g", "a", "scope-a", True, "detail-a")]

    def fn_b(ctx):
        return [
            hc.CheckResult("g", "b", "scope-b1", False, "detail-b1"),
            hc.CheckResult("g", "b", "scope-b2", None, "detail-b2"),
        ]

    results = hc.run_group_checks(object(), (("a", fn_a), ("b", fn_b)))
    assert [(r.check, r.scope) for r in results] == [
        ("a", "scope-a"), ("b", "scope-b1"), ("b", "scope-b2"),
    ]
