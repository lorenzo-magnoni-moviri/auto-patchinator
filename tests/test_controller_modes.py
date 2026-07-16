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
    # dp01 (3 actions) and fw01 (5 actions) have different profiles, so each is its
    # own group of 1 host; post-group (send_mail) is a third block. One ENTER per
    # block confirms everything, nothing executed, nothing printed per-host repeated.
    ctrl = _controller(tmp_path, inventory, ["m", "", "", ""], monkeypatch)
    ctrl.run()

    assert ctrl.state.is_complete()
    for _, action_state in ctrl.state.steps[2].all_action_states():
        assert action_state.status == ActionStatus.SUCCESS
        assert "manual guide" in action_state.output

    out = capsys.readouterr().out
    assert "MANUAL GUIDE" in out
    assert "ssh" not in out                      # WinSSH is used - no ssh command printed
    assert "become splunk with: sudo su - splunk" in out
    assert "task 1/3:" in out                    # dp01's own 3-task group
    assert "task 1/5:" in out                    # fw01's own 5-task group
    assert "why : Stop the Splunk process cleanly" in out


def test_manual_guide_batches_identical_hosts_into_one_confirmation(tmp_path, inventory, monkeypatch, capsys):
    """Two hosts of the same role (identical remaining tasks + su hint - ix01/ix02,
    both indexers with no per-host su override) are shown once and confirmed
    together, not once per host."""
    mapped, _ = map_team_steps([make_raw(2, "Stop application Group 1")], TEAM)
    plan = build_run_plan(resolve_order(mapped), {1: ("ix01", "ix02")}, inventory)
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    feed = iter(["m", "", ""])  # mode, ONE confirmation for both hosts, then send_mail
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl = RunController(plan, state, str(tmp_path), ExplodingConnectionFactory(), inventory)
    ctrl.run()

    ix01_states = [a.status for a in ctrl.state.steps[2].per_host["ix01"]]
    ix02_states = [a.status for a in ctrl.state.steps[2].per_host["ix02"]]
    assert ix01_states == [ActionStatus.SUCCESS] * len(ix01_states)
    assert ix02_states == [ActionStatus.SUCCESS] * len(ix02_states)

    out = capsys.readouterr().out
    # the 3-task group is shown once, not once per host (ix01 then ix02)
    assert out.count("task 1/3:") == 1
    assert out.count("stop_splunk") == 1
    assert "On 2 hosts (indexer, site milano): ix01, ix02" in out
    assert "Repeat the" in out and "IDENTICALLY on EACH of these 2 hosts" in out


def test_manual_guide_skip_and_quit(tmp_path, inventory, monkeypatch):
    # skip the whole dp01 group (3 actions), then quit at the fw01 group prompt
    # (then 'y' to save progress)
    ctrl = _controller(tmp_path, inventory, ["m", "s", "q", "y"], monkeypatch)
    ctrl.run()
    dp01_states = [a.status for a in ctrl.state.steps[2].per_host["dp01"]]
    fw01_states = [a.status for a in ctrl.state.steps[2].per_host["fw01"]]
    post_states = [a.status for a in ctrl.state.steps[2].post_group]
    assert dp01_states == [ActionStatus.SKIPPED] * len(dp01_states)
    assert all(s == ActionStatus.PENDING for s in fw01_states)
    assert all(s == ActionStatus.PENDING for s in post_states)


def test_manual_guide_list_all_shows_overview_and_stays_on_task(tmp_path, inventory, monkeypatch, capsys):
    ctrl = _controller(tmp_path, inventory, ["m", "l", "q", "y"], monkeypatch)
    ctrl.run()
    out = capsys.readouterr().out
    assert "All tasks in this step, in order:" in out
    assert "send_mail" in out                    # the overview lists later tasks too
    statuses = {a.status for _, a in ctrl.state.steps[2].all_action_states()}
    assert statuses == {ActionStatus.PENDING}    # listing marks nothing as done


def test_manual_guide_individual_fallback_from_a_batched_group(tmp_path, inventory, monkeypatch):
    """'i' at the group prompt falls back to confirming ix01/ix02 one at a time,
    so a batch can still be un-done for a single problem host."""
    mapped, _ = map_team_steps([make_raw(2, "Stop application Group 1")], TEAM)
    plan = build_run_plan(resolve_order(mapped), {1: ("ix01", "ix02")}, inventory)
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    # mode, 'i' at the group prompt, then 3 ENTERs for ix01, 's' + 2 ENTERs for ix02,
    # then ENTER for send_mail
    feed = iter(["m", "i", "", "", "", "s", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl = RunController(plan, state, str(tmp_path), ExplodingConnectionFactory(), inventory)
    ctrl.run()

    ix01_states = [a.status for a in ctrl.state.steps[2].per_host["ix01"]]
    ix02_states = [a.status for a in ctrl.state.steps[2].per_host["ix02"]]
    assert ix01_states == [ActionStatus.SUCCESS] * len(ix01_states)
    assert ix02_states[0] == ActionStatus.SKIPPED
    assert ix02_states[1:] == [ActionStatus.SUCCESS] * len(ix02_states[1:])


def test_build_host_groups_separates_different_profiles(tmp_path, inventory, monkeypatch):
    """dp01 (deployer, 3 tasks) and fw01 (forwarder, 5 tasks) must NOT be batched
    together; ix01/ix02 (both plain indexers) must be."""
    ctrl = _controller(tmp_path, inventory, [], monkeypatch)
    step_state = ctrl.state.steps[2]
    host_items = [
        (hostname, action, action_state)
        for hostname, actions in step_state.per_host.items()
        for action, action_state in zip(
            ctrl._plans[2].per_host_actions[hostname], actions
        )
    ]
    groups = ctrl._build_host_groups(host_items)
    grouped_hostsets = [set(hostnames) for hostnames, _ in groups]
    assert {"dp01"} in grouped_hostsets
    assert {"fw01"} in grouped_hostsets
    assert not any(len(names) > 1 for names in grouped_hostsets)  # no overlap here


def _two_step_dp01_plan(inventory):
    """A stop step + a start step depending on it (both internal, no external-dep
    prompt), single host dp01 - used to test that a capital-letter mode choice
    sticks across steps without re-asking."""
    rows = [
        make_raw(2, "Stop application Group 1"),
        make_raw(4, "Start application Group 1", deps=(2,)),
    ]
    mapped, _ = map_team_steps(rows, TEAM)
    return build_run_plan(resolve_order(mapped), {1: ("dp01",)}, inventory)


def test_capital_T_locks_task_mode_for_all_remaining_steps(tmp_path, inventory, monkeypatch, capsys):
    plan = _two_step_dp01_plan(inventory)
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    # 'T' once, then 'd' (mark done manually) for every action of both steps:
    # step 2 = 3 dp01 actions + send_mail = 4; step 4 = 4 dp01 actions + send_mail = 5
    feed = iter(["T"] + ["d"] * 9)
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl = RunController(plan, state, str(tmp_path), ExplodingConnectionFactory(), inventory)
    ctrl.run()

    assert ctrl.state.is_complete()
    assert ctrl._locked_mode == "task"
    out = capsys.readouterr().out
    assert out.count("How do you want to run this step?") == 1  # only asked once, for step 2


def test_capital_M_locks_manual_guide_for_all_remaining_steps(tmp_path, inventory, monkeypatch, capsys):
    plan = _two_step_dp01_plan(inventory)
    state = store.build_initial_state("t", "p.xlsx", "s", plan)
    # 'M' once, then one ENTER per (host-group, post-group) block across both steps
    feed = iter(["M", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(feed))
    ctrl = RunController(plan, state, str(tmp_path), ExplodingConnectionFactory(), inventory)
    ctrl.run()

    assert ctrl.state.is_complete()
    assert ctrl._locked_mode == "manual"
    out = capsys.readouterr().out
    assert out.count("How do you want to run this step?") == 1
    assert out.count("MANUAL GUIDE") == 2  # printed once per step, not just once overall


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
