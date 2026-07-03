#!/usr/bin/env python3
"""Generates a small, fully-synthetic Vulnerability Plan .xlsx for CI.

No dependency on any real wave data - the referenced hostnames (prdmilbbspkfw01,
prdmilbbmsosh01) come from the checked-in inventory/hosts.example.yaml, so the fixture
and the example inventory always agree. Real wave Excel files are gitignored (contain
sensitive scheduling data), so CI can't use one directly - this stands in for one.

Usage: python scripts/gen_ci_fixture.py <output-path.xlsx>
"""
from __future__ import annotations

import sys

import openpyxl

TEAM = "AOM Sky CSO"


def build(path: str) -> None:
    wb = openpyxl.Workbook()

    plan = wb.active
    plan.title = "Plan"
    plan.append(["Step", "Dependancy", "Applicativo", "Nome", "Gruppo_referente",
                 "Stato", "Tipo", "StartDate_Prevista", "EndDate_Prevista", "Note"])
    # Rolling wave: stop group 1 -> patch -> start group 1 -> stop group 2 -> patch -> start group 2
    rows = [
        (1, None, "IT-SA", "Comunication: start patching", "IT-SA"),
        (2, 1, "Splunk Broadband", "Stop application: Splunk Group 1", TEAM),
        (3, 2, "SO", "OS Patching Group 1", "Sys Unix"),
        (4, 3, "Splunk Broadband", "Start application: Splunk Group 1", TEAM),
        (5, 4, "Splunk Broadband", "Stop application: Splunk Group 2", TEAM),
        (6, 5, "SO", "OS Patching Group 2", "Sys Unix"),
        (7, 6, "Splunk Broadband", "Start application: Splunk Group 2", TEAM),
    ]
    for step, dep, app, nome, ref in rows:
        plan.append([step, dep, app, nome, ref, "Da iniziare", None, None, None, None])

    hosts = wb.create_sheet("List Host NO IT")
    host_rows = [
        ("prdmilbbspkfw01.sky.local", "prdmilbbspkfw01", 1),
        ("prdmilbbmsosh01.sky.local", "prdmilbbmsosh01", 2),
    ]
    hosts.append([f"=SUBTOTAL(3,A3:A{2 + len(host_rows)})"])
    hosts.append(["Computer", "Hostname", "Groups"])
    for computer, hostname, group in host_rows:
        hosts.append([computer, hostname, group])

    wb.save(path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: gen_ci_fixture.py <output-path.xlsx>")
    build(sys.argv[1])
    print(f"wrote {sys.argv[1]}")
