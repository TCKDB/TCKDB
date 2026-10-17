"""Relative conformer energy routes (+ ``/manifest`` download).

``POST /api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies``
``POST /api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies/manifest``
``POST /api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies/compare``
``POST /api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies/compare/manifest``

The two ``/compare`` routes describe the deviations between two stored protocols on one entry (candidate minus
reference), in one snapshot. They are descriptive: they never say that one protocol is better and never rank.

How much higher or lower in energy each conformer of ONE species entry is than a conformer the caller names. Read-only:
a POST because the request is a structured body, not because anything is written.

The path takes a public ref only; an integer id is refused. The body carries no database id, no sort expression, no
pagination, no rule and no bound. The reference conformer, the energy kind, the level and the certificate are required
and have no default.

The session is a read-only REPEATABLE READ snapshot opened before the route body runs (``get_snapshot_db``). The
response is built from the manifest and returned directly (it carries public refs only); a test asserts that no
database id appears in either document.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_snapshot_db
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.schemas.reads.scientific_relative_conformer_energy import (
    RelativeConformerEnergyCompareManifest,
    RelativeConformerEnergyCompareRequest,
    RelativeConformerEnergyCompareResponse,
    RelativeConformerEnergyManifest,
    RelativeConformerEnergyRequest,
    RelativeConformerEnergyResponse,
)
from app.services.relative_conformer_energy import public
from app.services.scientific_read.profile import stamp_read_profile

router = APIRouter(prefix="/species-entries")

_ALLOWED_QS_KEYS: set[str] = set(PROFILE_QUERY_KEYS)


def _refuse_unknown_query_keys(request: Request) -> None:
    forbidden = set(request.query_params.keys()) - _ALLOWED_QS_KEYS
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=(
                "post_search_fields_must_be_in_body: query-string keys "
                f"{sorted(forbidden)!r} are not accepted on POST; supply all fields in the JSON body."
            ),
        )


@router.post(
    "/{species_entry_ref}/conformers/relative-energies",
    response_model=RelativeConformerEnergyResponse,
    summary="Relative energies of one species entry's conformers against a named reference conformer",
)
def relative_conformer_energies(
    request: Request,
    body: RelativeConformerEnergyRequest,
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> RelativeConformerEnergyResponse:
    """How far above or below the named reference conformer each conformer lies, in kJ/mol.

    The caller names the reference, the energy (electronic or zero-kelvin), the level and the minimum certificate;
    nothing is chosen for them and a negative difference stays negative. A conformer with no usable value at that level
    is listed as absent with a coded reason and null numbers, never as zero. Members are ordered by conformer ref and
    carry no rank. Two records of one conformer that disagree are reported as unresolved, never resolved by taking the
    lower one. Enthalpy, Gibbs energy and a request for no certificate are 422 ``relative_conformer_energy_unsupported``;
    more than 500 visible conformer groups is 422 ``relative_conformer_energy_population_too_large``. Nothing is
    answered from a subset. The answer covers the caller's authorized population only.
    """
    _refuse_unknown_query_keys(request)
    public.refuse_unsupported(body)
    entry_id = public.resolve_entry(session, species_entry_ref)
    payload = public.build_response(public.run(session, entry_id=entry_id, body=body), entry_ref=species_entry_ref)
    stamp_read_profile(payload)
    return payload


@router.post(
    "/{species_entry_ref}/conformers/relative-energies/manifest",
    response_model=RelativeConformerEnergyManifest,
    summary="Download the replayable manifest for a relative conformer energy answer",
)
def relative_conformer_energies_manifest(
    request: Request,
    body: RelativeConformerEnergyRequest,
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> JSONResponse:
    """The same request, answered with the replayable manifest as a download (a new snapshot, not a saved answer)."""
    _refuse_unknown_query_keys(request)
    public.refuse_unsupported(body)
    entry_id = public.resolve_entry(session, species_entry_ref)
    run_ = public.run(session, entry_id=entry_id, body=body)
    return JSONResponse(
        run_.manifest,
        headers={"Content-Disposition": f'attachment; filename="relative_conformer_energy_{species_entry_ref}.json"'},
    )


@router.post(
    "/{species_entry_ref}/conformers/relative-energies/compare",
    response_model=RelativeConformerEnergyCompareResponse,
    summary="Describe the deviations between two stored protocols' relative conformer energies",
)
def relative_conformer_energies_compare(
    request: Request,
    body: RelativeConformerEnergyCompareRequest,
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> RelativeConformerEnergyCompareResponse:
    """Deviations, candidate minus reference, in kJ/mol, of two stored protocols against one named reference conformer.

    Both protocols are evaluated over one population in one snapshot and paired by conformer; ``pairing`` is required
    (``same_geometry`` or ``same_basin``). The reference conformer is not a pair. A pair that is missing on either side,
    or fails the pairing, is listed with a coded ``excluded_reason`` and never counted as a zero deviation. With no
    pair every statistic is null; under ``all_requested_members`` any excluded pair gives ``incomplete_pairing`` and no
    statistics. The answer is descriptive: ``claims.superiority`` and ``claims.ranking`` are ``not_licensed``, and the
    evidence a superiority claim would need (E1 to E7) is listed as missing. Naming the same level with the same recipe facts twice is
    422 ``relative_conformer_energy_compare_identical_protocols``; enthalpy, Gibbs energy and no certificate are 422
    ``relative_conformer_energy_unsupported``. External or published references are not supported.
    """
    _refuse_unknown_query_keys(request)
    public.refuse_unsupported_compare(body)
    entry_id = public.resolve_entry(session, species_entry_ref)
    payload = public.build_compare_response(
        public.run_compare(session, entry_id=entry_id, body=body), entry_ref=species_entry_ref
    )
    stamp_read_profile(payload)
    return payload


@router.post(
    "/{species_entry_ref}/conformers/relative-energies/compare/manifest",
    response_model=RelativeConformerEnergyCompareManifest,
    summary="Download the replayable manifest for a relative conformer energy comparison",
)
def relative_conformer_energies_compare_manifest(
    request: Request,
    body: RelativeConformerEnergyCompareRequest,
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> JSONResponse:
    """The same request, answered with the replayable manifest as a download (a new snapshot, not a saved answer)."""
    _refuse_unknown_query_keys(request)
    public.refuse_unsupported_compare(body)
    entry_id = public.resolve_entry(session, species_entry_ref)
    run_ = public.run_compare(session, entry_id=entry_id, body=body)
    return JSONResponse(
        run_.manifest,
        headers={
            "Content-Disposition": f'attachment; filename="relative_conformer_energy_comparison_{species_entry_ref}.json"'
        },
    )
