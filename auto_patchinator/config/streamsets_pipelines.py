"""Loads the StreamSets pipeline registry for prdmilbbspkfw02's HOST_OVERRIDES
sequence (actions/sequences.py) from a YAML file, instead of hardcoding it in Python -
so the list can be edited without touching code. Deliberately self-contained (only
stdlib + PyYAML) rather than living in config/inventory.py: that module already
imports from actions/sequences.py (for NodeRole), so importing it back the other way
would be a circular import.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_STREAMSETS_PIPELINES_PATH = "inventory/streamsets_pipelines.yaml"


def load_streamsets_pipelines(
    path: str | Path = DEFAULT_STREAMSETS_PIPELINES_PATH,
) -> tuple[tuple[str, str], ...]:
    """Returns ((label, pipeline_id), ...) in file order, from the YAML file's
    top-level 'pipelines' list (each entry: {label: ..., id: ...})."""
    path = Path(path)
    if not path.exists():
        raise SystemExit(
            f"StreamSets pipeline registry not found: {path}. This is required for "
            "prdmilbbspkfw02's automated pipeline stop/start - see "
            "inventory/streamsets_pipelines.yaml (create it if missing)."
        )
    raw = yaml.safe_load(path.read_text())
    if not raw or "pipelines" not in raw:
        raise ValueError(f"{path}: must define a top-level 'pipelines' list")
    try:
        return tuple((entry["label"], entry["id"]) for entry in raw["pipelines"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{path}: each entry under 'pipelines' needs 'label' and 'id'") from exc
