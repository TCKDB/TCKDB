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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session
from tckdb_schemas.enthalpy_reference import (
    W_ENTHALPY_DECLARATION_ABSENT,
    enthalpy_reference_error,
)
from tckdb_schemas.rights import DepositRights

from app.db.models.common import (
    ActivationEnergyUnits,
    ReactionRole,
    SubmissionRecordType,
    ThermoModelKind,
    ThermoTargetKind,
)
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntryStructureParticipant,
)
from app.db.models.species import SpeciesEntry
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoNASA9Interval,
    ThermoPoint,
    ThermoWilhoit,
)
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
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.release.record_rights import linked_rights

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
# Undeclared legacy enthalpy: report a gap, never emit a broken bundle
# ---------------------------------------------------------------------------
#
# A thermo row deposited before the enthalpy-reference-declaration rule
# existed can carry an enthalpy (a 298 K scalar, a NASA/NASA-9 fit, a
# Wilhoit h0, point enthalpies, or point Gibbs energies, which sit on the
# same zero) with no ``enthalpy_reference_kind``
# declared. The DB never backfills a declaration onto such a row (see
# ``e7b1c9d4a632``'s docstring), and the upload workflow's own rule
# (``tckdb_schemas.enthalpy_reference.enthalpy_reference_error``, the same
# function ``app.workflows.thermo.assert_enthalpy_reference`` calls) refuses
# to import that exact shape. Reusing it here -- rather than re-deriving
# "does this row have enthalpy content" independently -- is deliberate: the
# export's notion of "undeclared" can never drift from the importer's.
#
# Mirrors the ``ExportGap`` precedent in
# ``app.services.scientific_read.export``: report what could not be carried
# forward, named by public ref, instead of silently dropping it or emitting
# something the importer refuses.


@dataclass(frozen=True)
class BundleExportOmission:
    """A thermo record (or one of its enthalpy values) left out of a bundle.

    :param action: ``"record_omitted"`` -- the whole record was left out of
        the bundle, for one of two reasons. Either its enthalpy lives inside
        a NASA-7/NASA-9 fit: those coefficients are mandatory together
        (``ThermoNASACreate`` / ``ThermoNASA9IntervalCreate`` require every
        ``a``/``b`` coefficient, the enthalpy term included), so there is no
        way to drop only the enthalpy without destroying the whole fit.
        Or every value the record held was undeclared enthalpy content, so
        once that is dropped no point, fit or scalar value remains.
        ``"enthalpy_pruned"`` -- the record was still exported, with its
        undeclared enthalpy value(s) dropped (298 K scalar, point
        enthalpies, point Gibbs energies, and/or a Wilhoit ``h0_kj_mol``,
        each independently optional); entropy, heat capacity and every
        other field are unaffected. A tabulated point left with no value
        at all is dropped, and its temperature listed in
        ``points_dropped_at_k``.
        ``"declaration_pruned"`` -- the record was exported, but part of its
        target or protocol declaration was left out because a portable bundle
        cannot carry it: a ``single_conformer`` target names a conformer group
        of *this* database, and a protocol's supporting calculations are named
        by public ref to calculations of this database. Left out means absent,
        never replaced (a dropped single-conformer target does not become an
        equilibrium target). When the record also had an enthalpy pruned, the
        action stays ``"enthalpy_pruned"`` and the detail says both.
    :param ref: The record's public ref (``thermo.public_ref``) -- never a
        row id, so the report stays meaningful outside this DB instance.
    :param detail: Human-readable reason.
    :param points_dropped_at_k: Temperatures (K, ascending) of the tabulated
        points whose only values were undeclared enthalpy content, and which
        were therefore dropped. Empty when no point was dropped.
    """

    action: str
    ref: str
    detail: str
    points_dropped_at_k: tuple[float, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"action": self.action, "ref": self.ref, "detail": self.detail}
        if self.points_dropped_at_k:
            out["points_dropped_at_k"] = list(self.points_dropped_at_k)
        return out


@dataclass
class ThermoBundleExport:
    """Result of :func:`export_thermo_bundle`.

    ``bundle`` is ``None`` only when every selected thermo record was
    omitted entirely (see ``omissions``) and nothing legitimate remains to
    export -- callers should treat that the same as any other export
    failure, using ``omissions`` to explain why.
    """

    bundle: ContributionBundleV0 | None
    omissions: list[BundleExportOmission] = field(default_factory=list)


def _thermo_export_disposition(
    payload: dict[str, Any],
) -> tuple[str, str] | None:
    """Decide how an undeclared legacy enthalpy affects export of ``payload``.

    Returns ``None`` when there is nothing to do: the payload declares its
    enthalpy reference, or carries no enthalpy content at all. Otherwise
    returns ``(action, detail)`` -- see :class:`BundleExportOmission`.

    :raises ContributionBundleExportError: ``payload`` fails the shared rule
        for a reason other than a missing declaration (e.g. a declared but
        unrecognized ``enthalpy_reference_kind``). A row read back out of
        this DB should never hit those -- the enum only ever stores
        ``formation_298k`` and the DB trigger refuses a declaration with no
        content -- so this is a defensive "do not silently export something
        broken", not an expected path.
    """
    error = enthalpy_reference_error(payload)
    if error is None:
        return None
    code, message = error
    if code != W_ENTHALPY_DECLARATION_ABSENT:
        raise ContributionBundleExportError(
            f"thermo_upload_incompatible: unexpected enthalpy_reference_error "
            f"outcome ({code}): {message}"
        )
    if payload.get("nasa") is not None or payload.get("nasa9_intervals"):
        return (
            "record_omitted",
            "carries a NASA fit enthalpy with no enthalpy_reference_kind "
            "declared -- a legacy shape predating the declaration rule "
            "that the import workflow refuses. A NASA-7/NASA-9 fit's "
            "coefficients are mandatory together, so the enthalpy cannot "
            "be dropped without destroying the whole fit; the record is "
            "omitted from this bundle.",
        )
    return (
        "enthalpy_pruned",
        "carries a 298 K scalar, point, and/or Wilhoit enthalpy, or point "
        "Gibbs energies, with no enthalpy_reference_kind declared -- a legacy "
        "shape predating the declaration rule that the import workflow "
        "refuses. The enthalpy and Gibbs value(s) were dropped from this "
        "export; entropy, heat capacity and other fields are unaffected.",
    )


#: The point columns ``ThermoPointCreate`` requires at least one of.
_THERMO_POINT_VALUE_FIELDS = ("cp_j_mol_k", "h_kj_mol", "s_j_mol_k", "g_kj_mol")


@dataclass(frozen=True)
class _EnthalpyPrune:
    """What :func:`_prune_undeclared_enthalpy` removed beyond the values."""

    points_dropped_at_k: tuple[float, ...] = ()
    model_kind_cleared: bool = False


def _format_temperatures(temperatures: Iterable[float]) -> str:
    return ", ".join(str(t) for t in temperatures)


def _undeclared_enthalpy_values(payload: dict[str, Any]) -> list[str]:
    """Name each undeclared enthalpy value :func:`_prune_undeclared_enthalpy`
    is about to drop, for a truthful omission message."""
    names: list[str] = []
    if payload.get("h298_kj_mol") is not None:
        names.append("h298_kj_mol")
    wilhoit = payload.get("wilhoit")
    if wilhoit is not None and wilhoit.get("h0_kj_mol") is not None:
        names.append("wilhoit h0_kj_mol")
    points = payload.get("points") or []
    for column in ("h_kj_mol", "g_kj_mol"):
        temperatures = sorted(p["temperature_k"] for p in points if p.get(column) is not None)
        if temperatures:
            names.append(f"point {column} at {_format_temperatures(temperatures)} K")
    return names


def _has_scientific_content(payload: dict[str, Any]) -> bool:
    """Whether any point, fit or scalar value survives in ``payload``.

    Mirrors ``ThermoUploadRequest.validate_has_scientific_content``. Only
    used to word the omission truthfully: the payload is still validated
    against the real schema afterwards.
    """
    return bool(
        payload.get("h298_kj_mol") is not None
        or payload.get("s298_j_mol_k") is not None
        or payload.get("enthalpy_formation_0k_kj_mol") is not None
        or payload.get("nasa") is not None
        or payload.get("nasa9_intervals")
        or payload.get("wilhoit") is not None
        or payload.get("points")
    )


def _prune_undeclared_enthalpy(payload: dict[str, Any]) -> _EnthalpyPrune:
    """Drop the optional enthalpy sub-values ``_thermo_export_disposition``
    identified as this record's ONLY enthalpy content, in place.

    A tabulated point left with no value at all is dropped from
    ``payload["points"]``: the point schema refuses an empty point, and it
    holds nothing the record still needs. If that removes every point, the
    ``points`` key goes too, and a stored ``model_kind`` of ``tabulated``
    (which the upload schema refuses without points) is removed so the
    importer infers the kind from what remains. Both are reported in the
    returned :class:`_EnthalpyPrune`.

    Never touches ``nasa`` / ``nasa9_intervals`` -- callers only reach this
    for the ``"enthalpy_pruned"`` disposition, which never fires when either
    is present.
    """
    payload["h298_kj_mol"] = None
    payload["h298_uncertainty_kj_mol"] = None
    wilhoit = payload.get("wilhoit")
    if wilhoit is not None:
        payload["wilhoit"] = {**wilhoit, "h0_kj_mol": None}
    points = payload.get("points")
    if not points:
        return _EnthalpyPrune()
    # A point G is H(T) - T*S(T) on the same undeclared zero, and the shared
    # rule counts it as enthalpy content: keep it and the importer refuses
    # the bundle.
    kept: list[dict[str, Any]] = []
    emptied: list[float] = []
    for point in points:
        pruned = {**point, "h_kj_mol": None, "g_kj_mol": None}
        if any(pruned.get(name) is not None for name in _THERMO_POINT_VALUE_FIELDS):
            kept.append(pruned)
        else:
            emptied.append(pruned["temperature_k"])
    if not emptied:
        payload["points"] = kept
        return _EnthalpyPrune()
    dropped = tuple(sorted(emptied))
    model_kind_cleared = False
    if kept:
        payload["points"] = kept
    else:
        del payload["points"]
        if payload.get("model_kind") == ThermoModelKind.tabulated.value:
            del payload["model_kind"]
            model_kind_cleared = True
    return _EnthalpyPrune(points_dropped_at_k=dropped, model_kind_cleared=model_kind_cleared)


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
    rights: DepositRights | None = None,
) -> ThermoBundleExport:
    """Export selected thermo rows as a validated thermo contribution bundle.

    :param session: Active read-only SQLAlchemy session.
    :param thermo_ids: One or more local ``thermo.id`` values to export.
    :returns: A :class:`ThermoBundleExport`. ``omissions`` is non-empty when
        one or more selected rows carried a legacy undeclared enthalpy (see
        :class:`BundleExportOmission`); ``bundle`` is ``None`` only when
        every selected row was omitted outright and nothing remains to
        export.
    :raises ContributionBundleExportError: If any id is missing or any
        required dependency cannot be reconstructed.
    """
    if not thermo_ids:
        raise ContributionBundleExportError(
            "At least one thermo_id is required to export a thermo bundle."
        )

    thermo_rows = _load_thermo_rows(session, thermo_ids)

    thermo_uploads: list[dict[str, Any]] = []
    omissions: list[BundleExportOmission] = []
    local_refs: dict[str, BundleLocalRefEntry] = {}
    for row in thermo_rows:
        payload, omission = _thermo_to_upload(row)
        if omission is not None:
            omissions.append(omission)
        if payload is None:
            continue
        thermo_uploads.append(payload)
        _record_thermo_local_refs(local_refs, row)

    if not thermo_uploads:
        return ThermoBundleExport(bundle=None, omissions=omissions)

    bundle = _build_and_validate_bundle(
        bundle_kind=BundleKind.thermo,
        thermo_uploads=thermo_uploads,
        kinetics_uploads=[],
        local_refs=local_refs,
        title=title,
        summary=summary,
        submission_source_kind=submission_source_kind,
        rights=rights,
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
    return ThermoBundleExport(bundle=bundle, omissions=omissions)


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
    rights: DepositRights | None = None,
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
        rights=rights,
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
# Rights: what the source deposit stands under
# ---------------------------------------------------------------------------


def deposit_rights_for_records(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_ids: Sequence[int],
) -> DepositRights | None:
    """The ``rights`` fragment the exported records' deposits stand under.

    Follows each record to the submissions that link it and reads their
    *standing* attestation. One bundle carries one agreement, so the export
    refuses rather than picking a side whenever the records do not all stand
    under the same one: attested under different licenses, or some attested
    and some not (a record linked to no submission, or to a submission with
    no standing attestation). ``None`` only when *nothing* is attested: the
    exporter never manufactures consent, and a hosted release will refuse
    the records until somebody does.

    :raises ContributionBundleExportError: the source deposits are attested
        under different licenses, or only some of the records are attested.
    """
    pairs = {(record_type, int(record_id)) for record_id in record_ids}
    rights = linked_rights(session, pairs=pairs)
    attested = {
        link.attestation.id: link.attestation
        for links in rights.values()
        for link in links
        if link.attestation is not None
    }
    if not attested:
        return None
    # A record counts as unattested if it has no link at all, or any link
    # whose submission has no standing attestation -- the same fail-closed
    # reading the release gate applies.
    unattested = sorted(
        record_id
        for (_type, record_id), links in rights.items()
        if not links or any(link.attestation is None for link in links)
    )
    if unattested:
        raise ContributionBundleExportError(
            f"{len(unattested)} of the {len(pairs)} selected records carry no "
            "rights attestation while the others do; a bundle carries one "
            "rights statement and the exporter will not extend it to records "
            "nobody licensed. Attest the missing deposits, or export the "
            "attested records on their own."
        )
    licenses = sorted({row.license_id for row in attested.values()})
    if len(licenses) > 1:
        raise ContributionBundleExportError(
            "The selected records were deposited under different licenses "
            f"({', '.join(licenses)}); a bundle carries one rights statement. "
            "Export them as separate bundles."
        )
    terms = sorted({row.source_terms for row in attested.values() if row.source_terms})
    return DepositRights(
        license=licenses[0],
        depositor_attests_right_to_license=True,
        source_terms="; ".join(terms) if terms else None,
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


def _thermo_to_upload(
    thermo: Thermo,
) -> tuple[dict[str, Any] | None, BundleExportOmission | None]:
    """Reconstruct an upload-equivalent thermo dict from a ``Thermo`` row.

    Returns ``(payload, omission)``. ``payload`` is ``None`` only when the
    row's undeclared enthalpy could not be dropped without losing the whole
    record: it lives in a NASA fit (see ``_thermo_export_disposition``), or
    it was the record's only content, so no point, fit or scalar survives
    the prune. The caller must not add it to the bundle. ``omission`` is set whenever the row's legacy undeclared
    enthalpy changed what was exported, whether or not ``payload`` is
    ``None``.

    Otherwise returned as a plain ``dict`` so the bundle's
    ``ContributionBundleV0`` constructor runs the full nested upload
    validators (the same ones a real API upload would hit).
    """
    payload: dict[str, Any] = {
        "species_entry": _species_entry_payload(thermo.species_entry),
        "scientific_origin": thermo.scientific_origin.value,
        "phase": thermo.phase.value if thermo.phase is not None else None,
        "reference_pressure_bar": thermo.reference_pressure_bar,
        "enthalpy_reference_kind": (thermo.enthalpy_reference_kind.value
                                    if thermo.enthalpy_reference_kind is not None else None),
        "enthalpy_formation_0k_kj_mol": thermo.enthalpy_formation_0k_kj_mol,
        "enthalpy_formation_0k_uncertainty_kj_mol": thermo.enthalpy_formation_0k_uncertainty_kj_mol,
        "h298_kj_mol": thermo.h298_kj_mol,
        "s298_j_mol_k": thermo.s298_j_mol_k,
        "h298_uncertainty_kj_mol": thermo.h298_uncertainty_kj_mol,
        "s298_uncertainty_j_mol_k": thermo.s298_uncertainty_j_mol_k,
        "tmin_k": thermo.tmin_k,
        "tmax_k": thermo.tmax_k,
        "note": thermo.note,
    }
    if thermo.model_kind is not None:
        payload["model_kind"] = thermo.model_kind.value

    if thermo.points:
        payload["points"] = [_thermo_point_payload(p) for p in thermo.points]
    if thermo.nasa is not None:
        payload["nasa"] = _thermo_nasa_payload(thermo.nasa)
    if thermo.nasa9_intervals:
        payload["nasa9_intervals"] = [
            _thermo_nasa9_interval_payload(iv) for iv in thermo.nasa9_intervals
        ]
    if thermo.wilhoit is not None:
        payload["wilhoit"] = _thermo_wilhoit_payload(thermo.wilhoit)

    declaration_notes = _add_declarations(payload, thermo)

    literature = _literature_payload(thermo.literature)
    if literature is not None:
        payload["literature"] = literature
    software = _software_release_payload(thermo.software_release)
    if software is not None:
        payload["software_release"] = software
    workflow_tool = _workflow_tool_release_payload(thermo.workflow_tool_release)
    if workflow_tool is not None:
        payload["workflow_tool_release"] = workflow_tool

    # Legacy rows may carry an enthalpy with no reference declared -- a
    # shape the DB never backfills and the import workflow refuses. Export
    # it verbatim and this same server hands back a bundle it will not
    # re-accept; see BundleExportOmission for the two dispositions.
    omission: BundleExportOmission | None = None
    disposition = _thermo_export_disposition(payload)
    if disposition is not None:
        action, detail = disposition
        if action == "record_omitted":
            return None, BundleExportOmission(
                action=action, ref=thermo.public_ref, detail=detail
            )
        pruned_values = _undeclared_enthalpy_values(payload)
        prune = _prune_undeclared_enthalpy(payload)
        if prune.points_dropped_at_k and not _has_scientific_content(payload):
            # Every value the record held was undeclared enthalpy content:
            # with it dropped, and its emptied points with it, nothing is
            # left to export.
            return None, BundleExportOmission(
                action="record_omitted",
                ref=thermo.public_ref,
                detail=(
                    f"carries only undeclared enthalpy content ("
                    f"{'; '.join(pruned_values)}), with no "
                    "enthalpy_reference_kind declared -- a legacy shape "
                    "predating the declaration rule that the import workflow "
                    "refuses. Once those values are dropped, no point, fit or "
                    "scalar value remains, so the record is omitted from this "
                    "bundle."
                ),
                points_dropped_at_k=prune.points_dropped_at_k,
            )
        if prune.points_dropped_at_k:
            detail += (
                " Tabulated points left with no value were dropped, at "
                f"{_format_temperatures(prune.points_dropped_at_k)} K; the "
                "other points are exported."
            )
        if prune.model_kind_cleared:
            detail += (
                " No tabulated point remained, so the stored model_kind "
                "'tabulated' no longer describes the record; it was left out "
                "for the importer to infer from what remains."
            )
        omission = BundleExportOmission(
            action=action,
            ref=thermo.public_ref,
            detail=detail,
            points_dropped_at_k=prune.points_dropped_at_k,
        )

    if declaration_notes:
        note_text = " ".join(declaration_notes)
        if omission is None:
            omission = BundleExportOmission(
                action="declaration_pruned", ref=thermo.public_ref, detail=note_text
            )
        else:
            omission = BundleExportOmission(
                action=omission.action,
                ref=omission.ref,
                detail=f"{omission.detail} {note_text}",
                points_dropped_at_k=omission.points_dropped_at_k,
            )

    try:
        ThermoUploadRequest.model_validate(payload)
    except ValidationError as exc:
        if omission is not None:
            # Omit the whole record rather than export something the schema
            # itself refuses -- and say which of the two reasons applies.
            if _has_scientific_content(payload):
                reason = (
                    "the remaining content failed the upload schema, so the "
                    "record is omitted"
                )
            else:
                # e.g. h298 was this record's only content.
                reason = "nothing scientifically meaningful remained to export"
            return None, BundleExportOmission(
                action="record_omitted",
                ref=thermo.public_ref,
                detail=(
                    f"{omission.detail} After dropping the undeclared "
                    f"enthalpy, {reason}: {exc}"
                ),
                points_dropped_at_k=omission.points_dropped_at_k,
            )
        raise ContributionBundleExportError(
            f"thermo_upload_incompatible: {thermo.public_ref}: {exc}"
        ) from exc
    return payload, omission


def _add_declarations(payload: dict[str, Any], thermo: Thermo) -> list[str]:
    """Write the portable part of the target and protocol declarations into ``payload``.

    A bundle must be importable on any instance, so what names a row of *this*
    database is left out and reported rather than exported: the conformer group
    of a ``single_conformer`` target, and the supporting calculations of a
    protocol. What is left out is absent in the bundle, never replaced by
    something that reads differently. An equilibrium target and the protocol's
    recipe, formation reference, thermal approximation and departures carry
    over exactly.

    :returns: Sentences for the omission detail; empty when nothing was left out.
    """
    notes: list[str] = []
    kind = thermo.thermodynamic_target_kind
    if kind is ThermoTargetKind.equilibrium_ensemble:
        payload["thermodynamic_target"] = {"kind": ThermoTargetKind.equilibrium_ensemble.value}
    elif kind is ThermoTargetKind.single_conformer:
        notes.append(
            "Its single_conformer target names a conformer group of this database, which a "
            "portable bundle cannot carry, so the target declaration was left out (absent, "
            "not replaced by an equilibrium target)."
        )
    if thermo.protocol_declaration is not None:
        protocol = {
            key: value
            for key, value in thermo.protocol_declaration.items()
            if key != "supporting_calculations"
        }
        if thermo.protocol_declaration.get("supporting_calculations"):
            notes.append(
                "Its protocol declaration's supporting calculations are public refs to "
                "calculations of this database, which a portable bundle cannot carry, so "
                "they were left out."
            )
        if any(
            key in protocol
            for key in ("recipe", "formation_reference", "thermal_approximation", "departures")
        ):
            payload["protocol"] = protocol
        else:
            notes.append(
                "Nothing else was stated in the protocol declaration, so it was left out "
                "entirely."
            )
    return notes


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


def _thermo_nasa9_interval_payload(iv: ThermoNASA9Interval) -> dict[str, Any]:
    return {
        "interval_index": iv.interval_index,
        "t_min_k": iv.t_min_k,
        "t_max_k": iv.t_max_k,
        "a1": iv.a1, "a2": iv.a2, "a3": iv.a3, "a4": iv.a4, "a5": iv.a5,
        "a6": iv.a6, "a7": iv.a7, "a8": iv.a8, "a9": iv.a9,
    }


def _thermo_wilhoit_payload(w: ThermoWilhoit) -> dict[str, Any]:
    return {
        "cp0_j_mol_k": w.cp0_j_mol_k,
        "cp_inf_j_mol_k": w.cp_inf_j_mol_k,
        "b_k": w.b_k,
        "a0": w.a0, "a1": w.a1, "a2": w.a2, "a3": w.a3,
        "h0_kj_mol": w.h0_kj_mol,
        "s0_j_mol_k": w.s0_j_mol_k,
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
        "t0_k": kinetics.t0_k,
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
        "degeneracy_convention": kinetics.degeneracy_convention.value,
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
    # `species.smiles` is isotope-blind by construction (isotopologues share a
    # species row), so an isotopically substituted entry must round-trip via
    # its atom-resolved `isotope_key`, which *is* a canonical isotopic SMILES
    # for the same graph. Exporting `species.smiles` here would silently
    # export CD3OH as CH3OH.
    payload: dict[str, Any] = {
        "molecule_kind": species.kind.value,
        "smiles": species_entry.isotope_key or species.smiles,
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
    # `isotopologue_label` is deliberately NOT exported: it is a deprecated,
    # non-identity legacy annotation and is no longer accepted by any upload
    # schema, so emitting it would produce a bundle that fails re-import.
    # The isotope content it used to gesture at is carried exactly by the
    # isotope labels now present in `smiles` above.
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
    rights: DepositRights | None,
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
        rights=rights,
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
