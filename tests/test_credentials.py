from auto_patchinator.executor.credentials import SplunkApiCredentials, load_splunk_api_credentials


def _clear_env(monkeypatch):
    for var in ("SPLUNK_API_TOKEN", "SPLUNK_API_USER", "SPLUNK_API_PASSWORD"):
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
