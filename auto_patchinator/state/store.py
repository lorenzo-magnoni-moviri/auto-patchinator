"""Persists RunState to disk and detects an incomplete run to resume on startup."""
from __future__ import annotations

import json
from pathlib import Path

from auto_patchinator.plan.run_plan import RunStepPlan
from auto_patchinator.state.models import ActionState, RunState, StepState


def build_initial_state(
    run_id: str,
    excel_path: str | Path,
    host_source: str,
    run_plan: list[RunStepPlan],
) -> RunState:
    steps: dict[int, StepState] = {}
    order: list[int] = []
    for step_plan in run_plan:
        order.append(step_plan.excel_step)
        steps[step_plan.excel_step] = StepState(
            excel_step=step_plan.excel_step,
            verb=step_plan.verb.value,
            label=step_plan.label,
            pre_group=[ActionState(name=a.name) for a in step_plan.pre_group_actions],
            per_host={
                hostname: [ActionState(name=a.name) for a in actions]
                for hostname, actions in step_plan.per_host_actions.items()
            },
            post_group=[ActionState(name=a.name) for a in step_plan.post_group_actions],
        )
    return RunState(
        run_id=run_id,
        excel_path=str(excel_path),
        host_source=host_source,
        step_order=order,
        steps=steps,
    )


def run_state_path(directory: str | Path, run_id: str) -> Path:
    return Path(directory) / f"run-{run_id}.json"


def save(state: RunState, directory: str | Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    state.touch()
    path = run_state_path(directory, state.run_id)
    path.write_text(json.dumps(state.to_dict(), indent=2))
    return path


def load(path: str | Path) -> RunState:
    return RunState.from_dict(json.loads(Path(path).read_text()))


def delete_run(run_id: str, directory: str | Path) -> None:
    path = run_state_path(directory, run_id)
    path.unlink(missing_ok=True)


def find_incomplete_run(directory: str | Path) -> Path | None:
    directory = Path(directory)
    if not directory.exists():
        return None
    candidates = sorted(directory.glob("run-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        if not load(path).is_complete():
            return path
    return None
