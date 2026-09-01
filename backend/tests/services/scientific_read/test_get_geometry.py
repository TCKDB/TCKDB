"""Service-layer tests for ``get_geometry``."""

from __future__ import annotations

import pytest

from app.api.errors import NotFoundError
from app.db.models.calculation import (
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationGeometryRole,
    CalculationType,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
    TransitionStateEntryStatus,
)
from app.db.models.geometry import GeometryAtom
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.reads.scientific_geometry import GeometryReadRequest
from app.services.scientific_read.geometry import get_geometry
from tests.services.scientific_read._factories import (
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _make_ts_entry(
    db_session, *, unmapped_smiles: str | None = None, charge: int = 0, multiplicity: int = 2
) -> TransitionStateEntry:
    """Build a minimal ChemReaction -> ReactionEntry -> TransitionState -> entry chain.

    No factory for this exists in ``_factories.py``; mirrors the local
    helper of the same name in
    ``tests/api/scientific/test_api_scientific_calculations.py``.
    """
    rxn = ChemReaction(reversible=True)
    db_session.add(rxn)
    db_session.flush()
    rxe = ReactionEntry(reaction_id=rxn.id)
    db_session.add(rxe)
    db_session.flush()
    ts = TransitionState(reaction_entry_id=rxe.id, label="ts-identity-test")
    db_session.add(ts)
    db_session.flush()
    tse = TransitionStateEntry(
        transition_state_id=ts.id,
        charge=charge,
        multiplicity=multiplicity,
        unmapped_smiles=unmapped_smiles,
        status=TransitionStateEntryStatus.optimized,
    )
    db_session.add(tse)
    db_session.flush()
    return tse


def _link_submission(db_session, *, calculation_id: int, created_by: int) -> Submission:
    """Create a Submission and link it to *calculation_id* via SubmissionRecordLink."""
    submission = Submission(
        created_by=created_by,
        submission_kind=SubmissionKind.conformer,
        source_kind=SubmissionSourceKind.api,
        status=SubmissionStatus.pending,
    )
    db_session.add(submission)
    db_session.flush()
    db_session.add(
        SubmissionRecordLink(
            submission_id=submission.id,
            record_type=SubmissionRecordType.calculation,
            record_id=calculation_id,
        )
    )
    db_session.flush()
    return submission


def _seed_water_geometry(db_session):
    """Build a 3-atom geometry whose coordinates look like water."""
    geom = make_geometry(db_session, natoms=3, xyz_text="O 0 0 0\nH 0 .76 .58\nH 0 -.76 .58")
    rows = [
        GeometryAtom(geometry_id=geom.id, atom_index=1, element="O", x=0.0, y=0.0, z=0.0),
        GeometryAtom(geometry_id=geom.id, atom_index=2, element="H", x=0.0, y=0.76, z=0.58),
        GeometryAtom(geometry_id=geom.id, atom_index=3, element="H", x=0.0, y=-0.76, z=0.58),
    ]
    for r in rows:
        db_session.add(r)
    db_session.flush()
    return geom


def test_get_geometry_returns_atoms_in_order(db_session):
    geom = _seed_water_geometry(db_session)

    response = get_geometry(
        db_session,
        geometry_handle=geom.public_ref,
        request=GeometryReadRequest(),
    )

    assert response.geometry_ref == geom.public_ref
    assert response.geometry_id == geom.id
    assert response.natoms == 3
    assert response.geom_hash == geom.geom_hash
    assert response.format == "cartesian"
    assert response.coordinate_units == "angstrom"
    assert response.symbols == ["O", "H", "H"]
    assert response.coords == [
        [0.0, 0.0, 0.0],
        [0.0, 0.76, 0.58],
        [0.0, -0.76, 0.58],
    ]
    assert [a.atom_index for a in response.atoms] == [1, 2, 3]


def test_get_geometry_accepts_integer_handle(db_session):
    geom = _seed_water_geometry(db_session)

    response = get_geometry(
        db_session,
        geometry_handle=str(geom.id),
        request=GeometryReadRequest(),
    )
    assert response.geometry_ref == geom.public_ref


def test_get_geometry_handle_not_found_404(db_session):
    with pytest.raises(NotFoundError, match="geometry not found"):
        get_geometry(
            db_session,
            geometry_handle="geom_neverexistsabcdefxyzqr",
            request=GeometryReadRequest(),
        )


def test_get_geometry_wrong_prefix_422(db_session):
    with pytest.raises(ValueError, match="handle_type_mismatch"):
        get_geometry(
            db_session,
            geometry_handle="spe_abcdef0123456789",
            request=GeometryReadRequest(),
        )


def test_get_geometry_malformed_handle_422(db_session):
    with pytest.raises(ValueError, match="invalid_handle"):
        get_geometry(
            db_session,
            geometry_handle="not-a-handle",
            request=GeometryReadRequest(),
        )


def test_get_geometry_provenance_lists_input_and_output_calcs(db_session):
    """Provenance surfaces every calc that produced or consumed the geometry."""
    geom = _seed_water_geometry(db_session)
    species = make_species(
        db_session, smiles="O", inchi_key=next_inchi_key("GP")
    )
    entry = make_species_entry(db_session, species)
    opt_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    sp_calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    freq_calc = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    # opt produced the geometry (role=final); sp + freq consumed it.
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=opt_calc.id,
            geometry_id=geom.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.add(
        CalculationInputGeometry(
            calculation_id=sp_calc.id, geometry_id=geom.id, input_order=1
        )
    )
    db_session.add(
        CalculationInputGeometry(
            calculation_id=freq_calc.id, geometry_id=geom.id, input_order=1
        )
    )
    db_session.flush()

    response = get_geometry(
        db_session,
        geometry_handle=geom.public_ref,
        request=GeometryReadRequest(),
    )
    prov = response.provenance

    produced_refs = {link.calculation_ref for link in prov.produced_by}
    assert produced_refs == {opt_calc.public_ref}
    assert prov.produced_by[0].role == "final"
    assert prov.produced_by[0].calculation_type == "opt"

    consumed_refs = {link.calculation_ref for link in prov.used_as_input_by}
    assert consumed_refs == {sp_calc.public_ref, freq_calc.public_ref}
    for link in prov.used_as_input_by:
        assert link.role is None


def test_get_geometry_unknown_include_token_422(db_session):
    geom = _seed_water_geometry(db_session)
    with pytest.raises(ValueError, match="unknown_include_token"):
        get_geometry(
            db_session,
            geometry_handle=geom.public_ref,
            request=GeometryReadRequest(include=["banana"]),
        )


# ---------------------------------------------------------------------------
# Molecular identity
# ---------------------------------------------------------------------------


def test_get_geometry_identity_null_when_no_owner(db_session):
    """A geometry reachable from no calculation has genuinely absent identity.

    The field itself is ``None`` -- not an empty/half-populated object --
    so a client can distinguish "no owner" from "owner exists but has
    nothing to report" (the TS-without-unmapped_smiles case below).
    """
    geom = _seed_water_geometry(db_session)

    response = get_geometry(
        db_session, geometry_handle=geom.public_ref, request=GeometryReadRequest()
    )
    assert response.identity is None


def test_get_geometry_identity_species_owned_uses_correct_owner(db_session):
    """The identity block reports the geometry's *actual* owner, not just any owner.

    A decoy species/entry is created first (lower id, unrelated to the
    geometry) with SMILES/InChI distinct from the real owner. A builder
    that returns "the first species entry" instead of resolving the
    calculation -> owner join correctly would report the decoy's
    values here and this test would catch it.
    """
    decoy_species = make_species(
        db_session, smiles="C", inchi_key=next_inchi_key("DECOY")
    )
    make_species_entry(db_session, decoy_species)

    real_species = make_species(
        db_session, smiles="N#N", inchi_key=next_inchi_key("REALOWNER")
    )
    real_entry = make_species_entry(db_session, real_species)

    geom = _seed_water_geometry(db_session)
    opt_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=real_entry.id
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=opt_calc.id,
            geometry_id=geom.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()

    response = get_geometry(
        db_session, geometry_handle=geom.public_ref, request=GeometryReadRequest()
    )
    identity = response.identity
    assert identity is not None
    assert identity.kind == "species_entry"
    assert identity.transition_state_entry is None
    se = identity.species_entry
    assert se is not None
    assert se.species_ref == real_species.public_ref
    assert se.species_entry_ref == real_entry.public_ref
    assert se.canonical_smiles == "N#N"
    assert se.canonical_smiles != decoy_species.smiles
    assert se.inchi_key.strip() == real_species.inchi_key.strip()
    assert se.inchi_key.strip() != decoy_species.inchi_key.strip()
    assert se.charge == 0
    assert se.multiplicity == 1
    assert se.formula == "N2"


def test_get_geometry_identity_transition_state_owned(db_session):
    tse = _make_ts_entry(db_session, unmapped_smiles="[CH3]", charge=0, multiplicity=2)
    geom = _seed_water_geometry(db_session)
    sp_calc = make_calculation(
        db_session, type=CalculationType.sp, transition_state_entry_id=tse.id
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=sp_calc.id,
            geometry_id=geom.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()

    response = get_geometry(
        db_session, geometry_handle=geom.public_ref, request=GeometryReadRequest()
    )
    identity = response.identity
    assert identity is not None
    assert identity.kind == "transition_state_entry"
    assert identity.species_entry is None
    ts = identity.transition_state_entry
    assert ts is not None
    assert ts.transition_state_entry_ref == tse.public_ref
    assert ts.transition_state_ref == tse.transition_state.public_ref
    assert ts.unmapped_smiles == "[CH3]"
    assert ts.formula == "CH3"
    assert ts.charge == 0
    assert ts.multiplicity == 2


def test_get_geometry_identity_ts_owned_without_unmapped_smiles(db_session):
    """A TS owner with no ``unmapped_smiles`` is not the same as "no owner".

    The identity block is non-null (there IS an owner) but its
    SMILES/formula fields are null because the entry itself has nothing
    to report -- distinct from :func:`test_get_geometry_identity_null_when_no_owner`.
    """
    tse = _make_ts_entry(db_session, unmapped_smiles=None)
    geom = _seed_water_geometry(db_session)
    sp_calc = make_calculation(
        db_session, type=CalculationType.sp, transition_state_entry_id=tse.id
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=sp_calc.id,
            geometry_id=geom.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()

    response = get_geometry(
        db_session, geometry_handle=geom.public_ref, request=GeometryReadRequest()
    )
    assert response.identity is not None
    ts = response.identity.transition_state_entry
    assert ts is not None
    assert ts.unmapped_smiles is None
    assert ts.formula is None
    # The entry is still fully identified even with no SMILES to parse.
    assert ts.transition_state_entry_ref == tse.public_ref


def test_get_geometry_identity_ambiguous_when_multiple_distinct_owners(db_session):
    """Two calcs from two different owning entries sharing one geometry.

    Content-hash dedup makes this real: two isotopologues have identical
    plain-element coordinates and can end up citing the same geometry row
    from calculations under two different species entries. The service
    must not silently pick one.
    """
    species_a = make_species(
        db_session, smiles="O", inchi_key=next_inchi_key("AMBIGA")
    )
    entry_a = make_species_entry(db_session, species_a)
    species_b = make_species(
        db_session, smiles="N#N", inchi_key=next_inchi_key("AMBIGB")
    )
    entry_b = make_species_entry(db_session, species_b)

    geom = _seed_water_geometry(db_session)
    calc_a = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry_a.id
    )
    calc_b = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry_b.id
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=calc_a.id,
            geometry_id=geom.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=calc_b.id,
            geometry_id=geom.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()

    response = get_geometry(
        db_session, geometry_handle=geom.public_ref, request=GeometryReadRequest()
    )
    identity = response.identity
    assert identity is not None
    assert identity.kind is None
    assert identity.species_entry is None
    assert identity.transition_state_entry is None
    owner_refs = {(o.kind, o.ref) for o in identity.ambiguous_owners}
    assert owner_refs == {
        ("species_entry", entry_a.public_ref),
        ("species_entry", entry_b.public_ref),
    }


# ---------------------------------------------------------------------------
# Submission reference
# ---------------------------------------------------------------------------


def test_get_geometry_submission_present_for_single_producing_submission(
    db_session, _api_test_user
):
    species = make_species(db_session, smiles="O", inchi_key=next_inchi_key("SUB1"))
    entry = make_species_entry(db_session, species)
    geom = _seed_water_geometry(db_session)
    opt_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=opt_calc.id,
            geometry_id=geom.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()
    submission = _link_submission(
        db_session, calculation_id=opt_calc.id, created_by=_api_test_user
    )

    response = get_geometry(
        db_session, geometry_handle=geom.public_ref, request=GeometryReadRequest()
    )
    assert response.submission_id == submission.id
    assert response.submission_ref == submission.public_ref


def test_get_geometry_submission_null_when_no_producing_calculation(
    db_session, _api_test_user
):
    """A submission on a *consuming* calculation does not count as the deposit.

    ``submission_ref`` answers "which upload produced this geometry", so
    only ``provenance.produced_by`` calculations are consulted -- a calc
    that merely reads a shared geometry did not deposit it.
    """
    species = make_species(db_session, smiles="O", inchi_key=next_inchi_key("SUB2"))
    entry = make_species_entry(db_session, species)
    geom = _seed_water_geometry(db_session)
    sp_calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    db_session.add(
        CalculationInputGeometry(
            calculation_id=sp_calc.id, geometry_id=geom.id, input_order=1
        )
    )
    db_session.flush()
    _link_submission(db_session, calculation_id=sp_calc.id, created_by=_api_test_user)

    response = get_geometry(
        db_session, geometry_handle=geom.public_ref, request=GeometryReadRequest()
    )
    assert response.submission_id is None
    assert response.submission_ref is None


def test_get_geometry_submission_null_when_producers_disagree(
    db_session, _api_test_user
):
    """Two producing calcs citing two different submissions: no single deposit to name."""
    species = make_species(db_session, smiles="O", inchi_key=next_inchi_key("SUB3"))
    entry = make_species_entry(db_session, species)
    geom = _seed_water_geometry(db_session)
    calc_1 = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    calc_2 = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=calc_1.id,
            geometry_id=geom.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=calc_2.id,
            geometry_id=geom.id,
            output_order=2,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()
    _link_submission(db_session, calculation_id=calc_1.id, created_by=_api_test_user)
    _link_submission(db_session, calculation_id=calc_2.id, created_by=_api_test_user)

    response = get_geometry(
        db_session, geometry_handle=geom.public_ref, request=GeometryReadRequest()
    )
    assert response.submission_id is None
    assert response.submission_ref is None
