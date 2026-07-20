"""Workflow + read tests for group-additivity (Benson) estimation provenance.

Exercises the vertical slice added in DR-0035: an estimated thermo upload
can carry a GA breakdown (scheme + per-group components); the breakdown
persists to ``group_additivity_scheme`` / ``applied_group_additivity`` /
``applied_group_additivity_component`` and is surfaced on the thermo read.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models.common import GroupAdditivityComponentKind
from app.db.models.group_additivity import (
    AppliedGroupAdditivity,
    AppliedGroupAdditivityComponent,
    GroupAdditivityScheme,
)
from app.schemas.reads.scientific_thermo import ThermoReadRequest
from app.schemas.workflows.group_additivity_upload import (
    AppliedGroupAdditivityUploadPayload,
    GroupAdditivitySchemeRef,
)
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.group_additivity_resolution import (
    create_applied_group_additivity,
    resolve_or_create_ga_scheme,
)

_GA_LOGGER = "app.services.group_additivity_resolution"
from app.services.scientific_read.thermo import get_species_thermo
from app.workflows.thermo import persist_thermo_upload

_SPECIES_ENTRY = {"smiles": "CC", "charge": 0, "multiplicity": 1}


def _ga_payload(**scheme_overrides) -> dict:
    scheme = {
        "name": "RMG-database GAV",
        "version": "3.1.0",
        "description": "RMG group-additivity values",
        "code_commit": "abc1234",
    }
    scheme.update(scheme_overrides)
    return {
        "scheme": scheme,
        "note": "ethane from two C/C/H3 groups",
        "components": [
            {
                "component_kind": "group",
                "group_label": "C/C/H3",
                "count": 2,
                "h298_contribution_kj_mol": -42.19,
                "s298_contribution_j_mol_k": 127.24,
                "cp298_contribution_j_mol_k": 25.91,
            },
            {
                "component_kind": "symmetry_correction",
                "group_label": "external symmetry sigma=6",
                "count": 1,
                "s298_contribution_j_mol_k": -14.9,
            },
        ],
    }


def _estimated_request(**overrides) -> ThermoUploadRequest:
    base: dict = {
        "species_entry": dict(_SPECIES_ENTRY),
        "scientific_origin": "estimated",
        "h298_kj_mol": -84.0,
        "s298_j_mol_k": 229.2,
        "group_additivity": _ga_payload(),
    }
    base.update(overrides)
    return ThermoUploadRequest(**base)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_estimated_thermo_persists_ga_scheme_and_breakdown(db_session):
    """The scheme, applied row, and each component persist and link up."""
    thermo = persist_thermo_upload(db_session, _estimated_request())

    applied = db_session.scalar(
        select(AppliedGroupAdditivity).where(
            AppliedGroupAdditivity.thermo_id == thermo.id
        )
    )
    assert applied is not None
    assert applied.scheme_id is not None
    assert applied.note == "ethane from two C/C/H3 groups"

    scheme = db_session.get(GroupAdditivityScheme, applied.scheme_id)
    assert scheme.name == "RMG-database GAV"
    assert scheme.version == "3.1.0"
    assert scheme.code_commit == "abc1234"
    assert scheme.public_ref.startswith("gasch_")

    components = db_session.scalars(
        select(AppliedGroupAdditivityComponent)
        .where(
            AppliedGroupAdditivityComponent.applied_group_additivity_id
            == applied.id
        )
        .order_by(AppliedGroupAdditivityComponent.id)
    ).all()
    assert len(components) == 2

    group = components[0]
    assert group.component_kind == GroupAdditivityComponentKind.group
    assert group.group_label == "C/C/H3"
    assert group.count == 2
    assert group.h298_contribution_kj_mol == pytest.approx(-42.19)
    assert group.s298_contribution_j_mol_k == pytest.approx(127.24)
    assert group.cp298_contribution_j_mol_k == pytest.approx(25.91)

    correction = components[1]
    assert (
        correction.component_kind
        == GroupAdditivityComponentKind.symmetry_correction
    )
    assert correction.h298_contribution_kj_mol is None
    assert correction.s298_contribution_j_mol_k == pytest.approx(-14.9)

    # The thermo relationship exposes the one-to-one breakdown.
    assert thermo.applied_group_additivity is not None
    assert thermo.applied_group_additivity.id == applied.id


def test_ga_scheme_deduped_by_name_and_version(db_session):
    """Two estimated uploads sharing a scheme identity reuse one scheme row."""
    t1 = persist_thermo_upload(db_session, _estimated_request())
    t2 = persist_thermo_upload(
        db_session,
        _estimated_request(species_entry={"smiles": "CCC", "charge": 0, "multiplicity": 1}),
    )

    a1 = db_session.scalar(
        select(AppliedGroupAdditivity).where(
            AppliedGroupAdditivity.thermo_id == t1.id
        )
    )
    a2 = db_session.scalar(
        select(AppliedGroupAdditivity).where(
            AppliedGroupAdditivity.thermo_id == t2.id
        )
    )
    assert a1.scheme_id == a2.scheme_id

    schemes = db_session.scalars(
        select(GroupAdditivityScheme).where(
            GroupAdditivityScheme.name == "RMG-database GAV",
            GroupAdditivityScheme.version == "3.1.0",
        )
    ).all()
    assert len(schemes) == 1


def test_ga_scheme_code_commit_mismatch_warns_and_keeps_existing(db_session, caplog):
    """A reused (name, version) with a DIFFERENT code_commit warns but does not
    mutate the stored row (dedup semantics unchanged); an equal or None commit
    is silent."""
    ref_a = GroupAdditivitySchemeRef(name="GAV-warn", version="9.9.9", code_commit="abc123")
    first = resolve_or_create_ga_scheme(db_session, ref_a)
    db_session.flush()

    # Second resolve, same (name, version), DIFFERENT commit → exactly one WARNING.
    with caplog.at_level(logging.WARNING, logger=_GA_LOGGER):
        second = resolve_or_create_ga_scheme(
            db_session,
            GroupAdditivitySchemeRef(name="GAV-warn", version="9.9.9", code_commit="def456"),
        )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "GAV-warn" in msg and "9.9.9" in msg
    assert "abc123" in msg  # existing (retained) commit
    assert "def456" in msg  # uploaded (ignored) commit
    # Same existing row, original commit retained — dedup semantics unchanged.
    assert second.id == first.id
    assert second.code_commit == "abc123"

    # EQUAL commit → no warning.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=_GA_LOGGER):
        resolve_or_create_ga_scheme(
            db_session,
            GroupAdditivitySchemeRef(name="GAV-warn", version="9.9.9", code_commit="abc123"),
        )
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    # None commit → no warning.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=_GA_LOGGER):
        resolve_or_create_ga_scheme(
            db_session,
            GroupAdditivitySchemeRef(name="GAV-warn", version="9.9.9", code_commit=None),
        )
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_ga_rejected_on_non_estimated_origin():
    """A GA breakdown may only attach to scientific_origin='estimated'."""
    with pytest.raises(ValidationError, match="scientific_origin='estimated'"):
        ThermoUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="computed",
            h298_kj_mol=-84.0,
            group_additivity=_ga_payload(),
        )


def test_service_guard_rejects_ga_on_non_estimated_thermo(db_session):
    """create_applied_group_additivity refuses a non-estimated thermo target.

    This guards programmatic (non-upload) callers that bypass the Pydantic
    upload-schema validator: a computed thermo must not receive a GA
    breakdown.
    """
    computed = persist_thermo_upload(
        db_session,
        ThermoUploadRequest(
            species_entry={"smiles": "N", "charge": 0, "multiplicity": 1},
            scientific_origin="computed",
            h298_kj_mol=-45.9,
            s298_j_mol_k=192.8,
        ),
    )
    db_session.flush()

    payload = AppliedGroupAdditivityUploadPayload.model_validate(_ga_payload())
    with pytest.raises(ValueError, match="scientific_origin='estimated'"):
        create_applied_group_additivity(
            db_session, payload, thermo_id=computed.id
        )


def test_ga_requires_at_least_one_component():
    """An empty component list is rejected at the payload level."""
    with pytest.raises(ValidationError, match="at least one component"):
        ThermoUploadRequest(
            species_entry=dict(_SPECIES_ENTRY),
            scientific_origin="estimated",
            h298_kj_mol=-84.0,
            group_additivity={
                "scheme": {"name": "X", "version": "1"},
                "components": [],
            },
        )


# ---------------------------------------------------------------------------
# Read exposure
# ---------------------------------------------------------------------------


def test_thermo_read_surfaces_group_additivity_block(db_session):
    """get_species_thermo exposes the GA breakdown on the estimated record."""
    thermo = persist_thermo_upload(db_session, _estimated_request())
    db_session.flush()

    response = get_species_thermo(
        db_session,
        species_entry_id=thermo.species_entry_id,
        request=ThermoReadRequest(),
    )
    # Key on our own thermo_id: the session-scoped DB may hold unrelated
    # records for the same species from other tests.
    record = next(r for r in response.records if r.thermo_id == thermo.id)
    block = record.group_additivity
    assert block is not None
    assert block.scheme_name == "RMG-database GAV"
    assert block.scheme_version == "3.1.0"
    assert block.code_commit == "abc1234"
    assert block.scheme_ref.startswith("gasch_")
    assert len(block.components) == 2
    assert block.components[0].group_label == "C/C/H3"
    assert block.components[0].count == 2
    assert block.components[0].h298_contribution_kj_mol == pytest.approx(-42.19)


def test_thermo_read_ga_block_null_for_non_estimated(db_session):
    """A computed thermo record surfaces group_additivity=null."""
    thermo = persist_thermo_upload(
        db_session,
        ThermoUploadRequest(
            species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
            scientific_origin="computed",
            h298_kj_mol=-241.8,
            s298_j_mol_k=188.8,
        ),
    )
    db_session.flush()

    response = get_species_thermo(
        db_session,
        species_entry_id=thermo.species_entry_id,
        request=ThermoReadRequest(),
    )
    record = next(r for r in response.records if r.thermo_id == thermo.id)
    assert record.group_additivity is None
