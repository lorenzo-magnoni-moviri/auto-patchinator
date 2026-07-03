from auto_patchinator.plan.action_mapping import map_team_steps
from auto_patchinator.plan.dependency import resolve_order
from auto_patchinator.plan.run_plan import build_run_plan
from tests.conftest import TEAM, make_raw


def _plan(rows, mapping, inventory):
    mapped, unmapped = map_team_steps(rows, TEAM)
    assert not unmapped
    return build_run_plan(resolve_order(mapped), mapping, inventory)


def test_external_dependency_becomes_first_manual_confirmation(inventory):
    rows = [make_raw(2, "Stop application Group 1", deps=(1,))]
    plans = _plan(rows, {1: ("dp01",)}, inventory)
    assert plans[0].pre_group_actions[0].name == "confirm_external_dependencies"
    assert "step(s) 1" in plans[0].pre_group_actions[0].note


def test_send_mail_is_last_post_group_action_of_every_step(inventory):
    rows = [
        make_raw(2, "Stop application Group 1", deps=()),
        make_raw(4, "Start application Group 1", deps=(2,)),
    ]
    plans = _plan(rows, {1: ("dp01",)}, inventory)
    for plan in plans:
        assert plan.post_group_actions[-1].name == "send_mail"


def test_captain_transfer_injected_once_before_first_stretched_stop(inventory):
    rows = [
        make_raw(2, "Stop application Group 1", deps=()),   # stretched shx01
        make_raw(4, "Start application Group 1", deps=(2,)),
        make_raw(5, "Stop application Group 2", deps=(4,)),  # stretched shx02
        make_raw(7, "Start application Group 2", deps=(5,)),
    ]
    mapping = {1: ("shx01",), 2: ("shx02",)}
    plans = _plan(rows, mapping, inventory)

    transfers = [
        (p.excel_step, a.name)
        for p in plans for a in p.pre_group_actions if a.name == "transfer_captain_static"
    ]
    reverts = [
        (p.excel_step, a.name)
        for p in plans for a in p.post_group_actions if a.name == "revert_captain_dynamic"
    ]
    assert transfers == [(2, "transfer_captain_static")]   # only the FIRST stop
    assert reverts == [(7, "revert_captain_dynamic")]      # only the LAST start
    # the transfer instruction names the other site (shx01 is milano -> roma)
    transfer_note = plans[0].pre_group_actions[0].note
    assert "roma" in transfer_note


def test_group_actions_deduped_per_role_not_per_host(inventory):
    rows = [make_raw(2, "Stop application Group 1", deps=())]
    plans = _plan(rows, {1: ("ix01", "ix02")}, inventory)
    # two indexers -> per-host actions for both, but no duplicated group actions
    assert set(plans[0].per_host_actions) == {"ix01", "ix02"}
    names = [a.name for a in plans[0].pre_group_actions]
    assert len(names) == len(set(names))
