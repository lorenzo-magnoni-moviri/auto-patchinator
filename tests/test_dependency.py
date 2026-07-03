import pytest

from auto_patchinator.plan.action_mapping import map_team_steps
from auto_patchinator.plan.dependency import resolve_order
from tests.conftest import TEAM, make_raw


def _team_steps(rows):
    mapped, unmapped = map_team_steps(rows, TEAM)
    assert not unmapped
    return mapped


def test_rolling_wave_keeps_excel_order_despite_external_only_deps():
    """Start steps gated only on other teams' patching steps must not float to the top."""
    rows = [
        make_raw(2, "Stop application Group 1", deps=(1,)),
        make_raw(4, "Start application Group 1", deps=(3,)),   # 3 = external patching
        make_raw(5, "Stop application Group 2", deps=(4,)),
        make_raw(7, "Start application Group 2", deps=(6,)),   # 6 = external patching
    ]
    ordered = resolve_order(_team_steps(rows))
    assert [o.team_step.step for o in ordered] == [2, 4, 5, 7]


def test_external_dependencies_are_flagged():
    rows = [make_raw(4, "Start application Group 1", deps=(3, 2))]
    # step 2 is not in the team set either here - both become external
    ordered = resolve_order(_team_steps(rows))
    assert ordered[0].external_dependencies == (3, 2)


def test_internal_dependency_ordering_wins_over_step_number():
    rows = [
        make_raw(10, "Stop application Group 1"),
        make_raw(2, "Start application Group 1", deps=(10,)),  # depends on higher-numbered step
    ]
    ordered = resolve_order(_team_steps(rows))
    assert [o.team_step.step for o in ordered] == [10, 2]


def test_cycle_raises():
    rows = [
        make_raw(1, "Stop application Group 1", deps=(2,)),
        make_raw(2, "Start application Group 1", deps=(1,)),
    ]
    with pytest.raises(ValueError, match="cycle"):
        resolve_order(_team_steps(rows))
