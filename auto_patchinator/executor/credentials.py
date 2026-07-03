"""Prompts once at startup for the username/password used to build PAS login strings and
the later 'sudo su' step. Held in memory only - never written to disk or logged.

If AP_USERNAME and AP_PASSWORD are set in the environment (or in a .env file at the
project root), they are used without prompting.

Also loads (optional, not yet consumed by anything) Splunk REST API credentials for
future automations - captain transfer, cluster status polling - via
SPLUNK_API_TOKEN or SPLUNK_API_USER/SPLUNK_API_PASSWORD. Never hardcode these values;
type/store them only via .env (gitignored)."""
from __future__ import annotations

import getpass
import os
from dataclasses import dataclass


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
        password = getpass.getpass("Password: ")
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
