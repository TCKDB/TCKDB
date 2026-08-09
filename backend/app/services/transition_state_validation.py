"""Persistence seam for structured IRC validation evidence on a TS candidate.

Every deposit path that can carry a transition state routes through here — the
pressure-dependent network bundle, the computed-reaction bundle, and the
standalone transition-state upload — so all three write identical rows and
report an identical gap.

IRC evidence is recommended, not required. Refusing a deposit without it would
lose the saddle point entirely, so its absence is reported as a structured
:class:`UploadWarning` instead. That is only honest if every path can actually
deposit the evidence: before this seam existed, only the PDep bundle could, so
a TS uploaded any other way always read back as ``validation: {"irc":
"absent"}`` even when the depositor had run the IRC.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
)
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.transition_state import TransitionStateValidationEvidence
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)
from app.services.reaction_atom_map import (
    validate_atom_map_agrees_with_irc_evidence,
)
from app.services.reaction_resolution import (
    validate_ts_evidence_participant_composition,
)

#: Emitted when a transition state is deposited with no *passing* IRC evidence.
W_MISSING_TS_IRC_EVIDENCE = "transition_state_missing_irc_evidence"


def persist_transition_state_validation_evidence(
    session: Session,
    evidence: Sequence[TransitionStateValidationEvidenceIn],
    *,
    transition_state_entry_id: int,
    reconstruction_calculation_ids: Sequence[int | None],
    subject_label: str,
    field_path: str,
    reaction_entry_id: int,
    transition_state_geometry_id: int | None,
    created_by: int | None = None,
    warnings: list[UploadWarning] | None = None,
) -> list[TransitionStateValidationEvidence]:
    """Write one evidence row per record and report an absent-evidence gap.

    :param evidence: Producer-declared evidence records, already validated.
    :param transition_state_entry_id: The TS candidate the evidence describes.
    :param reconstruction_calculation_ids: Resolved calculation id per record,
        positionally aligned with ``evidence``. Each path resolves its own
        locator (a bundle-local calculation key, or the upload's single ``irc``
        calculation) before calling in.
    :param subject_label: Producer-facing name of the TS, for the warning text.
    :param field_path: Dot-path of the evidence field, for the warning.
    :param reaction_entry_id: The reaction whose declared participants the
        evidence's ``reactant:N`` / ``product:N`` mappings name. Required rather
        than optional: it is what lets the element check below run on *every*
        path, and a default would let a new path silently opt out of it.
    :param transition_state_geometry_id: Saddle-point geometry the mappings'
        atom indices count into. ``None`` where the path has no geometry, which
        skips the element check as an absence.
    :param warnings: Optional sink for the absent-evidence warning.
    :returns: The persisted rows.
    """

    if len(reconstruction_calculation_ids) != len(evidence):
        raise ValueError(
            "reconstruction_calculation_ids must align with the evidence records."
        )

    # Definitional, therefore blocking (ADR 0008). The mappings' *shape* was
    # already settled at the wire boundary by ``validate_ts_evidence_set``;
    # what the mapped atoms actually **are** needs a species SMILES and so
    # needs RDKit, which the chemistry-free wire package does not have. Doing
    # it here rather than in the three workflows is deliberate: this seam is
    # the one place all three deposit paths already meet, and three call sites
    # is exactly how the pseudo-exemption divergence next door happened.
    validate_ts_evidence_participant_composition(
        session,
        evidence,
        reaction_entry_id=reaction_entry_id,
        transition_state_geometry_id=transition_state_geometry_id,
        subject_label=subject_label,
        field_path=field_path,
    )

    rows: list[TransitionStateValidationEvidence] = []
    for record, calculation_id in zip(
        evidence, reconstruction_calculation_ids, strict=True
    ):
        if calculation_id is None:
            raise ValueError(
                f"Transition state '{subject_label}' validation evidence could not "
                "be linked to the irc calculation that produced it."
            )
        row = TransitionStateValidationEvidence(
            transition_state_entry_id=transition_state_entry_id,
            kind=record.kind,
            passed=record.passed,
            rationale=record.rationale,
            reconstruction_calculation_id=calculation_id,
            reactant_participant_mapping=record.reactant_participant_mapping,
            product_participant_mapping=record.product_participant_mapping,
            created_by=created_by,
        )
        session.add(row)
        rows.append(row)

    # The same comparison the atom-map seam runs, from the other side. Whichever
    # of the two surfaces a deposit writes second is the one that can see both,
    # and today that is always the atom map — ``persist_computed_reaction_upload``
    # is the only path with an ``atom_map`` field and writes it after this call,
    # while every transition-state entry is created fresh by the deposit that
    # writes it, so a map can never arrive for a saddle point deposited earlier.
    # Both of those are incidental orderings a later edit could reverse, and the
    # check reads both surfaces from the database precisely so it does not
    # depend on either. Calling it here costs one indexed lookup that finds
    # nothing on today's paths and removes the ordering from the contract.
    validate_atom_map_agrees_with_irc_evidence(
        session,
        reaction_entry_id=reaction_entry_id,
        transition_state_entry_id=transition_state_entry_id,
        subject_label=subject_label,
        field_path=field_path,
    )

    if warnings is not None and not any(record.passed for record in evidence):
        warnings.append(
            UploadWarning(
                field=field_path,
                code=W_MISSING_TS_IRC_EVIDENCE,
                message=(
                    f"Transition state '{subject_label}' was deposited without passed "
                    "IRC validation evidence. The saddle point is stored, but nothing "
                    "in this deposit shows it connects the declared reactants and "
                    "products."
                ),
            )
        )
    return rows


CHECK_TS_IRC_EVIDENCE = ScientificCheck(
    group="Stationary points",
    sort_key=6,
    code=W_MISSING_TS_IRC_EVIDENCE,
    asserts=(
        "A deposited saddle point should carry passing intrinsic-reaction-"
        "coordinate evidence that it connects the declared reactants and "
        "products."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "Absence, not contradiction. Refusing a transition state without an "
        "IRC would lose the saddle point entirely, and a saddle point with no "
        "IRC is an incomplete record rather than a false one. The evidence is "
        "recommended, not required."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            persist_transition_state_validation_evidence,
            note=(
                "Every path that can carry a transition state routes through "
                "this seam — the PDep bundle, the computed-reaction bundle and "
                "the standalone transition-state upload — so all three write "
                "identical rows and report an identical gap. Before the seam "
                "existed only the PDep bundle could deposit the evidence, so a "
                "TS uploaded any other way always read back as ``irc: "
                "absent`` even when the depositor had run one."
            ),
        ),
    ),
    escape_hatch=(
        "None needed — the warning is the accommodation. Note the warning "
        "fires on absence of a *passing* record, so evidence that was run and "
        "failed is stored and still warns."
    ),
)


__all__ = [
    "W_MISSING_TS_IRC_EVIDENCE",
    "persist_transition_state_validation_evidence",
]
