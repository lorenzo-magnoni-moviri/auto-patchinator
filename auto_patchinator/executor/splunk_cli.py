"""Pure text-parsing helpers for `splunk show <subcommand> --verbose` CLI output.
No I/O here - callers run the actual command (over SSH, via run_plain_with_secret)
and pass the raw output in. Shared by the pre-patch pretest's captain/kvstore checks
(preflight.py) and the post-restart cluster-health wait action (runner/controller.py).
"""
from __future__ import annotations

import re


def normalize_line_endings(output: str) -> str:
    """The raw PTY-captured SSH output uses \\r\\n throughout - normalize to bare \\n
    once, centrally, so every regex here can assume plain line endings. A regex
    expecting a line boundary right after some captured text silently fails to match
    if there's a stray \\r in between - found live, 2026-09-16, see TODO.md."""
    return output.replace("\r\n", "\n")


def parse_shcluster_members(output: str) -> dict[str, dict[str, str]]:
    """Parse the "Members:" section of `splunk show shcluster-status --verbose` -
    returns {member_fqdn: {field: value}} for every member, e.g. 'status', 'label',
    'out_of_sync_node', 'restart_required'. Each member's block is a right-aligned
    "key : value" table, same shape as the "Captain:"/"KV store members:" sections
    (see preflight.py) - confirmed live, 2026-09-16 (see TODO.md), though this
    specific section (as opposed to "Captain:"/"KV store members:", already
    live-verified) has not itself been exercised against a real post-restart poll
    loop yet - the member-header line here is a bare hostname/FQDN with no ':port'
    suffix, unlike "KV store members:"'s 'host:8191' - confirm this holds the first
    time wait_for_shcluster_member_healthy runs for real."""
    output = normalize_line_endings(output)
    section = re.search(r"^ Members:\s*\n(.*?)\Z", output, re.DOTALL | re.MULTILINE)
    if not section:
        return {}

    # Each member is a tab-indented "hostname" line (no ':port', unlike KV store
    # members) followed by its own double-tab-indented key:value block.
    blocks = re.split(r"\n\t(\S+)\n", "\n" + section.group(1))
    members: dict[str, dict[str, str]] = {}
    for i in range(1, len(blocks) - 1, 2):
        member, block = blocks[i], blocks[i + 1]
        # Leading whitespace is tabs *and* spaces (right-aligned within the block) -
        # \s+ covers both, unlike the member-header split above which needs \t
        # specifically to avoid matching a field line by accident.
        fields = dict(re.findall(r"^\s+(\S+)\s*:\s*(.*)$", block, re.MULTILINE))
        members[member] = fields
    return members


def find_member(members: dict[str, dict[str, str]], hostname: str) -> dict[str, str] | None:
    """Members are keyed by FQDN ('prdrmlbbspksh01.sky.local'); `hostname` is
    typically the short inventory name ('prdrmlbbspksh01') - match either an exact
    key or a '<hostname>.<anything>' FQDN, never a looser substring (so
    'prdrmlbbspksh01' doesn't accidentally match a hypothetical 'prdrmlbbspksh010')."""
    if hostname in members:
        return members[hostname]
    pattern = re.compile(rf"^{re.escape(hostname)}\.")
    for member, fields in members.items():
        if pattern.match(member):
            return fields
    return None


# Fields observed live (2026-09-16, see TODO.md) that together stand in for "Up and
# artifact replication complete" (the TODO item's own wording) - no single field
# observed so far is named anything more directly like "replication complete";
# 'reported_preexisting_artifacts' looked like the closest candidate but its exact
# semantics aren't confirmed, so it's deliberately NOT relied on here. Revisit if a
# real poll run shows a member stuck healthy-looking-but-actually-not by this
# definition, or vice versa.
_REQUIRED_HEALTHY_FIELDS = {
    "status": "Up",
    "out_of_sync_node": "0",
    "restart_required": "0",
}


def member_health(fields: dict[str, str] | None) -> tuple[bool, str]:
    """Returns (healthy, description) for a single member's field dict (from
    parse_shcluster_members/find_member). description is always safe to print as a
    progress line, healthy or not."""
    if fields is None:
        return False, "not found in shcluster-status output"
    unmet = {
        name: fields.get(name, "<missing>")
        for name, expected in _REQUIRED_HEALTHY_FIELDS.items()
        if fields.get(name) != expected
    }
    description = "  ".join(f"{name}={fields.get(name, '<missing>')!r}" for name in _REQUIRED_HEALTHY_FIELDS)
    return not unmet, description
