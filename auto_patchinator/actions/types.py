"""Core action data model shared by sequence templates, the executor and the state store."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Identity(str, Enum):
    """Which SSH identity an action must run as."""

    SPLUNK = "splunk"
    ROOT = "root"


class ActionKind(str, Enum):
    """How the executor should run the action."""

    PLAIN = "plain"
    INTERACTIVE = "interactive"
    MANUAL = "manual"
    WAIT = "wait"


@dataclass(frozen=True)
class ExpectStep:
    """One send/expect pair in an interactive command sequence."""

    send: str
    expect: str | None = None


DEFAULT_COMMAND_TIMEOUT = 60  # seconds; override per action for slow commands (splunk stop/start use 900)


@dataclass(frozen=True)
class Action:
    """A single, idempotent unit of work targeting one node."""

    name: str
    kind: ActionKind
    identity: Identity | None = None
    command: str | None = None
    script: tuple[ExpectStep, ...] = field(default_factory=tuple)
    wait_seconds: int | None = None
    note: str | None = None
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT

    def __post_init__(self) -> None:
        if self.kind == ActionKind.PLAIN and not self.command:
            raise ValueError(f"action {self.name!r}: PLAIN action requires a command")
        if self.kind == ActionKind.INTERACTIVE and not self.script:
            raise ValueError(f"action {self.name!r}: INTERACTIVE action requires a script")
        if self.kind == ActionKind.WAIT and self.wait_seconds is None:
            raise ValueError(f"action {self.name!r}: WAIT action requires wait_seconds")
        if self.kind in (ActionKind.PLAIN, ActionKind.INTERACTIVE) and self.identity is None:
            raise ValueError(f"action {self.name!r}: {self.kind.value} action requires an identity")
