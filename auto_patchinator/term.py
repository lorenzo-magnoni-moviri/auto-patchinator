"""Minimal ANSI color helpers and progress animation for operator-facing output.

Colors and animation are disabled automatically when stdout is not a terminal
(piped/redirected) or when the NO_COLOR environment variable is set.
"""
from __future__ import annotations

import os
import sys
import threading


def _enabled() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _wrap(code: str):
    def colorize(text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if _enabled() else text
    return colorize


green = _wrap("32")
red = _wrap("31;1")
yellow = _wrap("33")
bold = _wrap("1")
cyan = _wrap("1;36")  # literal commands the operator should type/paste verbatim


class _StaticLine:
    """Non-tty fallback: print the line once, no animation."""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix

    def __enter__(self) -> "_StaticLine":
        print(f"{self._prefix} ... ", end="", flush=True)
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _AnimatedDots:
    """Pulse the trailing dots (. .. ...) on the same line while a block runs."""

    _INTERVAL = 0.4

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        count = 1
        while True:
            print(f"\r{self._prefix} {'.' * count:<3} ", end="", flush=True)
            if self._stop.wait(self._INTERVAL):
                return
            count = count % 3 + 1

    def __enter__(self) -> "_AnimatedDots":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        self._thread.join()
        # Settle the line at three dots so the outcome appends to a stable prefix.
        print(f"\r{self._prefix} ... ", end="", flush=True)
        return False


def progress_line(prefix: str):
    """Context manager: show `prefix ... ` while the body runs, dots pulsing on a tty."""
    return _AnimatedDots(prefix) if sys.stdout.isatty() else _StaticLine(prefix)


def clear_screen() -> None:
    """Clear the visible terminal screen (not the scrollback buffer - the operator can
    still scroll up normally to review earlier steps) before starting a new step, so
    its output isn't lost in the previous step's. No-op when stdout isn't a real
    terminal (piped/redirected output, e.g. captured in a log or a test)."""
    if sys.stdout.isatty():
        print("\033[H\033[2J", end="")
