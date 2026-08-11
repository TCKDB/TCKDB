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
from tckdb_schemas.enums import MoleculeKind
from tckdb_schemas.fragments.identity import participant_has_no_atoms


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

    A participant's atom list may be **empty**, and that is a claim rather than
    an omission: "this participant is made of no saddle-point atoms". It is
    what a reaction releasing a free electron has to be able to write —
    ``{"product:1": [1, 2, 3], "product:2": []}`` for ``OH- + H -> H2O + e-``
    — and it is legitimate only for a participant that genuinely has no atoms.
    This model cannot tell which participant that is, because a key such as
    ``product:2`` means nothing without the reaction it counts into; the
    reaction's own participant kinds are checked by
    :func:`validate_ts_evidence_set`, and what the atoms actually **are** is
    checked once more in the service layer against the resolved species.
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
            # A mapping with no participants at all is still refused above: it
            # partitions nothing and names nobody. An individual participant's
            # list may be empty -- see the class docstring -- but only a
            # participant with no atoms may use it, which is decided against
            # the reaction in ``validate_ts_evidence_set``.
            if not participant_key.strip() or any(index < 1 for index in atom_indices):
                raise ValueError("participant mappings use 1-based atom indices and require non-empty keys.")
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
    reactant_kinds: Sequence[MoleculeKind],
    product_kinds: Sequence[MoleculeKind],
) -> None:
    """Check an evidence set against the TS geometry and its reaction.

    Enforced identically on every deposit path:

    * at most one record per kind (mirrors ``uq_ts_validation_evidence_kind``);
    * a *passing* record that carries participant mappings must name every
      participant as ``reactant:N`` / ``product:N`` and account for every TS
      atom exactly once on BOTH sides. A map covering 2 of 4 atoms says
      nothing about the other 2, so it can never be passing evidence that the
      saddle point connects the declared endpoints.
    * a participant's atom list is empty **iff** that participant has no atoms.
      An empty list is the only way ``OH- + H -> H2O + e-`` can partition its
      saddle point at all, and it is a claim about the participant, so it is
      refused on anything but a free electron: allowing it generally would
      re-admit the completeness hole this function exists to close, since the
      atoms of a participant declared empty would have to be attributed to some
      other participant to keep the coverage rule satisfied. The converse is
      refused for the same reason it was worth closing in the first place — a
      mapping that hands the electron a real atom steals it from the molecule
      it belongs to.

    Which participants have no atoms is the caller's to state, and is why this
    takes participant *kinds* rather than participant counts: the count is
    ``len(kinds)``, so the two cannot disagree, and no deposit path can supply
    one without the other.

    :param reactant_kinds: ``molecule_kind`` of each declared reactant, in
        participant order — ``reactant:1`` is ``reactant_kinds[0]``.
    :param product_kinds: The same for the products.
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

    expected_reactants = {
        f"reactant:{index}" for index in range(1, len(reactant_kinds) + 1)
    }
    expected_products = {
        f"product:{index}" for index in range(1, len(product_kinds) + 1)
    }
    kinds_by_participant_key = {
        f"reactant:{index}": kind
        for index, kind in enumerate(reactant_kinds, start=1)
    } | {
        f"product:{index}": kind for index, kind in enumerate(product_kinds, start=1)
    }
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
            for participant_key, atom_indices in sorted(side.items()):
                kind = kinds_by_participant_key[participant_key]
                atomless = participant_has_no_atoms(kind)
                if atomless and atom_indices:
                    raise ValueError(
                        f"Transition state '{subject_label}' evidence mapping "
                        f"assigns saddle-point atom(s) {sorted(atom_indices)} to "
                        f"{participant_key}, which is declared "
                        f"molecule_kind='{kind.value}' and has no atoms. Those "
                        "atoms belong to another participant."
                    )
                if not atomless and not atom_indices:
                    raise ValueError(
                        f"Transition state '{subject_label}' evidence mapping "
                        f"gives {participant_key} no saddle-point atoms, but it "
                        f"is declared molecule_kind='{kind.value}' and has "
                        "atoms of its own. An empty list states that a "
                        "participant has none; a participant whose atoms were "
                        "not resolved is not passing evidence of anything, so "
                        "omit the mappings instead."
                    )
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
