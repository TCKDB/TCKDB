"""Automatic dependency edges on ``additional_calculations`` fit their role.

A conformer upload may carry extra calculations beside its primary one, and
the server links each to the primary ("this single point ran on that
optimisation"). Those links are inferred, not declared by the depositor, so
when the primary is not an ``opt`` the link the role table forbids
(``_DEPENDENCY_ROLE_TO_PARENT_TYPE``) is not written -- DAG edges are
opportunistic enrichment -- and the upload is not refused for a link nobody
asked for. The skip is disclosed as a ``dependency_edge_not_inferred``
warning.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationDependency
from app.db.models.common import CalculationDependencyRole, CalculationType

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}


def _payload(primary_type: str, extra_types: list[str], label: str) -> dict:
    def calc(kind: str) -> dict:
        return {"type": kind, "software_release": _SOFTWARE, "level_of_theory": _LOT}

    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": calc(primary_type),
        "additional_calculations": [calc(kind) for kind in extra_types],
        "label": label,
    }


def _edges(session) -> list[tuple]:
    """(parent type, role, child type) for every stored edge."""
    out = []
    for edge in session.scalars(select(CalculationDependency)).all():
        parent = session.get(Calculation, edge.parent_calculation_id)
        child = session.get(Calculation, edge.child_calculation_id)
        out.append((parent.type, edge.dependency_role, child.type))
    return out


def _edge_warnings(resp) -> list[dict]:
    return [
        w
        for w in resp.json()["warnings"]
        if w["code"] == "dependency_edge_not_inferred"
    ]


def _count_calcs(session) -> int:
    return len(session.scalars(select(Calculation.id)).all())


class TestAutoEdgesNeedAnOptParent:
    def test_opt_primary_keeps_freq_and_sp_edges(self, client):
        """Positive control: the guard must not swallow the legal edges."""
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_payload("opt", ["freq", "sp"], "edge-opt-ok"),
        )
        assert resp.status_code == 201, resp.text
        edges = _edges(client._db_session)
        assert (
            CalculationType.opt,
            CalculationDependencyRole.freq_on,
            CalculationType.freq,
        ) in edges
        assert (
            CalculationType.opt,
            CalculationDependencyRole.single_point_on,
            CalculationType.sp,
        ) in edges
        assert _edge_warnings(resp) == []

    def test_sp_primary_with_extra_freq_writes_no_freq_on_edge(self, client):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_payload("sp", ["freq"], "edge-sp-freq"),
        )
        assert resp.status_code == 201, resp.text
        session = client._db_session
        # The extra calculation is still stored; only the forbidden link is not.
        assert _count_calcs(session) == 2
        assert _edges(session) == []
        (warning,) = _edge_warnings(resp)
        assert warning["field"] == "additional_calculations[0]"
        for fragment in ("freq_on", "'opt'", "'sp'", "type='freq'"):
            assert fragment in warning["message"]

    def test_freq_primary_with_extra_sp_writes_no_single_point_on_edge(
        self, client
    ):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_payload("freq", ["sp"], "edge-freq-sp"),
        )
        assert resp.status_code == 201, resp.text
        session = client._db_session
        assert _count_calcs(session) == 2
        assert _edges(session) == []
        (warning,) = _edge_warnings(resp)
        assert warning["field"] == "additional_calculations[0]"
        for fragment in ("single_point_on", "'opt'", "'freq'", "type='sp'"):
            assert fragment in warning["message"]
