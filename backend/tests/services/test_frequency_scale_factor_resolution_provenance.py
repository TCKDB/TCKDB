"""Tests for the correction-scheme-provenance release-grain widening of
``resolve_or_create_freq_scale_factor_ref`` (plan v2 §6 -- the
``frequency_scale_factor`` sibling of what
``test_energy_correction_resolution_provenance.py`` covers for
``energy_correction_scheme``).

FSF has no ``units`` axis (plan §6: a scale factor is dimensionless), so
this file's scope is narrower than the ECS one -- release grain only.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.energy_correction import FrequencyScaleFactor
from app.schemas.fragments.refs import FreqScaleFactorRef
from app.services.energy_correction_resolution import (
    resolve_or_create_freq_scale_factor_ref,
)

_LOT = {"method": "wB97X-D", "basis": "def2-TZVP"}


def _fsf_ref(**overrides) -> FreqScaleFactorRef:
    base = {
        "level_of_theory": dict(_LOT),
        "scale_kind": "fundamental",
        "value": 0.988,
    }
    base.update(overrides)
    return FreqScaleFactorRef(**base)


def test_two_factors_same_identity_different_software_release_are_distinct_rows(
    db_conn,
) -> None:
    """A Gaussian-derived factor and an ORCA-derived factor at the same
    (lot, scale_kind, value) are not a collision -- they coexist as two
    distinct rows, mirroring ECS's software-as-identity-dimension test."""
    with Session(db_conn) as session, session.begin():
        gaussian = resolve_or_create_freq_scale_factor_ref(
            session, _fsf_ref(software={"name": "Gaussian"})
        )
        orca = resolve_or_create_freq_scale_factor_ref(
            session, _fsf_ref(software={"name": "ORCA"})
        )

        assert gaussian.id != orca.id
        assert gaussian.software_release.software.name == "Gaussian"
        assert orca.software_release.software.name == "ORCA"

        # Re-supplying the same software reuses the same row.
        again = resolve_or_create_freq_scale_factor_ref(
            session, _fsf_ref(software={"name": "Gaussian"})
        )
        assert again.id == gaussian.id


def test_two_fully_software_less_factors_still_collapse(db_conn) -> None:
    """Two factors NULL on every dimension still collapse into one row --
    ``NULLS NOT DISTINCT`` still treats them as duplicates."""
    with Session(db_conn) as session, session.begin():
        first = resolve_or_create_freq_scale_factor_ref(session, _fsf_ref())
        second = resolve_or_create_freq_scale_factor_ref(session, _fsf_ref())
        assert first.id == second.id


def test_versioned_and_version_less_release_of_same_program_are_distinct_rows(
    db_conn,
) -> None:
    """A software release with a stated build and the version-less
    release of the same program are two different values of
    ``software_release_id`` (plan v2 §3.2's table, which this sibling
    revision inherits), not a collision."""
    with Session(db_conn) as session, session.begin():
        versioned = resolve_or_create_freq_scale_factor_ref(
            session,
            _fsf_ref(
                software={"name": "Gaussian", "version": "16", "revision": "C.02"}
            ),
        )
        version_less = resolve_or_create_freq_scale_factor_ref(
            session, _fsf_ref(software={"name": "Gaussian"})
        )

        assert versioned.id != version_less.id
        assert versioned.software_release_id != version_less.software_release_id


def test_software_ref_with_version_resolves_the_exact_release(db_conn) -> None:
    """A depositor who states version/revision resolves to exactly that
    release row, not the bare-program one."""
    with Session(db_conn) as session, session.begin():
        fsf = resolve_or_create_freq_scale_factor_ref(
            session,
            _fsf_ref(
                software={"name": "Gaussian", "version": "16", "revision": "C.02"}
            ),
        )

        assert fsf.software_release is not None
        assert fsf.software_release.version == "16"
        assert fsf.software_release.revision == "C.02"
        assert fsf.software_release.software.name == "Gaussian"


def test_software_ref_name_only_resolves_and_reuses_the_version_less_release(
    db_conn,
) -> None:
    """"Program known, build not stated" is a first-class, complete value
    of ``software_release_id`` -- a depositor naming only the program
    resolves to the one version-less release row for it, and a second
    factor identity naming the same bare program reuses that same
    release row rather than minting another."""
    with Session(db_conn) as session, session.begin():
        first = resolve_or_create_freq_scale_factor_ref(
            session, _fsf_ref(software={"name": "Gaussian"})
        )

        assert first.software_release is not None
        assert first.software_release.version is None
        assert first.software_release.software.name == "Gaussian"

        second = resolve_or_create_freq_scale_factor_ref(
            session,
            _fsf_ref(value=0.987, software={"name": "Gaussian"}),
        )

        assert second.id != first.id
        assert second.software_release_id == first.software_release_id


def test_workflow_tool_release_and_software_release_are_independent_axes(
    db_conn,
) -> None:
    """A factor differing only by workflow-tool release (already in the
    identity before this revision) is still distinct from one differing
    only by software release -- the two axes do not collapse into each
    other."""
    with Session(db_conn) as session, session.begin():
        with_wtr = resolve_or_create_freq_scale_factor_ref(
            session,
            _fsf_ref(workflow_tool_release={"name": "ARC", "version": "1.1.0"}),
        )
        with_release = resolve_or_create_freq_scale_factor_ref(
            session, _fsf_ref(software={"name": "Gaussian"})
        )
        without_either = resolve_or_create_freq_scale_factor_ref(session, _fsf_ref())

        assert len({with_wtr.id, with_release.id, without_either.id}) == 3


def test_repeated_upload_reuses_row_regardless_of_note(db_conn) -> None:
    """``note`` is descriptive only and never used for matching -- a
    second upload against the same identity with a different note reuses
    the existing row and keeps the first-written note."""
    with Session(db_conn) as session, session.begin():
        first = resolve_or_create_freq_scale_factor_ref(
            session,
            _fsf_ref(software={"name": "Gaussian"}, note="first writer note"),
        )
        second = resolve_or_create_freq_scale_factor_ref(
            session,
            _fsf_ref(software={"name": "Gaussian"}, note="second writer note"),
        )
        assert second.id == first.id

        fsf = session.get(FrequencyScaleFactor, first.id)
        assert fsf is not None
        assert fsf.note == "first writer note"
