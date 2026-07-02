"""Human-readable per-run report, generated from the same RunState used for resume."""
from __future__ import annotations

from pathlib import Path

from auto_patchinator.plan.run_plan import RunStepPlan
from auto_patchinator.state.models import RunState


def _step_status_label(step_state) -> str:
    if step_state.has_failure():
        return "FAILED"
    if step_state.is_complete():
        return "DONE"
    return "INCOMPLETE"


def generate_markdown_report(state: RunState, run_plan: list[RunStepPlan]) -> str:
    plans = {p.excel_step: p for p in run_plan}
    lines = [
        f"# Run report: {state.run_id}",
        f"- Excel: {state.excel_path}",
        f"- Host source: {state.host_source}",
        f"- Created: {state.created_at}",
        f"- Updated: {state.updated_at}",
        "",
    ]
    for excel_step in state.step_order:
        step_state = state.steps[excel_step]
        plan = plans[excel_step]
        lines.append(
            f"## Step {excel_step} - {step_state.verb.upper()} - {step_state.label} "
            f"[{_step_status_label(step_state)}]"
        )
        lines.append(f"hosts: {', '.join(plan.hostnames) or '(none)'}")
        for scope, action in step_state.all_action_states():
            detail = f" - {action.error}" if action.error else ""
            lines.append(f"- [{scope}] {action.name}: {action.status.value}{detail}")
        lines.append("")
    return "\n".join(lines)


def write_report(state: RunState, run_plan: list[RunStepPlan], directory: str | Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"report-{state.run_id}.md"
    path.write_text(generate_markdown_report(state, run_plan))
    return path
