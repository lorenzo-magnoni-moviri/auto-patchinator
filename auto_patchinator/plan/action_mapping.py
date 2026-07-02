"""Maps raw Excel rows belonging to our team to a typed (verb, group) step.

Tipo is empty for every Splunk row we've seen, so the verb and group number have to be
read out of the free-text 'Nome' column instead (e.g. "Stop application: ... Group 3").
Anything we can't confidently parse is returned separately so it can be flagged for
manual review rather than silently guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from auto_patchinator.plan.excel_parser import RawStep

GROUP_PATTERN = re.compile(r"group\s+(\d+)", re.IGNORECASE)
STOP_PATTERN  = re.compile(r"\b(stop|shutdown)\b", re.IGNORECASE)
START_PATTERN = re.compile(r"\b(start|restart)\b", re.IGNORECASE)


class ActionVerb(str, Enum):
    STOP = "stop"
    START = "start"


@dataclass(frozen=True)
class TeamStep:
    step: int
    verb: ActionVerb
    groups: tuple[int, ...]
    dependencies: tuple[int, ...]
    label: str
    raw: RawStep


def _parse_verb(nome: str) -> ActionVerb | None:
    if STOP_PATTERN.search(nome):
        return ActionVerb.STOP
    if START_PATTERN.search(nome):
        return ActionVerb.START
    return None


def map_team_steps(
    raw_steps: list[RawStep], team_filter: str | list[str]
) -> tuple[list[TeamStep], list[RawStep]]:
    """Split team rows into (cleanly mapped steps, rows needing manual review).

    team_filter can be a single string or a list of strings; a row matches if its
    Gruppo_referente equals any of the given values (case-insensitive).
    """
    filters = {f.lower() for f in ([team_filter] if isinstance(team_filter, str) else team_filter)}
    team_rows = [r for r in raw_steps if (r.gruppo_referente or "").lower() in filters]

    mapped: list[TeamStep] = []
    unmapped: list[RawStep] = []
    for row in team_rows:
        verb = _parse_verb(row.nome)
        groups = tuple(int(g) for g in GROUP_PATTERN.findall(row.nome))
        if verb is None or not groups:
            unmapped.append(row)
            continue
        mapped.append(
            TeamStep(
                step=row.step,
                verb=verb,
                groups=groups,
                dependencies=row.dependencies,
                label=row.nome,
                raw=row,
            )
        )
    return mapped, unmapped
