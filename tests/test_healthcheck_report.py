"""Tests for the `health-check` subcommand's markdown report (auto_patchinator/
healthcheck/report.py)."""
from auto_patchinator.healthcheck.checks import CheckResult
from auto_patchinator.healthcheck.report import generate_markdown_report, write_report

RESULTS = [
    CheckResult("search_heads", "cluster_membership", "shx01", True, "status='Up'"),
    CheckResult("search_heads", "cluster_membership", "shx02", False, "status='Stopped'"),
    CheckResult("search_heads", "captain_and_election", "stretched cluster", None, "no host answered"),
    CheckResult("forwarders", "up", "fw01", True, "whoami='splunk'"),
]


def test_generate_markdown_report_summary_counts():
    report = generate_markdown_report(RESULTS, "search-heads-20261028T000000")
    assert "**Summary**: 2 OK, 1 FAIL, 1 SKIP" in report


def test_generate_markdown_report_groups_and_checks_are_sectioned():
    report = generate_markdown_report(RESULTS, "run-1")
    assert "## search_heads" in report
    assert "## forwarders" in report
    assert "### cluster_membership" in report
    assert "### captain_and_election" in report
    assert "### up" in report
    # forwarders section comes after search_heads, matching insertion order
    assert report.index("## search_heads") < report.index("## forwarders")


def test_generate_markdown_report_renders_status_labels_and_detail():
    report = generate_markdown_report(RESULTS, "run-1")
    assert "| shx01 | OK | status='Up' |" in report
    assert "| shx02 | FAIL | status='Stopped' |" in report
    assert "| stretched cluster | SKIP | no host answered |" in report


def test_generate_markdown_report_header_includes_run_id():
    report = generate_markdown_report(RESULTS, "search-heads-20261028T000000")
    assert report.startswith("# Health check: search-heads-20261028T000000")


def test_generate_markdown_report_empty_results():
    report = generate_markdown_report([], "run-1")
    assert "**Summary**: 0 OK, 0 FAIL, 0 SKIP" in report


def test_write_report_creates_directory_and_file(tmp_path):
    out_dir = tmp_path / "reports"
    path = write_report(RESULTS, "search-heads-20261028T000000", out_dir)
    assert path == out_dir / "healthcheck-search-heads-20261028T000000.md"
    assert path.exists()
    assert path.read_text() == generate_markdown_report(RESULTS, "search-heads-20261028T000000")


def test_write_report_accepts_existing_directory(tmp_path):
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    path = write_report(RESULTS, "run-1", out_dir)
    assert path.exists()
