"""Tests for splunk_cli.py's pure parsing helpers, against real captured
`splunk show shcluster-status --verbose` output (prdrmlbbspksh01, 2026-09-16, see
TODO.md) - trimmed to 2 members but structurally verbatim, CRLF line endings
included since that's exactly what tripped this up live the first time."""
from auto_patchinator.executor.splunk_cli import (
    find_member,
    member_health,
    normalize_line_endings,
    parse_shcluster_members,
)

# \r\n throughout, matching the real raw SSH output exactly - the bug this session
# was a regex silently failing to match because of the stray \r.
REAL_MEMBERS_OUTPUT = (
    " Captain:\r\n"
    "\t\t               dynamic_captain : 1\r\n"
    "\t\t                         label : prdmilbbspksh04.sky.local\r\n"
    "\r\n"
    " Cluster Manager(s):\r\n"
    "\thttps://prdmilbbspkdp01:8089\t\tsplunk_version: 9.4.15\r\n"
    "\r\n"
    " Members: \r\n"
    "\tprdrmlbbspksh05.sky.local\r\n"
    "\t\t                kvstore_status : ready\r\n"
    "\t\t                         label : prdrmlbbspksh05.sky.local\r\n"
    "\t\t              out_of_sync_node : 0\r\n"
    "\t\t             preferred_captain : 0\r\n"
    "\t\treported_preexisting_artifacts : 1\r\n"
    "\t\t              restart_required : 0\r\n"
    "\t\t                splunk_version : 9.4.15\r\n"
    "\t\t                        status : Up\r\n"
    "\tprdrmlbbspksh02.sky.local\r\n"
    "\t\t                kvstore_status : ready\r\n"
    "\t\t                         label : prdrmlbbspksh02.sky.local\r\n"
    "\t\t              out_of_sync_node : 0\r\n"
    "\t\t             preferred_captain : 0\r\n"
    "\t\treported_preexisting_artifacts : 1\r\n"
    "\t\t              restart_required : 0\r\n"
    "\t\t                splunk_version : 9.4.15\r\n"
    "\t\t                        status : Up\r\n"
)


def test_normalize_line_endings_strips_crlf():
    assert normalize_line_endings("a\r\nb\r\n") == "a\nb\n"


def test_parse_shcluster_members_extracts_every_member_and_field():
    members = parse_shcluster_members(REAL_MEMBERS_OUTPUT)
    assert set(members) == {"prdrmlbbspksh05.sky.local", "prdrmlbbspksh02.sky.local"}
    assert members["prdrmlbbspksh05.sky.local"]["status"] == "Up"
    assert members["prdrmlbbspksh05.sky.local"]["out_of_sync_node"] == "0"
    assert members["prdrmlbbspksh05.sky.local"]["restart_required"] == "0"
    assert members["prdrmlbbspksh05.sky.local"]["kvstore_status"] == "ready"


def test_parse_shcluster_members_does_not_pick_up_the_captain_section():
    members = parse_shcluster_members(REAL_MEMBERS_OUTPUT)
    assert "label" not in members  # would indicate the Captain: section leaked in
    assert all("dynamic_captain" not in fields for fields in members.values())


def test_find_member_matches_short_hostname_against_fqdn_key():
    members = parse_shcluster_members(REAL_MEMBERS_OUTPUT)
    fields = find_member(members, "prdrmlbbspksh05")
    assert fields is not None
    assert fields["status"] == "Up"


def test_find_member_does_not_substring_match():
    members = {"prdrmlbbspksh010.sky.local": {"status": "Up"}}
    assert find_member(members, "prdrmlbbspksh01") is None


def test_find_member_returns_none_when_absent():
    members = parse_shcluster_members(REAL_MEMBERS_OUTPUT)
    assert find_member(members, "someotherhost") is None


def test_member_health_healthy_case():
    members = parse_shcluster_members(REAL_MEMBERS_OUTPUT)
    healthy, description = member_health(find_member(members, "prdrmlbbspksh05"))
    assert healthy is True
    assert "status='Up'" in description


def test_member_health_unhealthy_case():
    fields = {"status": "Up", "out_of_sync_node": "1", "restart_required": "0"}
    healthy, description = member_health(fields)
    assert healthy is False
    assert "out_of_sync_node='1'" in description


def test_member_health_missing_member():
    healthy, description = member_health(None)
    assert healthy is False
    assert "not found" in description
