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

Point enthalpies and Gibbs energies are pruned like the scalar. A point left
with no value is dropped on its own and its temperature reported (#556); the
record is omitted only when no point, fit or scalar survives.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, ScientificOriginKind, SubmissionRecordType, ThermoModelKind
from app.db.models.thermo import Thermo, ThermoPoint
from app.schemas.workflows.contribution_bundle import ContributionBundleV0
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.contribution_bundle_export import export_thermo_bundle
from app.workflows.contribution_bundle_submit import submit_contribution_bundle
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
# Legacy point Gibbs energies: pruned exactly like point enthalpies
#
# A tabulated G is H(T) - T*S(T) on the record's enthalpy zero, so the import
# rule counts it as enthalpy content. A legacy row carrying G with no
# declaration was a shape the importer accepted when it was deposited and
# refuses now; exporting it verbatim would hand back a bundle this server
# will not re-import. G is independently optional on a point, so it is
# dropped the way a point H is, and the rest of each point survives.
# ---------------------------------------------------------------------------


def _legacy_point_thermo(
    session: Session,
    *,
    prefix: str,
    points: list[dict],
    s298_j_mol_k: float | None = 188.8,
    model_kind: ThermoModelKind | None = None,
) -> Thermo:
    """Points with H and/or G, no declaration, no h298.

    A plain insert: the DB guard fires only on ``thermo.h298_kj_mol`` /
    ``thermo.enthalpy_reference_kind``, and point H and G live in the child
    ``thermo_point`` table -- no trigger governs them.
    """
    species = make_species(session, inchi_key=next_inchi_key(prefix))
    entry = make_species_entry(session, species)
    thermo = Thermo(
        species_entry_id=entry.id,
        scientific_origin=ScientificOriginKind.computed,
        s298_j_mol_k=s298_j_mol_k,
        model_kind=model_kind,
    )
    session.add(thermo)
    session.flush()
    for point in points:
        session.add(ThermoPoint(thermo_id=thermo.id, **point))
    session.flush()
    session.refresh(thermo)
    assert thermo.enthalpy_reference_kind is None
    assert thermo.h298_kj_mol is None
    assert any(p.h_kj_mol is not None or p.g_kj_mol is not None for p in thermo.points)
    return thermo


_legacy_gibbs_thermo = _legacy_point_thermo


def test_legacy_point_gibbs_is_pruned_and_reimports(db_session) -> None:
    thermo = _legacy_gibbs_thermo(
        db_session,
        prefix="ENTHEXPGIBBS",
        points=[
            {"temperature_k": 300.0, "s_j_mol_k": 188.9, "g_kj_mol": -298.5},
            {"temperature_k": 500.0, "cp_j_mol_k": 35.2, "s_j_mol_k": 206.5, "g_kj_mol": -338.0},
        ],
    )

    result = export_thermo_bundle(
        db_session,
        thermo_ids=[thermo.id],
        title="legacy gibbs export",
        summary="A legacy thermo with point Gibbs energies and no declaration.",
        exporter_label="tester",
    )

    assert [(o.action, o.ref) for o in result.omissions] == [("enthalpy_pruned", thermo.public_ref)]
    assert "Gibbs" in result.omissions[0].detail
    assert result.bundle is not None
    [upload] = result.bundle.records.thermo_uploads
    assert upload.enthalpy_reference_kind is None
    exported = sorted(
        (p.temperature_k, p.cp_j_mol_k, p.s_j_mol_k, p.h_kj_mol, p.g_kj_mol) for p in upload.points
    )
    assert exported == [
        (300.0, None, 188.9, None, None),
        (500.0, 35.2, 206.5, None, None),
    ]
    assert upload.s298_j_mol_k == pytest.approx(188.8)

    # The importer accepts exactly what was exported.
    reimported = persist_thermo_upload(db_session, upload)
    db_session.flush()
    assert reimported.enthalpy_reference_kind is None
    assert sorted((p.temperature_k, p.g_kj_mol) for p in reimported.points) == [
        (300.0, None),
        (500.0, None),
    ]


def test_legacy_gibbs_only_points_are_dropped_and_s298_survives(db_session) -> None:
    """Points whose ONLY value is G are empty once G is pruned. Until #556
    that omitted the whole record, losing its s298 with a report that
    nothing meaningful remained. Now only the empty point is dropped, its
    temperature is reported, and the record exports with its s298."""
    thermo = _legacy_gibbs_thermo(
        db_session,
        prefix="ENTHEXPGONLY",
        points=[{"temperature_k": 300.0, "g_kj_mol": -298.5}],
    )

    result = export_thermo_bundle(
        db_session,
        thermo_ids=[thermo.id],
        title="legacy gibbs-only export",
        summary="A legacy thermo whose points carry only G.",
        exporter_label="tester",
    )

    assert [(o.action, o.ref, o.points_dropped_at_k) for o in result.omissions] == [
        ("enthalpy_pruned", thermo.public_ref, (300.0,))
    ]
    assert result.bundle is not None
    [upload] = result.bundle.records.thermo_uploads
    assert upload.points == []
    assert upload.s298_j_mol_k == pytest.approx(188.8)
    reimported = persist_thermo_upload(db_session, upload)
    db_session.flush()
    assert reimported.points == []
    assert reimported.s298_j_mol_k == pytest.approx(188.8)


# ---------------------------------------------------------------------------
# #556 -- pruning that empties SOME points drops those points, not the record
#
# A point emptied by the prune fails the point schema. Dropping just that
# point loses nothing the record still holds; omitting the whole record threw
# away every surviving S/Cp point with it. The record is omitted only when no
# point, fit or scalar survives.
# ---------------------------------------------------------------------------


def _round_trip_through_bundle_import(db_session: Session, bundle: ContributionBundleV0) -> Thermo:
    """JSON-dump the bundle the way the CLI writes it, then submit it through
    the real hosted importer (dry-run gate, then persist)."""
    reloaded = ContributionBundleV0.model_validate(bundle.model_dump(mode="json"))
    actor = AppUser(username=f"enthexp-{uuid4().hex[:10]}", role=AppUserRole.user)
    db_session.add(actor)
    db_session.flush()
    submitted = submit_contribution_bundle(db_session, reloaded, actor=actor)
    [thermo_id] = [
        r.record_id for r in submitted.records if r.record_type.value == SubmissionRecordType.thermo.value
    ]
    db_session.flush()
    imported = db_session.get(Thermo, thermo_id)
    assert imported is not None
    db_session.refresh(imported)
    return imported


def _point_rows(points) -> list[tuple]:
    return sorted((p.temperature_k, p.cp_j_mol_k, p.h_kj_mol, p.s_j_mol_k, p.g_kj_mol) for p in points)


def test_legacy_mixed_points_keep_survivors_and_round_trip(db_session) -> None:
    """H-only and G-only points beside S/Cp points, and no scalar at all:
    the S/Cp points are the record's only surviving content and must export."""
    thermo = _legacy_point_thermo(
        db_session,
        prefix="ENTHEXPMIXPTS",
        s298_j_mol_k=None,
        model_kind=ThermoModelKind.tabulated,
        points=[
            {"temperature_k": 300.0, "h_kj_mol": -74.5},
            {"temperature_k": 400.0, "h_kj_mol": -70.1, "s_j_mol_k": 197.4},
            {"temperature_k": 500.0, "cp_j_mol_k": 46.3, "s_j_mol_k": 207.2},
            {"temperature_k": 600.0, "g_kj_mol": -205.0},
        ],
    )

    result = export_thermo_bundle(
        db_session,
        thermo_ids=[thermo.id],
        title="legacy mixed-points export",
        summary="A legacy thermo with H-only, G-only and S/Cp points.",
        exporter_label="tester",
    )

    [omission] = result.omissions
    assert (omission.action, omission.ref) == ("enthalpy_pruned", thermo.public_ref)
    assert omission.points_dropped_at_k == (300.0, 600.0)
    assert omission.to_dict()["points_dropped_at_k"] == [300.0, 600.0]
    assert "300.0, 600.0 K" in omission.detail
    assert result.bundle is not None
    [upload] = result.bundle.records.thermo_uploads
    assert upload.model_kind == ThermoModelKind.tabulated
    assert upload.enthalpy_reference_kind is None
    assert _point_rows(upload.points) == [
        (400.0, None, None, 197.4, None),
        (500.0, 46.3, None, 207.2, None),
    ]

    imported = _round_trip_through_bundle_import(db_session, result.bundle)
    assert imported.id != thermo.id
    assert imported.enthalpy_reference_kind is None
    assert imported.model_kind == ThermoModelKind.tabulated
    assert _point_rows(imported.points) == [
        (400.0, None, None, 197.4, None),
        (500.0, 46.3, None, 207.2, None),
    ]


def test_legacy_tabulated_record_losing_every_point_exports_its_scalar(db_session) -> None:
    """Every point emptied, but s298 survives: the record is not omitted.
    Its stored ``model_kind='tabulated'`` no longer describes what is
    exported (the upload schema refuses tabulated with no points), so it is
    left for the importer to infer, and the report says so."""
    thermo = _legacy_point_thermo(
        db_session,
        prefix="ENTHEXPTABALL",
        model_kind=ThermoModelKind.tabulated,
        points=[
            {"temperature_k": 300.0, "h_kj_mol": -74.5},
            {"temperature_k": 500.0, "g_kj_mol": -310.0},
        ],
    )

    result = export_thermo_bundle(
        db_session,
        thermo_ids=[thermo.id],
        title="legacy tabulated export",
        summary="A legacy tabulated thermo whose points are all enthalpy.",
        exporter_label="tester",
    )

    [omission] = result.omissions
    assert (omission.action, omission.ref, omission.points_dropped_at_k) == (
        "enthalpy_pruned",
        thermo.public_ref,
        (300.0, 500.0),
    )
    assert "model_kind 'tabulated'" in omission.detail
    assert result.bundle is not None
    [upload] = result.bundle.records.thermo_uploads
    assert upload.points == []
    assert upload.model_kind is None
    assert upload.s298_j_mol_k == pytest.approx(188.8)

    imported = _round_trip_through_bundle_import(db_session, result.bundle)
    assert imported.points == []
    assert imported.model_kind == ThermoModelKind.scalar
    assert imported.s298_j_mol_k == pytest.approx(188.8)


def test_legacy_gibbs_only_record_with_nothing_else_is_omitted_truthfully(db_session) -> None:
    """G-only points, no scalar, no fit: every value the record holds is
    undeclared enthalpy content, so nothing survives and the record is
    omitted -- with a message saying exactly that, not a schema error."""
    thermo = _legacy_point_thermo(
        db_session,
        prefix="ENTHEXPGNOTHING",
        s298_j_mol_k=None,
        points=[
            {"temperature_k": 300.0, "g_kj_mol": -298.5},
            {"temperature_k": 500.0, "g_kj_mol": -338.0},
        ],
    )

    result = export_thermo_bundle(
        db_session,
        thermo_ids=[thermo.id],
        title="legacy gibbs-only export",
        summary="A legacy thermo that holds nothing but G.",
        exporter_label="tester",
    )

    assert result.bundle is None
    [omission] = result.omissions
    assert (omission.action, omission.ref, omission.points_dropped_at_k) == (
        "record_omitted",
        thermo.public_ref,
        (300.0, 500.0),
    )
    assert "no point, fit or scalar value remains" in omission.detail
    assert "point g_kj_mol at 300.0, 500.0 K" in omission.detail
    # The reason is stated in words, not as a leaked validation error.
    assert "validation error" not in omission.detail


def test_legacy_nasa_fit_with_h_only_points_is_omitted_whole(db_session) -> None:
    """The existing NASA rule wins over point pruning: an undeclared NASA
    fit's enthalpy cannot be dropped without destroying the fit, so the whole
    record is omitted, even though its H-only points alone could have been
    dropped. Confirmed and kept by #556."""
    thermo = _legacy_fit_thermo(db_session, prefix="ENTHEXPNASAPTS")
    for temperature, h in ((300.0, -74.5), (500.0, -66.0)):
        db_session.add(ThermoPoint(thermo_id=thermo.id, temperature_k=temperature, h_kj_mol=h))
    db_session.flush()
    db_session.refresh(thermo)

    result = export_thermo_bundle(
        db_session,
        thermo_ids=[thermo.id],
        title="legacy nasa + points export",
        summary="A legacy NASA fit with H-only points and no declaration.",
        exporter_label="tester",
    )

    assert result.bundle is None
    [omission] = result.omissions
    assert (omission.action, omission.ref, omission.points_dropped_at_k) == (
        "record_omitted",
        thermo.public_ref,
        (),
    )
    assert omission.detail.startswith("carries a NASA fit enthalpy with no enthalpy_reference_kind")
    assert "points_dropped_at_k" not in omission.to_dict()


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
