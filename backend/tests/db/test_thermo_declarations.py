"""``thermo`` target and protocol declarations.

A protocol declaration is a versioned object, ORM ``None`` is SQL NULL and never JSON null, an approved declaration
cannot be changed in place, and a declared row changes the reproducibility snapshot but not the consistency context.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models.common import ScientificOriginKind, SubmissionRecordType
from app.db.models.thermo import Thermo
from app.services.consistency.core import encoded, thermo_inputs
from app.services.reproducibility_rubric import _mapped_columns
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.services.scientific_read._factories import (
    make_conformer_group,
    make_species,
    make_species_entry,
    next_inchi_key,
)

NEW_COLUMNS = {"thermodynamic_target_kind", "target_conformer_group_id", "protocol_declaration"}
PROTOCOL = '{"version": 1, "recipe": {"name": "g4"}}'


def _entry(db_session, tag: str):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key(tag)))


def _legacy_thermo(db_session, entry_id: int) -> int:
    """A thermo row as the previous schema stored it (no declaration columns exist)."""
    return db_session.scalar(
        text(
            "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, h298_kj_mol, "
            "enthalpy_reference_kind) VALUES (:ref, :entry, 'computed', -74.5, 'formation_298k') RETURNING id"
        ),
        {"ref": "thm_" + uuid4().hex[:26], "entry": entry_id},
    )


def test_the_group_is_named_exactly_when_the_kind_is_single_conformer(db_session):
    entry = _entry(db_session, "THMDECLCK")
    group = make_conformer_group(db_session, entry)

    def insert(kind: str | None, group_id: int | None):
        return db_session.execute(
            text(
                "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, "
                "thermodynamic_target_kind, target_conformer_group_id) "
                "VALUES (:ref, :entry, 'computed', CAST(:kind AS thermo_target_kind), :group)"
            ),
            {"ref": "thm_" + uuid4().hex[:26], "entry": entry.id, "kind": kind, "group": group_id},
        )

    insert(None, None)
    insert("equilibrium_ensemble", None)
    insert("single_conformer", group.id)
    for kind, group_id in (
        ("single_conformer", None),
        ("equilibrium_ensemble", group.id),
        # The case a plain ``=`` would let through: a group on a row with no kind
        # evaluates to NULL, and a CHECK passes on NULL.
        (None, group.id),
    ):
        with pytest.raises(IntegrityError, match="target_group_iff_single_conformer"), db_session.begin_nested():
            insert(kind, group_id)


def test_a_protocol_declaration_must_be_a_versioned_object(db_session):
    entry = _entry(db_session, "THMDECLPR")

    def insert(protocol: str):
        return db_session.execute(
            text(
                "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, protocol_declaration) "
                "VALUES (:ref, :entry, 'computed', CAST(:protocol AS jsonb))"
            ),
            {"ref": "thm_" + uuid4().hex[:26], "entry": entry.id, "protocol": protocol},
        )

    insert(PROTOCOL)
    for bad in ("[]", "null", '"g4"', '{"recipe": {}}', '{"version": "1"}'):
        with pytest.raises(IntegrityError, match="protocol_declaration_versioned_object"), db_session.begin_nested():
            insert(bad)


def test_orm_none_is_sql_null_never_json_null(db_session):
    """``Thermo(protocol_declaration=None)`` must store "not stated", not a stored JSON ``null``."""
    entry = _entry(db_session, "THMDECLNULL")
    thermo = Thermo(
        species_entry_id=entry.id, scientific_origin=ScientificOriginKind.computed, protocol_declaration=None
    )
    db_session.add(thermo)
    db_session.flush()
    assert db_session.scalar(
        text("SELECT protocol_declaration IS NULL FROM thermo WHERE id = :id"), {"id": thermo.id}
    ) is True


@pytest.mark.parametrize(
    "update",
    [
        "SET thermodynamic_target_kind = 'equilibrium_ensemble'",
        "SET protocol_declaration = CAST('{\"version\": 1, \"recipe\": {\"name\": \"g3\"}}' AS jsonb)",
        "SET target_conformer_group_id = NULL, thermodynamic_target_kind = NULL",
    ],
    ids=["target_kind", "protocol", "target_group"],
)
def test_an_approved_declaration_cannot_be_changed_in_place(db_session, update):
    """The whole-row guard on accepted thermo covers the new columns without being told about them."""
    entry = _entry(db_session, "THMDECLIMM")
    group = make_conformer_group(db_session, entry)
    thermo_id = db_session.scalar(
        text(
            "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, thermodynamic_target_kind, "
            "target_conformer_group_id, protocol_declaration) VALUES (:ref, :entry, 'computed', "
            "'single_conformer', :group, CAST(:protocol AS jsonb)) RETURNING id"
        ),
        {"ref": "thm_" + uuid4().hex[:26], "entry": entry.id, "group": group.id, "protocol": PROTOCOL},
    )
    _approve(db_session, SubmissionRecordType.thermo, thermo_id, _actor(db_session))

    with pytest.raises(DBAPIError, match="accepted"), db_session.begin_nested():
        db_session.execute(text(f"UPDATE thermo {update} WHERE id = :id"), {"id": thermo_id})

    stored = db_session.execute(
        text("SELECT thermodynamic_target_kind, protocol_declaration FROM thermo WHERE id = :id"),
        {"id": thermo_id},
    ).one()
    assert stored.thermodynamic_target_kind == "single_conformer"
    assert stored.protocol_declaration == {"version": 1, "recipe": {"name": "g4"}}


def test_an_unapproved_row_may_still_be_edited(db_session):
    """The guard is for accepted science only; a draft declaration is correctable."""
    entry = _entry(db_session, "THMDECLDRAFT")
    thermo_id = _legacy_thermo(db_session, entry.id)
    db_session.execute(
        text("UPDATE thermo SET thermodynamic_target_kind = 'equilibrium_ensemble' WHERE id = :id"),
        {"id": thermo_id},
    )
    assert db_session.scalar(
        text("SELECT thermodynamic_target_kind FROM thermo WHERE id = :id"), {"id": thermo_id}
    ) == "equilibrium_ensemble"


def test_a_declared_row_changes_the_reproducibility_snapshot_but_not_the_consistency_context(db_session):
    """Hash precedent (#619/#633): no consistency check reads a declaration, so it stays out of that
    context; the reproducibility snapshot is the digest a declaration is part of."""
    entry = _entry(db_session, "THMDECLHASH")
    thermo_id = _legacy_thermo(db_session, entry.id)
    thermo = db_session.get(Thermo, thermo_id)
    consistency = encoded(thermo_inputs(thermo))
    undeclared = _mapped_columns(thermo)

    thermo.thermodynamic_target_kind = "equilibrium_ensemble"
    db_session.flush()
    assert encoded(thermo_inputs(thermo)) == consistency
    assert _mapped_columns(thermo) != undeclared
    assert _mapped_columns(thermo)["thermodynamic_target_kind"] == "equilibrium_ensemble"

    thermo.thermodynamic_target_kind = None
    thermo.protocol_declaration = {"version": 1, "recipe": {"name": "g4"}}
    db_session.flush()
    assert encoded(thermo_inputs(thermo)) == consistency
    assert _mapped_columns(thermo) != undeclared
    assert _mapped_columns(thermo)["protocol_declaration"] == {"recipe": {"name": "g4"}, "version": 1}

    thermo.protocol_declaration = None
    db_session.flush()
    assert _mapped_columns(thermo) == undeclared


# ---------------------------------------------------------------------------
# The real alembic run, on a disposable database that holds existing thermo rows
# ---------------------------------------------------------------------------
