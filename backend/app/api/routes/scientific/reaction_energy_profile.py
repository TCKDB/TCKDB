"""GET /api/v1/scientific/reaction-entries/{reaction_entry_ref}/energy-profile.

Electronic-energy profiles (reactants, transition state, products) of one reaction entry, one level of theory and one
program at a time. Public ref only; read in a snapshot.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_snapshot_db
from app.api.server_faults import ServerFaultRoute
from app.db.models.common import RecordReviewStatus
from app.db.models.reaction import ReactionEntry
from app.schemas.reads.scientific_reaction_energy_profile import ReactionEnergyProfileResponse
from app.services.scientific_read.handles import is_ref_handle, resolve_path_handle
from app.services.scientific_read.internal_ids import apply_internal_ids_visibility
from app.services.scientific_read.profile import current_read_profile
from app.services.scientific_read.reaction_energy_profile import get_reaction_energy_profile

router = APIRouter(route_class=ServerFaultRoute, prefix="/reaction-entries")


@router.get(
    "/{reaction_entry_ref}/energy-profile",
    response_model=ReactionEnergyProfileResponse,
    summary="Electronic-energy profiles of a reaction entry, one level of theory and one program each",
)
def reaction_entry_energy_profile(
    reaction_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
) -> ReactionEnergyProfileResponse:
    """One profile per transition-state entry, level of theory and program: reactants, transition state, products.

    Every point of a profile comes from this reaction entry's own participants and transition-state entries, at one level
    from one program; programs are never mixed. A transition-state entry with no complete profile is listed under
    ``unavailable`` with a code. Nothing is ranked or chosen between profiles. ``cited_by_kinetics`` lists, per kinetics
    record, the energy calculations the record cites, apart from the profiles. The review floor of the read profile applies
    to energies and transition-state entries; ``include_rejected``, ``include_deprecated`` and ``min_review_status`` apply
    to the kinetics records, and hidden ones are counted in ``request.hidden_by_default``.
    """
    if not is_ref_handle(reaction_entry_ref):
        raise ValueError(f"invalid_handle: {reaction_entry_ref!r} is not a <prefix>_<body> public ref")
    entry_id = resolve_path_handle(session, ReactionEntry, reaction_entry_ref, kind_label="reaction_entry")
    payload = get_reaction_energy_profile(
        session,
        reaction_entry_id=entry_id,
        reaction_entry_ref=reaction_entry_ref,
        profile=current_read_profile(),
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        min_review_status=min_review_status,
    )
    return apply_internal_ids_visibility(payload)
