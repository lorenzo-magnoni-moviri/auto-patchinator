import logging
import os
import time
from pathlib import Path

from auto_patchinator.logging_setup import ROOT_LOGGER_NAME, prune_old_logs, setup_run_logging


def _reset_logger():
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def test_prune_old_logs_keeps_only_the_n_most_recent(tmp_path):
    for i in range(5):
        f = tmp_path / f"run-{i}.log"
        f.write_text("x")
        os.utime(f, (time.time() + i, time.time() + i))  # ascending mtime: 4 is newest

    deleted = prune_old_logs(tmp_path, keep=3)
    remaining = {p.name for p in tmp_path.glob("*.log")}
    assert remaining == {"run-2.log", "run-3.log", "run-4.log"}
    assert {p.name for p in deleted} == {"run-0.log", "run-1.log"}


def test_prune_old_logs_missing_dir_is_a_noop(tmp_path):
    assert prune_old_logs(tmp_path / "nope", keep=3) == []


def test_prune_old_logs_fewer_than_keep_deletes_nothing(tmp_path):
    (tmp_path / "run-a.log").write_text("x")
    assert prune_old_logs(tmp_path, keep=3) == []


def test_setup_run_logging_prunes_down_to_keep_limit(tmp_path):
    _reset_logger()
    try:
        for i in range(4):
            path = setup_run_logging(tmp_path, f"old-{i}")
            time.sleep(0.01)
        remaining = sorted(p.name for p in tmp_path.glob("*.log"))
        assert len(remaining) == 3
        assert "run-old-3.log" in remaining
        assert "run-old-0.log" not in remaining
    finally:
        _reset_logger()


def test_setup_run_logging_reuses_same_file_for_same_run_id(tmp_path):
    _reset_logger()
    try:
        path1 = setup_run_logging(tmp_path, "resume-me")
        logging.getLogger(f"{ROOT_LOGGER_NAME}.x").info("first")
        path2 = setup_run_logging(tmp_path, "resume-me")
        logging.getLogger(f"{ROOT_LOGGER_NAME}.x").info("second")
        assert path1 == path2
        content = path1.read_text()
        assert "first" in content and "second" in content
    finally:
        _reset_logger()
