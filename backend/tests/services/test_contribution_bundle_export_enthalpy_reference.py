"""Legacy undeclared-enthalpy thermo rows must never export a bundle this
same server refuses to re-import.

Before this module, ``contribution_bundle_export.py`` emitted
``enthalpy_reference_kind`` verbatim: a pre-existing thermo row with an
enthalpy and no declaration (never backfilled -- see
``e7b1c9d4a632``/``.claude/rules/migration-rules.md``) round-tripped into a
bundle that ``persist_thermo_upload`` (``app.workflows.thermo``, via the
shared ``tckdb_schemas.enthalpy_reference.enthalpy_reference_error`` rule)
refuses on import. See ``docs/contribution-bundles/v0-format.md`` for the
documented contract these tests pin.

Three record shapes, each exported then re-imported:

* **legacy, scalar-only enthalpy** -- ``h298_kj_mol`` set, no declaration,
  the rest of the record (entropy, temperature range) still meaningful.
  The enthalpy value is pruned from the export; everything else, and the
  record itself, survives.
* **legacy, fit-only enthalpy** -- the enthalpy lives inside a NASA fit.
  ``ThermoNASACreate`` requires every coefficient, so the enthalpy cannot
  be dropped without destroying the whole fit, which is this record's
  entire scientific content. The record is omitted from the bundle and
  reported, never exported broken or silently dropped.
* **declared** -- unaffected; exports and re-imports exactly as before.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models.common import ScientificOriginKind
from app.db.models.thermo import Thermo
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.contribution_bundle_export import export_thermo_bundle
from app.workflows.thermo import persist_thermo_upload
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    make_species,
    make_species_entry,
    next_inchi_key,
)

# ---------------------------------------------------------------------------
# Fixtures: legacy rows only reachable by disabling the guard trigger for
# the insert -- mirrors ``_legacy_undeclared_thermo_id`` in
# tests/db/test_enthalpy_reference_migration.py, which this module models a
# read-path consequence of.
# ---------------------------------------------------------------------------


def _legacy_scalar_thermo(session: Session, *, prefix: str) -> Thermo:
    """h298 + s298, no declaration -- the record is meaningful without h298."""
    species = make_species(session, inchi_key=next_inchi_key(prefix))
    entry = make_species_entry(session, species)
    session.execute(text("ALTER TABLE thermo DISABLE TRIGGER trg_guard_thermo_enthalpy_reference"))
    try:
        thermo_id = session.scalar(
            text(
                "INSERT INTO thermo "
                "(public_ref, species_entry_id, scientific_origin, h298_kj_mol, "
                "s298_j_mol_k, tmin_k, tmax_k) "
                "VALUES (:ref, :entry, 'computed', -74.5, 186.3, 200, 2000) "
                "RETURNING id"
            ),
            {"entry": entry.id, "ref": "th_" + uuid4().hex[:26]},
        )
    finally:
        session.execute(text("ALTER TABLE thermo ENABLE TRIGGER trg_guard_thermo_enthalpy_reference"))
    session.flush()
    thermo = session.get(Thermo, thermo_id)
    assert thermo is not None
    return thermo


def _legacy_fit_thermo(session: Session, *, prefix: str) -> Thermo:
    """NASA-7 fit, no declaration, no scalar h298 -- the fit is everything.

    No trigger juggling needed: the DB guard only fires on
    ``thermo.h298_kj_mol``/``thermo.enthalpy_reference_kind`` themselves
    (see ``e7b1c9d4a632``'s docstring -- "child-only enthalpy ... is a
    workflow rule, not this one"), so a NASA-only legacy row is a plain
    insert.
    """
    species = make_species(session, inchi_key=next_inchi_key(prefix))
    entry = make_species_entry(session, species)
    thermo = Thermo(
        species_entry_id=entry.id, scientific_origin=ScientificOriginKind.computed
    )
    session.add(thermo)
    session.flush()
    attach_thermo_nasa(session, thermo=thermo)
    session.flush()
    return thermo


def _declared_thermo(session: Session, *, prefix: str) -> Thermo:
    request = ThermoUploadRequest(
        enthalpy_reference_kind="formation_298k",
        species_entry={
            "smiles": "O",
            "charge": 0,
            "multiplicity": 1,
        },
        scientific_origin="computed",
        h298_kj_mol=-241.8,
        s298_j_mol_k=188.8,
        h298_uncertainty_kj_mol=0.5,
        s298_uncertainty_j_mol_k=0.2,
        tmin_k=200.0,
        tmax_k=3000.0,
        note=f"declared-{prefix}",
    )
    thermo = persist_thermo_upload(session, request)
    session.flush()
    return thermo


# ---------------------------------------------------------------------------
# Shape 1 -- legacy scalar-only: pruned, not omitted
# ---------------------------------------------------------------------------


def test_legacy_scalar_only_enthalpy_is_pruned_and_reimports(db_session) -> None:
    thermo = _legacy_scalar_thermo(db_session, prefix="ENTHEXPSCAL")

    result = export_thermo_bundle(
        db_session,
        thermo_ids=[thermo.id],
        title="legacy scalar export",
        summary="A legacy scalar-only thermo with no declaration.",
        exporter_label="tester",
    )

    # Reported, named by public ref -- never the row id.
    assert len(result.omissions) == 1
    omission = result.omissions[0]
    assert omission.action == "enthalpy_pruned"
    assert omission.ref == thermo.public_ref
    assert omission.detail  # a reason is always given, never a blank report

    # Still exported: the enthalpy is dropped, the rest of the record is not.
    assert result.bundle is not None
    assert len(result.bundle.records.thermo_uploads) == 1
    upload = result.bundle.records.thermo_uploads[0]
    assert upload.h298_kj_mol is None
    assert upload.h298_uncertainty_kj_mol is None
    assert upload.enthalpy_reference_kind is None
    assert upload.s298_j_mol_k == pytest.approx(186.3)
    assert upload.tmin_k == pytest.approx(200.0)
    assert upload.tmax_k == pytest.approx(2000.0)

    # And the pruned payload is exactly what the importer accepts.
    reimported = persist_thermo_upload(db_session, upload)
    assert reimported.h298_kj_mol is None
    assert reimported.enthalpy_reference_kind is None
    assert reimported.s298_j_mol_k == pytest.approx(186.3)


# ---------------------------------------------------------------------------
# Shape 2 -- legacy fit-only: omitted and reported, never exported broken
# ---------------------------------------------------------------------------


def test_legacy_fit_only_enthalpy_is_omitted_and_reported(db_session) -> None:
    thermo = _legacy_fit_thermo(db_session, prefix="ENTHEXPFIT")

    result = export_thermo_bundle(
        db_session,
        thermo_ids=[thermo.id],
        title="legacy fit export",
        summary="A legacy NASA-fit-only thermo with no declaration.",
        exporter_label="tester",
    )

    # Nothing left to export -- the fit IS the record's scientific content.
    assert result.bundle is None
    assert len(result.omissions) == 1
    omission = result.omissions[0]
    assert omission.action == "record_omitted"
    assert omission.ref == thermo.public_ref
    assert omission.detail  # a reason is always given, never a blank report


def test_mixed_selection_keeps_declared_and_reports_legacy_fit(db_session) -> None:
    """A partial omission: the declared record still exports; the legacy
    fit-only record is dropped and reported alongside it."""
    declared = _declared_thermo(db_session, prefix="ENTHEXPMIXDECL")
    legacy_fit = _legacy_fit_thermo(db_session, prefix="ENTHEXPMIXFIT")

    result = export_thermo_bundle(
        db_session,
        thermo_ids=[declared.id, legacy_fit.id],
        title="mixed export",
        summary="One declared, one legacy fit-only.",
        exporter_label="tester",
    )

    assert result.bundle is not None
    assert len(result.bundle.records.thermo_uploads) == 1
    upload = result.bundle.records.thermo_uploads[0]
    assert upload.enthalpy_reference_kind is not None
    assert upload.enthalpy_reference_kind == "formation_298k"

    assert len(result.omissions) == 1
    assert result.omissions[0].action == "record_omitted"
    assert result.omissions[0].ref == legacy_fit.public_ref

    # The declared record's export re-imports as-is.
    persist_thermo_upload(db_session, upload)


# ---------------------------------------------------------------------------
# Shape 3 -- declared: unaffected, round-trips unchanged
# ---------------------------------------------------------------------------


def test_declared_thermo_round_trips_unchanged(db_session) -> None:
    thermo = _declared_thermo(db_session, prefix="ENTHEXPDECL")

    result = export_thermo_bundle(
        db_session,
        thermo_ids=[thermo.id],
        title="declared export",
        summary="A properly declared thermo record.",
        exporter_label="tester",
    )

    assert result.omissions == []
    assert result.bundle is not None
    upload = result.bundle.records.thermo_uploads[0]
    assert upload.enthalpy_reference_kind is not None
    assert upload.enthalpy_reference_kind == "formation_298k"
    assert upload.h298_kj_mol == pytest.approx(-241.8)
    assert upload.h298_uncertainty_kj_mol == pytest.approx(0.5)
    assert upload.s298_j_mol_k == pytest.approx(188.8)

    reimported = persist_thermo_upload(db_session, upload)
    assert reimported.h298_kj_mol == pytest.approx(-241.8)
    assert reimported.enthalpy_reference_kind is not None
    assert reimported.enthalpy_reference_kind == "formation_298k"


# ---------------------------------------------------------------------------
# The Phase A contract inventory must count an omitted legacy fit as a
# genuine incompatibility, not silently pass it as creatable.
# ---------------------------------------------------------------------------


def test_contract_inventory_flags_legacy_fit_omission(db_session) -> None:
    from app.services.thermo_contract_inventory import iter_thermo_contract_inventory

    thermo = _legacy_fit_thermo(db_session, prefix="ENTHEXPINV")
    db_session.flush()

    inventory = list(iter_thermo_contract_inventory(db_session))
    row = next(r for r in inventory if r["ref"] == thermo.public_ref)
    assert row["upload_incompatibilities"]
    assert "record_omitted" in row["upload_incompatibilities"][0]


def test_contract_inventory_does_not_flag_pruned_scalar(db_session) -> None:
    """A prunable legacy row still produces a valid upload payload, so it
    must not be counted among this inventory's *creation* incompatibilities
    -- unlike the fit-only row above, which genuinely cannot be created.

    (The row may still appear for an unrelated reason -- e.g. it has no
    ``phase``/``reference_pressure_bar``, a CHEMKIN-export gap this
    inventory also reports -- so this checks the *reason*, not absence.)
    """
    from app.services.thermo_contract_inventory import iter_thermo_contract_inventory

    thermo = _legacy_scalar_thermo(db_session, prefix="ENTHEXPINVSCAL")
    db_session.flush()

    inventory = list(iter_thermo_contract_inventory(db_session))
    row = next((r for r in inventory if r["ref"] == thermo.public_ref), None)
    if row is not None:
        assert not row["upload_incompatibilities"]
