"""Shared fixtures: in-memory RawSteps, a small inventory, and generated xlsx files."""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from auto_patchinator.actions.sequences import NodeRole
from auto_patchinator.actions.types import Identity
from auto_patchinator.config.inventory import Host, Inventory
from auto_patchinator.plan.excel_parser import RawStep

PLAN_HEADER = ["Step", "Dependancy", "Applicativo", "Nome", "Gruppo_referente",
               "Stato", "Tipo", "StartDate_Prevista", "EndDate_Prevista", "Note"]

TEAM = "AOM Sky CSO"


def make_raw(step: int, nome: str, referente: str = TEAM, deps: tuple[int, ...] = ()) -> RawStep:
    return RawStep(
        step=step, dependencies=deps, applicativo="Splunk Broadband", nome=nome,
        gruppo_referente=referente, stato=None, tipo=None,
        start_date=None, end_date=None, note=None,
    )


@pytest.fixture
def inventory() -> Inventory:
    """Two-site inventory covering every role, mirroring the real hosts.yaml shape."""
    hosts = {
        "dp01": Host(hostname="dp01", role=NodeRole.DEPLOYER, site="milano"),
        "fw01": Host(hostname="fw01", role=NodeRole.FORWARDER, site="milano"),
        "ix01": Host(hostname="ix01", role=NodeRole.INDEXER, site="milano"),
        "ix02": Host(hostname="ix02", role=NodeRole.INDEXER, site="milano"),
        "shs01": Host(hostname="shs01", role=NodeRole.SEARCH_HEAD_SIMPLE, site="milano"),
        "shx01": Host(hostname="shx01", role=NodeRole.SEARCH_HEAD_STRETCHED, site="milano"),
        "shx02": Host(hostname="shx02", role=NodeRole.SEARCH_HEAD_STRETCHED, site="roma",
                      manual_identities=(Identity.SPLUNK,)),
    }
    return Inventory(hosts=hosts, environment="test", stretched_sh_sites=("milano", "roma"))


def write_workbook(path: Path, sheets: dict[str, list[list]]) -> Path:
    """Write {sheet_name: rows} to an xlsx file and return its path."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


@pytest.fixture
def plan_xlsx(tmp_path: Path) -> Path:
    """A minimal rolling-wave plan: stop 1 -> patch (other team) -> start 1."""
    rows = [
        PLAN_HEADER,
        [1, None, "IT-SA", "Comunication: start patching", "IT-SA",
         None, None, None, None, None],
        [2, 1, "Splunk Broadband", "Stop application: Splunk Group 1", TEAM,
         None, None, None, None, None],
        [3, 2, "SO", "OS Patching Group 1", "Sys Unix",
         None, None, None, None, None],
        [4, 3, "Splunk Broadband", "Start application: Splunk Group 1", TEAM,
         None, None, None, None, None],
    ]
    host_rows = [
        ["=SUBTOTAL(3,A3:A6)"],
        ["Computer", "Hostname", "Groups", "Env"],
        ["dp01.sky.local", "dp01", 1, "NO PROD"],
        ["fw01.sky.local", "fw01", 1, "NO PROD"],
        ["foreign01.sky.local", "foreign01", 1, "NO PROD"],  # not in inventory
    ]
    return write_workbook(tmp_path / "plan.xlsx", {"Plan": rows, "List Host NO IT": host_rows})
