"""Tests for config/streamsets_pipelines.py - the YAML loader for prdmilbbspkfw02's
pipeline registry (previously hardcoded in actions/sequences.py, see TODO.md)."""
import pytest

from auto_patchinator.config.streamsets_pipelines import (
    DEFAULT_STREAMSETS_PIPELINES_PATH,
    load_streamsets_pipelines,
)


def test_loads_the_real_inventory_file():
    """The actual inventory/streamsets_pipelines.yaml shipped with the repo - must
    parse cleanly and have no duplicate pipeline ids."""
    pipelines = load_streamsets_pipelines(DEFAULT_STREAMSETS_PIPELINES_PATH)
    assert len(pipelines) >= 1
    for label, pipeline_id in pipelines:
        assert label and pipeline_id
    ids = [pid for _, pid in pipelines]
    assert len(ids) == len(set(ids))


def test_loads_labels_and_ids_in_file_order(tmp_path):
    path = tmp_path / "streamsets_pipelines.yaml"
    path.write_text(
        "pipelines:\n"
        "  - label: RDK\n"
        "    id: id-rdk\n"
        "  - label: ODP\n"
        "    id: id-odp\n"
    )
    assert load_streamsets_pipelines(path) == (("RDK", "id-rdk"), ("ODP", "id-odp"))


def test_missing_file_raises_actionable_error(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        load_streamsets_pipelines(tmp_path / "nope.yaml")


def test_missing_pipelines_key_raises(tmp_path):
    path = tmp_path / "streamsets_pipelines.yaml"
    path.write_text("not_pipelines: []\n")
    with pytest.raises(ValueError, match="pipelines"):
        load_streamsets_pipelines(path)


def test_entry_missing_label_or_id_raises(tmp_path):
    path = tmp_path / "streamsets_pipelines.yaml"
    path.write_text("pipelines:\n  - label: RDK\n")
    with pytest.raises(ValueError, match="label.*id"):
        load_streamsets_pipelines(path)
