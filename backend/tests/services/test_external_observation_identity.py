"""Tests for the shared identity predicate in
``app.services.external_observation_identity``.

:func:`~app.services.external_observation_identity.
ground_state_minimum_entries_for_species` is the one definition of
"a matchable species entry" shared by two callers with different
consequences: the automatic importer resolver (:func:`resolve_identity`,
which requires *exactly one* match) and the curator-only attach service
(``app.services.observation_identity_attach.attach_observation_identity``,
which accepts *any* match). Phase C-E5 review round 3 (R3) found this
predicate had no test coverage of its own: dropping its
``kind == StationaryPointKind.minimum`` clause and dropping its
``species_id ==`` clause both survived the full suite (75+ tests). This
file adds direct predicate coverage plus one resolver-side and one
attach-side test per clause, where the clause's effect is actually
observable from that side (see ``TestAttachSide`` docstring for why the
``species_id`` clause has no attach-side observable failure mode and is
covered by the predicate-level and resolver-level tests instead).
"""

from __future__ import annotations

import pytest

from app.db.models.app_user import AppUser, AppUserRole
from app.db.models.common import (
    SpeciesEntryStateKind,
    StationaryPointKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.schemas.entities.molecular_property_observation import (
    MolecularPropertyObservationCreate,
)
from app.services.external_observation_identity import (
    IDENTITY_NOT_FOUND,
    IDENTITY_RESOLVED,
    ground_state_minimum_entries_for_species,
    resolve_identity,
)
from app.services.observation_identity_attach import attach_observation_identity
from app.services.submission import create_submission, link_records
from tests.services.scientific_read._factories import (
    make_observation,
    make_species,
    make_species_entry,
    next_inchi_key,
)


@pytest.fixture
def curator(db_session) -> AppUser:
    user = AppUser(username="ext_ident_curator_test", role=AppUserRole.curator)
    db_session.add(user)
    db_session.flush()
    return user


def _payload(*, inchikey: str) -> MolecularPropertyObservationCreate:
    return MolecularPropertyObservationCreate.model_validate(
        {
            "species_entry_id": None,
            "scientific_origin": "experimental",
            "property_kind": "dipole_moment",
            "scalar_value": 1.0,
            "scalar_unit": "D",
            "external_source_name": "CCCBDB",
            "external_source_release": "22",
            "external_source_record_key": "rec",
            "raw_payload_json": {"identity_hint": {"inchikey": inchikey}},
        }
    )


def _link_to_submission(db_session, obs, *, created_by: int):
    submission = create_submission(
        db_session,
        created_by=created_by,
        submission_kind=SubmissionKind.other,
        title="external-observation-identity predicate test deposit",
    )
    link_records(
        db_session,
        submission=submission,
        records=[
            (SubmissionRecordType.molecular_property_observation, obs.id, None)
        ],
    )
    return submission


# ---------------------------------------------------------------------------
# Predicate: species_id scoping
# ---------------------------------------------------------------------------


class TestPredicateSpeciesScoping:
    def test_scoped_to_the_requested_species_only(self, db_session):
        """Pins the ``species_id ==`` clause directly: an unrelated
        species's ground-state minimum entry must never appear in the
        result for a different species_id, no matter how many other
        species have qualifying entries."""

        species_a = make_species(db_session, inchi_key=next_inchi_key("PREDA"))
        entry_a = make_species_entry(db_session, species_a)
        species_b = make_species(db_session, inchi_key=next_inchi_key("PREDB"))
        make_species_entry(db_session, species_b)

        result = ground_state_minimum_entries_for_species(db_session, species_a.id)
        assert {e.id for e in result} == {entry_a.id}


# ---------------------------------------------------------------------------
# Resolver side (app.services.external_observation_identity.resolve_identity)
# ---------------------------------------------------------------------------


class TestResolverSide:
    def test_only_vdw_complex_entry_is_not_found(self, db_session):
        """Pins the ``kind == minimum`` clause: a species whose only entry
        is a van der Waals complex has no *minimum* entry at all, so the
        resolver must not treat it as a match."""

        species = make_species(db_session, inchi_key=next_inchi_key("RESVDW"))
        make_species_entry(
            db_session, species, kind=StationaryPointKind.vdw_complex
        )

        result = resolve_identity(_payload(inchikey=species.inchi_key), db_session)
        assert result.status == IDENTITY_NOT_FOUND
        assert result.species_entry_id is None

    def test_only_excited_state_entry_is_not_found(self, db_session):
        """Pins the ``electronic_state_kind == ground`` clause: a species
        whose only entry is excited-state has no ground-state entry, so
        the resolver must not treat it as a match."""

        species = make_species(db_session, inchi_key=next_inchi_key("RESEXC"))
        make_species_entry(
            db_session,
            species,
            electronic_state_kind=SpeciesEntryStateKind.excited,
            electronic_state_label="A",
        )

        result = resolve_identity(_payload(inchikey=species.inchi_key), db_session)
        assert result.status == IDENTITY_NOT_FOUND
        assert result.species_entry_id is None

    def test_cross_species_entries_do_not_leak_into_resolution(self, db_session):
        """Pins the ``species_id ==`` clause from the resolver side: the
        InChIKey-matching species has exactly one compatible entry, and an
        entirely unrelated species also has a compatible entry of its own.
        Dropping the ``species_id ==`` clause would pool both species'
        entries together and the resolver would refuse as ambiguous
        instead of resolving.
        """

        species_a = make_species(db_session, inchi_key=next_inchi_key("RESXA"))
        entry_a = make_species_entry(db_session, species_a)
        species_b = make_species(db_session, inchi_key=next_inchi_key("RESXB"))
        make_species_entry(db_session, species_b)

        result = resolve_identity(_payload(inchikey=species_a.inchi_key), db_session)
        assert result.status == IDENTITY_RESOLVED
        assert result.species_entry_id == entry_a.id


# ---------------------------------------------------------------------------
# Attach side (app.services.observation_identity_attach.
# attach_observation_identity)
# ---------------------------------------------------------------------------


class TestAttachSide:
    """Only ``kind`` and ``electronic_state_kind`` have an attach-side
    observable failure mode. ``_assert_ground_state_minimum_entry`` always
    queries ``ground_state_minimum_entries_for_species(session,
    target.species_id)`` -- i.e. it is always called with the *target's
    own, correct* species id -- and then checks only whether ``target.id``
    is a member of the result. Dropping the ``species_id ==`` clause turns
    that query into a strict superset (every species' qualifying entries,
    not just the target's), but a superset can never remove ``target.id``
    from the result, and ``target.id``'s membership depends only on
    ``target``'s own ``kind``/``electronic_state_kind`` -- never on which
    species it belongs to. So there is no observation (target accepted vs.
    refused) that a ``species_id``-drop mutation could flip on this side;
    that clause's coverage lives in ``TestPredicateSpeciesScoping`` and
    ``TestResolverSide`` above instead.
    """

    def test_vdw_complex_target_refuses(self, db_session, curator):
        species = make_species(db_session, inchi_key=next_inchi_key("ATTVDW"))
        entry = make_species_entry(
            db_session, species, kind=StationaryPointKind.vdw_complex
        )
        obs = make_observation(db_session, species_entry=None)
        _link_to_submission(db_session, obs, created_by=curator.id)

        with pytest.raises(
            ValueError,
            match="observation_identity_target_not_ground_state_minimum",
        ):
            attach_observation_identity(
                db_session,
                observation_handle=obs.public_ref,
                species_entry_ref=entry.public_ref,
                actor=curator,
            )
