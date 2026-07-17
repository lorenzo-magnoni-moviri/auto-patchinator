"""Tests for term.py's tty-gated color/clear-screen helpers."""
import sys

from auto_patchinator.term import clear_screen, cyan, green


def test_clear_screen_noop_when_not_a_tty(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    clear_screen()
    assert capsys.readouterr().out == ""


def test_clear_screen_emits_escape_sequence_on_a_tty(monkeypatch, capsys):
    # \x1b[2J clears the visible screen only - deliberately not \x1b[3J, which would
    # also wipe the scrollback buffer the operator needs to scroll back through.
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    clear_screen()
    assert capsys.readouterr().out == "\033[H\033[2J"


def test_color_helpers_disabled_when_not_a_tty(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert green("x") == "x"
    assert cyan("x") == "x"


def test_color_helpers_enabled_on_a_tty(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert green("x") == "\033[32mx\033[0m"
    assert cyan("x") == "\033[1;36mx\033[0m"
