"""Orders TeamSteps by dependency (Kahn's algorithm), flagging dependencies that point
outside our team's own step set (e.g. on a CTRL-M or DBA step) as external - those can't
be executed by this tool and must be confirmed done by the user before we proceed.
"""
from __future__ import annotations

from dataclasses import dataclass

from auto_patchinator.plan.action_mapping import TeamStep


@dataclass(frozen=True)
class OrderedStep:
    team_step: TeamStep
    external_dependencies: tuple[int, ...]


def resolve_order(steps: list[TeamStep]) -> list[OrderedStep]:
    by_id = {s.step: s for s in steps}
    internal_deps = {s.step: [d for d in s.dependencies if d in by_id] for s in steps}
    external_deps = {s.step: tuple(d for d in s.dependencies if d not in by_id) for s in steps}

    remaining = dict(internal_deps)
    ordered: list[OrderedStep] = []
    while remaining:
        ready = sorted(step_id for step_id, deps in remaining.items() if not deps)
        if not ready:
            cycle = ", ".join(str(s) for s in remaining)
            raise ValueError(f"dependency cycle detected among steps: {cycle}")
        # Process one step at a time (lowest step number first).  Processing all ready
        # steps in a batch would cause steps whose only predecessors are external (other
        # teams' steps, already stripped) to all float to the top together, losing the
        # intended sequential ordering encoded in the Excel step numbers.
        step_id = ready[0]
        ordered.append(OrderedStep(by_id[step_id], external_deps[step_id]))
        del remaining[step_id]
        for deps in remaining.values():
            deps[:] = [d for d in deps if d != step_id]
    return ordered
