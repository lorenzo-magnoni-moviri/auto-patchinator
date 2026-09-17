from auto_patchinator.actions.sequences import NodeRole
from auto_patchinator.actions.types import Identity
from auto_patchinator.executor.credentials import Credentials
from auto_patchinator.executor.ssh import (
    INDEXER_SPLUNK_SU,
    PASSWORD_EXPIRED_PATTERN,
    PROMPT_MARKER,
    CommandResult,
    SSHConnection,
    login_username,
    su_command,
)


def test_login_username_basic():
    assert login_username("lmm992", Identity.SPLUNK, "host01") == "lmm992@pas.prd.spk@host01"


def test_login_username_with_domain_suffix_and_port():
    name = login_username(
        "lmm992", Identity.ROOT, "host01",
        pas_domain_suffix=".sky.local", pas_port=10100,
    )
    assert name == "lmm992@pas.prd.spk.root@host01.sky.local#10100"


def test_login_username_does_not_double_domain_suffix():
    name = login_username("u", Identity.SPLUNK, "host01.sky.local", pas_domain_suffix=".sky.local")
    assert name == "u@pas.prd.spk@host01.sky.local"


def test_login_username_honours_custom_suffixes():
    suffixes = {Identity.SPLUNK: "pas.tst.spk", Identity.ROOT: "pas.tst.spk.root"}
    name = login_username("u", Identity.SPLUNK, "tst01", pas_suffixes=suffixes)
    assert name == "u@pas.tst.spk@tst01"


def test_su_command_selection():
    assert su_command(Identity.ROOT, NodeRole.FORWARDER) == "sudo su - root"
    assert su_command(Identity.SPLUNK, NodeRole.FORWARDER) == "sudo su - splunk"
    assert su_command(Identity.SPLUNK, NodeRole.INDEXER) == INDEXER_SPLUNK_SU


def test_extract_result_parses_exit_code():
    ok = SSHConnection._extract_result("output...\r\nAP_EXIT_CODE:0\r\n<<AP_READY>>")
    ko = SSHConnection._extract_result("boom\r\nAP_EXIT_CODE:127\r\n<<AP_READY>>")
    missing = SSHConnection._extract_result("no marker at all")
    assert ok.exit_code == 0 and ok.success
    assert ko.exit_code == 127 and not ko.success
    assert missing.exit_code is None and not missing.success


def test_command_result_success_only_on_zero():
    assert CommandResult(exit_code=0, output="").success
    assert not CommandResult(exit_code=1, output="").success
    assert not CommandResult(exit_code=None, output="").success


def test_password_expired_pattern_matches_real_banner_text():
    # Actual banner text observed against a real PAS/CyberArk gateway with an expired
    # root credential (see TODO.md) - a forced passwd-change prompt, not a shell prompt.
    banner = (
        "You are required to change your password immediately (password expired).\r\n"
        "WARNING: Your password has expired.\r\n"
        "You must change your password now and login again!\r\n"
        "Changing password for user pas.tst.spk.root.\r\n"
        "Current password: "
    )
    assert PASSWORD_EXPIRED_PATTERN.search(banner)


def test_password_expired_pattern_does_not_match_normal_shell_prompt():
    assert not PASSWORD_EXPIRED_PATTERN.search("[splunk@tstmilbbspkdp01 ~]$ ")


class _FakeSession:
    """Records every send() call (and whether it was marked sensitive), and always
    responds as if the command completed successfully."""

    def __init__(self):
        self.sends: list[tuple[str, bool]] = []

    def send(self, text, sensitive=False):
        self.sends.append((text, sensitive))

    def read_until(self, pattern, timeout=30):
        return f"...\r\nAP_EXIT_CODE:0\r\n{PROMPT_MARKER}"


def test_run_plain_with_secret_never_sends_the_secret_in_the_command_itself():
    conn = SSHConnection("host01", Identity.SPLUNK, NodeRole.DEPLOYER, Credentials("u", "p"))
    fake_session = _FakeSession()
    conn._session = fake_session  # bypass connect() - this only tests run_plain_with_secret

    result = conn.run_plain_with_secret('splunk show shcluster-status -u "admin:$AP_SECRET"', "hunter2")

    assert result.success
    secret_sends = [text for text, sensitive in fake_session.sends if sensitive]
    assert len(secret_sends) == 1
    assert "hunter2" in secret_sends[0]  # the ONE sensitive send carries the real secret

    non_sensitive_sends = [text for text, sensitive in fake_session.sends if not sensitive]
    assert all("hunter2" not in text for text in non_sensitive_sends)  # never in a logged send
    assert any("$AP_SECRET" in text for text in non_sensitive_sends)  # the command references it by name
