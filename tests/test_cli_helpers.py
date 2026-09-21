from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_patchinator.actions.sequences import manual_todo
from auto_patchinator.actions.types import Identity
from auto_patchinator.cli import _load_team_steps, _resolve_pas_gateway
from auto_patchinator.runner.controller import is_forced_manual
from tests.conftest import PLAN_HEADER, write_workbook


def _inv(gateway):
    return SimpleNamespace(pas_gateway=gateway)


def test_gateway_from_inventory_default_port_22():
    assert _resolve_pas_gateway(_inv("invgw")) == ("invgw", 22)


def test_gateway_from_inventory_explicit_port():
    assert _resolve_pas_gateway(_inv("invgw:10100")) == ("invgw", 10100)


def test_gateway_none_when_unset():
    assert _resolve_pas_gateway(_inv(None)) == (None, 22)


def test_manual_action_is_always_forced_manual(inventory):
    action = manual_todo("send_mail", "send it")
    assert is_forced_manual(inventory, "dp01", action)


def test_manual_only_identity_forces_manual(inventory):
    from auto_patchinator.actions.sequences import stop_splunk, SPLUNK_BIN

    action = stop_splunk(SPLUNK_BIN)
    assert action.identity == Identity.SPLUNK
    assert is_forced_manual(inventory, "shx02", action)      # splunk is CyberArk-GUI-only
    assert not is_forced_manual(inventory, "shx01", action)  # normal host


def test_load_team_steps_warns_when_no_row_matches_the_team_filter(tmp_path: Path, capsys):
    """A wave can relabel Gruppo_referente (e.g. 'Splunk Broadband' instead of 'AOM Sky
    CSO') without any row failing to parse - map_team_steps would then silently return
    an empty list. This must be surfaced, not left to produce a silent empty plan."""
    path = write_workbook(tmp_path / "relabeled.xlsx", {"Plan": [
        PLAN_HEADER,
        [1, None, "App", "Stop application Group 1", "Some Other Team", None, None, None, None, None],
    ]})
    mapped = _load_team_steps(str(path), ["AOM Sky CSO"])
    assert mapped == []
    out = capsys.readouterr().out
    assert "no rows matched team filter" in out
    assert "Some Other Team" in out


def test_load_team_steps_missing_plan_sheet_gives_actionable_error(tmp_path: Path):
    """No --plan-sheet override exists anymore - a workbook that doesn't have a 'Plan'
    sheet at all (or an ambiguous one) must fail with guidance on the expected format,
    not a bare ValueError traceback."""
    path = write_workbook(tmp_path / "wrong.xlsx", {"NotThePlanSheet": [PLAN_HEADER]})
    with pytest.raises(SystemExit, match="expected format"):
        _load_team_steps(str(path), ["AOM Sky CSO"])
