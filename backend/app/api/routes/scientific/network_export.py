"""POST /api/v1/scientific/networks/{network_ref}/kinetics/export-selected.

Serialise one verified network selection (native, or CHEMKIN where the chosen forms allow). Read-only.

The caller submits the decision manifest it saved from ``.../kinetics/select/manifest``, the node it chose and one
representation per member. The server replays the manifest, re-runs the selection under one read-only REPEATABLE READ
snapshot (opened before the network ref is resolved) and compares, and refuses a stale, forged or incomplete manifest,
a choice the decision does not allow, an inexact representation choice and any form it cannot serialise. A manifest
that no longer matches is never silently refreshed: it needs a fresh selection.

Like the selection endpoints this reads only what the caller's read profile can already read, so it asks for no
elevated role; the legacy curator-only ``/export/chemkin`` is a different, reaction-seeded surface.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy.orm import Session

from app.api.deps import get_optional_current_user, get_snapshot_db
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.db.models.app_user import AppUser
from app.schemas.reads.scientific_network_export import (
    NetworkSelectedKineticsExport,
    NetworkSelectedKineticsExportRequest,
)
from app.services.network_selection.export import ExportChoice, export_selected
from app.services.network_selection.public import SNAPSHOT_OPT_OUT, resolve_network_ref
from app.services.scientific_read.profile import stamp_read_profile

router = APIRouter(prefix="/networks")

_ALLOWED_QS_KEYS: set[str] = set(PROFILE_QUERY_KEYS)


@router.post(
    "/{network_ref}/kinetics/export-selected",
    response_model=NetworkSelectedKineticsExport,
    summary="Export a verified network selection (native or CHEMKIN)",
)
def export_selected_network_kinetics(
    request: Request,
    body: NetworkSelectedKineticsExportRequest,
    network_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
    user: AppUser | None = Depends(get_optional_current_user),
) -> NetworkSelectedKineticsExport:
    """Serialise the chosen node of a saved selection manifest, after verifying it against the server.

    Refusals (all 422, nothing exported): ``network_export_manifest_invalid`` (incomplete, another network's, or it
    does not replay), ``network_export_manifest_stale`` (the content, review states, rules, population or read profile
    changed, or the document was edited), ``network_export_choice_not_allowed`` (not the selected node; an unranked
    leading alternative without ``allow_administrative_choice``; a conflict or an empty selection),
    ``network_export_representation_choice_invalid`` (not exactly one eligible fit per member) and
    ``network_export_unsupported_form`` (a form or species with no CHEMKIN serialisation, or two chosen channels
    with one equation). CHEMKIN output is forward-only and writes no thermodynamics; alternatives are never added and
    ``DUPLICATE`` is never written.
    """
    forbidden = set(request.query_params.keys()) - _ALLOWED_QS_KEYS
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=(
                "post_search_fields_must_be_in_body: query-string keys "
                f"{sorted(forbidden)!r} are not accepted on POST; supply all fields in the JSON body."
            ),
        )
    resolve_network_ref(session, network_ref)
    result = export_selected(
        session,
        network_ref=network_ref,
        manifest=body.manifest,
        choice=ExportChoice(
            node_ref=body.node_ref,
            representation_refs=tuple(body.representation_refs),
            format=body.format,
            allow_administrative_choice=body.allow_administrative_choice,
            energy_units=body.energy_units,
            naming_policy=body.naming_policy,
            include_reported=body.include_reported,
        ),
        require_snapshot=not session.info.get(SNAPSHOT_OPT_OUT, False),
        actor=user.username if user is not None else "anonymous",
    )
    payload = NetworkSelectedKineticsExport(
        request={
            "network_ref": network_ref,
            "node_ref": body.node_ref,
            "format": body.format,
            "allow_administrative_choice": body.allow_administrative_choice,
            "include_reported": body.include_reported,
        },
        **result,
    )
    stamp_read_profile(payload)
    return payload
