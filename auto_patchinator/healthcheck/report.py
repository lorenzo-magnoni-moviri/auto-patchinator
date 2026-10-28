"""Markdown report for the `health-check` subcommand - sibling to reports/report.py,
not an extension of it (that one is tied to RunState/wave-run data; this one is tied
to CheckResult, a different shape). Mirrors its same write_report(...) -> Path
pattern for consistency.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from auto_patchinator.healthcheck.checks import CheckResult

_STATUS_LABEL = {True: "OK", False: "FAIL", None: "SKIP"}


def generate_markdown_report(results: list[CheckResult], run_id: str) -> str:
    lines = [f"# Health check: {run_id}", f"- Generated: {datetime.now().isoformat(timespec='seconds')}", ""]

    by_group: dict[str, list[CheckResult]] = {}
    for r in results:
        by_group.setdefault(r.group, []).append(r)

    fail_count = sum(1 for r in results if r.passed is False)
    skip_count = sum(1 for r in results if r.passed is None)
    ok_count = len(results) - fail_count - skip_count
    lines.append(f"**Summary**: {ok_count} OK, {fail_count} FAIL, {skip_count} SKIP")
    lines.append("")

    for group, group_results in by_group.items():
        lines.append(f"## {group}")
        by_check: dict[str, list[CheckResult]] = {}
        for r in group_results:
            by_check.setdefault(r.check, []).append(r)
        for check, check_results in by_check.items():
            lines.append(f"### {check}")
            lines.append("| Scope | Status | Detail |")
            lines.append("|---|---|---|")
            for r in check_results:
                lines.append(f"| {r.scope} | {_STATUS_LABEL[r.passed]} | {r.detail} |")
            lines.append("")
    return "\n".join(lines)


def write_report(results: list[CheckResult], run_id: str, directory: str | Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"healthcheck-{run_id}.md"
    path.write_text(generate_markdown_report(results, run_id))
    return path
