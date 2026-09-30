"""Correction-scheme ``data_revision`` and ``atom_params_applied_as`` (#619).

Atom-energy and BAC tables live in RMG-database, but a producer records only
the RMG-Py commit (the workflow-tool release). The adapter audit found two
failures of that: every RMG-Py commit created a new scheme row, and a
database-only change to one parameter under the same identity was refused as a
conflict. The owner's decision: an optional ``data_revision`` joins scheme
identity when present; the tool build stays provenance.

The two baseline tests below pin what happens WITHOUT a ``data_revision``.
They are the reproduction of the audit's two findings, and they stay green on
purpose: absence of a revision keeps the original identity, so nothing
deposited before this column existed changes meaning.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.common import AtomParamApplication
from app.db.models.energy_correction import EnergyCorrectionScheme
from app.schemas.workflows.energy_correction_upload import EnergyCorrectionSchemeRef
from app.services.energy_correction_resolution import resolve_or_create_scheme
from app.services.public_refs import _canonical_energy_correction_scheme

_LOT = {"method": "B3LYP", "basis": "def2-TZVP"}
_RMG_DB_A = "a" * 40
_RMG_DB_B = "b" * 40


def _ref(**overrides) -> EnergyCorrectionSchemeRef:
    base = {
        "kind": "atom_energy",
        "name": "Arkane atom energies",
        "level_of_theory": dict(_LOT),
        "software": {"name": "Gaussian", "version": "16"},
        "units": "hartree",
        "atom_params": [{"element": "H", "value": -0.5}, {"element": "C", "value": -37.8}],
    }
    base.update(overrides)
    return EnergyCorrectionSchemeRef(**base)


def _build(commit: str) -> dict:
    """The RMG-Py build, which is what the adapter stamps as the tool release."""
    return {"name": "Arkane", "version": "3.3.0", "git_commit": commit}


def _count(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(EnergyCorrectionScheme).where(
            EnergyCorrectionScheme.name == "Arkane atom energies"
        )
    )


# ---------------------------------------------------------------------------
# Baseline: no data_revision keeps the original identity (the audit's findings)
# ---------------------------------------------------------------------------


def test_without_data_revision_every_tool_build_is_its_own_scheme(db_conn) -> None:
    """Audit result 1: two RMG-Py commits, identical tables, two rows."""
    with Session(db_conn) as session, session.begin():
        one = resolve_or_create_scheme(
            session, _ref(workflow_tool_release=_build("1" * 40))
        )
        two = resolve_or_create_scheme(
            session, _ref(workflow_tool_release=_build("2" * 40))
        )
        assert one.id != two.id
        assert _count(session) == 2


def test_without_data_revision_a_database_only_change_is_refused(db_conn) -> None:
    """Audit result 2: same identity, one changed parameter -> whole-upload 422."""
    with Session(db_conn) as session, session.begin():
        resolve_or_create_scheme(session, _ref(workflow_tool_release=_build("1" * 40)))
        changed = _ref(
            workflow_tool_release=_build("1" * 40),
            atom_params=[{"element": "H", "value": -0.4999}],
        )
        with pytest.raises(ValueError, match="Conflicting"):
            resolve_or_create_scheme(session, changed)


# ---------------------------------------------------------------------------
# The fix
# ---------------------------------------------------------------------------


def test_same_data_revision_dedupes_across_tool_builds(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        one = resolve_or_create_scheme(
            session,
            _ref(data_revision=_RMG_DB_A, workflow_tool_release=_build("1" * 40)),
        )
        two = resolve_or_create_scheme(
            session,
            _ref(data_revision=_RMG_DB_A, workflow_tool_release=_build("2" * 40)),
        )
        assert two.id == one.id
        assert _count(session) == 1
        # The build is provenance: the first depositor's build is what stays.
        assert one.workflow_tool_release.git_commit == "1" * 40
        # And it does not reach the ref, so the two builds share one handle.
        assert two.public_ref == one.public_ref


def test_a_build_with_and_a_build_without_a_tool_release_share_a_revision(
    db_conn,
) -> None:
    with Session(db_conn) as session, session.begin():
        bare = resolve_or_create_scheme(session, _ref(data_revision=_RMG_DB_A))
        built = resolve_or_create_scheme(
            session,
            _ref(data_revision=_RMG_DB_A, workflow_tool_release=_build("3" * 40)),
        )
        assert built.id == bare.id


def test_a_new_data_revision_is_a_new_scheme_and_not_a_conflict(db_conn) -> None:
    """The database-only change that used to 422 now lands as its own scheme."""
    with Session(db_conn) as session, session.begin():
        old = resolve_or_create_scheme(
            session,
            _ref(data_revision=_RMG_DB_A, workflow_tool_release=_build("1" * 40)),
        )
        new = resolve_or_create_scheme(
            session,
            _ref(
                data_revision=_RMG_DB_B,
                workflow_tool_release=_build("1" * 40),
                atom_params=[{"element": "H", "value": -0.4999}],
            ),
        )
        assert new.id != old.id
        assert new.public_ref != old.public_ref
        assert {p.value for p in old.atom_params if p.element == "H"} == {-0.5}
        assert {p.value for p in new.atom_params if p.element == "H"} == {-0.4999}


def test_the_same_data_revision_with_different_values_is_still_refused(
    db_conn,
) -> None:
    """A revision names one set of tables, so the values under it must agree."""
    with Session(db_conn) as session, session.begin():
        resolve_or_create_scheme(session, _ref(data_revision=_RMG_DB_A))
        with pytest.raises(ValueError, match="data_revision"):
            resolve_or_create_scheme(
                session,
                _ref(
                    data_revision=_RMG_DB_A,
                    atom_params=[{"element": "H", "value": -0.4999}],
                ),
            )


def test_a_revised_scheme_never_matches_an_unrevised_one(db_conn) -> None:
    """Absence is not a revision: identical tables, still two schemes."""
    with Session(db_conn) as session, session.begin():
        unrevised = resolve_or_create_scheme(session, _ref())
        revised = resolve_or_create_scheme(session, _ref(data_revision=_RMG_DB_A))
        assert revised.id != unrevised.id
        assert revised.public_ref != unrevised.public_ref
        assert unrevised.data_revision is None


def test_an_unrevised_scheme_keeps_the_ref_it_had_before_the_column(db_conn) -> None:
    """The canonical string for a NULL revision is byte-for-byte the old one.

    Pinned as a literal, not recomputed from the function under test, so a
    change to the unrevised identity fails here instead of silently
    re-keying every scheme ref that already exists.
    """
    with Session(db_conn) as session, session.begin():
        scheme = resolve_or_create_scheme(
            session, _ref(workflow_tool_release=_build("1" * 40))
        )
        expected = (
            f"ecs:kind=atom_energy;name=arkane atom energies;"
            f"level_of_theory_id={scheme.level_of_theory_id};"
            f"source_literature_id=None;"
            f"software_release_id={scheme.software_release_id};"
            f"workflow_tool_release_id={scheme.workflow_tool_release_id}"
        )
        assert _canonical_energy_correction_scheme(scheme) == expected


def test_the_identity_index_backs_the_resolver_for_revised_rows(db_conn) -> None:
    """Two revised rows that differ only in the tool build cannot coexist."""
    with Session(db_conn) as session:
        with session.begin():
            first = resolve_or_create_scheme(
                session,
                _ref(data_revision=_RMG_DB_A, workflow_tool_release=_build("1" * 40)),
            )
            other_build = resolve_or_create_scheme(
                session,
                _ref(data_revision=_RMG_DB_B, workflow_tool_release=_build("2" * 40)),
            )
            # The probe carries its own public_ref, so the only index that
            # can refuse it is the identity one -- asserted by name below,
            # not assumed from "some IntegrityError".
            with pytest.raises(IntegrityError) as refused:
                with session.begin_nested():
                    session.add(
                        EnergyCorrectionScheme(
                            kind=first.kind,
                            name=first.name,
                            level_of_theory_id=first.level_of_theory_id,
                            software_release_id=first.software_release_id,
                            data_revision=first.data_revision,
                            workflow_tool_release_id=other_build.workflow_tool_release_id,
                            public_ref="ecs_index_probe",
                        )
                    )
                    session.flush()
            assert (
                refused.value.orig.diag.constraint_name
                == "uq_energy_correction_scheme_identity_revised"
            )


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("ABCDEF1234567", "abcdef1234567"),
        ("  " + _RMG_DB_A + "  ", _RMG_DB_A),
        ("v3.3.0", "v3.3.0"),
        ("Release-ABC", "Release-ABC"),
        ("   ", None),
    ],
)
def test_data_revision_normalisation(given: str, stored: str | None) -> None:
    """A commit-looking value is lower-cased; a tag keeps its case; blank is absent."""
    assert _ref(data_revision=given).data_revision == stored


# ---------------------------------------------------------------------------
# atom_params_applied_as
# ---------------------------------------------------------------------------


def test_applied_as_round_trips(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        scheme = resolve_or_create_scheme(
            session,
            _ref(data_revision=_RMG_DB_A, atom_params_applied_as="subtracted"),
        )
        session.flush()
        session.expire(scheme)
        assert scheme.atom_params_applied_as is AtomParamApplication.subtracted

        added = resolve_or_create_scheme(
            session,
            _ref(
                kind="atom_hf",
                name="Arkane atom HF",
                units="kcal_mol",
                data_revision=_RMG_DB_A,
                software=None,
                atom_params_applied_as="added",
            ),
        )
        assert added.atom_params_applied_as is AtomParamApplication.added


def test_applied_as_is_not_inferred_from_the_scheme_kind(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        scheme = resolve_or_create_scheme(session, _ref(data_revision=_RMG_DB_A))
        assert scheme.atom_params_applied_as is None


def test_applied_as_fills_a_row_that_never_stated_it(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        first = resolve_or_create_scheme(session, _ref(data_revision=_RMG_DB_A))
        again = resolve_or_create_scheme(
            session,
            _ref(data_revision=_RMG_DB_A, atom_params_applied_as="subtracted"),
        )
        assert again.id == first.id
        assert first.atom_params_applied_as is AtomParamApplication.subtracted
        # Omitting it later neither clears nor conflicts.
        resolve_or_create_scheme(session, _ref(data_revision=_RMG_DB_A))
        assert first.atom_params_applied_as is AtomParamApplication.subtracted


def test_a_different_applied_as_for_the_same_scheme_is_refused(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        resolve_or_create_scheme(
            session,
            _ref(data_revision=_RMG_DB_A, atom_params_applied_as="subtracted"),
        )
        with pytest.raises(ValueError, match="atom_params_applied_as"):
            resolve_or_create_scheme(
                session,
                _ref(data_revision=_RMG_DB_A, atom_params_applied_as="added"),
            )


def test_applied_as_needs_atom_params() -> None:
    with pytest.raises(ValidationError, match="atom_params"):
        _ref(atom_params=[], atom_params_applied_as="subtracted")


def _transient(**overrides) -> EnergyCorrectionScheme:
    fields = {
        "kind": "atom_energy",
        "name": "Arkane atom energies",
        "level_of_theory_id": 1,
        "software_release_id": 2,
        "workflow_tool_release_id": 10,
    }
    fields.update(overrides)
    return EnergyCorrectionScheme(**fields)


def test_the_ref_of_a_revised_scheme_does_not_depend_on_the_tool_build() -> None:
    """Two builds of one revision must hand out one handle.

    Checked on the canonical identity string itself, because resolving two
    builds through the resolver reuses one row and so never asks the ref
    function a second question.
    """
    a = _transient(data_revision=_RMG_DB_A, workflow_tool_release_id=10)
    b = _transient(data_revision=_RMG_DB_A, workflow_tool_release_id=11)
    assert _canonical_energy_correction_scheme(a) == _canonical_energy_correction_scheme(b)
    other = _transient(data_revision=_RMG_DB_B, workflow_tool_release_id=10)
    assert _canonical_energy_correction_scheme(a) != _canonical_energy_correction_scheme(other)


def test_the_ref_of_an_unrevised_scheme_still_depends_on_the_tool_build() -> None:
    a = _transient(workflow_tool_release_id=10)
    b = _transient(workflow_tool_release_id=11)
    assert _canonical_energy_correction_scheme(a) != _canonical_energy_correction_scheme(b)


# ---------------------------------------------------------------------------
# Review follow-ups: identity legs the first round did not pin
# ---------------------------------------------------------------------------


def test_the_revised_lookup_still_matches_on_software_release(db_conn) -> None:
    """One revision under two programs is two schemes (software stays in identity)."""
    with Session(db_conn) as session, session.begin():
        gaussian = resolve_or_create_scheme(
            session, _ref(data_revision=_RMG_DB_A, software={"name": "Gaussian"})
        )
        orca = resolve_or_create_scheme(
            session, _ref(data_revision=_RMG_DB_A, software={"name": "ORCA"})
        )
        again = resolve_or_create_scheme(
            session, _ref(data_revision=_RMG_DB_A, software={"name": "ORCA"})
        )
        assert gaussian.id != orca.id
        assert again.id == orca.id


def test_an_unrevised_deposit_does_not_land_on_a_revised_row(db_conn) -> None:
    """The reverse order of the absent-vs-present test: revised first."""
    with Session(db_conn) as session, session.begin():
        revised = resolve_or_create_scheme(session, _ref(data_revision=_RMG_DB_A))
        unrevised = resolve_or_create_scheme(session, _ref())
        assert unrevised.id != revised.id
        assert unrevised.data_revision is None
        assert revised.data_revision == _RMG_DB_A
        # And the unrevised row is found again by an unrevised deposit.
        assert resolve_or_create_scheme(session, _ref()).id == unrevised.id


def test_atom_thermal_is_subtracted_and_the_first_deposit_fixes_the_sign(
    db_conn,
) -> None:
    """Arkane applies ``+ count * (atom_hf - atom_thermal)``: atom_hf is added and
    atom_thermal subtracted. A second deposit disagreeing on the sign is refused."""
    from tckdb_schemas import enums as wire_enums

    # Both copies, and the wire one is what producers read.
    for enum_cls in (AtomParamApplication, wire_enums.AtomParamApplication):
        doc = " ".join(enum_cls.__doc__.split())
        assert "``atom_hf`` is ``added`` and ``atom_thermal`` is ``subtracted``" in doc, (
            enum_cls.__module__
        )
    thermal = {
        "kind": "atom_thermal",
        "name": "Arkane atom thermal",
        "units": "kcal_mol",
        "software": None,
        "data_revision": _RMG_DB_A,
        "atom_params": [{"element": "H", "value": 1.01}],
    }
    with Session(db_conn) as session, session.begin():
        scheme = resolve_or_create_scheme(
            session, _ref(**thermal, atom_params_applied_as="subtracted")
        )
        assert scheme.atom_params_applied_as is AtomParamApplication.subtracted
        with pytest.raises(ValueError, match="atom_params_applied_as"):
            resolve_or_create_scheme(
                session, _ref(**thermal, atom_params_applied_as="added")
            )
