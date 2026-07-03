from pathlib import Path

import pytest

from auto_patchinator.plan.wave_mapping import hosts_for_groups, load_wave_mapping_from_excel
from tests.conftest import write_workbook


def test_reads_groups_and_filters_to_inventory(plan_xlsx: Path, inventory):
    mapping = load_wave_mapping_from_excel(plan_xlsx, inventory)
    # foreign01 is in the sheet but not in the inventory - silently dropped
    assert mapping == {1: ("dp01", "fw01")}


def test_finds_sheet_by_no_it_substring(tmp_path: Path, inventory):
    path = write_workbook(tmp_path / "w.xlsx", {"Host list_NO IT_11on12": [
        ["=SUBTOTAL(3,A3:A3)"],
        ["Computer", "Hostname", "Groups"],
        ["dp01.sky.local", "dp01", 2],
    ]})
    mapping = load_wave_mapping_from_excel(path, inventory)
    assert mapping == {2: ("dp01",)}


def test_falls_back_to_computer_fqdn_when_no_hostname_column(tmp_path: Path, inventory):
    path = write_workbook(tmp_path / "w.xlsx", {"List Host NO IT": [
        ["count"],
        ["Computer", "Group"],
        ["fw01.sky.local", 1],
    ]})
    mapping = load_wave_mapping_from_excel(path, inventory)
    assert mapping == {1: ("fw01",)}


def test_missing_group_column_raises(tmp_path: Path, inventory):
    path = write_workbook(tmp_path / "w.xlsx", {"List Host NO IT": [
        ["count"],
        ["Computer", "Hostname"],
        ["dp01.sky.local", "dp01"],
    ]})
    with pytest.raises(ValueError, match="group column"):
        load_wave_mapping_from_excel(path, inventory)


def test_no_matching_sheet_raises(tmp_path: Path, inventory):
    path = write_workbook(tmp_path / "w.xlsx", {"Sheet1": [["x"]]})
    with pytest.raises(ValueError, match="host sheet"):
        load_wave_mapping_from_excel(path, inventory)


def test_hosts_for_groups_dedupes_and_requires_known_groups():
    mapping = {1: ("a", "b"), 2: ("b", "c")}
    assert hosts_for_groups((1, 2), mapping) == ("a", "b", "c")
    with pytest.raises(KeyError, match="group 9"):
        hosts_for_groups((9,), mapping)
