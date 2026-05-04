"""Local contribution bundle export service.

This module turns selected local database rows (``Thermo``, ``Kinetics``)
into validated :class:`ContributionBundleV0` payloads suitable for writing
to disk.

Scope (Local export v0, see ``docs/roadmaps/local-bundle-export-v0-spec.md``):

- Read-only against the local database.
- Reconstruct upload-equivalent payloads (``ThermoUploadRequest`` /
  ``KineticsUploadRequest``) and embed them in a ``ContributionBundleV0``.
- Validate the assembled bundle before returning it; never produce an
  invalid bundle.
- Fail clearly when required dependency data is missing instead of
  silently emitting an incomplete bundle.

Out of scope: hosted import, network transfer, raw DB sync, artifact
packaging, public API routes. The service is consumed by the
``scripts/export_contribution_bundle.py`` CLI wrapper.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db.models.common import ActivationEnergyUnits, ReactionRole
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
)
from app.db.models.species import SpeciesEntry
from app.db.models.thermo import Thermo, ThermoNASA, ThermoPoint
from app.schemas.workflows.contribution_bundle import (
    BUNDLE_FORMAT,
    BUNDLE_VERSION,
    BundleExporter,
    BundleKind,
    BundleLocalRefEntry,
    BundleLocalRefRecordType,
    BundleManifest,
    BundleRecordSet,
    BundleSourceInstance,
    BundleSourceInstanceKind,
    BundleSubmissionMetadata,
    BundleSubmissionSourceKind,
    ContributionBundleV0,
)


# Schema version of the local DB at the time of writing. The local export
# stamps this into the bundle so a future hosted importer can refuse
# bundles that target a schema it does not yet know how to ingest. Bumped
# alongside the next initial-migration revision.
DEFAULT_SCHEMA_VERSION = "d861dfd60891"

DEFAULT_INSTANCE_NAME = "local-tckdb"


class ContributionBundleExportError(ValueError):
    """Raised when a local record cannot be exported as a contribution bundle.

    Used for both "root not found" and "dependency closure incomplete"
    failures so the CLI can surface a single, actionable error class.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_thermo_bundle(
    session: Session,
    *,
    thermo_ids: Sequence[int],
    title: str,
    summary: str,
    exporter_label: str,
    instance_name: str = DEFAULT_INSTANCE_NAME,
    instance_kind: BundleSourceInstanceKind = BundleSourceInstanceKind.local,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    software_version: str | None = None,
    orcid: str | None = None,
    affiliation: str | None = None,
    email: str | None = None,
    exporter_notes: str | None = None,
    submission_source_kind: BundleSubmissionSourceKind = (
        BundleSubmissionSourceKind.local_bundle
    ),
) -> ContributionBundleV0:
    """Export selected thermo rows as a validated thermo contribution bundle.

    :param session: Active read-only SQLAlchemy session.
    :param thermo_ids: One or more local ``thermo.id`` values to export.
    :raises ContributionBundleExportError: If any id is missing or any
        required dependency cannot be reconstructed.
    """
    if not thermo_ids:
        raise ContributionBundleExportError(
            "At least one thermo_id is required to export a thermo bundle."
        )

    thermo_rows = _load_thermo_rows(session, thermo_ids)
    thermo_uploads = [_thermo_to_upload(row) for row in thermo_rows]

    local_refs: dict[str, BundleLocalRefEntry] = {}
    for row in thermo_rows:
        _record_thermo_local_refs(local_refs, row)

    return _build_and_validate_bundle(
        bundle_kind=BundleKind.thermo,
        thermo_uploads=thermo_uploads,
        kinetics_uploads=[],
        local_refs=local_refs,
        title=title,
        summary=summary,
        submission_source_kind=submission_source_kind,
        exporter_label=exporter_label,
        orcid=orcid,
        affiliation=affiliation,
        email=email,
        exporter_notes=exporter_notes,
        instance_name=instance_name,
        instance_kind=instance_kind,
        schema_version=schema_version,
        software_version=software_version,
    )


def export_kinetics_bundle(
    session: Session,
    *,
    kinetics_ids: Sequence[int],
    title: str,
    summary: str,
    exporter_label: str,
    instance_name: str = DEFAULT_INSTANCE_NAME,
    instance_kind: BundleSourceInstanceKind = BundleSourceInstanceKind.local,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    software_version: str | None = None,
    orcid: str | None = None,
    affiliation: str | None = None,
    email: str | None = None,
    exporter_notes: str | None = None,
    submission_source_kind: BundleSubmissionSourceKind = (
        BundleSubmissionSourceKind.local_bundle
    ),
) -> ContributionBundleV0:
    """Export selected kinetics rows as a validated kinetics contribution bundle."""
    if not kinetics_ids:
        raise ContributionBundleExportError(
            "At least one kinetics_id is required to export a kinetics bundle."
        )

    kinetics_rows = _load_kinetics_rows(session, kinetics_ids)
    kinetics_uploads = [_kinetics_to_upload(row) for row in kinetics_rows]

    local_refs: dict[str, BundleLocalRefEntry] = {}
    for row in kinetics_rows:
        _record_kinetics_local_refs(local_refs, row)

    return _build_and_validate_bundle(
        bundle_kind=BundleKind.kinetics,
        thermo_uploads=[],
        kinetics_uploads=kinetics_uploads,
        local_refs=local_refs,
        title=title,
        summary=summary,
        submission_source_kind=submission_source_kind,
        exporter_label=exporter_label,
        orcid=orcid,
        affiliation=affiliation,
        email=email,
        exporter_notes=exporter_notes,
        instance_name=instance_name,
        instance_kind=instance_kind,
        schema_version=schema_version,
        software_version=software_version,
    )


# ---------------------------------------------------------------------------
# Loading + dependency-closure checks
# ---------------------------------------------------------------------------


def _load_thermo_rows(session: Session, ids: Iterable[int]) -> list[Thermo]:
    rows: list[Thermo] = []
    for thermo_id in ids:
        row = session.get(Thermo, thermo_id)
        if row is None:
            raise ContributionBundleExportError(
                f"Cannot export thermo_id={thermo_id}: no such thermo row."
            )
        if row.species_entry is None or row.species_entry.species is None:
            raise ContributionBundleExportError(
                f"Cannot export thermo_id={thermo_id}: missing species entry "
                "or species identity needed to build upload payload."
            )
        rows.append(row)
    return rows


def _load_kinetics_rows(session: Session, ids: Iterable[int]) -> list[Kinetics]:
    rows: list[Kinetics] = []
    for kinetics_id in ids:
        row = session.get(Kinetics, kinetics_id)
        if row is None:
            raise ContributionBundleExportError(
                f"Cannot export kinetics_id={kinetics_id}: no such kinetics row."
            )
        entry = row.reaction_entry
        if entry is None or entry.reaction is None:
            raise ContributionBundleExportError(
                f"Cannot export kinetics_id={kinetics_id}: missing reaction "
                "entry or chem reaction needed to build upload payload."
            )
        if not entry.structure_participants:
            raise ContributionBundleExportError(
                f"Cannot export kinetics_id={kinetics_id}: reaction entry has "
                "no structure participants; nothing to export as reactants/products."
            )
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Thermo conversion
# ---------------------------------------------------------------------------


def _thermo_to_upload(thermo: Thermo) -> dict[str, Any]:
    """Reconstruct an upload-equivalent thermo dict from a ``Thermo`` row.

    Returned as a plain ``dict`` so the bundle's ``ContributionBundleV0``
    constructor runs the full nested upload validators (the same ones a
    real API upload would hit).
    """
    payload: dict[str, Any] = {
        "species_entry": _species_entry_payload(thermo.species_entry),
        "scientific_origin": thermo.scientific_origin.value,
        "h298_kj_mol": thermo.h298_kj_mol,
        "s298_j_mol_k": thermo.s298_j_mol_k,
        "h298_uncertainty_kj_mol": thermo.h298_uncertainty_kj_mol,
        "s298_uncertainty_j_mol_k": thermo.s298_uncertainty_j_mol_k,
        "tmin_k": thermo.tmin_k,
        "tmax_k": thermo.tmax_k,
        "note": thermo.note,
    }

    if thermo.points:
        payload["points"] = [_thermo_point_payload(p) for p in thermo.points]
    if thermo.nasa is not None:
        payload["nasa"] = _thermo_nasa_payload(thermo.nasa)

    literature = _literature_payload(thermo.literature)
    if literature is not None:
        payload["literature"] = literature
    software = _software_release_payload(thermo.software_release)
    if software is not None:
        payload["software_release"] = software
    workflow_tool = _workflow_tool_release_payload(thermo.workflow_tool_release)
    if workflow_tool is not None:
        payload["workflow_tool_release"] = workflow_tool

    return payload


def _thermo_point_payload(point: ThermoPoint) -> dict[str, Any]:
    return {
        "temperature_k": point.temperature_k,
        "cp_j_mol_k": point.cp_j_mol_k,
        "h_kj_mol": point.h_kj_mol,
        "s_j_mol_k": point.s_j_mol_k,
        "g_kj_mol": point.g_kj_mol,
    }


def _thermo_nasa_payload(nasa: ThermoNASA) -> dict[str, Any]:
    return {
        "t_low": nasa.t_low,
        "t_mid": nasa.t_mid,
        "t_high": nasa.t_high,
        "a1": nasa.a1, "a2": nasa.a2, "a3": nasa.a3, "a4": nasa.a4,
        "a5": nasa.a5, "a6": nasa.a6, "a7": nasa.a7,
        "b1": nasa.b1, "b2": nasa.b2, "b3": nasa.b3, "b4": nasa.b4,
        "b5": nasa.b5, "b6": nasa.b6, "b7": nasa.b7,
    }


# ---------------------------------------------------------------------------
# Kinetics conversion
# ---------------------------------------------------------------------------


def _kinetics_to_upload(kinetics: Kinetics) -> dict[str, Any]:
    """Reconstruct an upload-equivalent kinetics dict from a ``Kinetics`` row."""
    entry = kinetics.reaction_entry
    chem_reaction = entry.reaction

    reactants_payload = [
        _kinetics_participant_payload(p)
        for p in entry.structure_participants
        if p.role == ReactionRole.reactant
    ]
    products_payload = [
        _kinetics_participant_payload(p)
        for p in entry.structure_participants
        if p.role == ReactionRole.product
    ]

    if not reactants_payload or not products_payload:
        raise ContributionBundleExportError(
            f"Cannot export kinetics_id={kinetics.id}: reaction entry "
            f"id={entry.id} is missing reactants or products."
        )

    reaction_payload: dict[str, Any] = {
        "reversible": chem_reaction.reversible,
        "reactants": reactants_payload,
        "products": products_payload,
    }
    family_payload = _reaction_family_payload(chem_reaction)
    reaction_payload.update(family_payload)

    payload: dict[str, Any] = {
        "reaction": reaction_payload,
        "scientific_origin": kinetics.scientific_origin.value,
        "model_kind": kinetics.model_kind.value,
        "a": kinetics.a,
        "a_units": kinetics.a_units.value if kinetics.a_units is not None else None,
        "n": kinetics.n,
        "a_uncertainty": kinetics.a_uncertainty,
        "a_uncertainty_kind": (
            kinetics.a_uncertainty_kind.value
            if kinetics.a_uncertainty_kind is not None
            else None
        ),
        "n_uncertainty": kinetics.n_uncertainty,
        "tmin_k": kinetics.tmin_k,
        "tmax_k": kinetics.tmax_k,
        "degeneracy": kinetics.degeneracy,
        "tunneling_model": kinetics.tunneling_model,
        "note": kinetics.note,
    }

    # Round-trip the canonical ea_kj_mol back to the (reported_ea,
    # reported_ea_units) pair the upload schema requires together.
    if kinetics.ea_kj_mol is not None:
        payload["reported_ea"] = kinetics.ea_kj_mol
        payload["reported_ea_units"] = ActivationEnergyUnits.kj_mol.value
        if kinetics.ea_uncertainty_kj_mol is not None:
            payload["d_reported_ea"] = kinetics.ea_uncertainty_kj_mol

    literature = _literature_payload(kinetics.literature)
    if literature is not None:
        payload["literature"] = literature
    software = _software_release_payload(kinetics.software_release)
    if software is not None:
        payload["software_release"] = software
    workflow_tool = _workflow_tool_release_payload(kinetics.workflow_tool_release)
    if workflow_tool is not None:
        payload["workflow_tool_release"] = workflow_tool

    return payload


def _kinetics_participant_payload(
    participant: ReactionEntryStructureParticipant,
) -> dict[str, Any]:
    species_entry = participant.species_entry
    if species_entry is None or species_entry.species is None:
        raise ContributionBundleExportError(
            "Reaction participant is missing species-entry identity needed "
            "to build kinetics upload payload."
        )
    payload: dict[str, Any] = {
        "species_entry": _species_entry_payload(species_entry),
    }
    if participant.note:
        payload["note"] = participant.note
    return payload


def _reaction_family_payload(chem_reaction: ChemReaction) -> dict[str, Any]:
    """Return the reaction-family fields, if any, in upload-schema shape.

    The upload validator requires ``reaction_family_source_note`` whenever
    a non-canonical ``reaction_family`` is supplied. The DB enforces the
    same coupling for ``reaction_family_raw`` via a CHECK constraint, so
    the only cases we can hit from a valid DB row are:

    * canonical family (``reaction_family`` relation set, no raw override)
    * raw family + source note
    * neither
    """
    family = chem_reaction.reaction_family
    raw = chem_reaction.reaction_family_raw
    source_note = chem_reaction.reaction_family_source_note

    if raw is not None:
        # CHECK constraint guarantees source_note is non-null here.
        return {
            "reaction_family": raw,
            "reaction_family_source_note": source_note,
        }
    if family is not None:
        return {"reaction_family": family.name}
    return {}


# ---------------------------------------------------------------------------
# Shared identity / provenance fragment builders
# ---------------------------------------------------------------------------


def _species_entry_payload(species_entry: SpeciesEntry) -> dict[str, Any]:
    species = species_entry.species
    payload: dict[str, Any] = {
        "molecule_kind": species.kind.value,
        "smiles": species.smiles,
        "charge": species.charge,
        "multiplicity": species.multiplicity,
        "species_entry_kind": species_entry.kind.value,
        "stereo_kind": species.stereo_kind.value,
        "electronic_state_kind": species_entry.electronic_state_kind.value,
    }
    if species_entry.unmapped_smiles is not None:
        payload["unmapped_smiles"] = species_entry.unmapped_smiles
    if species_entry.stereo_label is not None:
        payload["stereo_label"] = species_entry.stereo_label
    if species_entry.electronic_state_label is not None:
        payload["electronic_state_label"] = species_entry.electronic_state_label
    if species_entry.term_symbol_raw is not None:
        payload["term_symbol_raw"] = species_entry.term_symbol_raw
    if species_entry.term_symbol is not None:
        payload["term_symbol"] = species_entry.term_symbol
    if species_entry.isotopologue_label is not None:
        payload["isotopologue_label"] = species_entry.isotopologue_label
    return payload


def _literature_payload(literature) -> dict[str, Any] | None:
    if literature is None:
        return None
    payload: dict[str, Any] = {"kind": literature.kind.value}
    if literature.title is not None:
        payload["title"] = literature.title
    if literature.journal is not None:
        payload["journal"] = literature.journal
    if literature.year is not None:
        payload["year"] = literature.year
    if literature.volume is not None:
        payload["volume"] = literature.volume
    if literature.issue is not None:
        payload["issue"] = literature.issue
    if literature.pages is not None:
        payload["pages"] = literature.pages
    if literature.doi is not None:
        payload["doi"] = literature.doi
    if literature.isbn is not None:
        payload["isbn"] = literature.isbn
    if literature.url is not None:
        payload["url"] = literature.url
    if literature.publisher is not None:
        payload["publisher"] = literature.publisher
    if literature.institution is not None:
        payload["institution"] = literature.institution
    return payload


def _software_release_payload(release) -> dict[str, Any] | None:
    if release is None:
        return None
    payload: dict[str, Any] = {"name": release.software.name}
    if release.version is not None:
        payload["version"] = release.version
    if release.revision is not None:
        payload["revision"] = release.revision
    if release.build is not None:
        payload["build"] = release.build
    if release.release_date is not None:
        payload["release_date"] = release.release_date.isoformat()
    if release.notes is not None:
        payload["notes"] = release.notes
    return payload


def _workflow_tool_release_payload(release) -> dict[str, Any] | None:
    if release is None:
        return None
    payload: dict[str, Any] = {"name": release.workflow_tool.name}
    if release.version is not None:
        payload["version"] = release.version
    if release.git_commit is not None:
        payload["git_commit"] = release.git_commit
    if release.release_date is not None:
        payload["release_date"] = release.release_date.isoformat()
    if release.notes is not None:
        payload["notes"] = release.notes
    return payload


# ---------------------------------------------------------------------------
# Local refs
# ---------------------------------------------------------------------------


def _record_thermo_local_refs(
    refs: dict[str, BundleLocalRefEntry], thermo: Thermo
) -> None:
    refs[f"thermo:t{thermo.id}"] = BundleLocalRefEntry(
        record_type=BundleLocalRefRecordType.thermo,
        label=f"t{thermo.id}",
        note="Local DB id for traceability only; not a hosted identity.",
    )
    species_entry = thermo.species_entry
    refs[f"species_entry:se{species_entry.id}"] = BundleLocalRefEntry(
        record_type=BundleLocalRefRecordType.species_entry,
        label=f"se{species_entry.id}",
    )
    species = species_entry.species
    refs[f"species:s{species.id}"] = BundleLocalRefEntry(
        record_type=BundleLocalRefRecordType.species,
        label=f"s{species.id}",
    )


def _record_kinetics_local_refs(
    refs: dict[str, BundleLocalRefEntry], kinetics: Kinetics
) -> None:
    refs[f"kinetics:k{kinetics.id}"] = BundleLocalRefEntry(
        record_type=BundleLocalRefRecordType.kinetics,
        label=f"k{kinetics.id}",
        note="Local DB id for traceability only; not a hosted identity.",
    )
    entry = kinetics.reaction_entry
    refs[f"reaction:r{entry.reaction_id}"] = BundleLocalRefEntry(
        record_type=BundleLocalRefRecordType.reaction,
        label=f"r{entry.reaction_id}",
    )
    seen_species: set[int] = set()
    seen_entries: set[int] = set()
    for participant in entry.structure_participants:
        species_entry = participant.species_entry
        if species_entry is None:
            continue
        if species_entry.id not in seen_entries:
            seen_entries.add(species_entry.id)
            refs[f"species_entry:se{species_entry.id}"] = BundleLocalRefEntry(
                record_type=BundleLocalRefRecordType.species_entry,
                label=f"se{species_entry.id}",
            )
        species = species_entry.species
        if species is not None and species.id not in seen_species:
            seen_species.add(species.id)
            refs[f"species:s{species.id}"] = BundleLocalRefEntry(
                record_type=BundleLocalRefRecordType.species,
                label=f"s{species.id}",
            )


# ---------------------------------------------------------------------------
# Bundle assembly + validation
# ---------------------------------------------------------------------------


def _build_and_validate_bundle(
    *,
    bundle_kind: BundleKind,
    thermo_uploads: list[dict[str, Any]],
    kinetics_uploads: list[dict[str, Any]],
    local_refs: dict[str, BundleLocalRefEntry],
    title: str,
    summary: str,
    submission_source_kind: BundleSubmissionSourceKind,
    exporter_label: str,
    orcid: str | None,
    affiliation: str | None,
    email: str | None,
    exporter_notes: str | None,
    instance_name: str,
    instance_kind: BundleSourceInstanceKind,
    schema_version: str,
    software_version: str | None,
) -> ContributionBundleV0:
    source_instance = BundleSourceInstance(
        instance_kind=instance_kind,
        instance_name=instance_name,
        schema_version=schema_version,
        software_version=software_version,
    )
    exporter = BundleExporter(
        local_user_label=exporter_label,
        orcid=orcid,
        affiliation=affiliation,
        email=email,
        notes=exporter_notes,
    )
    submission = BundleSubmissionMetadata(
        title=title,
        summary=summary,
        source_kind=submission_source_kind,
    )
    manifest = BundleManifest(sha256=None, files=[])

    try:
        return ContributionBundleV0(
            bundle_format=BUNDLE_FORMAT,
            bundle_version=BUNDLE_VERSION,
            bundle_kind=bundle_kind,
            created_at=datetime.now(timezone.utc),
            source_instance=source_instance,
            exporter=exporter,
            submission=submission,
            records=BundleRecordSet(
                thermo_uploads=thermo_uploads,
                kinetics_uploads=kinetics_uploads,
            ),
            local_refs=local_refs,
            manifest=manifest,
        )
    except ValidationError as exc:
        raise ContributionBundleExportError(
            "Assembled contribution bundle failed schema validation; "
            f"export aborted to avoid writing an invalid bundle. Details: {exc}"
        ) from exc
