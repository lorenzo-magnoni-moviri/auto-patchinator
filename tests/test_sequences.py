from auto_patchinator.actions.sequences import (
    HOST_OVERRIDES,
    SPLUNK_BIN,
    SPLUNK_BIN_INDEXER,
    STREAMSETS_PIPELINES,
    NodeRole,
    get_role_sequences,
)
from auto_patchinator.actions.types import ActionKind, Identity


def test_start_sequence_order_is_enable_reload_start():
    seq = get_role_sequences("dp01", NodeRole.DEPLOYER)
    names = [a.name for a in seq.start_per_node]
    assert names == ["enable_boot_start", "daemon_reload", "start_splunk"]


def test_stop_sequence_is_stop_then_disable():
    seq = get_role_sequences("dp01", NodeRole.DEPLOYER)
    names = [a.name for a in seq.stop_per_node]
    assert names == ["stop_splunk", "disable_boot_start"]


def test_search_head_stretched_cleans_kvstore_before_starting():
    seq = get_role_sequences("anyhost", NodeRole.SEARCH_HEAD_STRETCHED)
    names = [a.name for a in seq.start_per_node]
    assert names.index("clean_kvstore") < names.index("start_splunk")
    assert names == [
        "enable_boot_start", "daemon_reload", "clean_kvstore", "start_splunk",
        "wait_for_shcluster_member_healthy",
    ]


def test_clean_kvstore_skips_the_interactive_confirmation():
    """Without --answer-yes, splunk interactively confirms the drop ('Are you sure you
    want to continue [y/n]?') - a PLAIN action can't answer that, so it just hangs
    until the timeout (found live against a real search_head_stretched host,
    2026-09-09 - see TODO.md)."""
    seq = get_role_sequences("anyhost", NodeRole.SEARCH_HEAD_STRETCHED)
    clean = next(a for a in seq.start_per_node if a.name == "clean_kvstore")
    assert "--answer-yes" in clean.command
    assert clean.kind == ActionKind.PLAIN  # still non-interactive - the flag is the fix


def test_indexer_uses_dedicated_splunk_bin():
    seq = get_role_sequences("ix01", NodeRole.INDEXER)
    stop = next(a for a in seq.stop_per_node if a.name == "stop_splunk")
    assert SPLUNK_BIN_INDEXER in stop.command
    assert SPLUNK_BIN not in stop.command


def test_forwarder_wraps_stop_start_with_crontab_handling():
    seq = get_role_sequences("fw01", NodeRole.FORWARDER)
    assert seq.stop_per_node[0].name == "backup_crontab"
    assert seq.stop_per_node[1].name == "disable_crontab"
    assert seq.start_per_node[-1].name == "enable_crontab"


def test_crontab_is_backed_up_before_deletion_everywhere():
    """crontab -r is destructive: every sequence that deletes it must back it up first,
    and the restore must read from the same file the backup writes."""
    for factory_host, role in (("fw01", NodeRole.FORWARDER), (next(iter(HOST_OVERRIDES)), NodeRole.FORWARDER)):
        seq = get_role_sequences(factory_host, role)
        names = [a.name for a in seq.stop_per_node]
        assert "backup_crontab" in names, factory_host
        assert names.index("backup_crontab") < names.index("disable_crontab"), factory_host

        backup = next(a for a in seq.stop_per_node if a.name == "backup_crontab")
        restore = next(a for a in seq.start_per_node if a.name == "enable_crontab")
        backup_file = backup.command.split(">")[1].strip()
        assert backup_file in restore.command


def test_host_override_replaces_role_sequence():
    hostname = next(iter(HOST_OVERRIDES))
    seq = get_role_sequences(hostname, NodeRole.FORWARDER)
    assert any(a.name.startswith("stop_streamsets_pipeline_") for a in seq.stop_per_node)


def test_fw02_stops_every_streamsets_pipeline_before_splunk_stop():
    hostname = next(iter(HOST_OVERRIDES))
    seq = get_role_sequences(hostname, NodeRole.FORWARDER)
    names = [a.name for a in seq.stop_per_node]

    pipeline_actions = [a for a in seq.stop_per_node if a.kind == ActionKind.STREAMSETS_PIPELINE]
    assert len(pipeline_actions) == len(STREAMSETS_PIPELINES) == 5

    for (label, pipeline_id), action in zip(STREAMSETS_PIPELINES, pipeline_actions):
        assert action.pipeline_label == label
        assert action.pipeline_id == pipeline_id
        assert action.target_status == "STOPPED"
        assert action.identity == Identity.SPLUNK
        assert action.poll_interval_seconds is not None
        assert action.timeout_seconds == 90

    # every pipeline must be stopped before splunk itself stops
    assert max(names.index(a.name) for a in pipeline_actions) < names.index("stop_splunk")


def test_fw02_pipeline_ids_are_unique():
    ids = [pid for _, pid in STREAMSETS_PIPELINES]
    assert len(ids) == len(set(ids))


def test_fw02_starts_every_streamsets_pipeline_after_splunk_start():
    hostname = next(iter(HOST_OVERRIDES))
    seq = get_role_sequences(hostname, NodeRole.FORWARDER)
    names = [a.name for a in seq.start_per_node]

    pipeline_actions = [a for a in seq.start_per_node if a.kind == ActionKind.STREAMSETS_PIPELINE]
    assert len(pipeline_actions) == len(STREAMSETS_PIPELINES) == 5

    for (label, pipeline_id), action in zip(STREAMSETS_PIPELINES, pipeline_actions):
        assert action.pipeline_label == label
        assert action.pipeline_id == pipeline_id
        assert action.target_status == "RUNNING"
        assert action.identity == Identity.SPLUNK
        assert action.poll_interval_seconds is not None
        assert action.timeout_seconds == 90

    # every pipeline must be started after splunk itself starts
    assert min(names.index(a.name) for a in pipeline_actions) > names.index("start_splunk")
    assert "enable_streamsets_pipelines" not in names  # the old manual placeholder is gone


def test_timeouts_60s_default_900s_for_splunk_stop_start():
    long_timeouts = {"stop_splunk": 900, "start_splunk": 900, "wait_for_shcluster_member_healthy": 600}
    for role in NodeRole:
        seq = get_role_sequences("anyhost", role)
        for actions in (seq.stop_per_node, seq.start_per_node):
            for action in actions:
                expected = long_timeouts.get(action.name, 60)
                assert action.timeout_seconds == expected, action.name


def test_enable_boot_start_runs_as_root_disable_as_splunk():
    seq = get_role_sequences("dp01", NodeRole.DEPLOYER)
    enable = next(a for a in seq.start_per_node if a.name == "enable_boot_start")
    disable = next(a for a in seq.stop_per_node if a.name == "disable_boot_start")
    assert enable.identity == Identity.ROOT
    assert disable.identity == Identity.SPLUNK


def test_plain_actions_all_have_identity_and_command():
    for role in NodeRole:
        seq = get_role_sequences("anyhost", role)
        for phase in ("stop_per_node", "start_per_node"):
            for action in getattr(seq, phase):
                if action.kind == ActionKind.PLAIN:
                    assert action.identity is not None and action.command
