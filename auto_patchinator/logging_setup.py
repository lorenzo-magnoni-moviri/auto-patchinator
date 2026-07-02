"""Per-run file logging.

Console output stays as plain prints (the operator's interactive view); the log file
captures everything at DEBUG - plan resolution, every action attempt, operator choices,
and the raw SSH traffic (sends and received buffers) - so a run can be audited or a
failed login flow diagnosed after the fact.

Passwords are never written to the log: the SSH layer marks password sends as sensitive
and they are logged as '<redacted>'.
"""
from __future__ import annotations

import logging
from pathlib import Path

ROOT_LOGGER_NAME = "auto_patchinator"

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_run_logging(logs_dir: str | Path, run_id: str) -> Path:
    """Attach a DEBUG file handler for this run; returns the log file path.

    Reusing the same run_id (a resumed run) appends to the existing file.
    """
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"run-{run_id}.log"

    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    # Avoid duplicate handlers if called twice in one process.
    if not any(
        isinstance(h, logging.FileHandler) and Path(h.baseFilename) == path.resolve()
        for h in logger.handlers
    ):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    return path
