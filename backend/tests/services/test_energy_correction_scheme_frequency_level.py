"""Correction-scheme ``frequency_level_of_theory`` (composite-levels plan P6).

Arkane keys Petersson and Melius BAC (only those; atom energies are keyed on
the energy level alone) on
``CompositeLevelOfTheory(freq=..., energy=...)``. A scheme held one level of
theory, so the frequency half was lost: the same energy level with two
different frequency levels was one scheme (identical tables) or a value
conflict (different tables). The owner's decision (2026-10-01, decision 10):
the frequency level joins scheme identity, as a nullable column under
``NULLS NOT DISTINCT``.

The first two tests describe what a producer could do BEFORE the field
existed. They stay green on purpose: a deposit that states no frequency level
is exactly the old identity, so nothing deposited earlier changes meaning.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.energy_correction import EnergyCorrectionScheme
from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge
from app.schemas.fragments.refs import LevelOfTheoryRef
from app.schemas.workflows.energy_correction_upload import EnergyCorrectionSchemeRef
from app.services.calculation_resolution import _level_of_theory_hash
from app.services.energy_correction_resolution import resolve_or_create_scheme
from app.services.provenance_warnings import (
    W_AMBIGUOUS_ENERGY_CORRECTION_SCHEME_WITHOUT_LITERATURE as W_AMBIGUOUS,
)
from app.services.public_refs import _canonical_energy_correction_scheme

_ENERGY = {"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12"}
_FREQ_A = {"method": "B3LYP", "basis": "6-311G(d,p)"}
_FREQ_B = {"method": "wB97XD", "basis": "def2-TZVP"}
_NAME = "Petersson BAC (freq test)"
_RMG_DB = "a" * 40


def _bac(freq: dict | None = None, *, cc: float = 0.1, **overrides) -> EnergyCorrectionSchemeRef:
    """An Arkane-style Petersson BAC keyed on ``energy`` and optionally ``freq``."""
    base: dict = {
        "kind": "bac_petersson",
        "name": _NAME,
        "level_of_theory": dict(_ENERGY),
        "units": "kcal_mol",
        "bond_params": [{"bond_key": "C-H", "value": cc}, {"bond_key": "C-C", "value": 0.3}],
    }
    if freq is not None:
        base["frequency_level_of_theory"] = dict(freq)
    base.update(overrides)
    return EnergyCorrectionSchemeRef(**base)


def _count(session: Session) -> int:
    return session.scalar(
        select(func.count())
        .select_from(EnergyCorrectionScheme)
        .where(EnergyCorrectionScheme.name == _NAME)
    )


def _transient(**overrides) -> EnergyCorrectionScheme:
    fields = {
        "kind": "bac_petersson",
        "name": _NAME,
        "level_of_theory_id": 1,
        "software_release_id": 2,
        "workflow_tool_release_id": 10,
    }
    fields.update(overrides)
    return EnergyCorrectionScheme(**fields)


# ---------------------------------------------------------------------------
# Baseline: no frequency level keeps the original identity
# ---------------------------------------------------------------------------


def test_without_a_frequency_level_the_same_energy_level_is_one_scheme(db_conn) -> None:
    """What a producer that cannot send the frequency half got: one row."""
    with Session(db_conn) as session, session.begin():
        one = resolve_or_create_scheme(session, _bac())
        two = resolve_or_create_scheme(session, _bac())
        assert two.id == one.id
        assert one.frequency_level_of_theory_id is None
        assert _count(session) == 1


def test_without_a_frequency_level_different_tables_are_refused(db_conn) -> None:
    """The audit result: two Arkane keys, same energy level, different numbers."""
    with Session(db_conn) as session, session.begin():
        resolve_or_create_scheme(session, _bac(cc=0.1))
        with pytest.raises(ValueError, match="Conflicting"):
            resolve_or_create_scheme(session, _bac(cc=0.2))


# ---------------------------------------------------------------------------
# The fix
# ---------------------------------------------------------------------------


def test_two_frequency_levels_give_two_schemes(db_conn) -> None:
    """Same energy level, different frequency level, different tables: two schemes."""
    with Session(db_conn) as session, session.begin():
        a = resolve_or_create_scheme(session, _bac(_FREQ_A, cc=0.1))
        b = resolve_or_create_scheme(session, _bac(_FREQ_B, cc=0.2))
        assert a.id != b.id
        assert a.public_ref != b.public_ref
        assert a.level_of_theory_id == b.level_of_theory_id
        assert a.frequency_level_of_theory_id != b.frequency_level_of_theory_id
        assert a.frequency_level_of_theory.method == "B3LYP"
        assert b.frequency_level_of_theory.method == "wB97XD"
        assert _count(session) == 2


def test_two_frequency_levels_are_two_schemes_even_with_identical_tables(db_conn) -> None:
    """Identity is the key, not the numbers: identical tables still do not merge."""
    with Session(db_conn) as session, session.begin():
        a = resolve_or_create_scheme(session, _bac(_FREQ_A))
        b = resolve_or_create_scheme(session, _bac(_FREQ_B))
        assert a.id != b.id
        assert _count(session) == 2


def test_the_same_frequency_level_reuses_the_scheme(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        one = resolve_or_create_scheme(session, _bac(_FREQ_A))
        two = resolve_or_create_scheme(session, _bac(_FREQ_A))
        assert two.id == one.id
        assert _count(session) == 1


def test_the_same_frequency_level_with_different_tables_is_still_refused(db_conn) -> None:
    """A key names one set of tables, so the values under it must agree."""
    with Session(db_conn) as session, session.begin():
        resolve_or_create_scheme(session, _bac(_FREQ_A, cc=0.1))
        with pytest.raises(ValueError, match="Conflicting"):
            resolve_or_create_scheme(session, _bac(_FREQ_A, cc=0.2))


def test_an_absent_frequency_level_never_matches_a_stated_one(db_conn) -> None:
    """Absence says nothing about the frequency level; it is not guessed."""
    with Session(db_conn) as session, session.begin():
        bare = resolve_or_create_scheme(session, _bac())
        keyed = resolve_or_create_scheme(session, _bac(_FREQ_A))
        assert keyed.id != bare.id
        assert bare.frequency_level_of_theory_id is None
        # And the bare deposit afterwards still finds the original row.
        assert resolve_or_create_scheme(session, _bac()).id == bare.id


def test_the_frequency_level_joins_the_revised_identity_too(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        a = resolve_or_create_scheme(session, _bac(_FREQ_A, data_revision=_RMG_DB))
        b = resolve_or_create_scheme(session, _bac(_FREQ_B, data_revision=_RMG_DB))
        again = resolve_or_create_scheme(session, _bac(_FREQ_A, data_revision=_RMG_DB))
        assert a.id != b.id
        assert again.id == a.id


def test_a_merged_frequency_level_resolves_to_the_level_that_holds_it(db_conn) -> None:
    """The frequency half follows ``level_of_theory_merge`` like the energy half."""
    dup_ref = {"method": "B3LYP-dup", "basis": "6-311G(d,p)"}
    with Session(db_conn) as session, session.begin():
        first = resolve_or_create_scheme(session, _bac(_FREQ_A))
        holder = first.frequency_level_of_theory
        # A level that is a merged duplicate: it keeps the hash its own
        # spelling produces, and the merge row names the holder.
        duplicate = LevelOfTheory(
            method=dup_ref["method"],
            basis=dup_ref["basis"],
            lot_hash=_level_of_theory_hash(LevelOfTheoryRef(**dup_ref)),
        )
        session.add(duplicate)
        session.flush()
        session.add(LevelOfTheoryMerge(merged_lot_id=duplicate.id, into_lot_id=holder.id))
        session.flush()

        via_dup = resolve_or_create_scheme(session, _bac(dup_ref))
        assert via_dup.frequency_level_of_theory_id == holder.id
        assert via_dup.id == first.id
        assert _count(session) == 1


# ---------------------------------------------------------------------------
# Public ref
# ---------------------------------------------------------------------------


def test_a_scheme_without_a_frequency_level_keeps_the_ref_it_had(db_conn) -> None:
    """The canonical string for a NULL frequency level is byte-for-byte the old one.

    Pinned as a literal, not recomputed from the function under test, so a
    change to the NULL-frequency identity fails here instead of silently
    re-keying every scheme ref that already exists.
    """
    with Session(db_conn) as session, session.begin():
        scheme = resolve_or_create_scheme(session, _bac())
        expected = (
            "ecs:kind=bac_petersson;name=petersson bac (freq test);"
            f"level_of_theory_id={scheme.level_of_theory_id};"
            "source_literature_id=None;"
            "software_release_id=None;"
            "workflow_tool_release_id=None"
        )
        assert _canonical_energy_correction_scheme(scheme) == expected


def test_the_ref_of_a_revised_scheme_without_a_frequency_level_is_unchanged() -> None:
    """The ``data_revision`` form (#633), pinned as a literal as well."""
    scheme = _transient(data_revision=_RMG_DB, level_of_theory_id=5, software_release_id=7)
    assert _canonical_energy_correction_scheme(scheme) == (
        "ecs:kind=bac_petersson;name=petersson bac (freq test);"
        "level_of_theory_id=5;source_literature_id=None;software_release_id=7;"
        f"data_revision={_RMG_DB}"
    )


def test_a_frequency_level_reaches_the_ref_in_both_forms() -> None:
    """Two schemes the index calls distinct must not share a canonical string."""
    for extra in ({}, {"data_revision": _RMG_DB}):
        none = _canonical_energy_correction_scheme(_transient(**extra))
        a = _canonical_energy_correction_scheme(
            _transient(frequency_level_of_theory_id=11, **extra)
        )
        b = _canonical_energy_correction_scheme(
            _transient(frequency_level_of_theory_id=12, **extra)
        )
        assert len({none, a, b}) == 3, extra
        assert "frequency_level_of_theory_id=11;" in a


# ---------------------------------------------------------------------------
# The index backs the resolver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data_revision", "index"),
    [
        (None, "uq_energy_correction_scheme_identity"),
        (_RMG_DB, "uq_energy_correction_scheme_identity_revised"),
    ],
)
def test_the_identity_indexes_include_the_frequency_level(
    db_conn, data_revision: str | None, index: str
) -> None:
    """Rows that differ only in frequency level coexist; equal ones cannot.

    The probe carries its own ``public_ref``, so the only index that can
    refuse it is the identity one, asserted by name.
    """
    with Session(db_conn) as session:
        with session.begin():
            first = resolve_or_create_scheme(
                session, _bac(_FREQ_A, data_revision=data_revision)
            )
            other = resolve_or_create_scheme(
                session, _bac(_FREQ_B, data_revision=data_revision)
            )
            assert first.id != other.id

            def probe(frequency_id: int | None, ref: str) -> None:
                with session.begin_nested():
                    session.add(
                        EnergyCorrectionScheme(
                            kind=first.kind,
                            name=first.name,
                            level_of_theory_id=first.level_of_theory_id,
                            frequency_level_of_theory_id=frequency_id,
                            software_release_id=first.software_release_id,
                            workflow_tool_release_id=first.workflow_tool_release_id,
                            data_revision=data_revision,
                            public_ref=ref,
                        )
                    )
                    session.flush()

            with pytest.raises(IntegrityError) as refused:
                probe(first.frequency_level_of_theory_id, "ecs_probe_equal")
            assert refused.value.orig.diag.constraint_name == index

            # NULLS NOT DISTINCT: two rows with no frequency level are equal.
            probe(None, "ecs_probe_null")
            with pytest.raises(IntegrityError) as refused_null:
                probe(None, "ecs_probe_null_again")
            assert refused_null.value.orig.diag.constraint_name == index


# ---------------------------------------------------------------------------
# Validation: what a frequency level may be attached to
# ---------------------------------------------------------------------------


def _refusal_code(**kwargs) -> str:
    with pytest.raises(ValidationError) as refused:
        EnergyCorrectionSchemeRef(**kwargs)
    return refused.value.errors()[0]["ctx"]["error"].code


@pytest.mark.parametrize("kind", ["atom_energy", "atom_hf", "atom_thermal", "soc", "isodesmic", "other"])
def test_a_frequency_level_is_refused_on_every_kind_but_the_bacs(kind: str) -> None:
    """Arkane keys only pbac and mbac on energy//freq; atom energies on the energy level."""
    code = _refusal_code(
        kind=kind,
        name="x",
        level_of_theory=dict(_ENERGY),
        frequency_level_of_theory=dict(_FREQ_A),
    )
    assert code == "energy_correction_scheme_frequency_level_not_applicable"


@pytest.mark.parametrize("kind", ["bac_petersson", "bac_melius"])
def test_a_frequency_level_is_accepted_on_both_bac_kinds(kind: str) -> None:
    ref = EnergyCorrectionSchemeRef(
        kind=kind,
        name="x",
        level_of_theory=dict(_ENERGY),
        frequency_level_of_theory=dict(_FREQ_A),
    )
    assert ref.frequency_level_of_theory is not None


def test_a_frequency_level_without_an_energy_level_is_refused() -> None:
    code = _refusal_code(
        kind="bac_petersson", name="x", frequency_level_of_theory=dict(_FREQ_A)
    )
    assert code == "energy_correction_scheme_frequency_level_without_energy_level"


def test_a_frequency_level_equal_to_the_energy_level_is_stored_as_absent(db_conn) -> None:
    """``energy//energy`` is one level: it must not become a second scheme.

    Normalised rather than refused: the payload says something true (the
    frequencies were at the same level), and nothing in it contradicts
    anything; it just names one level twice. Compared after resolution, so a
    different spelling of the same level counts too.
    """
    with Session(db_conn) as session, session.begin():
        bare = resolve_or_create_scheme(session, _bac())
        same = resolve_or_create_scheme(session, _bac(dict(_ENERGY)))
        respelled = resolve_or_create_scheme(
            session, _bac({"method": "ccsd(t)-f12", "basis": "CC-PVTZ-F12"})
        )
        assert same.id == bare.id
        assert respelled.id == bare.id
        assert bare.frequency_level_of_theory_id is None
        assert _count(session) == 1


# ---------------------------------------------------------------------------
# The ambiguous-uncited-sibling warning looks at the frequency level
# ---------------------------------------------------------------------------


def test_uncited_schemes_with_different_frequency_levels_are_not_called_ambiguous(
    db_conn,
) -> None:
    """Two uncited BACs on one energy level that differ in frequency level are
    distinguishable by identity, so the 'same correction twice?' warning would
    be false. A same-frequency uncited sibling (a different name) is still
    flagged, which is what shows the query is running at all.
    """
    with Session(db_conn) as session, session.begin():
        resolve_or_create_scheme(session, _bac(_FREQ_A))

        other_freq: list = []
        resolve_or_create_scheme(session, _bac(_FREQ_B), warnings_out=other_freq)
        assert W_AMBIGUOUS not in {w.code for w in other_freq}

        same_freq: list = []
        resolve_or_create_scheme(
            session, _bac(_FREQ_A, name=_NAME + " twin"), warnings_out=same_freq
        )
        assert W_AMBIGUOUS in {w.code for w in same_freq}
