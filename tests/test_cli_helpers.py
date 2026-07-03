from types import SimpleNamespace

from auto_patchinator.actions.sequences import manual_todo
from auto_patchinator.actions.types import Identity
from auto_patchinator.cli import _resolve_pas_gateway
from auto_patchinator.runner.controller import is_forced_manual


def _inv(gateway):
    return SimpleNamespace(pas_gateway=gateway)


def test_gateway_cli_value_wins_over_inventory():
    assert _resolve_pas_gateway("cligw:10100", _inv("invgw")) == ("cligw", 10100)


def test_gateway_falls_back_to_inventory_default_port_22():
    assert _resolve_pas_gateway(None, _inv("invgw")) == ("invgw", 22)


def test_gateway_none_when_unset():
    assert _resolve_pas_gateway(None, _inv(None)) == (None, 22)


def test_manual_action_is_always_forced_manual(inventory):
    action = manual_todo("send_mail", "send it")
    assert is_forced_manual(inventory, "dp01", action)


def test_manual_only_identity_forces_manual(inventory):
    from auto_patchinator.actions.sequences import stop_splunk, SPLUNK_BIN

    action = stop_splunk(SPLUNK_BIN)
    assert action.identity == Identity.SPLUNK
    assert is_forced_manual(inventory, "shx02", action)      # splunk is CyberArk-GUI-only
    assert not is_forced_manual(inventory, "shx01", action)  # normal host
