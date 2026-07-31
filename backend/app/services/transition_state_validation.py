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
    :param warnings: Optional sink for the absent-evidence warning.
    :returns: The persisted rows.
    """

    if len(reconstruction_calculation_ids) != len(evidence):
        raise ValueError(
            "reconstruction_calculation_ids must align with the evidence records."
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


__all__ = [
    "W_MISSING_TS_IRC_EVIDENCE",
    "persist_transition_state_validation_evidence",
]
