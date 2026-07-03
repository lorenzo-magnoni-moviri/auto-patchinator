"""SSH connection layer.

Login model (per the team's PAS/CyberArk-fronted setup): SSH connects using a username
that already encodes which target identity you want on which host, e.g.
`<username>@pas.prd.spk@<hostname>.sky.local` for the splunk user or
`<username>@pas.prd.spk.root@<hostname>.sky.local` for root, against a shared PAS
gateway.  Once connected you still `sudo su - splunk` / `sudo su - root` (indexers use
`sudo /bin/su - splunk -s /bin/bash` instead) using the same password.

The PAS gateway requires keyboard-interactive authentication (not password auth).
`connect()` tries password first and falls back to keyboard-interactive automatically,
responding to every challenge prompt with the same password.

Because that su step - and some commands like the splunk user's aliased `crontab -i` -
can prompt interactively, every command runs inside one persistent PTY shell session
rather than via a one-shot exec_command. A unique marker is used to detect when a
command has finished and to recover its exit code.
"""
from __future__ import annotations

import logging
import re
import shlex
import time
from dataclasses import dataclass

from auto_patchinator.actions.sequences import NodeRole
from auto_patchinator.actions.types import ExpectStep, Identity
from auto_patchinator.executor.credentials import Credentials

# Suppress paramiko's internal transport error logging - connection errors are caught
# and re-raised as Python exceptions; the log spam just clutters the terminal.
logging.getLogger("paramiko").setLevel(logging.CRITICAL)

_log = logging.getLogger(__name__)

DEFAULT_PAS_SUFFIXES = {
    Identity.SPLUNK: "pas.prd.spk",
    Identity.ROOT: "pas.prd.spk.root",
}

SU_COMMAND = {
    Identity.SPLUNK: "sudo su - splunk",
    Identity.ROOT: "sudo su - root",
}
INDEXER_SPLUNK_SU = "sudo /bin/su - splunk -s /bin/bash"

PASSWORD_PROMPT_PATTERN = re.compile(r"assword.*:\s*$", re.IGNORECASE)
# A forced password-change prompt (expired PAS/CyberArk credential) looks nothing like
# a normal shell prompt, so without this the initial connect() read just times out
# after 30s with no useful diagnosis - detect it and fail fast instead.
PASSWORD_EXPIRED_PATTERN = re.compile(r"password has expired|changing password for", re.IGNORECASE)
PROMPT_MARKER = "<<AP_READY>>"
EXIT_MARKER = "AP_EXIT_CODE"

_CONNECT_RETRIES = 2
_CONNECT_RETRY_DELAY = 3.0  # seconds between retries (handles PAS rate-limiting)


def login_username(
    username: str,
    identity: Identity,
    hostname: str,
    pas_domain_suffix: str | None = None,
    pas_port: int | None = None,
    pas_suffixes: dict[Identity, str] | None = None,
) -> str:
    suffixes = pas_suffixes if pas_suffixes is not None else DEFAULT_PAS_SUFFIXES
    target = hostname
    if pas_domain_suffix and not hostname.endswith(pas_domain_suffix):
        target += pas_domain_suffix
    if pas_port is not None:
        target += f"#{pas_port}"
    return f"{username}@{suffixes[identity]}@{target}"


def su_command(identity: Identity, role: NodeRole) -> str:
    if identity == Identity.SPLUNK and role == NodeRole.INDEXER:
        return INDEXER_SPLUNK_SU
    return SU_COMMAND[identity]


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    output: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class TimeoutReadingShell(RuntimeError):
    pass


class PasswordExpiredError(RuntimeError):
    pass


class _ShellSession:
    """A persistent PTY-backed shell channel, read up to a marker at a time."""

    def __init__(self, channel, label: str = "") -> None:
        self._channel = channel
        self._buffer = ""
        self._label = label

    def send(self, text: str, sensitive: bool = False) -> None:
        _log.debug("%s >> %s", self._label, "<redacted>" if sensitive else text)
        self._channel.send(text + "\n")

    def read_until(self, pattern: re.Pattern[str] | str, timeout: float = 30) -> str:
        deadline = time.monotonic() + timeout
        matcher = pattern if isinstance(pattern, re.Pattern) else re.compile(re.escape(pattern))
        while time.monotonic() < deadline:
            if self._channel.recv_ready():
                self._buffer += self._channel.recv(4096).decode(errors="replace")
                if matcher.search(self._buffer):
                    consumed, self._buffer = self._buffer, ""
                    _log.debug("%s << %r", self._label, consumed)
                    return consumed
            else:
                time.sleep(0.1)
        _log.warning(
            "%s timed out after %ss waiting for %r; buffer so far: %r",
            self._label, timeout, pattern, self._buffer,
        )
        raise TimeoutReadingShell(f"timed out waiting for {pattern!r}; buffer so far: {self._buffer!r}")


def _paramiko_connect(client, target: str, port: int, username: str, password: str) -> None:
    """Connect and authenticate, trying keyboard-interactive if the server rejects password auth."""
    import paramiko

    try:
        client.connect(
            target,
            port=port,
            username=username,
            password=password,
            look_for_keys=False,
            allow_agent=False,
            timeout=30,
        )
        return
    except paramiko.BadAuthenticationType as exc:
        allowed = getattr(exc, "allowed_types", [])
        if "keyboard-interactive" not in allowed:
            raise

    # Server requires keyboard-interactive: respond to every prompt with the password.
    transport = client.get_transport()
    if transport is None or not transport.is_active():
        raise RuntimeError(
            f"Cannot authenticate to {target}: server requires keyboard-interactive "
            "but transport is no longer active"
        )
    transport.auth_interactive(
        username,
        lambda title, instructions, fields: [password for _ in fields],
    )


class SSHConnection:
    """One PTY shell session to a node, already 'su'd into the requested identity."""

    def __init__(
        self,
        hostname: str,
        identity: Identity,
        role: NodeRole,
        credentials: Credentials,
        pas_gateway: str | None = None,
        port: int = 22,
        pas_domain_suffix: str | None = None,
        pas_port: int | None = None,
        splunk_su_command: str | None = None,
        pas_suffixes: dict[Identity, str] | None = None,
    ) -> None:
        self.hostname = hostname
        self.identity = identity
        self.role = role
        self._credentials = credentials
        self._pas_gateway = pas_gateway
        self._port = port
        self._pas_domain_suffix = pas_domain_suffix
        self._pas_port = pas_port
        self._splunk_su_command = splunk_su_command
        self._pas_suffixes = pas_suffixes
        self._client = None
        self._session: _ShellSession | None = None

    def connect(self) -> None:
        import paramiko  # imported lazily so dry-run mode never requires it installed

        target = self._pas_gateway or self.hostname
        username = (
            login_username(
                self._credentials.username,
                self.identity,
                self.hostname,
                pas_domain_suffix=self._pas_domain_suffix,
                pas_port=self._pas_port,
                pas_suffixes=self._pas_suffixes,
            )
            if self._pas_gateway
            else self._credentials.username
        )

        label = f"{self.hostname}({self.identity.value})"
        _log.info("%s connecting via %s as %r", label, target, username)

        last_exc: Exception | None = None
        for attempt in range(_CONNECT_RETRIES):
            if attempt > 0:
                _log.info("%s retrying connect (attempt %d) after: %s", label, attempt + 1, last_exc)
                time.sleep(_CONNECT_RETRY_DELAY)
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                _paramiko_connect(client, target, self._port, username, self._credentials.password)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                client = None

        if last_exc is not None:
            _log.error("%s connect failed after %d attempt(s): %s", label, _CONNECT_RETRIES, last_exc)
            raise last_exc

        # From here on the paramiko-level auth has already succeeded and `client` owns a
        # live socket - any failure in the shell/su setup below must close it before
        # propagating, or the connection leaks until garbage collection.
        try:
            channel = client.invoke_shell()
            session = _ShellSession(channel, label=label)
            banner = session.read_until(
                re.compile(PASSWORD_EXPIRED_PATTERN.pattern + r"|[#$>]\s*$", re.IGNORECASE),
                timeout=30,
            )
            if PASSWORD_EXPIRED_PATTERN.search(banner):
                raise PasswordExpiredError(
                    f"{label}: this identity's PAS/CyberArk password has expired on the "
                    "target host (forced password-change prompt) - rotate it via "
                    "CyberArk before retrying."
                )

            if self.identity == Identity.SPLUNK and self._splunk_su_command:
                su_cmd = self._splunk_su_command
            else:
                su_cmd = su_command(self.identity, self.role)
            session.send(su_cmd)
            marker_or_password = session.read_until(
                re.compile(PASSWORD_PROMPT_PATTERN.pattern + r"|[#$>]\s*$", re.IGNORECASE),
                timeout=15,
            )
            if PASSWORD_PROMPT_PATTERN.search(marker_or_password):
                session.send(self._credentials.password, sensitive=True)
                session.read_until(re.compile(r"[#$>]\s*$"), timeout=15)

            session.send(f"PS1='{PROMPT_MARKER}'")
            session.read_until(PROMPT_MARKER, timeout=15)
        except Exception:
            client.close()
            raise

        self._client = client
        self._session = session

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._session = None

    def run_plain(self, command: str, timeout: float = 60) -> CommandResult:
        session = self._require_session()
        session.send(f"{command}; echo {EXIT_MARKER}:$?")
        raw = session.read_until(PROMPT_MARKER, timeout=timeout)
        return self._extract_result(raw)

    def run_interactive(self, script: tuple[ExpectStep, ...], timeout: float = 60) -> CommandResult:
        session = self._require_session()
        output = ""
        for step in script:
            session.send(step.send)
            output += session.read_until(step.expect or PROMPT_MARKER, timeout=timeout)
        session.send(f"echo {EXIT_MARKER}:$?")
        output += session.read_until(PROMPT_MARKER, timeout=timeout)
        return self._extract_result(output)

    def _require_session(self) -> _ShellSession:
        if self._session is None:
            raise RuntimeError(f"{self.hostname}: call connect() before running commands")
        return self._session

    @staticmethod
    def _extract_result(raw: str) -> CommandResult:
        match = re.search(rf"{EXIT_MARKER}:(\d+)", raw)
        exit_code = int(match.group(1)) if match else None
        return CommandResult(exit_code=exit_code, output=raw)


class DryRunConnection:
    """Same interface as SSHConnection, but only prints/records what it would run."""

    def __init__(self, hostname: str, identity: Identity, role: NodeRole, **_ignored) -> None:
        self.hostname = hostname
        self.identity = identity
        self.role = role

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def run_plain(self, command: str, timeout: float = 60) -> CommandResult:
        return CommandResult(exit_code=0, output=f"[dry-run] {self.hostname} ({self.identity.value}) $ {command}")

    def run_interactive(self, script: tuple[ExpectStep, ...], timeout: float = 60) -> CommandResult:
        rendered = " -> ".join(shlex.quote(step.send) for step in script)
        return CommandResult(exit_code=0, output=f"[dry-run] {self.hostname} ({self.identity.value}) $ {rendered}")
