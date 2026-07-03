from auto_patchinator.plan.action_mapping import ActionVerb, map_team_steps
from tests.conftest import TEAM, make_raw


def test_maps_stop_and_start_with_groups():
    rows = [
        make_raw(2, "Stop application: Splunk Group 1"),
        make_raw(4, "Start application: Splunk Group 1"),
        make_raw(6, "Restart application: Splunk Group 2 Group 3"),
    ]
    mapped, unmapped = map_team_steps(rows, TEAM)
    assert not unmapped
    assert [(s.step, s.verb, s.groups) for s in mapped] == [
        (2, ActionVerb.STOP, (1,)),
        (4, ActionVerb.START, (1,)),
        (6, ActionVerb.START, (2, 3)),
    ]


def test_shutdown_counts_as_stop():
    mapped, _ = map_team_steps([make_raw(1, "Application Shutdown: Splunk Group 2")], TEAM)
    assert mapped[0].verb == ActionVerb.STOP


def test_rows_from_other_teams_are_ignored():
    rows = [make_raw(1, "Stop application Group 1", referente="Sys Unix")]
    mapped, unmapped = map_team_steps(rows, TEAM)
    assert mapped == [] and unmapped == []


def test_team_filter_is_case_insensitive_and_accepts_list():
    rows = [make_raw(1, "Stop Group 1", referente="aom sky cso")]
    mapped, _ = map_team_steps(rows, [TEAM, "AOM Splunk Broadband"])
    assert len(mapped) == 1


def test_unparseable_team_rows_are_flagged_not_guessed():
    rows = [
        make_raw(1, "Do something vague"),           # no verb, no group
        make_raw(2, "Stop application (all nodes)"),  # verb but no group
    ]
    mapped, unmapped = map_team_steps(rows, TEAM)
    assert mapped == []
    assert [r.step for r in unmapped] == [1, 2]
