from auto_patchinator.actions.sequences import NodeRole
from auto_patchinator.actions.types import Identity
from auto_patchinator.executor.ssh import (
    INDEXER_SPLUNK_SU,
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
