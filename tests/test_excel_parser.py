from pathlib import Path

import pytest

from auto_patchinator.plan.excel_parser import _parse_dependencies, load_plan_sheet
from tests.conftest import PLAN_HEADER, TEAM, write_workbook


def test_parse_dependencies_variants():
    assert _parse_dependencies(None) == ()
    assert _parse_dependencies(3) == (3,)
    assert _parse_dependencies(3.0) == (3,)
    assert _parse_dependencies("2 3 4") == (2, 3, 4)
    assert _parse_dependencies("Pre task 7") == (7,)  # non-numeric tokens ignored


def test_load_plan_sheet_basic(plan_xlsx: Path):
    steps = load_plan_sheet(plan_xlsx)
    assert [s.step for s in steps] == [1, 2, 3, 4]
    assert steps[1].gruppo_referente == TEAM
    assert steps[1].dependencies == (1,)


def test_load_plan_sheet_accepts_italian_dependency_header(tmp_path: Path):
    header = list(PLAN_HEADER)
    header[1] = "Dipendenza"
    path = write_workbook(tmp_path / "it.xlsx", {"Plan": [
        header,
        [1, "2 3", "App", "Nome", TEAM, None, None, None, None, None],
    ]})
    steps = load_plan_sheet(path)
    assert steps[0].dependencies == (2, 3)


def test_load_plan_sheet_skips_non_numeric_step_rows(tmp_path: Path):
    path = write_workbook(tmp_path / "mixed.xlsx", {"Plan": [
        PLAN_HEADER,
        ["Pre task 1", None, None, "section label", None, None, None, None, None, None],
        [None, None, None, "empty step", None, None, None, None, None, None],
        [5, None, "App", "Real row", TEAM, None, None, None, None, None],
    ]})
    steps = load_plan_sheet(path)
    assert [s.step for s in steps] == [5]


def test_load_plan_sheet_missing_column_raises(tmp_path: Path):
    header = [c for c in PLAN_HEADER if c != "Nome"]
    path = write_workbook(tmp_path / "bad.xlsx", {"Plan": [header]})
    with pytest.raises(ValueError, match="Nome"):
        load_plan_sheet(path)


def test_load_plan_sheet_missing_sheet_raises(plan_xlsx: Path):
    with pytest.raises(ValueError, match="NoSuchSheet"):
        load_plan_sheet(plan_xlsx, "NoSuchSheet")


def test_load_plan_sheet_tolerates_trailing_whitespace_in_sheet_name(tmp_path: Path):
    """Some waves' 'Plan' sheet has incidental trailing whitespace in its name."""
    path = write_workbook(tmp_path / "whitespace.xlsx", {"Plan ": [
        PLAN_HEADER,
        [1, None, "App", "Nome", TEAM, None, None, None, None, None],
    ]})
    steps = load_plan_sheet(path, "Plan")
    assert [s.step for s in steps] == [1]


def test_load_plan_sheet_does_not_match_another_teams_plan_sheet(tmp_path: Path):
    """'Plan IT 28on29_10' must never be mistaken for our 'Plan' sheet even though it
    starts with the same word - only whitespace differences are tolerated."""
    path = write_workbook(tmp_path / "other_team.xlsx", {"Plan IT 28on29_10": [
        PLAN_HEADER,
        [1, None, "App", "Nome", TEAM, None, None, None, None, None],
    ]})
    with pytest.raises(ValueError, match="no sheet named 'Plan'"):
        load_plan_sheet(path, "Plan")


def test_load_plan_sheet_ambiguous_sheets_raises(tmp_path: Path):
    path = write_workbook(tmp_path / "ambiguous.xlsx", {
        "Plan ": [PLAN_HEADER, [1, None, "App", "Nome", TEAM, None, None, None, None, None]],
        " Plan": [PLAN_HEADER, [2, None, "App", "Nome", TEAM, None, None, None, None, None]],
    })
    with pytest.raises(ValueError, match="multiple sheets"):
        load_plan_sheet(path, "Plan")
