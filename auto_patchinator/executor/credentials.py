"""Prompts once at startup for the username/password used to build PAS login strings and
the later 'sudo su' step. Held in memory only - never written to disk or logged.

If AP_USERNAME and AP_PASSWORD are set in the environment (or in a .env file at the
project root), they are used without prompting."""
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
