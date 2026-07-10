"""Regression — role tables must not let module volume starve other kinds.

``_parts_by_role`` truncates each role table to the top rows by coupling
(``Ca+Ce``). Application/service and entry-point parts carry no ca/ce
metrics (their dependencies aggregate at report level), so they rank last:
in a large bundle (airflow: 2,628 modules, 104 app parts; linux: 4,570
parts) thousands of coupled modules monopolized every row and the ``app:``
parts — the architectural headline of the inventory — silently vanished
from the report while remaining present in the YAML.

The fix gives every kind present in a role group a floored number of rows
(``_select_role_rows``); the "… N more" line keeps disclosing the rest.

Run from the repo root:  python -m pytest tests/decomposer/test_report_kind_diversity.py
"""
from __future__ import annotations

from decomposer.model import Part
from decomposer.report import _select_role_rows


def _module(i: int, coupling: int) -> Part:
    return Part(
        id=f"module:pkg{i}", name=f"pkg{i}", kind="module",
        metrics={"ca": coupling, "ce": coupling},
    )


def _app(i: int) -> Part:
    # Application parts carry no ca/ce — exactly the shape that was starved.
    return Part(id=f"app:svc{i}", name=f"svc{i}", kind="application")


def test_metricless_kinds_survive_module_volume():
    group = [_module(i, coupling=100 - i) for i in range(60)] + [_app(1), _app(2)]
    rows = _select_role_rows(group, cap=40, kind_floor=5)
    ids = {p.id for p in rows}
    assert "app:svc1" in ids and "app:svc2" in ids
    # The cap still bounds the dominant kind.
    assert sum(1 for p in rows if p.kind == "module") == 40


def test_single_kind_group_keeps_original_cap():
    group = [_module(i, coupling=100 - i) for i in range(60)]
    rows = _select_role_rows(group, cap=40, kind_floor=5)
    assert len(rows) == 40
    assert [p.id for p in rows] == [f"module:pkg{i}" for i in range(40)]


def test_kind_floor_is_bounded():
    # A kind never gets more floor rows than it has members, and the floor
    # does not inflate small groups: everything fits, nothing is duplicated.
    group = [_module(i, coupling=10) for i in range(3)] + [_app(1)]
    rows = _select_role_rows(group, cap=40, kind_floor=5)
    assert len(rows) == len(group)
    assert len({p.id for p in rows}) == len(group)


def test_row_order_stays_coupling_ranked():
    group = [_app(1)] + [_module(i, coupling=100 - i) for i in range(50)]
    rows = _select_role_rows(group, cap=40, kind_floor=5)
    module_rows = [p for p in rows if p.kind == "module"]
    couplings = [p.metrics["ca"] + p.metrics["ce"] for p in module_rows]
    assert couplings == sorted(couplings, reverse=True)
    # The metricless app part ranks after the coupled modules, but is present.
    assert rows[-1].id == "app:svc1"
