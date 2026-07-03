"""End-to-end: Excel file -> resolved RunStepPlan list, exactly as cli.cmd_run wires it."""
from pathlib import Path

from auto_patchinator.plan.action_mapping import ActionVerb, map_team_steps
from auto_patchinator.plan.dependency import resolve_order
from auto_patchinator.plan.excel_parser import load_plan_sheet
from auto_patchinator.plan.run_plan import build_run_plan
from auto_patchinator.plan.wave_mapping import load_wave_mapping_from_excel
from tests.conftest import TEAM


def test_excel_to_run_plan(plan_xlsx: Path, inventory):
    raw = load_plan_sheet(plan_xlsx)
    mapped, unmapped = map_team_steps(raw, [TEAM])
    assert not unmapped

    ordered = resolve_order(mapped)
    mapping = load_wave_mapping_from_excel(plan_xlsx, inventory)
    plans = build_run_plan(ordered, mapping, inventory)

    assert [(p.excel_step, p.verb) for p in plans] == [
        (2, ActionVerb.STOP),
        (4, ActionVerb.START),
    ]
    # hosts filtered to the inventory (foreign01 dropped), in sheet order
    assert plans[0].hostnames == ("dp01", "fw01")
    # stop step waits on the IT-SA comms step, start step on the patching step
    assert plans[0].external_dependencies == (1,)
    assert plans[1].external_dependencies == (3,)
    # every step ends with the send_mail confirmation
    assert all(p.post_group_actions[-1].name == "send_mail" for p in plans)
