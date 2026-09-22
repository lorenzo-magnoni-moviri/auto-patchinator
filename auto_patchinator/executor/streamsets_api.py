"""StreamSets Data Collector REST API integration - pipeline stop/start with
poll-until-target-status verification, used by prdmilbbspkfw02's HOST_OVERRIDES
sequence (actions/sequences.py). Commands and response shape verified live against a
real pipeline on that host, 2026-09-22 (see TODO.md):
  - Auth is HTTP Basic (username/password), no token support observed.
  - POST .../{start,stop}?rev=0 returns immediately with a *transitional* status
    (STARTING/STOPPING) - the operation is asynchronous, so a single check right after
    calling stop/start is not enough; the caller must poll .../status?rev=0 until the
    pipeline actually reaches the target status (STOPPED took ~4.6s to settle for an
    idle test pipeline; a busier real pipeline could take longer).
  - Plain curl (no --fail) exits 0 even on an HTTP error response, so `-w` is used to
    append the HTTP status code after the JSON body for a reliable success signal
    independent of curl's own exit code.
"""
from __future__ import annotations

import re

BASE_URL = "http://localhost:18630/rest/v1/pipeline"

# The top-level "status" field, e.g. "status" : "RUNNING" (StreamSets pretty-prints
# with a space before the colon). Deliberately lowercase-only and matched with
# re.search (first hit) - a STOPPED/RUNNING response also carries a large nested
# "metrics" blob that may contain differently-cased keys (e.g. "Status"), but never
# this exact lowercase key, so the first match is reliably the top-level field
# (verified against a real captured response, 2026-09-22).
_STATUS_RE = re.compile(r'"status"\s*:\s*"([A-Z_]+)"')
_HTTP_STATUS_RE = re.compile(r"HTTPSTATUS:(\d+)")


def build_command(action: str, pipeline_id: str, username: str) -> str:
    """action: 'start' | 'stop' | 'status'. Password is never a literal here - the
    caller must use SSHConnection.run_plain_with_secret so it's substituted from
    $AP_SECRET (redacted in the audit log) rather than embedded in this string."""
    common = (
        f'/usr/bin/curl --noproxy "*" -s -w "\\nHTTPSTATUS:%{{http_code}}" '
        f'-u "{username}:$AP_SECRET"'
    )
    url = f"{BASE_URL}/{pipeline_id}/{action}?rev=0"
    if action == "status":
        return f'{common} -X GET -H "X-Requested-By: Data Collector" "{url}"'
    return (
        f'{common} -X POST -H "Content-Type: application/json" '
        f'-H "X-Requested-By: Data Collector" "{url}"'
    )


def extract_status(output: str) -> str | None:
    """Pull the pipeline's top-level status out of a command's raw output (still
    carrying PTY control codes, the echoed command line, and the trailing
    HTTPSTATUS:/AP_EXIT_CODE: markers - same raw shape as every other SSH command
    result in this tool). None if no status field was found at all (e.g. an auth
    failure or malformed response)."""
    match = _STATUS_RE.search(output)
    return match.group(1) if match else None


def http_status_ok(output: str) -> bool:
    """True if the curl call's own -w-appended HTTP status code was 200. Needed
    because plain curl (no --fail) exits 0 even on a 4xx/5xx response."""
    match = _HTTP_STATUS_RE.search(output)
    return match is not None and match.group(1) == "200"
