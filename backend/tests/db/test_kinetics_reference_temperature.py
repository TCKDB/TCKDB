"""``kinetics.t0_k``: an approved row cannot have its reference temperature changed afterwards."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.common import SubmissionRecordType
from tests.db.test_accepted_science_immutability import _actor, _approve


def _reaction_entry(db_session) -> int:
    reaction_id = db_session.scalar(
        text("INSERT INTO chem_reaction (reversible, stoichiometry_hash, public_ref) VALUES (true, :h, :r) RETURNING id"),
        {"h": uuid4().hex * 2, "r": "rxn_" + uuid4().hex[:26]},
    )
    return db_session.scalar(
        text("INSERT INTO reaction_entry (reaction_id, public_ref) VALUES (:rid, :r) RETURNING id"),
        {"rid": reaction_id, "r": "rxe_" + uuid4().hex[:26]},
    )


def _kinetics(db_session, entry_id: int, *, with_t0: float | None) -> int:
    columns, values = "", ""
    params = {"entry": entry_id, "ref": "kin_" + uuid4().hex[:26]}
    if with_t0 is not None:
        columns, values, params["t0"] = ", t0_k", ", :t0", with_t0
    return db_session.scalar(
        text(
            f"INSERT INTO kinetics (public_ref, reaction_entry_id, scientific_origin, a, n{columns}) "
            f"VALUES (:ref, :entry, 'computed', 1e10, 2{values}) RETURNING id"
        ),
        params,
    )


def _t0(db_session, kinetics_id: int) -> float:
    return db_session.scalar(text("SELECT t0_k FROM kinetics WHERE id = :id"), {"id": kinetics_id})


def test_an_approved_row_cannot_have_its_t0_changed_afterwards(db_session):
    """Changing T0 changes what ``a`` means, so the existing whole-row guard on
    accepted kinetics must cover the new column without being told about it."""
    entry = _reaction_entry(db_session)
    kinetics_id = _kinetics(db_session, entry, with_t0=298.0)
    _approve(db_session, SubmissionRecordType.kinetics, kinetics_id, _actor(db_session))

    with pytest.raises(DBAPIError, match="accepted"), db_session.begin_nested():
        db_session.execute(text("UPDATE kinetics SET t0_k = 1 WHERE id = :id"), {"id": kinetics_id})
    assert _t0(db_session, kinetics_id) == 298.0
