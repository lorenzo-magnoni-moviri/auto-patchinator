"""Controller mode tests driven by scripted operator input (no SSH, no tty)."""
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


def _controller(tmp_path, inventory, inputs, monkeypatch):
    mapped, _ = map_team_steps([make_raw(2, "Stop application Group 1")], TEAM)
    plan = build_run_plan(resolve_order(mapped), {1: ("dp01", "fw01")}, inventory)
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    feed = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    return RunController(plan, state, str(tmp_path), ExplodingConnectionFactory(), inventory)


def _pending_count(ctrl):
    return sum(1 for _, a in ctrl.state.steps[2].all_action_states())


def test_manual_guide_steps_through_each_task(tmp_path, inventory, monkeypatch, capsys):
    ctrl = _controller(tmp_path, inventory, [], monkeypatch)
    total = _pending_count(ctrl)
    # mode 'm', then one ENTER per task -> everything confirmed, nothing executed
    feed = iter(["m"] + [""] * total)
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl.run()

    assert ctrl.state.is_complete()
    for _, action_state in ctrl.state.steps[2].all_action_states():
        assert action_state.status == ActionStatus.SUCCESS
        assert "manual guide" in action_state.output

    out = capsys.readouterr().out
    assert "MANUAL GUIDE" in out
    assert "ssh" not in out                      # WinSSH is used - no ssh command printed
    assert "become splunk with: sudo su - splunk" in out
    assert f"task 1/{total}:" in out
    assert "why : Stop the Splunk process cleanly" in out


def test_manual_guide_skip_and_quit(tmp_path, inventory, monkeypatch):
    # first task skipped, then quit at the second one (then 'y' to save progress)
    ctrl = _controller(tmp_path, inventory, ["m", "s", "q", "y"], monkeypatch)
    ctrl.run()
    states = [a.status for _, a in ctrl.state.steps[2].all_action_states()]
    assert states[0] == ActionStatus.SKIPPED
    assert all(s == ActionStatus.PENDING for s in states[1:])


def test_manual_guide_list_all_shows_overview_and_stays_on_task(tmp_path, inventory, monkeypatch, capsys):
    ctrl = _controller(tmp_path, inventory, ["m", "l", "q", "y"], monkeypatch)
    ctrl.run()
    out = capsys.readouterr().out
    assert "All tasks in this step, in order:" in out
    assert "send_mail" in out                    # the overview lists later tasks too
    statuses = {a.status for _, a in ctrl.state.steps[2].all_action_states()}
    assert statuses == {ActionStatus.PENDING}    # listing marks nothing as done


def test_su_hint_uses_role_specific_command(tmp_path, inventory, monkeypatch):
    ctrl = _controller(tmp_path, inventory, [], monkeypatch)
    assert ctrl._su_hint("ix01", Identity.SPLUNK) == "sudo /bin/su - splunk -s /bin/bash"
    assert ctrl._su_hint("dp01", Identity.ROOT) == "sudo su - root"


def test_guide_what_renders_interactive_scripts(tmp_path, inventory, monkeypatch):
    ctrl = _controller(tmp_path, inventory, [], monkeypatch)
    lines = ctrl._guide_what(disable_crontab())
    assert lines[0].startswith("crontab -r")
    assert "really delete" in lines[0]
    assert lines[1] == "yes"
