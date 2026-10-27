"""A calculation's citation is a paper, not a row id — on every root.

``CalculationWithResultsPayload`` is the calculation block of five upload
roots (``/uploads/`` conformers, transition-states, statmech, thermo,
transport) *and*, until #194, the internal shape three bundle workflows
built after resolving literature themselves. Because it was both, it kept
a raw ``literature_id``: correct for the internal use, a database primary
key on a depositor-facing surface for the other five.

#118 removed that field from the reaction bundle. #154 removed it from
the network-PDep route. #172 removed it from ``CalculationIn``. It
survived all three on the shared payload, and was found by hand a fourth
time. These tests pin both halves of the correction — the raw id is
refused, and the inline fragment it was replaced by actually reaches a
``literature`` row — on the two roots #194 named.

The general statement lives in
``tests/schemas/test_upload_roots_expose_no_fk_ids.py``, which asserts it
over every upload root discovered from the live route table. This file is
the behavioural half: that the replacement works, not merely that the
field is absent.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.workflows.conformer_upload import ConformerCalculationIn

from app.db.models.calculation import Calculation
from app.db.models.literature import Literature
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.workflows.conformer_upload import ConformerUploadRequest
from app.schemas.workflows.transition_state_upload import (
    TransitionStateUploadRequest,
)
from app.workflows.conformer import persist_conformer_upload
from app.workflows.transition_state import persist_transition_state_upload

_SOFTWARE = {"name": "gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}
_XYZ_TS = "3\nH transfer TS\nH 0.0 0.0 0.0\nH 0.0 0.0 0.9\nH 0.0 0.0 1.8\n"
_REACTION = {
    "reversible": True,
    "reactants": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
    ],
    "products": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
    ],
}


@pytest.fixture
def stub_doi(monkeypatch):
    """Resolve DOIs without reaching Crossref."""
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "A paper the depositor has and the row id they do not",
            "container-title": ["J. Chem. Phys."],
            "issued": 2010,
            "URL": f"https://doi.org/{doi}",
        },
    )


# ---------------------------------------------------------------------------
# The removed field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model", [CalculationWithResultsPayload, ConformerCalculationIn]
)
def test_the_shared_calculation_payload_no_longer_declares_literature_id(model):
    """Pinned on the model, not just on one route's rejection.

    ``ConformerCalculationIn`` subclasses the shared payload, so it is
    checked separately: a subclass could re-add the field and every
    route-level test that posts the base shape would still pass.
    """
    assert "literature_id" not in model.model_fields, model.__name__
    assert "literature" in model.model_fields, model.__name__


def test_conformer_upload_refuses_a_raw_literature_id():
    """Refused at the boundary, naming the field.

    ``SchemaBase`` sets ``extra="forbid"``, so an old client's payload is a
    422 that says ``literature_id`` rather than a 201 with the citation
    silently dropped. Asserting the field name matters: a 422 for an
    unrelated reason would satisfy a bare status check.
    """
    with pytest.raises(ValidationError, match="literature_id"):
        ConformerUploadRequest(
            species_entry={"smiles": "[H]", "charge": 0, "multiplicity": 2},
            geometry={"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
            calculation={
                "type": "freq",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_result": {"n_imag": 0},
                "literature_id": 42,
            },
        )


def test_transition_state_upload_refuses_a_raw_literature_id():
    with pytest.raises(ValidationError, match="literature_id"):
        TransitionStateUploadRequest(
            reaction=_REACTION,
            charge=0,
            multiplicity=2,
            geometry={"xyz_text": _XYZ_TS},
            primary_opt={
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "literature_id": 42,
            },
        )


# ---------------------------------------------------------------------------
# The replacement, end to end
# ---------------------------------------------------------------------------


def test_conformer_upload_resolves_an_inline_calculation_citation(
    db_conn, stub_doi
) -> None:
    """The fragment a depositor can actually produce reaches a row.

    Removing the id is only half a fix: if nothing resolved the fragment,
    the citation would be accepted and dropped, which is worse than
    refusing it. Assert the ``calculation.literature_id`` column is
    populated and points at the paper that was cited.
    """
    request = ConformerUploadRequest(
        species_entry={"smiles": "[H]", "charge": 0, "multiplicity": 2},
        geometry={"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        calculation={
            "type": "freq",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "freq_result": {"n_imag": 0},
            "literature": {
                "doi": "10.1063/conformer-calc-citation",
                "title": "fallback if DOI lookup fails",
            },
        },
        note="inline calculation citation",
    )

    with Session(db_conn) as session, session.begin():
        outcome = persist_conformer_upload(session, request)
        calc = session.get(
            Calculation, outcome.primary_calculation.calculation_id
        )
        assert calc.literature_id is not None
        lit = session.get(Literature, calc.literature_id)
        assert lit.doi == "10.1063/conformer-calc-citation"
        assert lit.title == "A paper the depositor has and the row id they do not"


def test_transition_state_upload_resolves_an_inline_calculation_citation(
    db_conn, stub_doi
) -> None:
    request = TransitionStateUploadRequest(
        reaction=_REACTION,
        charge=0,
        multiplicity=2,
        geometry={"xyz_text": _XYZ_TS},
        primary_opt={
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "literature": {
                "doi": "10.1063/ts-calc-citation",
                "title": "fallback if DOI lookup fails",
            },
        },
        label="TS with an inline citation",
    )

    with Session(db_conn) as session, session.begin():
        ts_entry = persist_transition_state_upload(session, request)
        calc = session.scalars(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry.id
            )
        ).one()
        assert calc.literature_id is not None
        lit = session.get(Literature, calc.literature_id)
        assert lit.doi == "10.1063/ts-calc-citation"


def test_an_uncited_calculation_stays_uncited(db_conn) -> None:
    """Absence must survive the new resolution step.

    The seam now calls literature resolution on every calculation. A
    resolver that created an empty row for a payload that cited nothing
    would attach a meaningless provenance record to most of the database,
    so the ``None`` path is asserted rather than assumed.
    """
    request = ConformerUploadRequest(
        species_entry={"smiles": "[H]", "charge": 0, "multiplicity": 2},
        geometry={"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        calculation={
            "type": "freq",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "freq_result": {"n_imag": 0},
        },
        note="no citation",
    )
    with Session(db_conn) as session, session.begin():
        outcome = persist_conformer_upload(session, request)
        calc = session.get(
            Calculation, outcome.primary_calculation.calculation_id
        )
        assert calc.literature_id is None
