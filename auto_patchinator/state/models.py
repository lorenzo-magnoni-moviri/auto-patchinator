"""Persistable run state: status of every (step, scope, action) so a run can be resumed,
navigated, or have individual steps marked manual/skipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

PRE_GROUP_SCOPE = "__pre_group__"
POST_GROUP_SCOPE = "__post_group__"


class ActionStatus(str, Enum):
    PENDING = "pending"
    MANUAL_PENDING = "manual_pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ActionState:
    name: str
    status: ActionStatus = ActionStatus.PENDING
    output: str | None = None
    error: str | None = None
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict) -> "ActionState":
        return ActionState(
            name=data["name"],
            status=ActionStatus(data["status"]),
            output=data.get("output"),
            error=data.get("error"),
            updated_at=data.get("updated_at", now_iso()),
        )


@dataclass
class StepState:
    excel_step: int
    verb: str
    label: str
    pre_group: list[ActionState]
    per_host: dict[str, list[ActionState]]
    post_group: list[ActionState]

    def all_action_states(self):
        for action in self.pre_group:
            yield PRE_GROUP_SCOPE, action
        for hostname, actions in self.per_host.items():
            for action in actions:
                yield hostname, action
        for action in self.post_group:
            yield POST_GROUP_SCOPE, action

    def is_complete(self) -> bool:
        return all(
            action.status in (ActionStatus.SUCCESS, ActionStatus.SKIPPED)
            for _, action in self.all_action_states()
        )

    def has_failure(self) -> bool:
        return any(action.status == ActionStatus.FAILED for _, action in self.all_action_states())

    def to_dict(self) -> dict:
        return {
            "excel_step": self.excel_step,
            "verb": self.verb,
            "label": self.label,
            "pre_group": [a.to_dict() for a in self.pre_group],
            "per_host": {h: [a.to_dict() for a in acts] for h, acts in self.per_host.items()},
            "post_group": [a.to_dict() for a in self.post_group],
        }

    @staticmethod
    def from_dict(data: dict) -> "StepState":
        return StepState(
            excel_step=data["excel_step"],
            verb=data["verb"],
            label=data["label"],
            pre_group=[ActionState.from_dict(a) for a in data["pre_group"]],
            per_host={h: [ActionState.from_dict(a) for a in acts] for h, acts in data["per_host"].items()},
            post_group=[ActionState.from_dict(a) for a in data["post_group"]],
        )


@dataclass
class RunState:
    run_id: str
    excel_path: str
    host_source: str
    step_order: list[int]
    steps: dict[int, StepState]
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    current_step: int | None = None

    def touch(self) -> None:
        self.updated_at = now_iso()

    def is_complete(self) -> bool:
        return all(self.steps[s].is_complete() for s in self.step_order)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "excel_path": self.excel_path,
            "host_source": self.host_source,
            "step_order": self.step_order,
            "steps": {str(k): v.to_dict() for k, v in self.steps.items()},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_step": self.current_step,
        }

    @staticmethod
    def from_dict(data: dict) -> "RunState":
        return RunState(
            run_id=data["run_id"],
            excel_path=data["excel_path"],
            host_source=data.get("host_source") or data.get("wave_mapping_path", ""),
            step_order=data["step_order"],
            steps={int(k): StepState.from_dict(v) for k, v in data["steps"].items()},
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            current_step=data.get("current_step"),
        )
