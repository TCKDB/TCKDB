"""Structured validation evidence for a transition-state candidate.

Shared by every deposit path that can carry a transition state: the
pressure-dependent network bundle, the computed-reaction bundle, and the
standalone transition-state upload. Before this lived in one place, only the
PDep bundle could deposit evidence, so a TS uploaded through the other two
paths always read back as ``validation: {"irc": "absent"}`` even when the
depositor had run the IRC.

Three kinds of evidence, one record per kind per transition state:

``irc``
    The reconstructed path connects the declared reactants and products, with
    optional participant-to-atom mappings.
``energy_ordering``
    The saddle point lies above both wells. The record carries the energies
    that were compared, and every energy names the calculation it came from
    and whether it is an electronic energy or an E0 (electronic energy plus
    zero-point energy). The two are different quantities, so each energy says
    which it is and the comparison is only ever made between like kinds.
``imaginary_mode``
    The frequency calculation found the expected imaginary mode: how many
    imaginary modes, the value of the reaction-coordinate mode in cm^-1, and
    whether the mode's displacement agrees with the bond change the reaction
    describes.

What is stored and what is not
------------------------------
A producer's *conclusion* about a mode or an ordering is stored here, with the
numbers it rests on, so a reader can see what was claimed. The arithmetic
anyone can redo from a stored Hessian is not: the ADR 0012 eigenvector
projections run at read time and write nothing. The displacement-agreement
flag is the producer's verdict, recorded as a verdict.

Evidence is OPTIONAL on every path. A transition state deposited without it
succeeds and the workflow emits a ``transition_state_missing_irc_evidence``
upload warning. That warning is about the *IRC* and only a passing ``irc``
record silences it: an energy ordering or an imaginary mode says something
true and useful about a saddle point, and neither says the saddle point
connects the declared endpoints. What is never accepted is *incomplete*
evidence presented as passing, or a pass the record's own numbers contradict.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import MoleculeKind
from tckdb_schemas.fragments.identity import participant_has_no_atoms


#: ``participant`` spelling of the saddle point itself in a compared energy; a
#: declared participant is ``reactant:N`` / ``product:N``, numbered the way
#: those keys mean everywhere else in this contract.
TS_ENERGY_TS_PARTICIPANT = "ts"

_PARTICIPANT_KEY_PATTERN = r"^(ts|reactant:[1-9][0-9]*|product:[1-9][0-9]*)$"


class TransitionStateComparedEnergy(SchemaBase):
    """One energy an ``energy_ordering`` record compared.

    :param participant: Whose energy this is: ``"ts"`` for the saddle point, or
        ``"reactant:N"`` / ``"product:N"`` for the N-th declared participant
        of that side (1-based, in the order the reaction declares them).
    :param energy_kind: ``"electronic"`` for the electronic energy, or ``"e0"``
        for the electronic energy plus the zero-point energy. They are
        different quantities and are never compared with each other.

        An ``e0`` is held against the *stored* values of the calculations it
        rests on: the stored electronic energy of this participant's
        ``electronic`` entry plus the zero-point energy stored on the cited
        ``freq`` calculation, which is the producer's own unscaled value (the
        program's raw zero-point correction). With ``zpe_scale_factor`` stated
        the sum is ``electronic + zpe_scale_factor * zpe``; without it the
        sum is ``electronic + zpe``, and an E0 that does not match that sum is
        not refused (the producer may have scaled the zero-point energy and not
        said so) but recorded as not compared.
    :param zpe_scale_factor: ``e0`` only. The factor ``s`` the producer
        multiplied the stored zero-point energy by in forming this E0
        (``E0 = E_electronic + s * ZPE``), for example a published ZPE scale
        factor for the level of theory. Provenance the producer states, never
        inferred. State it exactly as multiplied, or to at least four decimals:
        the tolerance assumes a rounding error of at most 5e-5, so a factor
        rounded to three decimals (0.954 for 0.953649) can be refused. Omit it when the E0 uses the zero-point energy as stored
        (an unscaled E0), not ``1.0``-as-a-guess: ``1.0`` is a claim that the
        sum is unscaled, and is held to the stored values like any other
        stated factor. Finite and positive.
    :param energy_hartree: The absolute energy, in hartree: finite and not
        positive. A bound system's total energy is below the zero of separated
        nuclei and electrons, so a positive value is a relative energy (or a
        unit slip) and is refused rather than stored where a reader would take
        it for absolute. Zero is allowed because it is exact for the bare
        proton (``[H+]``), a participant with atoms and no electrons. A side of the
        reaction with several participants is compared by the sum of their
        energies, so a participant is given its own and never a pre-summed
        total: a total has no single calculation to name.
    :param source_calculation_key: Local key of the calculation this energy
        was taken from, in the enclosing payload's calculation namespace. The
        calculation must belong to the thing the energy is of: the saddle
        point's own calculation for ``ts``, the participant species' own for a
        reactant or product.
    """

    participant: str = Field(pattern=_PARTICIPANT_KEY_PATTERN)
    energy_kind: Literal["electronic", "e0"]
    energy_hartree: float = Field(le=0, allow_inf_nan=False)
    source_calculation_key: str = Field(min_length=1)
    zpe_scale_factor: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_zpe_scale_factor_is_for_e0(self) -> Self:
        if self.zpe_scale_factor is not None and self.energy_kind != "e0":
            raise ValueError(
                "zpe_scale_factor scales the zero-point energy in an E0 and is accepted "
                f"only on energy_kind='e0', not '{self.energy_kind}'."
            )
        return self


class TransitionStateValidationEvidenceIn(SchemaBase):
    """Producer-declared validation evidence for one TS candidate.

    :param kind: ``"irc"``, ``"energy_ordering"`` or ``"imaginary_mode"``; see
        the module docstring. At most one record per kind.
    :param passed: The producer's verdict for this check.
    :param rationale: Why; free text, required.
    :param source_calculation_key: Local key of the calculation the evidence
        comes from, in the enclosing payload's calculation namespace: the
        ``irc`` calculation that reconstructed the path, or the ``freq``
        calculation that found the imaginary mode. Omitted on the standalone
        transition-state upload, which has no key namespace and binds to its
        single calculation of the needed type. Not accepted on an
        ``energy_ordering`` record, whose energies each name their own.
    :param energies: ``energy_ordering`` only. The energies compared.
    :param imaginary_frequency_count: ``imaginary_mode`` only. How many
        imaginary modes the frequency calculation found.
    :param imaginary_frequency_cm1: ``imaginary_mode`` only. The value, in
        cm^-1, of the imaginary mode taken as the reaction coordinate. Negative
        by convention, as it is on a frequency result.
    :param mode_displacement_agrees: ``imaginary_mode`` only. The producer's
        verdict on whether the mode's atom displacements agree with the bonds
        the reaction breaks and forms. ``None`` means not assessed; it is not
        ``False``.

    Participant->atom mappings (``irc`` only) are optional, but when supplied
    alongside ``passed=true`` they must be *complete*: a partial map proves
    nothing about the atoms it omits, so it is never accepted as passing
    evidence. Supply both sides or neither.

    A participant's atom list may be **empty**, and that is a claim rather than
    an omission: "this participant is made of no saddle-point atoms". It is
    what a reaction releasing a free electron has to be able to write --
    ``{"product:1": [1, 2, 3], "product:2": []}`` for ``OH- + H -> H2O + e-``
    -- and it is acceptable only for a participant that genuinely has no atoms.
    This model cannot tell which participant that is, because a key such as
    ``product:2`` means nothing without the reaction it counts into; the
    reaction's own participant kinds are checked by
    :func:`validate_ts_evidence_set`, and what the atoms actually **are** is
    checked once more in the service layer against the resolved species.

    A pass is refused when the record's own *stated* numbers contradict it: an
    ``energy_ordering`` that passes with a saddle point that is not above a
    side (checked by :func:`validate_ts_evidence_set`, which knows the
    reaction's participants and so whether a side is complete), or an
    ``imaginary_mode`` that passes having found no imaginary mode.
    The reverse is not refused. A record that fails with numbers that look
    fine is a producer applying a criterion this model cannot see.
    """

    kind: Literal["irc", "energy_ordering", "imaginary_mode"]
    passed: bool
    rationale: str = Field(min_length=1)
    source_calculation_key: str | None = Field(default=None, min_length=1)
    reactant_participant_mapping: dict[str, list[int]] | None = None
    product_participant_mapping: dict[str, list[int]] | None = None
    energies: list[TransitionStateComparedEnergy] | None = None
    imaginary_frequency_count: int | None = Field(default=None, ge=0)
    imaginary_frequency_cm1: float | None = Field(default=None, lt=0, allow_inf_nan=False)
    mode_displacement_agrees: bool | None = None

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

    @model_validator(mode="after")
    def validate_fields_belong_to_the_kind(self) -> Self:
        """A field is refused on a kind it does not describe.

        Accepting ``energies`` on an ``irc`` record would store a number the
        reader has no reason to look for there; refusing is what lets a reader
        trust that a field's presence means what its name says.
        """

        has_mapping = (
            self.reactant_participant_mapping is not None
            or self.product_participant_mapping is not None
        )
        has_mode_fields = (
            self.imaginary_frequency_count is not None
            or self.imaginary_frequency_cm1 is not None
            or self.mode_displacement_agrees is not None
        )
        if has_mapping and self.kind != "irc":
            raise ValueError(
                "participant mappings describe an IRC path and are accepted "
                f"only on kind='irc', not kind='{self.kind}'."
            )
        if self.energies is not None and self.kind != "energy_ordering":
            raise ValueError(
                f"energies are accepted only on kind='energy_ordering', not kind='{self.kind}'."
            )
        if has_mode_fields and self.kind != "imaginary_mode":
            raise ValueError(
                "imaginary_frequency_count, imaginary_frequency_cm1 and "
                "mode_displacement_agrees are accepted only on "
                f"kind='imaginary_mode', not kind='{self.kind}'."
            )
        return self

    @model_validator(mode="after")
    def validate_energy_ordering(self) -> Self:
        if self.kind != "energy_ordering":
            return self
        if not self.energies:
            raise ValueError(
                "kind='energy_ordering' requires the energies that were compared."
            )
        if self.source_calculation_key is not None:
            raise ValueError(
                "kind='energy_ordering' names its source calculations per "
                "energy; source_calculation_key is not accepted on the record."
            )
        seen: set[tuple[str, str]] = set()
        for energy in self.energies:
            slot = (energy.participant, energy.energy_kind)
            if slot in seen:
                raise ValueError(
                    f"energy_ordering gives {energy.participant} more than one "
                    f"'{energy.energy_kind}' energy."
                )
            seen.add(slot)

        for energy_kind in sorted({energy.energy_kind for energy in self.energies}):
            group = [e for e in self.energies if e.energy_kind == energy_kind]
            participants = {e.participant for e in group}
            reactants = [e for e in group if e.participant.startswith("reactant:")]
            products = [e for e in group if e.participant.startswith("product:")]
            if TS_ENERGY_TS_PARTICIPANT not in participants or not reactants or not products:
                raise ValueError(
                    f"energy_ordering compares the saddle point with a well on "
                    f"each side, so its '{energy_kind}' energies need 'ts', at "
                    "least one reactant and at least one product."
                )
        return self

    @model_validator(mode="after")
    def validate_imaginary_mode(self) -> Self:
        if self.kind != "imaginary_mode":
            return self
        count = self.imaginary_frequency_count
        if count == 0 and self.imaginary_frequency_cm1 is not None:
            raise ValueError(
                "imaginary_frequency_cm1 is given but imaginary_frequency_count is 0."
            )
        if self.passed and count == 0:
            raise ValueError(
                "imaginary_mode is marked passed, but the frequency calculation "
                "found no imaginary mode (imaginary_frequency_count=0)."
            )
        return self


def _validate_energy_ordering_covers_the_reaction(
    evidence: Sequence[TransitionStateValidationEvidenceIn],
    *,
    subject_label: str,
    reactant_kinds: Sequence[MoleculeKind],
    product_kinds: Sequence[MoleculeKind],
) -> None:
    """Hold each compared energy's participant against the reaction's own."""

    with_atoms = {
        f"reactant:{index}"
        for index, kind in enumerate(reactant_kinds, start=1)
        if not participant_has_no_atoms(kind)
    } | {
        f"product:{index}"
        for index, kind in enumerate(product_kinds, start=1)
        if not participant_has_no_atoms(kind)
    }
    atomless = {
        f"reactant:{index}"
        for index, kind in enumerate(reactant_kinds, start=1)
        if participant_has_no_atoms(kind)
    } | {
        f"product:{index}"
        for index, kind in enumerate(product_kinds, start=1)
        if participant_has_no_atoms(kind)
    }
    declared = with_atoms | atomless

    for record in evidence:
        if record.kind != "energy_ordering" or not record.energies:
            continue
        for energy in record.energies:
            if energy.participant == TS_ENERGY_TS_PARTICIPANT:
                continue
            if energy.participant not in declared:
                raise ValueError(
                    f"Transition state '{subject_label}' energy_ordering names "
                    f"'{energy.participant}', which the reaction does not declare."
                )
            if energy.participant in atomless:
                raise ValueError(
                    f"Transition state '{subject_label}' energy_ordering gives "
                    f"an energy for '{energy.participant}', which has no atoms "
                    "and so no calculation to take one from. Leave it out."
                )
        if not record.passed:
            continue
        for energy_kind in sorted({e.energy_kind for e in record.energies}):
            group = [e for e in record.energies if e.energy_kind == energy_kind]
            given = {e.participant for e in group}
            missing = sorted(with_atoms - given)
            if missing:
                raise ValueError(
                    f"Transition state '{subject_label}' energy_ordering is "
                    f"marked passed, but its '{energy_kind}' energies omit "
                    f"{', '.join(missing)}. A side summed over only some of its "
                    "participants is not that side's energy."
                )
            # Only now is a side's sum the side's energy, so only now can the
            # ordering itself be held against the claim. Checked after the
            # coverage rule above so that a participant left out is reported as
            # that, and not as a saddle point that looks too low.
            ts_energy = next(
                e.energy_hartree for e in group if e.participant == TS_ENERGY_TS_PARTICIPANT
            )
            for side in ("reactant", "product"):
                well = sum(e.energy_hartree for e in group if e.participant.startswith(f"{side}:"))
                if not ts_energy > well:
                    raise ValueError(
                        f"Transition state '{subject_label}' energy_ordering is "
                        f"marked passed, but its '{energy_kind}' energies put the "
                        f"saddle point ({ts_energy} hartree) at or below the "
                        f"{side} side ({well} hartree)."
                    )


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
    * an ``energy_ordering`` record names only participants the reaction
      declares, never one that has no atoms (a free electron has no energy to
      take from a calculation), and a *passing* one gives an energy for every
      participant that has atoms -- a well summed over only some of its
      participants is not the well, so the comparison proves nothing -- and
      its energies then actually put the saddle point above each side;
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
            f"Transition state '{subject_label}' may have at most one evidence "
            "record of each kind (irc, energy_ordering, imaginary_mode)."
        )
    if not evidence:
        return

    _validate_energy_ordering_covers_the_reaction(
        evidence,
        subject_label=subject_label,
        reactant_kinds=reactant_kinds,
        product_kinds=product_kinds,
    )

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


__all__ = [
    "TS_ENERGY_TS_PARTICIPANT",
    "TransitionStateComparedEnergy",
    "TransitionStateValidationEvidenceIn",
    "validate_ts_evidence_set",
]
