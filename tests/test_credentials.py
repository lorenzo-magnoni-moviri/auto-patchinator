import os
from pathlib import Path

from auto_patchinator.executor.credentials import (
    ENV_CREDENTIAL_FIELDS,
    SplunkApiCredentials,
    StreamSetsApiCredentials,
    _prompt_text,
    _write_env_values,
    ensure_env_credentials_complete,
    load_splunk_api_credentials,
    load_streamsets_api_credentials,
)


def _clear_env(monkeypatch):
    # _load_dotenv() would otherwise re-populate these straight from a real .env file
    # (dotenv fills in anything not already set) - stubbed out so these tests are
    # isolated from whatever secrets the developer's local .env actually has.
    monkeypatch.setattr("auto_patchinator.executor.credentials._load_dotenv", lambda: None)
    for var in ("SPLUNK_API_TOKEN",) + ENV_CREDENTIAL_FIELDS:
        monkeypatch.delenv(var, raising=False)


def test_returns_none_when_nothing_configured(monkeypatch):
    _clear_env(monkeypatch)
    assert load_splunk_api_credentials() is None


def test_token_alone_is_sufficient(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SPLUNK_API_TOKEN", "abc123")
    creds = load_splunk_api_credentials()
    assert creds is not None
    assert creds.token == "abc123"
    assert creds.configured


def test_username_without_password_is_not_configured(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SPLUNK_API_USER", "admin")
    assert load_splunk_api_credentials() is None


def test_username_and_password_together_are_configured(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SPLUNK_API_USER", "admin")
    monkeypatch.setenv("SPLUNK_API_PASSWORD", "secret")
    creds = load_splunk_api_credentials()
    assert creds is not None
    assert creds.username == "admin"
    assert creds.password == "secret"


def test_repr_never_leaks_secrets():
    creds = SplunkApiCredentials(token="supersecret", username="admin", password="hunter2")
    rendered = repr(creds)
    assert "supersecret" not in rendered
    assert "hunter2" not in rendered
    assert "admin" in rendered


def test_streamsets_returns_none_when_nothing_configured(monkeypatch):
    _clear_env(monkeypatch)
    assert load_streamsets_api_credentials() is None


def test_streamsets_username_without_password_is_not_configured(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("STREAMSETS_API_USER", "admin")
    assert load_streamsets_api_credentials() is None


def test_streamsets_username_and_password_together_are_configured(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("STREAMSETS_API_USER", "admin")
    monkeypatch.setenv("STREAMSETS_API_PASSWORD", "secret")
    creds = load_streamsets_api_credentials()
    assert creds is not None
    assert creds.username == "admin"
    assert creds.password == "secret"
    assert creds.configured


def test_streamsets_repr_never_leaks_secrets():
    creds = StreamSetsApiCredentials(username="admin", password="hunter2")
    rendered = repr(creds)
    assert "hunter2" not in rendered
    assert "admin" in rendered


def _force_tty(monkeypatch):
    # ensure_env_credentials_complete() skips prompting entirely when stdin isn't a
    # tty (CI, piped input) rather than crashing with EOFError once input() runs out
    # of data - see test_ensure_env_credentials_complete_skips_when_stdin_is_not_a_tty.
    # Tests that exercise the actual prompting need to simulate an interactive session.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)


def test_ensure_env_credentials_complete_prompts_and_writes_missing_fields(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    _force_tty(monkeypatch)
    answers = iter(["alice", "alicepw", "splunkuser", "splunkpw", "streamsetsuser", "streamsetspw"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    env_path = tmp_path / ".env"
    ensure_env_credentials_complete(env_path)

    assert os.environ["AP_USERNAME"] == "alice"
    assert os.environ["AP_PASSWORD"] == "alicepw"
    assert os.environ["SPLUNK_API_USER"] == "splunkuser"
    assert os.environ["SPLUNK_API_PASSWORD"] == "splunkpw"
    assert os.environ["STREAMSETS_API_USER"] == "streamsetsuser"
    assert os.environ["STREAMSETS_API_PASSWORD"] == "streamsetspw"

    content = env_path.read_text()
    for line in (
        "AP_USERNAME=alice", "AP_PASSWORD=alicepw",
        "SPLUNK_API_USER=splunkuser", "SPLUNK_API_PASSWORD=splunkpw",
        "STREAMSETS_API_USER=streamsetsuser", "STREAMSETS_API_PASSWORD=streamsetspw",
    ):
        assert line in content


def test_ensure_env_credentials_complete_skips_prompting_when_all_set(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    for name in ENV_CREDENTIAL_FIELDS:
        monkeypatch.setenv(name, f"{name.lower()}-value")

    def _boom(prompt=""):
        raise AssertionError("input() should not be called when nothing is missing")

    monkeypatch.setattr("builtins.input", _boom)

    env_path = tmp_path / ".env"
    ensure_env_credentials_complete(env_path)

    assert not env_path.exists()


def test_ensure_env_credentials_complete_only_prompts_for_missing_ones(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    _force_tty(monkeypatch)
    monkeypatch.setenv("AP_USERNAME", "alice")
    monkeypatch.setenv("AP_PASSWORD", "alicepw")

    prompted = []
    answers = iter(["splunkuser", "splunkpw", "streamsetsuser", "streamsetspw"])

    def _input(prompt=""):
        prompted.append(prompt.strip())
        return next(answers)

    monkeypatch.setattr("builtins.input", _input)

    env_path = tmp_path / ".env"
    ensure_env_credentials_complete(env_path)

    assert prompted == [
        _prompt_text("SPLUNK_API_USER").strip(),
        _prompt_text("SPLUNK_API_PASSWORD").strip(),
        _prompt_text("STREAMSETS_API_USER").strip(),
        _prompt_text("STREAMSETS_API_PASSWORD").strip(),
    ]


def test_ensure_env_credentials_complete_prompt_hints_ap_username_and_splunk_api_user():
    # AP_USERNAME: suggest the operator's own day-to-day Splunk login (no default -
    # there isn't a sensible one). SPLUNK_API_USER: suggest + default to "admin", since
    # that's virtually always the account used for the API checks in this environment.
    assert "your own Splunk login" in _prompt_text("AP_USERNAME")
    assert "[" not in _prompt_text("AP_USERNAME")

    assert "admin" in _prompt_text("SPLUNK_API_USER")
    assert "[admin]" in _prompt_text("SPLUNK_API_USER")


def test_ensure_env_credentials_complete_blank_input_is_skipped(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    _force_tty(monkeypatch)
    # SPLUNK_API_USER has a default ("admin") so blank resolves to that; the other
    # fields have no default, so blank there leaves them unset.
    answers = iter(["alice", "alicepw", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    env_path = tmp_path / ".env"
    ensure_env_credentials_complete(env_path)

    assert os.environ["AP_USERNAME"] == "alice"
    assert os.environ["AP_PASSWORD"] == "alicepw"
    assert os.environ["SPLUNK_API_USER"] == "admin"
    assert "SPLUNK_API_PASSWORD" not in os.environ
    assert "STREAMSETS_API_USER" not in os.environ
    assert "STREAMSETS_API_PASSWORD" not in os.environ

    content = env_path.read_text()
    assert "AP_USERNAME=alice" in content
    assert "SPLUNK_API_USER=admin" in content
    # skipped fields aren't written with a value - only .env.example's own blank
    # placeholder line (if any) survives, never a filled-in STREAMSETS_API_USER=<something>.
    assert "STREAMSETS_API_USER=streamsetsuser" not in content
    for line in content.splitlines():
        if line.startswith("STREAMSETS_API_USER="):
            assert line == "STREAMSETS_API_USER="


def test_ensure_env_credentials_complete_splunk_api_user_defaults_to_admin_on_blank(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    _force_tty(monkeypatch)
    monkeypatch.setenv("AP_USERNAME", "alice")
    monkeypatch.setenv("AP_PASSWORD", "alicepw")
    monkeypatch.setenv("SPLUNK_API_PASSWORD", "splunkpw")
    monkeypatch.setenv("STREAMSETS_API_USER", "streamsetsuser")
    monkeypatch.setenv("STREAMSETS_API_PASSWORD", "streamsetspw")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    env_path = tmp_path / ".env"
    ensure_env_credentials_complete(env_path)

    assert os.environ["SPLUNK_API_USER"] == "admin"
    assert "SPLUNK_API_USER=admin" in env_path.read_text()


def test_ensure_env_credentials_complete_skips_when_stdin_is_not_a_tty(monkeypatch, tmp_path):
    # Found live (2026-09-24): CI's --dry-run smoke test has no .env and pipes a
    # single canned answer to stdin - the old unconditional prompting loop consumed
    # that answer on its first field, then crashed with EOFError once stdin ran out.
    # No operator to prompt in a non-interactive context, so this must skip silently
    # rather than call input() at all.
    _clear_env(monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def _boom(prompt=""):
        raise AssertionError("input() should not be called when stdin isn't a tty")

    monkeypatch.setattr("builtins.input", _boom)

    env_path = tmp_path / ".env"
    ensure_env_credentials_complete(env_path)  # must not raise EOFError

    assert "AP_USERNAME" not in os.environ
    assert not env_path.exists()


def test_write_env_values_updates_existing_and_preserves_comments(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment line\n"
        "AP_USERNAME=old\n"
        "\n"
        "SPLUNK_API_USER=keepme\n"
    )

    _write_env_values(env_path, {"AP_USERNAME": "new"})

    lines = env_path.read_text().splitlines()
    assert "# comment line" in lines
    assert "AP_USERNAME=new" in lines
    assert "SPLUNK_API_USER=keepme" in lines
    assert lines.count("AP_USERNAME=new") == 1


def test_write_env_values_appends_missing_keys(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("AP_USERNAME=alice\n")

    _write_env_values(env_path, {"SPLUNK_API_USER": "newuser"})

    content = env_path.read_text()
    assert "AP_USERNAME=alice" in content
    assert "SPLUNK_API_USER=newuser" in content


def test_write_env_values_chmods_to_600(tmp_path):
    env_path = tmp_path / ".env"
    _write_env_values(env_path, {"AP_USERNAME": "alice"})

    mode = env_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_write_env_values_creates_from_example_when_env_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text(
        "# example header\n"
        "AP_USERNAME=\n"
        "AP_PASSWORD=\n"
    )
    env_path = tmp_path / ".env"

    _write_env_values(env_path, {"AP_USERNAME": "alice"})

    content = env_path.read_text()
    assert "# example header" in content
    assert "AP_USERNAME=alice" in content
    assert "AP_PASSWORD=" in content
