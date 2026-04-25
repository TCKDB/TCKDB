"""Hosted contribution-bundle dry-run preview.

This service inspects a parsed :class:`ContributionBundleV0` and returns a
preview of what a real import *would* do. It performs **only read-only
queries** — never ``resolve_or_create_*`` — so the database is guaranteed
to be unchanged after a dry-run regardless of whether the underlying
session would commit.

Read-only mirrors used here key off the same uniqueness columns that the
resolve-or-create write path uses (``Species.inchi_key``,
``ChemReaction.stoichiometry_hash``, ``Literature.doi``/``isbn``, the
software/workflow-tool release composite keys), so a ``would_reuse``
classification matches what the importer would later resolve to.
"""

from __future__ import annotations

from collections import Counter
from typing import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.species import canonical_species_identity
from app.db.models.literature import Literature
from app.db.models.reaction import ChemReaction
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.contribution_bundle_dry_run import (
    ContributionBundleDryRunItem,
    ContributionBundleDryRunMessage,
    ContributionBundleDryRunResult,
    ContributionBundleDryRunSummary,
    DryRunAction,
    DryRunMessageLevel,
    DryRunRecordType,
)
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import SoftwareReleaseRef, WorkflowToolReleaseRef
from app.schemas.workflows.contribution_bundle import (
    BundleKind,
    ContributionBundleV0,
)
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.literature_metadata import normalize_doi, normalize_isbn
from app.services.reaction_resolution import reaction_stoichiometry_hash
from app.services.software_resolution import normalize_software_name
from app.services.species_resolution import null_safe_equals


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def dry_run_contribution_bundle(
    session: Session,
    bundle: ContributionBundleV0,
) -> ContributionBundleDryRunResult:
    """Preview a contribution bundle against the hosted database.

    :param session: Active read-only SQLAlchemy session. The service issues
        ``SELECT`` queries only; nothing here calls ``flush``/``commit`` or
        any ``resolve_or_create_*`` helper.
    :param bundle: Parsed contribution bundle (already schema-validated).
    :returns: Preview result with summary counts, per-record items, and any
        bundle-wide messages.
    """

    items: list[ContributionBundleDryRunItem] = []
    messages: list[ContributionBundleDryRunMessage] = []

    if bundle.bundle_kind is BundleKind.thermo:
        for index, upload in enumerate(bundle.records.thermo_uploads):
            items.extend(_dry_run_thermo_upload(session, upload, index))
    elif bundle.bundle_kind is BundleKind.kinetics:
        for index, upload in enumerate(bundle.records.kinetics_uploads):
            items.extend(_dry_run_kinetics_upload(session, upload, index))
    else:
        # Schema validation should have rejected unknown kinds already; this
        # is a belt-and-braces guard so the response stays well-formed.
        messages.append(
            ContributionBundleDryRunMessage(
                level=DryRunMessageLevel.error,
                code="unsupported_bundle_kind",
                message=(
                    f"Bundle kind {bundle.bundle_kind!r} is not supported by "
                    "hosted dry-run v0."
                ),
                field="bundle_kind",
            )
        )
        return ContributionBundleDryRunResult(
            bundle_valid=False,
            bundle_kind=bundle.bundle_kind,
            summary=_summarize(items, messages),
            items=items,
            messages=messages,
        )

    return ContributionBundleDryRunResult(
        bundle_valid=True,
        bundle_kind=bundle.bundle_kind,
        summary=_summarize(items, messages),
        items=items,
        messages=messages,
    )


# ---------------------------------------------------------------------------
# Per-upload preview
# ---------------------------------------------------------------------------


def _dry_run_thermo_upload(
    session: Session,
    upload: ThermoUploadRequest,
    index: int,
) -> list[ContributionBundleDryRunItem]:
    """Build preview items for one thermo upload.

    Walks identity (species_entry) and provenance (literature,
    software_release, workflow_tool_release), then appends a single
    ``would_append`` item for the thermo result row itself. Inline
    calculations and applied energy corrections are intentionally not
    previewed in v0.
    """
    base_ref = f"thermo_uploads[{index}]"
    items: list[ContributionBundleDryRunItem] = []

    species_items, _species = _preview_species_entry_identity(
        session,
        payload=upload.species_entry,
        local_ref=f"{base_ref}.species_entry",
    )
    items.extend(species_items)

    if upload.literature is not None:
        items.append(
            _preview_literature(session, upload.literature, f"{base_ref}.literature")
        )
    if upload.software_release is not None:
        items.append(
            _preview_software_release(
                session, upload.software_release, f"{base_ref}.software_release"
            )
        )
    if upload.workflow_tool_release is not None:
        items.append(
            _preview_workflow_tool_release(
                session,
                upload.workflow_tool_release,
                f"{base_ref}.workflow_tool_release",
            )
        )

    items.append(
        ContributionBundleDryRunItem(
            record_type=DryRunRecordType.thermo,
            action=DryRunAction.would_append,
            reason=(
                "Thermo records are append-only; a real import would append "
                "a new thermo row attached to the resolved species entry."
            ),
            local_ref=base_ref,
        )
    )
    return items


def _dry_run_kinetics_upload(
    session: Session,
    upload: KineticsUploadRequest,
    index: int,
) -> list[ContributionBundleDryRunItem]:
    """Build preview items for one kinetics upload.

    Previews each participant species/species_entry, then the reaction
    identity (only attempted when every participant species is found —
    a missing species means the reaction by definition cannot exist
    yet on hosted), then provenance, then the kinetics result row.
    """
    base_ref = f"kinetics_uploads[{index}]"
    items: list[ContributionBundleDryRunItem] = []

    reactant_species: list[Species | None] = []
    for r_index, participant in enumerate(upload.reaction.reactants):
        ref = f"{base_ref}.reaction.reactants[{r_index}].species_entry"
        species_items, species = _preview_species_entry_identity(
            session, payload=participant.species_entry, local_ref=ref
        )
        items.extend(species_items)
        reactant_species.append(species)

    product_species: list[Species | None] = []
    for p_index, participant in enumerate(upload.reaction.products):
        ref = f"{base_ref}.reaction.products[{p_index}].species_entry"
        species_items, species = _preview_species_entry_identity(
            session, payload=participant.species_entry, local_ref=ref
        )
        items.extend(species_items)
        product_species.append(species)

    items.append(
        _preview_chem_reaction(
            session,
            reversible=upload.reaction.reversible,
            reactant_species=reactant_species,
            product_species=product_species,
            local_ref=f"{base_ref}.reaction",
        )
    )

    if upload.literature is not None:
        items.append(
            _preview_literature(session, upload.literature, f"{base_ref}.literature")
        )
    if upload.software_release is not None:
        items.append(
            _preview_software_release(
                session, upload.software_release, f"{base_ref}.software_release"
            )
        )
    if upload.workflow_tool_release is not None:
        items.append(
            _preview_workflow_tool_release(
                session,
                upload.workflow_tool_release,
                f"{base_ref}.workflow_tool_release",
            )
        )

    items.append(
        ContributionBundleDryRunItem(
            record_type=DryRunRecordType.kinetics,
            action=DryRunAction.would_append,
            reason=(
                "Kinetics records are append-only; a real import would append "
                "a new kinetics row attached to the resolved reaction entry."
            ),
            local_ref=base_ref,
        )
    )
    return items


# ---------------------------------------------------------------------------
# Read-only identity lookups
# ---------------------------------------------------------------------------


def _preview_species_entry_identity(
    session: Session,
    *,
    payload: SpeciesEntryIdentityPayload,
    local_ref: str,
) -> tuple[list[ContributionBundleDryRunItem], Species | None]:
    """Preview one species + species_entry pair.

    :returns: ``(items, species)`` where ``items`` always contains both the
        species and (when canonicalization succeeded) the species_entry
        preview, and ``species`` is the resolved hosted ``Species`` row
        when found — used by the kinetics path to build the reaction
        stoichiometry hash.
    """
    try:
        canonical_smiles, inchi_key = canonical_species_identity(payload)
    except Exception as exc:  # noqa: BLE001 - schema-shaped errors only
        return (
            [
                ContributionBundleDryRunItem(
                    record_type=DryRunRecordType.species,
                    action=DryRunAction.error,
                    reason=(
                        "Could not canonicalize species identity from the upload "
                        f"payload: {exc}"
                    ),
                    local_ref=local_ref,
                )
            ],
            None,
        )

    species = session.scalar(
        select(Species).where(Species.inchi_key == inchi_key)
    )

    species_item = ContributionBundleDryRunItem(
        record_type=DryRunRecordType.species,
        action=DryRunAction.would_reuse if species is not None else DryRunAction.would_create,
        reason=(
            "A species with this InChIKey already exists on the hosted instance."
            if species is not None
            else "No species with this InChIKey exists yet; one would be created during real import."
        ),
        local_ref=local_ref,
        target=canonical_smiles,
        hosted_identity={"inchi_key": inchi_key},
    )

    if species is None:
        # Without a Species row, a SpeciesEntry by definition cannot exist.
        species_entry_item = ContributionBundleDryRunItem(
            record_type=DryRunRecordType.species_entry,
            action=DryRunAction.would_create,
            reason=(
                "Species not present on hosted; the corresponding species "
                "entry would be created during real import."
            ),
            local_ref=local_ref,
            target=canonical_smiles,
            hosted_identity={"inchi_key": inchi_key},
        )
        return [species_item, species_entry_item], None

    species_entry = session.scalar(
        select(SpeciesEntry).where(
            SpeciesEntry.species_id == species.id,
            SpeciesEntry.kind == payload.species_entry_kind,
            null_safe_equals(SpeciesEntry.stereo_label, payload.stereo_label),
            SpeciesEntry.electronic_state_kind == payload.electronic_state_kind,
            null_safe_equals(
                SpeciesEntry.electronic_state_label,
                payload.electronic_state_label,
            ),
            null_safe_equals(SpeciesEntry.term_symbol, payload.term_symbol),
            null_safe_equals(
                SpeciesEntry.isotopologue_label,
                payload.isotopologue_label,
            ),
        )
    )

    species_entry_item = ContributionBundleDryRunItem(
        record_type=DryRunRecordType.species_entry,
        action=DryRunAction.would_reuse if species_entry is not None else DryRunAction.would_create,
        reason=(
            "A species entry with matching identity attributes already exists."
            if species_entry is not None
            else (
                "Species exists but no entry with these identity attributes "
                "(kind, stereo, electronic state, isotopologue) was found; "
                "one would be created during real import."
            )
        ),
        local_ref=local_ref,
        target=canonical_smiles,
        hosted_identity={"inchi_key": inchi_key},
    )
    return [species_item, species_entry_item], species


def _preview_chem_reaction(
    session: Session,
    *,
    reversible: bool,
    reactant_species: Sequence[Species | None],
    product_species: Sequence[Species | None],
    local_ref: str,
) -> ContributionBundleDryRunItem:
    """Preview the reaction (graph) identity for a kinetics upload.

    NOTE on policy: if any participant Species is missing on hosted the
    ChemReaction *cannot* exist (its stoichiometry_hash is computed from
    species_ids that don't exist yet), so we report ``would_create``
    without issuing the lookup query. This is conservative and never
    falsely promises ``would_reuse``.
    """
    if any(s is None for s in reactant_species) or any(s is None for s in product_species):
        return ContributionBundleDryRunItem(
            record_type=DryRunRecordType.chem_reaction,
            action=DryRunAction.would_create,
            reason=(
                "One or more participant species are not yet on hosted; "
                "the reaction identity would be created during real import."
            ),
            local_ref=local_ref,
        )

    reactant_stoich = _compress_species_stoich(reactant_species)
    product_stoich = _compress_species_stoich(product_species)
    stoichiometry_hash = reaction_stoichiometry_hash(
        reversible=reversible,
        reactants=reactant_stoich,
        products=product_stoich,
    )
    chem_reaction = session.scalar(
        select(ChemReaction).where(
            ChemReaction.stoichiometry_hash == stoichiometry_hash
        )
    )
    if chem_reaction is not None:
        return ContributionBundleDryRunItem(
            record_type=DryRunRecordType.chem_reaction,
            action=DryRunAction.would_reuse,
            reason="A reaction with this graph-identity stoichiometry already exists.",
            local_ref=local_ref,
            hosted_identity={"stoichiometry_hash": stoichiometry_hash},
        )
    return ContributionBundleDryRunItem(
        record_type=DryRunRecordType.chem_reaction,
        action=DryRunAction.would_create,
        reason=(
            "No reaction with this graph-identity stoichiometry was found; "
            "one would be created during real import."
        ),
        local_ref=local_ref,
        hosted_identity={"stoichiometry_hash": stoichiometry_hash},
    )


def _preview_literature(
    session: Session,
    payload: LiteratureUploadRequest,
    local_ref: str,
) -> ContributionBundleDryRunItem:
    """Preview a literature reference using DOI-then-ISBN lookup.

    Mirrors :func:`app.services.literature_resolution.resolve_or_create_literature`
    but issues only ``SELECT`` queries.
    """
    normalized_doi = normalize_doi(payload.doi) if payload.doi is not None else None
    normalized_isbn = normalize_isbn(payload.isbn) if payload.isbn is not None else None

    target = normalized_doi or normalized_isbn or payload.title
    hosted_identity: dict[str, str | int | None] = {}
    if normalized_doi is not None:
        hosted_identity["doi"] = normalized_doi
    if normalized_isbn is not None:
        hosted_identity["isbn"] = normalized_isbn

    existing: Literature | None = None
    if normalized_doi is not None:
        existing = session.scalar(
            select(Literature).where(Literature.doi == normalized_doi)
        )
    if existing is None and normalized_isbn is not None:
        existing = session.scalar(
            select(Literature).where(Literature.isbn == normalized_isbn)
        )

    if existing is not None:
        return ContributionBundleDryRunItem(
            record_type=DryRunRecordType.literature,
            action=DryRunAction.would_reuse,
            reason="A literature row matching this identifier already exists on hosted.",
            local_ref=local_ref,
            target=target,
            hosted_identity=hosted_identity or None,
        )
    return ContributionBundleDryRunItem(
        record_type=DryRunRecordType.literature,
        action=DryRunAction.would_create,
        reason=(
            "No literature row with this identifier was found; one would be "
            "created during real import."
        ),
        local_ref=local_ref,
        target=target,
        hosted_identity=hosted_identity or None,
    )


def _preview_software_release(
    session: Session,
    ref: SoftwareReleaseRef,
    local_ref: str,
) -> ContributionBundleDryRunItem:
    """Preview a software release reference.

    Looks up the parent ``Software`` by normalized name, then the exact
    ``SoftwareRelease`` by ``(software_id, version, revision, build)``
    using the same null-safe equality the resolver uses.
    """
    normalized_name = normalize_software_name(ref.name)
    target = (
        f"{normalized_name} {ref.version}".strip()
        if ref.version is not None
        else normalized_name
    )
    hosted_identity: dict[str, str | int | None] = {"software_name": normalized_name}
    if ref.version is not None:
        hosted_identity["version"] = ref.version

    software = session.scalar(
        select(Software).where(Software.name == normalized_name)
    )
    if software is None:
        return ContributionBundleDryRunItem(
            record_type=DryRunRecordType.software_release,
            action=DryRunAction.would_create,
            reason=(
                f"Software {normalized_name!r} is not yet on hosted; the "
                "release would be created during real import."
            ),
            local_ref=local_ref,
            target=target,
            hosted_identity=hosted_identity,
        )

    release = session.scalar(
        select(SoftwareRelease).where(
            SoftwareRelease.software_id == software.id,
            null_safe_equals(SoftwareRelease.version, ref.version),
            null_safe_equals(SoftwareRelease.revision, ref.revision),
            null_safe_equals(SoftwareRelease.build, ref.build),
        )
    )
    if release is not None:
        return ContributionBundleDryRunItem(
            record_type=DryRunRecordType.software_release,
            action=DryRunAction.would_reuse,
            reason="A software release with these exact attributes already exists on hosted.",
            local_ref=local_ref,
            target=target,
            hosted_identity=hosted_identity,
        )
    return ContributionBundleDryRunItem(
        record_type=DryRunRecordType.software_release,
        action=DryRunAction.would_create,
        reason=(
            "Software exists on hosted but no release with these exact "
            "(version, revision, build) attributes was found; one would be "
            "created during real import."
        ),
        local_ref=local_ref,
        target=target,
        hosted_identity=hosted_identity,
    )


def _preview_workflow_tool_release(
    session: Session,
    ref: WorkflowToolReleaseRef,
    local_ref: str,
) -> ContributionBundleDryRunItem:
    """Preview a workflow tool release reference."""
    target = (
        f"{ref.name} {ref.version}".strip() if ref.version is not None else ref.name
    )
    hosted_identity: dict[str, str | int | None] = {"workflow_tool_name": ref.name}
    if ref.version is not None:
        hosted_identity["version"] = ref.version
    if ref.git_commit is not None:
        hosted_identity["git_commit"] = ref.git_commit

    tool = session.scalar(
        select(WorkflowTool).where(WorkflowTool.name == ref.name)
    )
    if tool is None:
        return ContributionBundleDryRunItem(
            record_type=DryRunRecordType.workflow_tool_release,
            action=DryRunAction.would_create,
            reason=(
                f"Workflow tool {ref.name!r} is not yet on hosted; the "
                "release would be created during real import."
            ),
            local_ref=local_ref,
            target=target,
            hosted_identity=hosted_identity,
        )

    release = session.scalar(
        select(WorkflowToolRelease).where(
            WorkflowToolRelease.workflow_tool_id == tool.id,
            null_safe_equals(WorkflowToolRelease.version, ref.version),
            null_safe_equals(WorkflowToolRelease.git_commit, ref.git_commit),
        )
    )
    if release is not None:
        return ContributionBundleDryRunItem(
            record_type=DryRunRecordType.workflow_tool_release,
            action=DryRunAction.would_reuse,
            reason="A workflow tool release with these exact attributes already exists on hosted.",
            local_ref=local_ref,
            target=target,
            hosted_identity=hosted_identity,
        )
    return ContributionBundleDryRunItem(
        record_type=DryRunRecordType.workflow_tool_release,
        action=DryRunAction.would_create,
        reason=(
            "Workflow tool exists on hosted but no release with these exact "
            "(version, git_commit) attributes was found; one would be "
            "created during real import."
        ),
        local_ref=local_ref,
        target=target,
        hosted_identity=hosted_identity,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compress_species_stoich(
    species: Sequence[Species | None],
) -> Mapping[int, int]:
    """Compress a list of resolved Species rows into stoichiometry counts.

    Caller must have ensured no entry is ``None``.
    """
    return dict(Counter(s.id for s in species if s is not None))


def _summarize(
    items: list[ContributionBundleDryRunItem],
    messages: list[ContributionBundleDryRunMessage],
) -> ContributionBundleDryRunSummary:
    """Tally items/messages into the summary counts."""
    action_counts: Counter[DryRunAction] = Counter(item.action for item in items)
    warnings = sum(1 for m in messages if m.level is DryRunMessageLevel.warning)
    errors_from_items = action_counts[DryRunAction.error]
    errors_from_messages = sum(
        1 for m in messages if m.level is DryRunMessageLevel.error
    )
    return ContributionBundleDryRunSummary(
        records_seen=len(items),
        would_create=action_counts[DryRunAction.would_create],
        would_reuse=action_counts[DryRunAction.would_reuse],
        would_append=action_counts[DryRunAction.would_append],
        unsupported=action_counts[DryRunAction.unsupported],
        errors=errors_from_items + errors_from_messages,
        warnings=warnings,
    )
