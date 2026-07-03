"""Controller mode tests driven by scripted operator input (no SSH, no tty)."""
import pytest

from auto_patchinator.actions.sequences import disable_crontab
from auto_patchinator.actions.types import Identity
from auto_patchinator.plan.action_mapping import map_team_steps
from auto_patchinator.plan.dependency import resolve_order
from auto_patchinator.plan.run_plan import build_run_plan
from auto_patchinator.runner.controller import RunController
from auto_patchinator.state import store
from auto_patchinator.state.models import ActionStatus
from tests.conftest import TEAM, make_raw


class ExplodingConnectionFactory:
    """Any attempt to open a connection is a test failure."""

    def __call__(self, **kwargs):
        raise AssertionError(f"unexpected connection attempt: {kwargs}")


def _controller(tmp_path, inventory, inputs, monkeypatch, gateway=("pas.sky.local", 22)):
    mapped, _ = map_team_steps([make_raw(2, "Stop application Group 1")], TEAM)
    plan = build_run_plan(resolve_order(mapped), {1: ("dp01", "fw01")}, inventory)
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    feed = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    return RunController(
        plan, state, str(tmp_path), ExplodingConnectionFactory(), inventory, gateway=gateway
    )


def test_manual_guide_mark_all_done_executes_nothing(tmp_path, inventory, monkeypatch, capsys):
    ctrl = _controller(tmp_path, inventory, ["m", "d"], monkeypatch)
    ctrl.run()  # ExplodingConnectionFactory guarantees nothing was executed

    assert ctrl.state.is_complete()
    for _, action_state in ctrl.state.steps[2].all_action_states():
        assert action_state.status == ActionStatus.SUCCESS
        assert "manual guide" in action_state.output

    out = capsys.readouterr().out
    assert "MANUAL GUIDE" in out
    assert "ssh '<your-user>@pas.tst.spk@dp01#" not in out  # inventory fixture has no pas_port
    assert "ssh '<your-user>@pas.prd.spk@dp01'@pas.sky.local" in out
    assert "sudo su - splunk" in out
    assert "why : Stop the Splunk process cleanly" in out


def test_manual_guide_quit_keeps_actions_pending(tmp_path, inventory, monkeypatch):
    ctrl = _controller(tmp_path, inventory, ["m", "q", "y"], monkeypatch)
    ctrl.run()
    statuses = {a.status for _, a in ctrl.state.steps[2].all_action_states()}
    assert statuses == {ActionStatus.PENDING}


def test_ssh_hint_marks_cyberark_gui_only_identity(tmp_path, inventory, monkeypatch):
    ctrl = _controller(tmp_path, inventory, [], monkeypatch)
    hint = ctrl._ssh_hint("ix01", Identity.SPLUNK)
    # indexer role uses the dedicated su command
    assert hint.endswith("then: sudo /bin/su - splunk -s /bin/bash")


def test_guide_what_renders_interactive_scripts(tmp_path, inventory, monkeypatch):
    ctrl = _controller(tmp_path, inventory, [], monkeypatch)
    lines = ctrl._guide_what(disable_crontab())
    assert lines[0].startswith("crontab -r")
    assert "really delete" in lines[0]
    assert lines[1] == "yes"
