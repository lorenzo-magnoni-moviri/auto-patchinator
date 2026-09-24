"""Prompts once at startup for the username/password used to build PAS login strings and
the later 'sudo su' step. Held in memory only - never written to disk or logged (except
by ensure_env_credentials_complete(), which explicitly writes newly-entered values back
into .env itself - that's the point of that one).

If AP_USERNAME and AP_PASSWORD are set in the environment (or in a .env file at the
project root), they are used without prompting.

Also loads (optional) Splunk REST API credentials - consumed by preflight.py's pretest
and CLUSTER_WAIT - via SPLUNK_API_TOKEN or SPLUNK_API_USER/SPLUNK_API_PASSWORD, and
StreamSets Data Collector REST API credentials for prdmilbbspkfw02's pipeline
stop/start automation via STREAMSETS_API_USER/STREAMSETS_API_PASSWORD. Never hardcode
these values; type/store them only via .env (gitignored).

Terminal input for all of these is never masked (operator's explicit preference,
2026-09-24 - see TODO.md): easier to verify what was typed/pasted than a blind
getpass() prompt, and .env itself already isn't visible to anyone without shell access
to this machine."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str

    def __repr__(self) -> str:  # avoid accidental leakage via logging/repr
        return f"Credentials(username={self.username!r}, password='***')"


@dataclass(frozen=True)
class SplunkApiCredentials:
    """Splunk REST API auth - a token (preferred) or username/password.

    Not currently used by any action; reserved for future automations (captain
    transfer, cluster status checks) called out in TODO.md.
    """

    token: str | None = None
    username: str | None = None
    password: str | None = None

    def __repr__(self) -> str:  # avoid accidental leakage via logging/repr
        return (
            f"SplunkApiCredentials(username={self.username!r}, "
            f"token={'***' if self.token else None!r}, "
            f"password={'***' if self.password else None!r})"
        )

    @property
    def configured(self) -> bool:
        return bool(self.token) or bool(self.username and self.password)


@dataclass(frozen=True)
class StreamSetsApiCredentials:
    """StreamSets Data Collector REST API auth for prdmilbbspkfw02's pipeline
    stop/start automation (HOST_OVERRIDES in actions/sequences.py). HTTP Basic auth
    only (username/password) - matches the working curl commands verified live against
    a real pipeline on that host, 2026-09-22; no token support observed/used there.
    """

    username: str | None = None
    password: str | None = None

    def __repr__(self) -> str:  # avoid accidental leakage via logging/repr
        return (
            f"StreamSetsApiCredentials(username={self.username!r}, "
            f"password={'***' if self.password else None!r})"
        )

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        load_dotenv()
    except ImportError:
        pass


def prompt_credentials() -> Credentials:
    _load_dotenv()
    username = os.environ.get("AP_USERNAME", "").strip()
    password = os.environ.get("AP_PASSWORD", "")
    if username and password:
        print(f"Using credentials from environment (AP_USERNAME={username!r}).")
        return Credentials(username=username, password=password)
    if not username:
        username = input("Username: ").strip()
    if not password:
        password = input("Password: ")  # not masked - operator's explicit preference
    return Credentials(username=username, password=password)


def load_splunk_api_credentials() -> SplunkApiCredentials | None:
    """Load Splunk REST API credentials from the environment/.env, if configured.

    Returns None (never prompts) if neither a token nor a username+password pair is
    set - callers should treat that as "the feature needing these isn't available yet".
    """
    _load_dotenv()
    token = os.environ.get("SPLUNK_API_TOKEN", "").strip() or None
    username = os.environ.get("SPLUNK_API_USER", "").strip() or None
    password = os.environ.get("SPLUNK_API_PASSWORD", "") or None
    creds = SplunkApiCredentials(token=token, username=username, password=password)
    return creds if creds.configured else None


def load_streamsets_api_credentials() -> StreamSetsApiCredentials | None:
    """Load StreamSets Data Collector REST API credentials from the environment/.env,
    if configured. Returns None (never prompts) if username+password aren't both set -
    callers should treat that as "the feature needing these isn't available yet",
    same convention as load_splunk_api_credentials."""
    _load_dotenv()
    username = os.environ.get("STREAMSETS_API_USER", "").strip() or None
    password = os.environ.get("STREAMSETS_API_PASSWORD", "") or None
    creds = StreamSetsApiCredentials(username=username, password=password)
    return creds if creds.configured else None


# Every credential field this tool actually consumes for something - deliberately
# excludes SPLUNK_API_TOKEN: every feature that uses Splunk API credentials needs the
# `-auth user:password` CLI form (preflight's checks, CLUSTER_WAIT, CAPTAIN_TRANSFER/
# REVERT, all run `splunk show .../splunk bootstrap ... -auth`), never a bare token -
# prompting for a field nothing reads would just be noise.
ENV_CREDENTIAL_FIELDS: tuple[str, ...] = (
    "AP_USERNAME",
    "AP_PASSWORD",
    "SPLUNK_API_USER",
    "SPLUNK_API_PASSWORD",
    "STREAMSETS_API_USER",
    "STREAMSETS_API_PASSWORD",
)

# Per-field guidance shown alongside the prompt - AP_USERNAME has no sensible default
# (it's the operator's own PAS/Splunk login), so it just gets a hint; SPLUNK_API_USER
# is virtually always the Splunk admin account in this environment, so it gets both a
# hint and a default that blank input accepts (shown in the prompt as `[admin]`, the
# portable terminal-prompt equivalent of pre-filling the field - an actual editable
# pre-fill would need the readline module, which isn't available on native Windows and
# this tool is kept Windows-native, see CLAUDE.md).
_FIELD_HINTS: dict[str, str] = {
    "AP_USERNAME": "use your own Splunk login - the same credentials you use day to day",
    "SPLUNK_API_USER": "use the Splunk admin account",
}
_FIELD_DEFAULTS: dict[str, str] = {
    "SPLUNK_API_USER": "admin",
}


def _prompt_text(name: str) -> str:
    hint = _FIELD_HINTS.get(name)
    default = _FIELD_DEFAULTS.get(name)
    suffix = f" [{default}]" if default else ""
    prefix = f" ({hint})" if hint else ""
    return f"  {name}{prefix}{suffix}: "


def ensure_env_credentials_complete(env_path: str | Path = ".env") -> None:
    """Checks .env for every field in ENV_CREDENTIAL_FIELDS and interactively prompts
    for any that are missing or blank, then writes the answers back into .env so
    future runs don't ask again - called once, early in cli.py:cmd_run, before any
    plan/Excel work. .env is meant to be a stable, complete local config (unlike the
    wave Excel, which changes every run), so this gets it there up front instead of
    letting each feature independently discover a gap and silently degrade.

    Blank input is accepted (Enter to skip) for a field with no default in
    _FIELD_DEFAULTS - AP_USERNAME/AP_PASSWORD are the only fields this tool can't
    actually run without, but that's still left to the existing
    prompt_credentials()/load_*_credentials() fallback behavior to enforce, not
    duplicated here. A field with a default (currently just SPLUNK_API_USER, defaulting
    to "admin") uses that default instead of being left blank when the operator just
    hits Enter."""
    _load_dotenv()
    env_path = Path(env_path)

    missing = [name for name in ENV_CREDENTIAL_FIELDS if not os.environ.get(name, "").strip()]
    if not missing:
        return

    print(f"\n.env is missing: {', '.join(missing)}")
    print("Enter them now - saved to .env so this isn't asked again (blank to skip one):")
    answers: dict[str, str] = {}
    for name in missing:
        raw = input(_prompt_text(name)).strip()
        value = raw or _FIELD_DEFAULTS.get(name, "")
        if value:
            answers[name] = value
            os.environ[name] = value

    if answers:
        _write_env_values(env_path, answers)
        print(f".env updated ({', '.join(answers)}).\n")


def _write_env_values(env_path: Path, values: dict[str, str]) -> None:
    """Updates env_path in place: replaces the value on any existing `KEY=...` line
    for a key in `values`, appends a line for any key not already present, and
    leaves every other line (comments, blank lines, unrelated keys) untouched. If
    env_path doesn't exist yet, starts from .env.example's structure (so comments
    survive) if that's found, or from nothing otherwise."""
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    else:
        example = Path(".env.example")
        lines = example.read_text().splitlines() if example.exists() else []

    remaining = dict(values)
    updated = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in remaining:
            updated.append(f"{key}={remaining.pop(key)}")
        else:
            updated.append(line)
    for key, value in remaining.items():
        updated.append(f"{key}={value}")

    env_path.write_text("\n".join(updated) + "\n")
    try:
        env_path.chmod(0o600)
    except OSError:
        pass  # best-effort (e.g. unsupported on this filesystem) - not fatal
