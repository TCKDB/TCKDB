"""Bulk-export endpoints (docs/specs/bulk_export_design.md §3).

Two curator-gated, streaming exports over the scientific surface:

* ``GET  /scientific/export/ndjson``  — lossless TCKDB NDJSON, streamed one
  record per line via a server-side generator (never materializes the
  whole mechanism). For programmatic consumers, backups, and re-ingestion.
* ``POST /scientific/export/chemkin`` — a zip of ``chem.inp`` / ``therm.dat``
  / ``tran.dat`` + ``manifest.json``, ready for Cantera/CHEMKIN. POST
  because it carries options (units, transport, naming policy).

Both are above the normal read cap, so they are gated on the curator/admin
role. Thin handlers over
``app.services.scientific_read.{export,chemkin_serialize}``.
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


__all__ = ["router"]
