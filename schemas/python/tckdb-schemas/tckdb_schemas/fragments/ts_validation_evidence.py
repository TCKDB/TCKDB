"""Structured IRC validation evidence for a transition-state candidate.

Shared by every deposit path that can carry a transition state: the
pressure-dependent network bundle, the computed-reaction bundle, and the
standalone transition-state upload. Before this lived in one place, only the
PDep bundle could deposit evidence, so a TS uploaded through the other two
paths always read back as ``validation: {"irc": "absent"}`` even when the
depositor had run the IRC.

Normal-mode-displacement evidence is deliberately absent: reading an imaginary
mode's displacement vectors is a producer-side heuristic, not a database
record. Only the reconstructed-path evidence an IRC calculation actually
produces is stored.

Evidence is OPTIONAL on every path. A transition state deposited without it
succeeds and the workflow emits a ``transition_state_missing_irc_evidence``
upload warning. What is never accepted is *incomplete* evidence presented as
passing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from tckdb_schemas.common import SchemaBase


class TransitionStateValidationEvidenceIn(SchemaBase):
    """Producer-declared IRC validation evidence for one TS candidate.

    :param source_calculation_key: Local key of the ``irc`` calculation that
        reconstructed the path, in the enclosing payload's calculation
        namespace. Omitted on the standalone transition-state upload, which
        has no key namespace and binds to its single ``irc`` calculation.

    Participant→atom mappings are optional, but when supplied alongside
    ``passed=true`` they must be *complete*: a partial map proves nothing
    about the atoms it omits, so it is never accepted as passing evidence.
    Supply both sides or neither.
    """

    kind: Literal["irc"]
    passed: bool
    rationale: str = Field(min_length=1)
    source_calculation_key: str | None = Field(default=None, min_length=1)
    reactant_participant_mapping: dict[str, list[int]] | None = None
    product_participant_mapping: dict[str, list[int]] | None = None

    @field_validator("reactant_participant_mapping", "product_participant_mapping")
    @classmethod
    def validate_participant_mapping(
        cls, value: dict[str, list[int]] | None
    ) -> dict[str, list[int]] | None:
        if value is None:
            return value
        if not value:
            raise ValueError("participant mapping must not be empty when provided.")
        for participant_key, atom_indices in value.items():
            if not participant_key.strip() or not atom_indices or any(index < 1 for index in atom_indices):
                raise ValueError("participant mappings use 1-based atom indices and require non-empty keys/lists.")
            if len(atom_indices) != len(set(atom_indices)):
                raise ValueError("participant mappings must not repeat an atom index within one participant.")
        return value

    @model_validator(mode="after")
    def validate_mapping_sides_are_paired(self) -> Self:
        if (self.reactant_participant_mapping is None) != (
            self.product_participant_mapping is None
        ):
            raise ValueError(
                "participant mappings must be supplied for both sides or neither; "
                "a one-sided map cannot be checked for completeness."
            )
        return self


def validate_ts_evidence_set(
    evidence: Sequence[TransitionStateValidationEvidenceIn],
    *,
    subject_label: str,
    xyz_text: str,
    reactant_count: int,
    product_count: int,
) -> None:
    """Check an evidence set against the TS geometry and its reaction.

    Enforced identically on every deposit path:

    * at most one record per kind (mirrors ``uq_ts_validation_evidence_kind``);
    * a *passing* record that carries participant mappings must name every
      participant as ``reactant:N`` / ``product:N`` and account for every TS
      atom exactly once on BOTH sides. A map covering 2 of 4 atoms says
      nothing about the other 2, so it can never be passing evidence that the
      saddle point connects the declared endpoints.

    :raises ValueError: If the evidence set is internally inconsistent.
    """

    kinds = [record.kind for record in evidence]
    if len(kinds) != len(set(kinds)):
        raise ValueError(
            f"Transition state '{subject_label}' may have at most one IRC evidence record."
        )
    if not evidence:
        return

    try:
        atom_count = int(xyz_text.strip().splitlines()[0])
    except (IndexError, ValueError) as exc:
        raise ValueError(
            f"Transition state '{subject_label}' geometry must be valid XYZ for "
            "evidence validation."
        ) from exc

    expected_reactants = {f"reactant:{index}" for index in range(1, reactant_count + 1)}
    expected_products = {f"product:{index}" for index in range(1, product_count + 1)}
    full_atom_set = set(range(1, atom_count + 1))

    for record in evidence:
        if not record.passed or record.reactant_participant_mapping is None:
            continue
        assert record.product_participant_mapping is not None
        if (
            set(record.reactant_participant_mapping) != expected_reactants
            or set(record.product_participant_mapping) != expected_products
        ):
            raise ValueError(
                f"Transition state '{subject_label}' passed evidence mappings must "
                "name every participant as reactant:N/product:N."
            )
        for label, side in (
            ("reactant", record.reactant_participant_mapping),
            ("product", record.product_participant_mapping),
        ):
            atoms = [atom for mapped in side.values() for atom in mapped]
            if len(atoms) != len(set(atoms)):
                raise ValueError(
                    f"Transition state '{subject_label}' {label} evidence mapping "
                    "assigns an atom index to more than one participant."
                )
            if set(atoms) != full_atom_set:
                raise ValueError(
                    f"Transition state '{subject_label}' passed evidence {label} "
                    f"mapping must cover every one of the {atom_count} TS atoms "
                    f"exactly once (1..{atom_count}); a partial map is not passing "
                    "evidence."
                )


__all__ = ["TransitionStateValidationEvidenceIn", "validate_ts_evidence_set"]
