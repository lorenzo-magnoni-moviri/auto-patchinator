from pathlib import Path

from auto_patchinator.plan.action_mapping import map_team_steps
from auto_patchinator.plan.dependency import resolve_order
from auto_patchinator.plan.run_plan import build_run_plan
from auto_patchinator.state import store
from auto_patchinator.state.models import ActionStatus
from tests.conftest import TEAM, make_raw


def _run_plan(inventory):
    mapped, _ = map_team_steps([make_raw(2, "Stop application Group 1")], TEAM)
    return build_run_plan(resolve_order(mapped), {1: ("dp01", "fw01")}, inventory)


def test_state_roundtrip(tmp_path: Path, inventory):
    plan = _run_plan(inventory)
    state = store.build_initial_state("rt", "plan.xlsx", "List Host NO IT", plan)
    state.steps[2].per_host["dp01"][0].status = ActionStatus.SUCCESS
    state.steps[2].per_host["dp01"][0].output = "done"

    path = store.save(state, tmp_path)
    loaded = store.load(path)

    assert loaded.run_id == "rt"
    assert loaded.step_order == [2]
    assert loaded.steps[2].per_host["dp01"][0].status == ActionStatus.SUCCESS
    assert loaded.steps[2].per_host["dp01"][0].output == "done"


def test_find_incomplete_run(tmp_path: Path, inventory):
    plan = _run_plan(inventory)

    complete = store.build_initial_state("done", "p.xlsx", "s", plan)
    for step in complete.steps.values():
        for _, action in step.all_action_states():
            action.status = ActionStatus.SUCCESS
    store.save(complete, tmp_path)
    assert store.find_incomplete_run(tmp_path) is None

    incomplete = store.build_initial_state("wip", "p.xlsx", "s", plan)
    store.save(incomplete, tmp_path)
    found = store.find_incomplete_run(tmp_path)
    assert found is not None and "wip" in found.name


def test_skipped_counts_as_complete(tmp_path: Path, inventory):
    plan = _run_plan(inventory)
    state = store.build_initial_state("skip", "p.xlsx", "s", plan)
    for step in state.steps.values():
        for _, action in step.all_action_states():
            action.status = ActionStatus.SKIPPED
    assert state.is_complete()
