"""Bulk-export endpoints (docs/specs/bulk_export_design.md §3).

Four curator-gated, streaming exports over the scientific surface:

* ``GET  /scientific/export/ndjson``  — lossless TCKDB NDJSON, streamed one
  record per line via a server-side generator (never materializes the
  whole mechanism). For programmatic consumers, backups, and re-ingestion.
* ``POST /scientific/export/chemkin`` — a zip of ``chem.inp`` / ``therm.dat``
  / ``tran.dat`` + ``manifest.json``, ready for Cantera/CHEMKIN. POST
  because it carries options (units, transport, naming policy).
* ``GET  /scientific/export/ml/species.ndjson`` — ML dataset, one record
  per ``(species_entry, geometry)``: identity, Cartesian coordinates,
  LOT-labelled electronic energies, frequencies, optional Hessian, and
  the thermo summary.
* ``GET  /scientific/export/ml/reactions.ndjson`` — ML dataset, one record
  per ``reaction_entry``, RDB7-compatible in spirit: reactant/product
  SMILES, Arrhenius kinetics, electronic forward barrier, TS geometry.

All are above the normal read cap, so they are gated on the curator/admin
role. Thin handlers over
``app.services.scientific_read.{export,chemkin_serialize,ml_dataset}``.
"""

from __future__ import annotations

import io
import json
import zipfile

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_curator_or_admin
from app.db.models.app_user import AppUser
from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import CollapseMode, SelectionPolicy
from app.services.scientific_read.chemkin_serialize import (
    ChemkinOptions,
    serialize_chemkin,
)
from app.services.scientific_read.export import (
    SeedSelection,
    build_export_record_set,
    iter_export_ndjson,
)
from app.services.scientific_read.ml_dataset import (
    MLFilters,
    iter_ml_reactions_ndjson,
    iter_ml_species_ndjson,
)

router = APIRouter(prefix="/export")


@router.get("/ndjson")
def export_ndjson(
    session: Session = Depends(get_db),
    _user: AppUser = Depends(require_curator_or_admin),
    reaction_ref: list[str] | None = Query(
        None, description="reaction_entry or chem_reaction public refs"
    ),
    species_ref: list[str] | None = Query(
        None, description="species_entry public refs (species-only export)"
    ),
    reaction_family: str | None = Query(None),
    all: bool = Query(False, description="export all reactions (capped, logged)"),
    min_review_status: RecordReviewStatus | None = Query(
        RecordReviewStatus.approved
    ),
    collapse: CollapseMode = Query(CollapseMode.first),
    selection_policy: SelectionPolicy = Query(SelectionPolicy.default),
) -> StreamingResponse:
    """Stream a native NDJSON export for the seed selection.

    Emits a header ``manifest`` line, then one ``species`` / ``reaction``
    line per record (computed lazily), then a trailing ``export_summary``
    line with the gap list. Curator/admin only.

    :raises ValueError: 422 for an empty/unresolvable seed or an ``all``
        request over the export cap.
    """
    seed = SeedSelection(
        reaction_refs=reaction_ref,
        species_refs=species_ref,
        reaction_family=reaction_family,
        all_reactions=all,
    )

    # Resolve the seed eagerly so an empty/unresolvable seed raises 422
    # *before* the streaming response starts (a ValueError surfaced from
    # inside the generator would arrive after the headers were sent).
    line_iter = iter_export_ndjson(
        session,
        seed=seed,
        min_review_status=min_review_status,
        collapse=collapse,
        selection_policy=selection_policy,
    )
    return StreamingResponse(line_iter, media_type="application/x-ndjson")


class _SeedBody(BaseModel):
    reaction_refs: list[str] | None = None
    species_refs: list[str] | None = None
    reaction_family: str | None = None
    all_reactions: bool = False


class ChemkinExportRequest(BaseModel):
    """POST body for the CHEMKIN export."""

    seed: _SeedBody
    min_review_status: RecordReviewStatus | None = RecordReviewStatus.approved
    selection_policy: SelectionPolicy = SelectionPolicy.default
    energy_units: str = Field(
        "cal/mol",
        description="REACTIONS energy unit: cal/mol|kcal/mol|j/mol|kj/mol|k",
    )
    include_transport: bool = True
    naming_policy: str = Field("formula", description="formula | public_ref")


@router.post("/chemkin")
def export_chemkin(
    body: ChemkinExportRequest,
    session: Session = Depends(get_db),
    _user: AppUser = Depends(require_curator_or_admin),
) -> StreamingResponse:
    """Export a CHEMKIN mechanism zip (chem.inp/therm.dat/tran.dat + manifest).

    CHEMKIN cannot represent multiple candidates per record, so selection is
    forced to ``collapse=first``. Curator/admin only.

    :raises ValueError: 422 for an empty/unresolvable seed or an ``all``
        request over the export cap.
    """
    seed = SeedSelection(
        reaction_refs=body.seed.reaction_refs,
        species_refs=body.seed.species_refs,
        reaction_family=body.seed.reaction_family,
        all_reactions=body.seed.all_reactions,
    )
    record_set = build_export_record_set(
        session,
        seed=seed,
        min_review_status=body.min_review_status,
        collapse=CollapseMode.first,
        selection_policy=body.selection_policy,
    )
    options = ChemkinOptions(
        energy_units=body.energy_units,
        include_transport=body.include_transport,
        naming_policy=body.naming_policy,
    )
    result = serialize_chemkin(record_set, options=options)

    manifest = record_set.manifest()
    manifest["gaps"] = [g.to_dict() for g in result.gaps]
    manifest["counts"]["gaps"] = len(result.gaps)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in result.files.items():
            zf.writestr(filename, content)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=tckdb_chemkin_export.zip"},
    )


# ---------------------------------------------------------------------------
# ML-dataset exports (the "living RDB7" surface)
# ---------------------------------------------------------------------------


@router.get("/ml/species.ndjson")
def export_ml_species(
    session: Session = Depends(get_db),
    _user: AppUser = Depends(require_curator_or_admin),
    species_ref: list[str] | None = Query(
        None, description="species_entry public refs to export"
    ),
    all: bool = Query(
        False, description="export all species entries (capped, curator-gated)"
    ),
    min_review_status: RecordReviewStatus | None = Query(
        RecordReviewStatus.approved,
        description="identity trust floor; species_entry below it are skipped",
    ),
    lot_ref: str | None = Query(
        None,
        description="restrict energies to one level_of_theory public ref",
    ),
    element: list[str] | None = Query(
        None,
        description="element allow-list; only geometries whose atoms are a "
        "subset are emitted (e.g. element=C&element=H&element=O)",
    ),
    include_hessian: bool = Query(
        False, description="include the Cartesian Hessian block when present"
    ),
    limit: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0),
) -> StreamingResponse:
    """Stream a species/conformer-centric ML dataset as NDJSON (JSONL).

    One record per ``(species_entry, geometry)`` carrying species identity,
    Cartesian geometry, LOT-labelled electronic energies, frequencies,
    optional Hessian, and the species thermo summary. Curator/admin only.

    ``min_review_status`` defaults to ``approved``; on a pre-curation
    corpus (nothing approved yet) that exports zero records — pass
    ``min_review_status=under_review`` (or lower) to export uncurated data.

    :raises ValueError: 422 for an empty/unresolvable seed, an unknown
        ``lot_ref``, or an ``all`` request over the export cap.
    """
    filters = MLFilters(
        min_review_status=min_review_status,
        lot_ref=lot_ref,
        elements=(
            frozenset(e.strip() for e in element if e.strip()) if element else None
        ),
        include_hessian=include_hessian,
        limit=limit,
        offset=offset,
    )
    # Resolve the seed eagerly so a bad request 422s before the stream starts.
    line_iter = iter_ml_species_ndjson(
        session,
        species_refs=species_ref,
        all_species=all,
        filters=filters,
    )
    return StreamingResponse(line_iter, media_type="application/x-ndjson")


@router.get("/ml/reactions.ndjson")
def export_ml_reactions(
    session: Session = Depends(get_db),
    _user: AppUser = Depends(require_curator_or_admin),
    reaction_ref: list[str] | None = Query(
        None, description="reaction_entry or chem_reaction public refs"
    ),
    reaction_family: str | None = Query(None),
    all: bool = Query(
        False, description="export all reactions (capped, curator-gated)"
    ),
    min_review_status: RecordReviewStatus | None = Query(
        RecordReviewStatus.approved,
        description="identity trust floor; reaction_entry below it are skipped",
    ),
    limit: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0),
) -> StreamingResponse:
    """Stream a reaction-centric, RDB7-compatible ML dataset as NDJSON.

    One record per ``reaction_entry`` carrying reactant/product SMILES,
    Arrhenius kinetics with LOT labels, a best-effort electronic forward
    barrier, a reaction enthalpy, and the TS geometry+energy. The RDB7
    column mapping is documented on
    ``app.services.scientific_read.ml_dataset.MLReactionRecord``.
    Curator/admin only.

    ``min_review_status`` defaults to ``approved``; on a pre-curation
    corpus (nothing approved yet) that exports zero records — pass
    ``min_review_status=under_review`` (or lower) to export uncurated data.

    :raises ValueError: 422 for an empty/unresolvable seed or an ``all``
        request over the export cap.
    """
    filters = MLFilters(
        min_review_status=min_review_status,
        limit=limit,
        offset=offset,
    )
    line_iter = iter_ml_reactions_ndjson(
        session,
        reaction_refs=reaction_ref,
        reaction_family=reaction_family,
        all_reactions=all,
        filters=filters,
    )
    return StreamingResponse(line_iter, media_type="application/x-ndjson")


__all__ = ["router"]
