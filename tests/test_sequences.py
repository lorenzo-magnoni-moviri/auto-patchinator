from auto_patchinator.actions.sequences import (
    HOST_OVERRIDES,
    SPLUNK_BIN,
    SPLUNK_BIN_INDEXER,
    NodeRole,
    get_role_sequences,
)
from auto_patchinator.actions.types import ActionKind, Identity


def test_start_sequence_order_is_enable_restore_reload_start():
    seq = get_role_sequences("dp01", NodeRole.DEPLOYER)
    names = [a.name for a in seq.start_per_node]
    assert names == ["enable_boot_start", "restore_systemd_unit", "daemon_reload", "start_splunk"]


def test_stop_sequence_backs_up_unit_before_disabling():
    seq = get_role_sequences("dp01", NodeRole.DEPLOYER)
    names = [a.name for a in seq.stop_per_node]
    assert names.index("backup_systemd_unit") < names.index("disable_boot_start")


def test_indexer_uses_dedicated_splunk_bin():
    seq = get_role_sequences("ix01", NodeRole.INDEXER)
    stop = next(a for a in seq.stop_per_node if a.name == "stop_splunk")
    assert SPLUNK_BIN_INDEXER in stop.command
    assert SPLUNK_BIN not in stop.command


def test_forwarder_wraps_stop_start_with_crontab_handling():
    seq = get_role_sequences("fw01", NodeRole.FORWARDER)
    assert seq.stop_per_node[0].name == "disable_crontab"
    assert seq.start_per_node[-1].name == "enable_crontab"


def test_host_override_replaces_role_sequence():
    hostname = next(iter(HOST_OVERRIDES))
    seq = get_role_sequences(hostname, NodeRole.FORWARDER)
    assert any(a.name == "disable_streamsets_pipelines" for a in seq.stop_per_node)


def test_timeouts_60s_default_900s_for_splunk_stop_start():
    for role in NodeRole:
        seq = get_role_sequences("anyhost", role)
        for actions in (seq.stop_per_node, seq.start_per_node):
            for action in actions:
                expected = 900 if action.name in ("stop_splunk", "start_splunk") else 60
                assert action.timeout_seconds == expected, action.name


def test_restore_unit_overwrites_in_place_not_replace_inode():
    seq = get_role_sequences("dp01", NodeRole.DEPLOYER)
    restore = next(a for a in seq.start_per_node if a.name == "restore_systemd_unit")
    assert restore.command.startswith("cat ")
    assert ">" in restore.command
    assert restore.identity == Identity.ROOT


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
