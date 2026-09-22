from auto_patchinator.executor.streamsets_api import build_command, extract_status, http_status_ok

PIPELINE_ID = "testpipeldd81d72c-ef3b-4ef8-9099-2816551a7633"

# Trimmed but structurally faithful to a real raw SSH transcript captured live against
# prdmilbbspkfw02, 2026-09-22 (PTY control codes, echoed command, JSON body, then the
# -w-appended HTTPSTATUS: and the tool's own AP_EXIT_CODE: marker).
_RAW_STARTING = (
    '/usr/bin/curl ... "start?rev=0"; echo AP_EXIT_CODE:$?\r\n\x1b[?2004l{\n'
    '  "pipelineId" : "%s",\n  "status" : "STARTING",\n  "message" : null\n}\n'
    'HTTPSTATUS:200AP_EXIT_CODE:0\r\n\x1b]0;splunk@prdmilbbspkfw02:~\x07\x1b[?2004h<<AP_READY>>'
) % PIPELINE_ID

_RAW_RUNNING = _RAW_STARTING.replace("STARTING", "RUNNING").replace("start?rev=0", "status?rev=0")

# A real STOPPED response carries a large nested "metrics" JSON-as-a-string blob that
# includes differently-cased keys like "Status" (capital S) - must not be mistaken for
# the top-level lowercase "status" field.
_RAW_STOPPED = (
    '/usr/bin/curl ... "status?rev=0"; echo AP_EXIT_CODE:$?\r\n\x1b[?2004l{\n'
    '  "pipelineId" : "%s",\n  "status" : "STOPPED",\n'
    '  "message" : "The pipeline was stopped.",\n'
    '  "metrics" : "{\\n  \\"custom.Directory_01...\\" : {\\n    \\"Status\\" : \\"BATCH_GENERATED\\"\\n  }\\n}"\n'
    '}\nHTTPSTATUS:200AP_EXIT_CODE:0\r\n\x1b]0;splunk@prdmilbbspkfw02:~\x07\x1b[?2004h<<AP_READY>>'
) % PIPELINE_ID

_RAW_UNAUTHORIZED = (
    '/usr/bin/curl ... "status?rev=0"; echo AP_EXIT_CODE:$?\r\n\x1b[?2004lUnauthorized\n'
    'HTTPSTATUS:401AP_EXIT_CODE:0\r\n\x1b]0;splunk@prdmilbbspkfw02:~\x07\x1b[?2004h<<AP_READY>>'
)


def test_build_command_start_stop_use_post_and_reference_the_secret_variable():
    for action in ("start", "stop"):
        cmd = build_command(action, PIPELINE_ID, "admin")
        assert cmd.startswith("/usr/bin/curl")
        assert "-X POST" in cmd
        assert '-u "admin:$AP_SECRET"' in cmd  # never a literal password, only $AP_SECRET
        assert f"{PIPELINE_ID}/{action}?rev=0" in cmd


def test_build_command_status_uses_get():
    cmd = build_command("status", PIPELINE_ID, "admin")
    assert "-X GET" in cmd
    assert f"{PIPELINE_ID}/status?rev=0" in cmd


def test_extract_status_finds_top_level_field():
    assert extract_status(_RAW_STARTING) == "STARTING"
    assert extract_status(_RAW_RUNNING) == "RUNNING"


def test_extract_status_ignores_differently_cased_keys_in_nested_metrics():
    """The real STOPPED response nests a nested 'metrics' string containing a
    differently-cased 'Status' key - extract_status must still return the top-level
    lowercase 'status' field, not get confused by that."""
    assert extract_status(_RAW_STOPPED) == "STOPPED"


def test_extract_status_returns_none_when_absent():
    assert extract_status(_RAW_UNAUTHORIZED) is None


def test_http_status_ok():
    assert http_status_ok(_RAW_STARTING) is True
    assert http_status_ok(_RAW_UNAUTHORIZED) is False
