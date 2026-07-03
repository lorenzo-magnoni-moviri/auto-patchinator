"""Tests for --inventory/--excel default resolution and discovery helpers."""
from pathlib import Path

import pytest

from auto_patchinator.cli import (
    DEFAULT_INVENTORY_PATH,
    PLANS_DIR,
    _discover_excel_candidates,
    _prompt_for_excel_path,
    _resolve_inventory_path,
)


def test_resolve_inventory_uses_explicit_path_when_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    explicit = tmp_path / "custom.yaml"
    explicit.write_text("hosts: {}")
    assert _resolve_inventory_path(str(explicit)) == str(explicit)


def test_resolve_inventory_explicit_missing_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="Inventory file not found"):
        _resolve_inventory_path(str(tmp_path / "nope.yaml"))


def test_resolve_inventory_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "inventory").mkdir()
    (tmp_path / DEFAULT_INVENTORY_PATH).write_text("hosts: {}")
    assert _resolve_inventory_path(None) == DEFAULT_INVENTORY_PATH


def test_resolve_inventory_missing_default_raises_actionable_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="hosts.example.yaml"):
        _resolve_inventory_path(None)


def test_discover_excel_prefers_plans_dir_over_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cwd.xlsx").write_text("x")
    plans = tmp_path / PLANS_DIR
    plans.mkdir()
    (plans / "wave.xlsx").write_text("x")

    candidates = _discover_excel_candidates()
    assert candidates == [Path(PLANS_DIR) / "wave.xlsx"]


def test_discover_excel_falls_back_to_cwd_when_plans_dir_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / PLANS_DIR).mkdir()  # exists but empty
    (tmp_path / "wave.xlsx").write_text("x")

    assert _discover_excel_candidates() == [Path("wave.xlsx")]


def test_discover_excel_most_recent_first(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plans = tmp_path / PLANS_DIR
    plans.mkdir()
    older = plans / "older.xlsx"
    newer = plans / "newer.xlsx"
    older.write_text("x")
    newer.write_text("x")
    import os
    import time
    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))

    assert _discover_excel_candidates() == [Path(PLANS_DIR) / "newer.xlsx", Path(PLANS_DIR) / "older.xlsx"]


def test_discover_excel_empty_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _discover_excel_candidates() == []


def test_prompt_for_excel_picks_by_number(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plans = tmp_path / PLANS_DIR
    plans.mkdir()
    (plans / "a.xlsx").write_text("x")
    monkeypatch.setattr("builtins.input", lambda *_: "1")
    assert _prompt_for_excel_path() == str(Path(PLANS_DIR) / "a.xlsx")


def test_prompt_for_excel_accepts_typed_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plans = tmp_path / PLANS_DIR
    plans.mkdir()
    (plans / "a.xlsx").write_text("x")
    monkeypatch.setattr("builtins.input", lambda *_: "/some/other/path.xlsx")
    assert _prompt_for_excel_path() == "/some/other/path.xlsx"


def test_prompt_for_excel_blank_cancels(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plans = tmp_path / PLANS_DIR
    plans.mkdir()
    (plans / "a.xlsx").write_text("x")
    monkeypatch.setattr("builtins.input", lambda *_: "")
    with pytest.raises(SystemExit, match="No Excel file selected"):
        _prompt_for_excel_path()


def test_prompt_for_excel_no_candidates_asks_for_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_: "typed/path.xlsx")
    assert _prompt_for_excel_path() == "typed/path.xlsx"


def test_prompt_for_excel_no_candidates_blank_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    with pytest.raises(SystemExit, match="No Excel file provided"):
        _prompt_for_excel_path()
