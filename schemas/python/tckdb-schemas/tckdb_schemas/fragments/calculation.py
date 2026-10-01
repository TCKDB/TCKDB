from datetime import datetime
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, Field, model_validator

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.common import SchemaBase
from tckdb_schemas.composite_scheme_rules import COMPOSITE_INPUT_MISSING, match_inputs_to_definition
from tckdb_schemas.enums import (
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    CompositeAssembly,
    CompositeInputSlot,
    ConstraintKind,
    EnergyComponentKind,
    HessianSource,
    ImaginaryModeDisposition,
    IRCDirection,
    PathSearchMethod,
    SCFStabilityStatus,
)
from tckdb_schemas.frequency_completeness import evaluate_deposited_frequency_list
from tckdb_schemas.fragments.calculation_origin import CalculationOriginMetadata
from tckdb_schemas.fragments.geometry import GeometryPayload
from tckdb_schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload
from tckdb_schemas.fragments.refs import (
    LevelOfTheoryRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from tckdb_schemas.literature import LiteratureUploadRequest
from tckdb_schemas.sp_energy_components import SP_ENERGY_COMPONENTS_DESCRIPTION, check_sp_energy_components
from tckdb_schemas.stationary_point import (
    ImaginaryMode,
    StationaryPointFinding,
    TauResolution,
    evaluate_transition_state_frequency,
    resolve_tau_from_parameters,
)

if TYPE_CHECKING:
    from tckdb_schemas.fragments.scan import CalculationScanResultCreate

# ---------------------------------------------------------------------------
# Constraint payload (lives in fragments so calculation upload payloads can
# reuse it without an entities → fragments cycle).
# ---------------------------------------------------------------------------


class CalculationConstraintPayload(BaseModel):
    """Geometric constraint applied to a calculation.

    Mirrors the ``calculation_constraint`` row shape and arity check
    enforced by the database. Generic across opt, TS, scan, IRC, NEB,
    and any other constrained run — for scans the held-fixed
    coordinates land here while the stepped coordinate lives in
    ``calc_scan_coordinate``.

    Arity by ``constraint_kind``:

    * ``cartesian_atom`` — one atom (atom2/3/4 must be null)
    * ``bond`` — two atoms
    * ``angle`` — three atoms
    * ``dihedral`` / ``improper`` — four atoms
    """

    constraint_index: int = Field(ge=1)
    constraint_kind: ConstraintKind
    atom1_index: int = Field(ge=1)
    atom2_index: int | None = Field(default=None, ge=1)
    atom3_index: int | None = Field(default=None, ge=1)
    atom4_index: int | None = Field(default=None, ge=1)
    target_value: float | None = None

    @model_validator(mode="after")
    def validate_arity_and_distinct_atoms(self) -> Self:
        atoms = [self.atom1_index]
        if self.atom2_index is not None:
            atoms.append(self.atom2_index)
        if self.atom3_index is not None:
            atoms.append(self.atom3_index)
        if self.atom4_index is not None:
            atoms.append(self.atom4_index)

        expected = {
            ConstraintKind.cartesian_atom: 1,
            ConstraintKind.bond: 2,
            ConstraintKind.angle: 3,
            ConstraintKind.dihedral: 4,
            ConstraintKind.improper: 4,
        }
        n_expected = expected[self.constraint_kind]
        if len(atoms) != n_expected:
            raise ValueError(
                f"{self.constraint_kind.value} constraint requires "
                f"{n_expected} atom index(es), got {len(atoms)}."
            )
        if len(set(atoms)) != len(atoms):
            raise ValueError("Constraint atom indices must be distinct.")
        return self


class CalculationConstraintCreate(CalculationConstraintPayload, SchemaBase):
    pass


class CalculationPayload(SchemaBase):
    """Reusable upload fragment for calculation provenance.

    :param type: Calculation type.
    :param quality: Curation quality flag.
    :param software_release: The software release that produced the numbers.
        Required, except on an ``assembled`` composite (arithmetic over other
        deposited calculations, run by no program).
    :param workflow_tool_release: Optional workflow tool provenance reference.
    :param level_of_theory: Required level-of-theory reference.
    :param literature: Optional inline literature provenance, resolved (or
        created) by the workflow.
    """

    type: CalculationType
    quality: CalculationQuality = CalculationQuality.raw

    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    level_of_theory: LevelOfTheoryRef

    #: Replaces the former ``literature_id``, a raw database primary key on
    #: a depositor-facing surface. Only a client that had already queried
    #: this database could supply one, which is the client we do not design
    #: for (``.claude/rules/schema-rules.md``, DR-0029 Req 1). The species
    #: bundle took the inline fragment from the start and ``CalculationIn``
    #: was converted in #172; this is the same conversion for the five
    #: routes that reach the shared payload directly — conformers,
    #: transition-states, statmech, thermo and transport.
    literature: LiteratureUploadRequest | None = None

    @model_validator(mode="after")
    def validate_software_and_composite_shape(self) -> Self:
        """Software is required unless an assembled composite; composite rules (ADR 0021)."""
        assert_composite_calculation_shape(
            self.type,
            getattr(self, "composite_result", None),
            level_of_theory=self.level_of_theory,
            software_release=self.software_release,
        )
        return self


# ---------------------------------------------------------------------------
# Typed calculation-result payloads (upload-facing, no FK ids)
# ---------------------------------------------------------------------------


class OptResultPayload(SchemaBase):
    """Optional inline result for an optimisation calculation.

    :param converged: Whether the optimisation converged.
    :param n_steps: Number of optimisation steps.
    :param final_energy_hartree: Final electronic energy in hartree.
    """

    converged: bool | None = None
    n_steps: int | None = Field(default=None, ge=0)
    final_energy_hartree: float | None = None


#: ``calc_freq_result.n_imag`` is a scalar the ESS printed; ``calc_freq_mode``
#: is the evidence beside it. Where a deposit carries both and they disagree,
#: the record answers "how many imaginary modes?" two different ways and tells
#: neither reader that the other exists — the cheap summary says three, the
#: frequency list shows one, and the two consumers walk away with different
#: science from the same row. That is a contract between two fields of one
#: record rather than an expectation about a result, so ADR 0008 puts it at
#: the blocking tier.
#:
#: **Absence is not disagreement.** ``modes = null`` is a deposit that carries
#: no frequency list, which is incomplete rather than contradictory and is
#: accepted with ``n_imag`` at any value; ADR 0012's read surface already
#: reports ``n_imag_at_or_above_tau = null`` rather than ``0`` for exactly that
#: state. An *empty* list is a different claim — "here is the frequency list,
#: and nothing in it is imaginary" — and is judged like any other list.
W_FREQ_N_IMAG_DISAGREES_WITH_MODES = "freq_n_imag_disagrees_with_modes"

#: Two mode rows in one frequency list claiming the same ``mode_index``.
#:
#: Its neighbour above is a scientific position — two fields of one record
#: answering the same question about chemistry differently — and is declared
#: in ``app.scientific_checks``. This one deliberately is not, and the
#: distinction is the reason it needed a code of its own rather than a place
#: in that register. ``mode_index`` is the ESS's 1-based ordering, so a
#: repeated value is a malformed list: a serialiser that concatenated two
#: blocks, or a producer that restarted its counter. Nothing about the
#: potential energy surface is being claimed, so there is no position a
#: referee could argue with — and a register whose entries are not all
#: arguable claims is a register that dilutes the ones that are.
#:
#: It still needs a name. "Your frequency list is malformed, renumber the
#: modes" is a different repair from every other 422 the same request can
#: produce, and a client could previously tell it apart only by matching
#: English inside a ``request_validation_error``.
W_FREQ_MODE_INDEX_NOT_UNIQUE = "freq_mode_index_not_unique"


class FrequencyModePayload(BaseModel):
    """One vibrational mode within a frequency calculation result.

    Imaginary modes use a negative ``frequency_cm1`` together with
    ``is_imaginary=True``. Producers that have only positive magnitudes
    must flip the sign before upload; the cross-field validator below
    refuses inconsistent combinations rather than silently normalising,
    so the source of truth stays at the producer boundary.

    :param mode_index: 1-based ordering from the ESS output.
    :param frequency_cm1: Harmonic frequency in cm⁻¹; negative for
        imaginary modes.
    :param is_imaginary: Whether the mode is imaginary. Required and
        consistent with the sign of ``frequency_cm1``.
    :param reduced_mass_amu: Reduced mass in amu, when reported.
    :param force_constant_mdyne_angstrom: Force constant in
        mDyne/Ångström, when reported.
    :param ir_intensity_km_mol: IR intensity in km/mol, when reported.
    :param raman_activity: Raman activity (Å⁴/amu), when reported.
    :param symmetry_label: Irreducible representation label
        (e.g. ``"A1"``, ``"E"``), when reported.
    :param imaginary_disposition: What this imaginary mode *is*, when it
        is not the reaction coordinate. Declared by the depositor, never
        inferred — TCKDB stores no normal-mode displacement vectors to
        infer it from. Only meaningful on an imaginary mode; setting it
        on a real one is refused rather than ignored. See ADR 0012.
    :param note: Optional free-text annotation.
    """

    mode_index: int = Field(ge=1)
    frequency_cm1: float
    is_imaginary: bool
    reduced_mass_amu: float | None = Field(default=None, gt=0)
    force_constant_mdyne_angstrom: float | None = None
    ir_intensity_km_mol: float | None = Field(default=None, ge=0)
    raman_activity: float | None = None
    symmetry_label: str | None = None
    imaginary_disposition: ImaginaryModeDisposition | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_sign_matches_is_imaginary(self) -> Self:
        if self.is_imaginary and self.frequency_cm1 >= 0:
            raise ValueError(
                "is_imaginary=True requires frequency_cm1 < 0. Negate the "
                "magnitude at the producer boundary before upload."
            )
        if not self.is_imaginary and self.frequency_cm1 < 0:
            raise ValueError(
                "frequency_cm1 < 0 requires is_imaginary=True."
            )
        if self.imaginary_disposition is not None and not self.is_imaginary:
            raise ValueError(
                f"imaginary_disposition="
                f"{self.imaginary_disposition.value!r} was set on mode "
                f"{self.mode_index}, which is not imaginary. A disposition "
                f"says what an imaginary mode is instead of the reaction "
                f"coordinate; on a real mode it has no meaning."
            )
        return self


class FreqResultPayload(SchemaBase):
    """Optional inline result for a frequency calculation.

    :param n_imag: Number of imaginary frequencies.
    :param imag_freq_cm1: Value of the imaginary frequency in cm⁻¹.
    :param zpe_hartree: Zero-point energy in hartree.
    :param modes: Optional per-mode frequency rows. When supplied,
        ``mode_index`` values must be unique within the payload, and
        the count of imaginary modes must agree with ``n_imag`` if both
        are present.
    :param reaction_coordinate_mode_index: ``mode_index`` of the mode the
        depositor designates the reaction coordinate. ADR 0012 makes this
        the contract that replaces the old ``n_imag == 1`` gate: a
        transition state with more than one imaginary mode is accepted
        only if it says which one is the barrier and is removed from the
        partition function. Meaningless on a minimum, and refused there.
    """

    n_imag: int | None = None
    imag_freq_cm1: float | None = None
    zpe_hartree: float | None = None
    modes: list[FrequencyModePayload] | None = None
    reaction_coordinate_mode_index: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_modes_consistency(self) -> Self:
        if self.modes is None:
            return self
        indices = [m.mode_index for m in self.modes]
        if len(set(indices)) != len(indices):
            duplicates = sorted({i for i in indices if indices.count(i) > 1})
            raise CodedValidationError(
                W_FREQ_MODE_INDEX_NOT_UNIQUE,
                # First sentence unchanged, byte for byte: attaching a code
                # is additive and must not move published prose.
                "mode_index values must be unique within a freq result. "
                f"Index {', '.join(str(i) for i in duplicates)} appears "
                f"more than once, so the list does not say which row is "
                f"which mode and a consumer reading it cannot "
                f"reconstruct the spectrum "
                f"({W_FREQ_MODE_INDEX_NOT_UNIQUE}). Renumber the modes to "
                f"the ESS's own 1-based ordering.",
                context={
                    "field": "modes",
                    "duplicate_mode_indices": duplicates,
                    "mode_count": len(self.modes),
                },
                message_prefix=False,
            )
        if self.n_imag is not None:
            imaginary_count = sum(1 for m in self.modes if m.is_imaginary)
            if imaginary_count != self.n_imag:
                raise CodedValidationError(
                    W_FREQ_N_IMAG_DISAGREES_WITH_MODES,
                    # First sentence unchanged, byte for byte: attaching a
                    # code is additive and must not move published prose.
                    f"n_imag={self.n_imag} does not match imaginary mode count "
                    f"{imaginary_count} in modes. The scalar the ESS printed "
                    f"and the frequency list deposited beside it are the same "
                    f"claim made twice, and a record that makes it two ways "
                    f"tells a consumer reading the summary something different "
                    f"from one reading the evidence "
                    f"({W_FREQ_N_IMAG_DISAGREES_WITH_MODES}). Deposit the "
                    f"complete signed frequency list, or omit modes entirely "
                    f"-- absence is incompleteness, which is accepted, and only "
                    f"contradiction is refused.",
                    context={
                        "n_imag": self.n_imag,
                        "imaginary_mode_count": imaginary_count,
                        "mode_count": len(self.modes),
                    },
                    message_prefix=False,
                )
        return self

    @model_validator(mode="after")
    def validate_reaction_coordinate_designation(self) -> Self:
        """The designated reaction coordinate must be an imaginary mode.

        Checked here rather than in
        :func:`~tckdb_schemas.stationary_point.evaluate_transition_state_frequency`
        because it is a statement about the payload's internal
        consistency, not about the chemistry: an index naming a real mode
        or naming nothing at all is a malformed record whatever kind of
        stationary point it describes.
        """
        index = self.reaction_coordinate_mode_index
        if index is None:
            return self
        if self.n_imag == 0:
            raise ValueError(
                "reaction_coordinate_mode_index was set but n_imag=0: there "
                "is no imaginary mode to designate."
            )
        if self.modes is None:
            raise ValueError(
                "reaction_coordinate_mode_index requires the frequency list. "
                "Deposit modes so the designation can be checked and the "
                "other imaginary modes judged."
            )
        designated = [m for m in self.modes if m.mode_index == index]
        if not designated:
            raise ValueError(
                f"reaction_coordinate_mode_index={index} names no mode in "
                f"the deposited frequency list."
            )
        if not designated[0].is_imaginary:
            raise ValueError(
                f"reaction_coordinate_mode_index={index} names mode "
                f"{index} at {designated[0].frequency_cm1} cm-1, which is "
                f"not imaginary. The reaction coordinate is the imaginary "
                f"mode."
            )
        if designated[0].imaginary_disposition is not None:
            raise ValueError(
                f"mode {index} is designated the reaction coordinate and "
                f"also carries imaginary_disposition="
                f"{designated[0].imaginary_disposition.value!r}. A "
                f"disposition says what a mode is *instead of* the reaction "
                f"coordinate; the two cannot both be true."
            )
        return self

    def imaginary_modes(self) -> list[ImaginaryMode]:
        """The deposited imaginary modes, in the shape the rule expects.

        Empty when no frequency list was deposited — which is a state the
        rule reports on rather than one it can paper over.
        """
        if not self.modes:
            return []
        return [
            ImaginaryMode(
                frequency_cm1=mode.frequency_cm1,
                mode_index=mode.mode_index,
                disposition=mode.imaginary_disposition,
            )
            for mode in self.modes
            if mode.is_imaginary
        ]


class SPResultPayload(SchemaBase):
    """Optional inline result for a single-point calculation.

    :param electronic_energy_hartree: Electronic energy in hartree.
    """

    electronic_energy_hartree: float | None = None


#: A ``composite_result`` was sent on a calculation whose ``type`` is not
#: ``composite``. A composite energy is the result of a composite calculation
#: and of nothing else (ADR 0021); it is not an ``sp`` or ``opt`` result.
COMPOSITE_RESULT_REQUIRES_COMPOSITE_TYPE = "composite_result_requires_composite_type"

#: A calculation with ``type: "composite"`` carried no ``composite_result``.
#: The calculation exists to record that energy; without the block there is
#: nothing for it to say.
COMPOSITE_TYPE_REQUIRES_COMPOSITE_RESULT = "composite_type_requires_composite_result"

#: The stated 0 K energy is not the stated ZPE-free energy plus the stated
#: recipe ZPE. Applied only when all three are present (ADR 0021, block tier:
#: it asserts a definition, e0 = electronic + recipe ZPE).
COMPOSITE_E0_INCONSISTENT = "composite_e0_inconsistent"

#: The stated terms do not sum to the stated ZPE-free energy. Applied only when
#: terms are given and the total is present.
COMPOSITE_TERMS_DO_NOT_SUM = "composite_terms_do_not_sum"

#: Floor, in hartree, of the tolerance of the two arithmetic checks above
#: (ADR 0021: 1e-6 Eh). A deposit that differs by more than the tolerance is
#: blocked; TCKDB never replaces a stated number with one it computed.
COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE = 1e-6

#: Rounding error of one number as a program prints it: half a unit of its
#: last digit. Gaussian prints every energy of a composite method to six
#: decimals, so each printed quantity is wrong by up to 5e-7 hartree.
COMPOSITE_PRINTED_ROUNDING_HARTREE = 5e-7

#: Slack for the binary representation of the two sides of a comparison, so a
#: gap that is exactly the tolerance on paper is not refused because of how
#: it is stored. Far below any printed digit.
_FLOAT_NOISE = 1e-12


def composite_arithmetic_tolerance_hartree(rounded_quantities: int) -> float:
    """The tolerance of an equation among ``rounded_quantities`` printed numbers.

    ``max(1e-6, 5e-7 * n)``: an equation in which ``n`` numbers were each
    rounded to six decimals can disagree by up to ``n * 5e-7`` hartree without
    anything being wrong, so seven CBS-QB3 terms and their total (n = 8) may
    differ by 4e-6. The fixed 1e-6 floor still applies to short equations.
    The check is for a number that is *wrong*, not for printed precision.

    :param rounded_quantities: How many numbers in the equation are rounded
        values: ``len(terms) + 1`` for the terms and their total, 3 for
        ``e0 = electronic + zpe``.
    """
    return max(COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE, COMPOSITE_PRINTED_ROUNDING_HARTREE * rounded_quantities)


class CompositeTermPayload(SchemaBase):
    """One term of a composite energy's breakdown.

    :param term_position: Position of the term. Where the calculation's scheme
        lists terms, this is one of their positions; a named method lists none
        today, so it is then the producer's own ordering (0-based).
    :param value_hartree: The term's contribution to the ZPE-free energy.
    """

    term_position: int = Field(ge=0, le=32767)
    value_hartree: float = Field(allow_inf_nan=False)


#: ``composite_result.inputs[]`` names a calculation by both a local key and a
#: ref, or by neither.
COMPOSITE_INPUT_REFERENCE_INVALID = "composite_input_reference_invalid"

#: ``composite_result.inputs`` on a ``program_run`` composite: a program printed
#: that number, so there is no arithmetic over other calculations to evidence.
COMPOSITE_INPUTS_REQUIRE_ASSEMBLED = "composite_inputs_require_assembled"

#: An ``assembled`` composite's ``level_of_theory`` is not a user-built scheme
#: carried inline: the definition is what its inputs are matched to. (Published
#: with P3a, when no assembled composite was accepted at all; since P5 it names
#: the one way an assembled composite is still not accepted.)
COMPOSITE_ASSEMBLED_NOT_ACCEPTED = "composite_assembled_not_accepted"

#: A calculation names no software release and is not an assembled composite.
CALCULATION_SOFTWARE_RELEASE_REQUIRED = "calculation_software_release_required"


class CompositeInputPayload(SchemaBase):
    """One input of an ``assembled`` composite: the calculation that fills a scheme slot.

    Names the term by the **key you gave it** in the level of theory's
    ``composite_scheme`` and the slot (with its cardinal number on an
    extrapolation), and the calculation by a bundle-local ``calculation_key`` or,
    for a calculation deposited earlier, its ``calc_...`` ref. Never a database
    id. Send exactly one of the two.

    :param term_key: The ``key`` of a term of the composite's scheme.
    :param slot: The slot of that term this calculation fills.
    :param cardinal_number: The slot's cardinal number; required on a
        ``cardinal`` slot, ignored elsewhere.
    :param calculation_key: Local key of a calculation declared in this request.
    :param calculation_ref: The ``calc_...`` ref of an already-deposited
        calculation.
    """

    term_key: str = Field(min_length=1)
    slot: CompositeInputSlot
    cardinal_number: int | None = Field(default=None, ge=1, le=32767)
    calculation_key: str | None = Field(default=None, min_length=1)
    calculation_ref: str | None = Field(default=None, pattern=r"^calc_[a-z2-7]+$")

    @model_validator(mode="after")
    def validate_one_reference(self) -> Self:
        """Exactly one of ``calculation_key`` and ``calculation_ref``."""
        if (self.calculation_key is None) == (self.calculation_ref is None):
            raise CodedValidationError(
                COMPOSITE_INPUT_REFERENCE_INVALID,
                (
                    f"composite_result.inputs entry for term {self.term_key!r} must name its calculation "
                    "by exactly one of calculation_key (a calculation declared in this request) or "
                    "calculation_ref (a calc_... ref from an earlier deposit)."
                ),
                context={"field": "composite_result.inputs", "term_key": self.term_key},
                message_prefix=False,
            )
        return self


class CompositeResultPayload(SchemaBase):
    """The energy of a ``composite`` calculation (ADR 0021).

    Every energy is optional and ``null`` means *not stated*, never zero.
    TCKDB never stores a total it computed itself: these are the producer's
    numbers (or the program's own output), and the checks only test them.

    :param assembly: ``program_run`` (a program printed the final number: a
        named method such as CBS-QB3 or G4, or one program run of a scheme) or
        ``assembled`` (arithmetic over other deposited calculations: a
        user-built scheme, with ``inputs``). An ``assembled`` composite's
        ``level_of_theory`` carries the scheme inline and its ``software_release``
        is optional; a ``program_run`` names the software that ran it.
    :param electronic_energy_hartree: ZPE-free energy with every term of the
        recipe included (the empirical terms of a named method among them).
        This is the number a correction layer is applied to.
    :param e0_hartree: The 0 K energy *including* the recipe's own scaled
        zero-point energy (Gaussian's ``CBS-QB3 (0 K)``).
    :param recipe_zpe_hartree: The zero-point energy the recipe added, after
        its own scale factor. Never negative.
    :param terms: The optional breakdown of ``electronic_energy_hartree``.
    :param inputs: For an ``assembled`` composite, the calculation that fills each
        slot of the level of theory's scheme (one entry per slot). Refused on a
        ``program_run``. TCKDB recomputes the total from these calculations'
        stored energies to check ``electronic_energy_hartree``; it never stores
        the recomputed value.
    """

    assembly: CompositeAssembly
    electronic_energy_hartree: float | None = Field(default=None, allow_inf_nan=False)
    e0_hartree: float | None = Field(default=None, allow_inf_nan=False)
    recipe_zpe_hartree: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    terms: list[CompositeTermPayload] = Field(default_factory=list)
    inputs: list[CompositeInputPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_terms_unique(self) -> Self:
        """``composite_result.terms`` must have one entry per ``term_position``."""
        positions = [t.term_position for t in self.terms]
        if len(set(positions)) != len(positions):
            raise ValueError("composite_result.terms must have unique term_position values.")
        return self

    @model_validator(mode="after")
    def validate_inputs_match_assembly(self) -> Self:
        """``inputs`` belong to an ``assembled`` composite, and an assembled one needs them."""
        if self.inputs and self.assembly != CompositeAssembly.assembled:
            raise CodedValidationError(
                COMPOSITE_INPUTS_REQUIRE_ASSEMBLED,
                (
                    "composite_result.inputs is only allowed with assembly='assembled'. A "
                    "program_run composite is a number a program printed; it has no deposited "
                    "calculations to evidence."
                ),
                context={"field": "composite_result.inputs", "assembly": self.assembly.value},
                message_prefix=False,
            )
        if not self.inputs and self.assembly == CompositeAssembly.assembled:
            raise CodedValidationError(
                COMPOSITE_INPUT_MISSING,
                (
                    "an assembled composite must name the calculations it was assembled from in "
                    "composite_result.inputs, one per slot of its scheme."
                ),
                context={"field": "composite_result.inputs"},
                message_prefix=False,
            )
        return self

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        """The stated composite energies must agree with each other, to printed precision.

        With ``e0_hartree``, ``electronic_energy_hartree`` and
        ``recipe_zpe_hartree`` all present, ``e0_hartree`` must be their sum
        (``composite_e0_inconsistent``); with ``terms`` given and
        ``electronic_energy_hartree`` present, the terms must sum to it
        (``composite_terms_do_not_sum``). TCKDB only compares: it never replaces
        a stated number with one it computed.
        """
        assert_composite_result_arithmetic(self)
        return self


def assert_composite_result_arithmetic(result: "CompositeResultPayload") -> None:
    """Block a composite result whose stated numbers contradict each other.

    The single owner of both checks. The wire model runs it on parse, and the
    backend runs the same function when it persists (a payload built with
    ``model_copy`` skips validators), so the tolerance cannot differ between
    the two.

    * ``composite_e0_inconsistent``: when ``e0_hartree``,
      ``electronic_energy_hartree`` and ``recipe_zpe_hartree`` are all present,
      ``|e0 - (electronic + zpe)|`` must be within
      :func:`composite_arithmetic_tolerance_hartree` of three rounded quantities
      (1.5e-6 hartree).
    * ``composite_terms_do_not_sum``: when terms are given and
      ``electronic_energy_hartree`` is present, ``|sum(terms) - electronic|``
      must be within the tolerance of ``len(terms) + 1`` rounded quantities.

    :raises CodedValidationError: on either contradiction.
    """
    electronic = result.electronic_energy_hartree
    e0 = result.e0_hartree
    zpe = result.recipe_zpe_hartree
    if electronic is not None and e0 is not None and zpe is not None:
        gap = e0 - (electronic + zpe)
        tolerance = composite_arithmetic_tolerance_hartree(3)
        if abs(gap) > tolerance + _FLOAT_NOISE:
            raise CodedValidationError(
                COMPOSITE_E0_INCONSISTENT,
                f"In composite_result, e0_hartree ({e0!r}) is not electronic_energy_hartree "
                f"({electronic!r}) plus recipe_zpe_hartree ({zpe!r}); they differ by "
                f"{gap:.3e} hartree and the tolerance is "
                f"{tolerance:.2e}. The 0 K energy includes the "
                "recipe's scaled zero-point energy and the electronic energy does not, so the "
                "three numbers must agree. Send the values the program printed, or omit the one "
                "you did not read.",
                context={
                    "field": "composite_result",
                    "difference_hartree": gap,
                    "tolerance_hartree": tolerance,
                },
                message_prefix=False,
            )
    if result.terms and electronic is not None:
        total = sum(t.value_hartree for t in result.terms)
        gap = total - electronic
        tolerance = composite_arithmetic_tolerance_hartree(len(result.terms) + 1)
        if abs(gap) > tolerance + _FLOAT_NOISE:
            raise CodedValidationError(
                COMPOSITE_TERMS_DO_NOT_SUM,
                f"In composite_result, the terms sum to {total!r} hartree but "
                f"electronic_energy_hartree is {electronic!r}; they differ by {gap:.3e} hartree "
                f"and the tolerance is {tolerance:.2e}. The terms "
                "are a breakdown of the ZPE-free energy with every recipe term included. Send "
                "every term, or omit the terms.",
                context={
                    "field": "composite_result.terms",
                    "difference_hartree": gap,
                    "tolerance_hartree": tolerance,
                },
                message_prefix=False,
            )


def assert_composite_result_matches_type(
    calc_type: CalculationType, composite_result: "CompositeResultPayload | None"
) -> None:
    """Pair ``type: "composite"`` with a ``composite_result`` block, both ways.

    Shared by every calculation payload shape so no route can take one without
    the other.

    :raises CodedValidationError: ``composite_result_requires_composite_type`` or
        ``composite_type_requires_composite_result``.
    """
    is_composite = calc_type == CalculationType.composite
    if composite_result is not None and not is_composite:
        raise CodedValidationError(
            COMPOSITE_RESULT_REQUIRES_COMPOSITE_TYPE,
            f"composite_result is only allowed for calculation type 'composite', got "
            f"'{calc_type.value}'. A composite energy is recorded by a composite calculation, "
            "not as the result of an sp or opt: change the calculation's type to 'composite', "
            "or drop the block.",
            context={"field": "composite_result", "calculation_type": calc_type.value},
            message_prefix=False,
        )
    if composite_result is None and is_composite:
        raise CodedValidationError(
            COMPOSITE_TYPE_REQUIRES_COMPOSITE_RESULT,
            "a calculation of type 'composite' needs a composite_result block: the calculation "
            "exists to record that energy. Send the assembly and whichever of "
            "electronic_energy_hartree, e0_hartree and recipe_zpe_hartree the program reported "
            "(each may be null).",
            context={"field": "composite_result", "calculation_type": calc_type.value},
            message_prefix=False,
        )


def assert_composite_calculation_shape(
    calc_type: CalculationType,
    composite_result: "CompositeResultPayload | None",
    *,
    level_of_theory: object,
    software_release: object | None,
) -> None:
    """Every wire rule that ties a calculation's block, level and software together.

    Shared by every calculation payload shape (the three the wire carries), and
    run again by the backend at the write, so no route can take a composite
    without these rules and a payload built with ``model_copy`` cannot skip them.

    * the ``type`` / ``composite_result`` pairing
      (:func:`assert_composite_result_matches_type`);
    * ``software_release`` is required on every calculation except an
      ``assembled`` composite (``calculation_software_release_required``): a
      program printed every other number, but an assembled one is arithmetic
      over other deposited calculations and no program ran it;
    * an ``assembled`` composite's level of theory carries the scheme inline
      (``composite_assembled_not_accepted``), and its inputs fill
      every slot of that scheme exactly once
      (``composite_input_missing`` / ``_slot_unknown`` / ``_duplicate``).

    :raises CodedValidationError: any of the codes above.
    """
    assert_composite_result_matches_type(calc_type, composite_result)
    assembled = composite_result is not None and composite_result.assembly == CompositeAssembly.assembled
    if software_release is None and not assembled:
        raise CodedValidationError(
            CALCULATION_SOFTWARE_RELEASE_REQUIRED,
            (
                "software_release is required: it names the program that produced this "
                "calculation's numbers. Only an assembled composite (arithmetic over other "
                "deposited calculations, run by no program) may omit it."
            ),
            context={"field": "software_release", "calculation_type": calc_type.value},
            message_prefix=False,
        )
    if not assembled or composite_result is None:
        return
    scheme = getattr(level_of_theory, "composite_scheme", None)
    if scheme is None:
        raise CodedValidationError(
            COMPOSITE_ASSEMBLED_NOT_ACCEPTED,
            (
                "an assembled composite's level_of_theory must carry the scheme inline "
                "(level_of_theory.composite_scheme): the definition is what composite_result.inputs "
                "are matched to, and what the energy is recomputed with. A named method such as "
                "CBS-QB3 is a program_run composite."
            ),
            context={"field": "level_of_theory"},
            message_prefix=False,
        )
    match_inputs_to_definition(scheme, composite_result.inputs)


class SPEnergyComponentPayload(SchemaBase):
    """One deposited part of a single point's electronic energy (ADR 0021).

    :param component: Which part: ``reference`` (the SCF / HF energy),
        ``correlation``, ``triples``, ``dboc``, ``scalar_relativistic``, or
        ``total`` (the whole electronic energy).
    :param value_hartree: The part's value in hartree, as the program printed
        it. TCKDB checks it against the single point's energy but never
        derives or fills one.
    """

    component: EnergyComponentKind
    value_hartree: float = Field(allow_inf_nan=False)


class SCFStabilityBase(SchemaBase):
    """The SCF wavefunction stability finding, with nothing that cites another row.

    Attaches to any calculation type — there is no calc_type restriction.
    Producers must only emit ``status = stable`` when an actual
    SCF/wavefunction stability analysis was observed; ordinary SCF
    convergence does NOT qualify. When unsure whether a stability
    analysis was performed, omit the block — the read API will project
    ``not_checked``. Use ``status = inconclusive`` only when a stability
    analysis was clearly attempted but its result could not be parsed.

    :param status: Persisted status. ``not_checked`` is NOT a valid
        stored value — omit the block to express that.
    :param lowest_eigenvalue: Smallest eigenvalue from the stability
        Hessian (software-specific).
    :param instability_count: Number of distinct instabilities found.
    :param instability_type: Free-text describing the instability class
        (e.g. ``"RHF→UHF"``, ``"internal"``).
    :param reoptimized_wavefunction: Whether a stable wavefunction was
        obtained by stability optimisation / reoptimisation.

    Holds the stability finding and nothing that names another row. The
    two routes that cite the measuring job do it differently, each in its own
    subclass: a bundle by local key (:class:`SCFStabilityContent`), the
    primitive routes by id (:class:`SCFStabilityPayload`).
    """

    status: SCFStabilityStatus
    lowest_eigenvalue: float | None = None
    instability_count: int | None = Field(default=None, ge=0)
    instability_type: str | None = None
    reoptimized_wavefunction: bool | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_status_consistency(self) -> Self:
        """Cross-field consistency between ``status`` and other fields.

        Mirrors the soft semantic invariants encoded as DB check
        constraints on ``calc_scf_stability``: catching them here gives
        a 422 with a producer-friendly message rather than letting the
        DB raise an opaque IntegrityError.

        Producer contract is intentionally narrow: we do NOT require
        evidence-bearing fields (``lowest_eigenvalue`` /
        ``source_artifact_id``) for ``status = stable`` — that is left
        to the producer documentation.
        """
        if (
            self.status == SCFStabilityStatus.stable
            and self.reoptimized_wavefunction is True
        ):
            raise ValueError(
                "scf_stability.status = 'stable' is inconsistent with "
                "reoptimized_wavefunction = True. A stable wavefunction "
                "did not need to be re-optimised; use 'stabilized' if a "
                "re-optimisation actually occurred."
            )
        if (
            self.status == SCFStabilityStatus.stabilized
            and self.instability_count == 0
        ):
            raise ValueError(
                "scf_stability.status = 'stabilized' implies at least "
                "one instability was found and then resolved; "
                "instability_count = 0 contradicts that. Leave it null "
                "if unknown."
            )
        if (
            self.status == SCFStabilityStatus.unstable
            and self.reoptimized_wavefunction is True
        ):
            raise ValueError(
                "scf_stability.status = 'unstable' records that an "
                "instability remains. Use 'stabilized' if a stable "
                "wavefunction was subsequently obtained."
            )
        return self


class SCFStabilityContent(SCFStabilityBase):
    """SCF stability evidence as a bundle carries it.

    The finding of :class:`SCFStabilityBase` plus, optionally, the local key of
    the job that measured it.

    :param source_calculation_key: Optional local key of the calculation
        (job) that measured this verdict, when that is a different job from
        the one this block hangs off. Meaningful only inside a bundle, where
        it must name a calculation the same bundle declares for the same
        species entry (or transition state) and on the same conformer as the
        calculation carrying the block. It may not name the carrier itself
        or form a cycle with another block's key, and a level of theory that
        differs from the carrier's is accepted with an upload warning. The
        job that measured it keeps its own type; there is no separate stability
        calculation type. The key is resolved after every calculation in the
        bundle exists, so it may point at a calculation declared later in
        the payload. Omit it when the calculation carrying the block is the
        one that measured the stability.
    """

    source_calculation_key: str | None = Field(default=None, min_length=1)


class SCFStabilityPayload(SCFStabilityBase):
    """SCF stability evidence that may cite rows outside its own record.

    The primitive upload routes take this shape. They already accept
    database ids as a deliberate programmatic-chaining mechanism, so
    naming the calculation or artifact that carries the stability log is
    the same kind of claim they already support.

    Bundle roots take :class:`SCFStabilityContent` instead, which names the
    measuring job by local key rather than by id. Both share
    :class:`SCFStabilityBase`, so neither route publishes the other's
    citation field.

    :param source_calculation_id: Optional FK to the calculation whose
        log carries the stability evidence (when separate from the
        owning calculation).
    :param source_artifact_id: Optional FK to a ``calculation_artifact``
        row holding the stability log bytes (e.g. an ``ancillary`` or
        ``output_log`` artifact).
    """

    source_calculation_id: int | None = None
    source_artifact_id: int | None = None


class HessianPayload(SchemaBase):
    """Optional inline Cartesian Hessian (second-derivative) matrix.

    The Hessian is the primitive from which harmonic frequencies, normal
    modes, and thermochemistry are derived. It is meaningful only relative
    to a specific atomic configuration, ordering, and orientation, so the
    payload carries its own ``geometry``: the resolution layer dedupes it
    through the content-addressed geometry seam (so it usually coincides
    with the calculation's input geometry with no duplication) and stores
    the resulting ``geometry_id`` as a mandatory binding.

    Only the lower triangle including the diagonal of the symmetric 3N×3N
    matrix is stored, row-major, in fixed units of hartree/bohr². For
    ``N`` atoms that is exactly ``3N(3N+1)/2`` values.

    Attaches to ``freq`` calculations and to ``opt`` calculations run with
    an analytic Hessian (``opt=calcall``-style). See DR-0030.

    :param geometry: The geometry the Hessian was computed at.
    :param lower_triangle_hartree_bohr2: Packed lower triangle (with
        diagonal), row-major, length ``3N(3N+1)/2``, in hartree/bohr².
    :param source: Where the matrix was obtained from.
    :param parser_version: Optional version tag of the parser that
        produced the matrix.
    :param note: Optional free-text annotation.
    """

    geometry: GeometryPayload
    lower_triangle_hartree_bohr2: list[float] = Field(min_length=1)
    source: HessianSource
    parser_version: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_triangle_length(self) -> Self:
        # Standard XYZ: the first line is the integer atom count. If it is
        # malformed we defer to the backend geometry parser for the precise
        # error rather than duplicating its diagnostics here.
        stripped = self.geometry.xyz_text.strip().splitlines()
        if not stripped:
            return self
        try:
            n = int(stripped[0].strip())
        except ValueError:
            return self
        expected = (3 * n) * (3 * n + 1) // 2
        actual = len(self.lower_triangle_hartree_bohr2)
        if actual != expected:
            raise ValueError(
                f"hessian.lower_triangle_hartree_bohr2 has {actual} entries "
                f"but a {n}-atom Hessian lower triangle must have exactly "
                f"{expected} (= 3N(3N+1)/2 for N={n}). Provide the packed "
                f"lower triangle including the diagonal, row-major."
            )
        return self


class WavefunctionDiagnosticPayload(SchemaBase):
    """Optional inline wavefunction diagnostics parsed from a calculation's output.

    Carries scalar coupled-cluster / multireference diagnostics — T1
    (Lee–Taylor), D1 (Janowski), the norm of the T1 amplitude vector,
    and the largest T2 amplitude. Spin-contamination ``<S^2>`` signals
    are intentionally NOT included in this first slice.

    Generic across calculation types that produce electronic-structure
    output (typically ``sp``). The producer contract is to emit a block
    only when at least one diagnostic was actually parsed from the
    calculation; this payload rejects an all-null block with no note.

    :param t1_diagnostic: Coupled-cluster T1 diagnostic (Lee–Taylor).
    :param d1_diagnostic: Janowski D1 diagnostic.
    :param t1_norm: Norm of the T1 amplitude vector, when reported.
    :param largest_t2_amplitude: Largest T2 amplitude magnitude, when
        reported.
    :param note: Optional free-text annotation.
    """

    t1_diagnostic: float | None = Field(default=None, ge=0)
    d1_diagnostic: float | None = Field(default=None, ge=0)
    t1_norm: float | None = Field(default=None, ge=0)
    largest_t2_amplitude: float | None = Field(default=None, ge=0)
    note: str | None = None

    @model_validator(mode="after")
    def validate_has_diagnostic_value(self) -> Self:
        if (
            self.t1_diagnostic is None
            and self.d1_diagnostic is None
            and self.t1_norm is None
            and self.largest_t2_amplitude is None
        ):
            raise ValueError(
                "wavefunction_diagnostic must include at least one of "
                "t1_diagnostic, d1_diagnostic, t1_norm, "
                "largest_t2_amplitude. Omit the block entirely if no "
                "diagnostic was parsed."
            )
        return self


class SpinDiagnosticPayload(SchemaBase):
    """Optional inline spin-contamination ``<S^2>`` evidence parsed from a
    calculation's output.

    The companion block to :class:`WavefunctionDiagnosticPayload`: T1/D1 stay
    there, spin-contamination signals land here. Carries the observed
    ``<S^2>`` and, when the ESS reports them, the ideal ``S(S+1)`` for the
    target spin state and the ``<S^2>`` after annihilation of the first spin
    contaminant.

    Applies to any UNRESTRICTED calculation (not just coupled cluster). The
    producer contract is to emit the block only when ``<S^2>`` was actually
    parsed; ``s_squared`` is required because the row exists precisely because
    that observation is present. Omit the block entirely for restricted /
    closed-shell runs that have no contamination to report.

    :param s_squared: Observed ``<S^2>`` expectation value.
    :param s_squared_expected: Ideal ``S(S+1)`` for the target spin state as
        reported by the ESS, when reported.
    :param s_squared_annihilated: ``<S^2>`` after annihilation of the first
        spin contaminant (e.g. Gaussian's "after annihilation" value), when
        reported.
    :param note: Optional free-text annotation.
    """

    s_squared: float = Field(ge=0)
    s_squared_expected: float | None = Field(default=None, ge=0)
    s_squared_annihilated: float | None = Field(default=None, ge=0)
    note: str | None = None


class IRCPointPayload(SchemaBase):
    """Upload-facing inline payload for one IRC-path sampled point.

    Geometries are accepted inline as ``GeometryPayload`` and resolved/deduped
    via the existing geometry resolution service at persistence time.

    :param point_index: Zero-based index preserving the source step number.
    :param direction: Per-point direction (nullable for the TS marker point).
    :param is_ts: Whether this point marks the transition state.
    :param reaction_coordinate: Reaction-coordinate value at this point.
    :param electronic_energy_hartree: Electronic energy in hartree.
    :param relative_energy_kj_mol: Relative energy vs the zero-energy reference.
    :param max_gradient: Max gradient component at this point.
    :param rms_gradient: RMS gradient at this point.
    :param geometry: Optional inline geometry payload for this point.
    :param note: Optional free-text note.
    """

    point_index: int = Field(ge=0)
    direction: IRCDirection | None = None
    is_ts: bool = False
    reaction_coordinate: float | None = None
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    max_gradient: float | None = None
    rms_gradient: float | None = None
    geometry: GeometryPayload | None = None
    note: str | None = None


class IRCResultPayload(SchemaBase):
    """Upload-facing inline result for an IRC calculation.

    ``direction``, ``has_forward`` and ``has_reverse`` are each optional, and
    omitting one says "the producer did not state it". That is a real position
    for a producer to be in: an IRC whose log does not record which way it ran
    is still a path with points on it, and requiring the three to be written
    made such a producer drop the whole result. A value that is not stated is
    stored as NULL and read back as null, never as ``false``: ``has_forward``
    false is a claim that no forward-branch point exists, which is a different
    statement from not knowing.

    :param direction: Overall run mode (forward / reverse / both). Omit when
        the run mode is not stated.
    :param has_forward: True when at least one forward-branch point is present,
        false when none is. Omit when not stated.
    :param has_reverse: True when at least one reverse-branch point is present,
        false when none is. Omit when not stated.
    :param ts_point_index: Optional index of the point marked as TS.
    :param point_count: Optional total sampled-point count (consistency check).
    :param zero_energy_reference_hartree: Optional energy used as relative zero.
    :param note: Optional free-text note.
    :param points: Sampled IRC-path points attached to the result.
    """

    direction: IRCDirection | None = None
    has_forward: bool | None = None
    has_reverse: bool | None = None
    ts_point_index: int | None = Field(default=None, ge=0)
    point_count: int | None = Field(default=None, ge=0)
    zero_energy_reference_hartree: float | None = None
    note: str | None = None
    points: list[IRCPointPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        """Enforce unique indices, TS index consistency, and direction flags.

        A flag that is *stated false* contradicts a point in that direction; a
        flag that is not stated contradicts nothing, and is left unstated
        rather than inferred from the points.
        """

        if not self.points:
            return self

        indices = [point.point_index for point in self.points]
        if len(set(indices)) != len(indices):
            raise ValueError("IRC point_index values must be unique.")

        if (
            self.ts_point_index is not None
            and self.ts_point_index not in set(indices)
        ):
            raise ValueError(
                "ts_point_index must match the point_index of one of the provided points."
            )

        has_forward_in_points = any(
            point.direction == IRCDirection.forward for point in self.points
        )
        has_reverse_in_points = any(
            point.direction == IRCDirection.reverse for point in self.points
        )
        if has_forward_in_points and self.has_forward is False:
            raise ValueError(
                "has_forward must be true when forward-direction points are provided."
            )
        if has_reverse_in_points and self.has_reverse is False:
            raise ValueError(
                "has_reverse must be true when reverse-direction points are provided."
            )
        return self


class PathSearchPointPayload(SchemaBase):
    """Upload-facing inline payload for one path-search point.

    Generalizes NEB images, GSM nodes, and string-method path points.

    :param point_index: Zero-based point index along the path.
    :param electronic_energy_hartree: Electronic energy at this point.
    :param relative_energy_kj_mol: Energy relative to the zero-energy reference.
    :param path_coordinate: Optional path-coordinate value (e.g. cumulative
        path distance for NEB, reaction-coordinate for GSM/string methods).
    :param max_force: Max force component at this point.
    :param rms_force: RMS force at this point.
    :param max_gradient: Max gradient component at this point.
    :param rms_gradient: RMS gradient at this point.
    :param is_ts_guess: Whether this point is the algorithm's TS guess.
    :param is_climbing_image: Whether this image was the climbing image
        (NEB-CI specific; ignored by string-method outputs). ``false``
        is the default and is stored exactly as sent, so ``false`` reads as
        "not a climbing image" and as "not stated" alike: a producer that
        does not know should name the climbing image through the result's
        ``climbing_image_index`` or leave every point at the default. A
        tri-state value would need a nullable column and is not offered yet.
    :param geometry: Optional inline geometry payload for this point.
    :param note: Optional free-text note.
    """

    point_index: int = Field(ge=0)
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    path_coordinate: float | None = None
    max_force: float | None = None
    rms_force: float | None = None
    max_gradient: float | None = None
    rms_gradient: float | None = None
    is_ts_guess: bool = False
    is_climbing_image: bool = False
    geometry: GeometryPayload | None = None
    note: str | None = None


class PathSearchResultPayload(SchemaBase):
    """Upload-facing inline result bundle for a path-search calculation.

    A path-search calculation explores a reaction path between or from
    molecular endpoints to produce a TS guess. The specific algorithm
    (NEB, GSM, growing/freezing string, ...) lives on ``method`` rather
    than as a separate top-level calculation type.

    :param method: The path-search algorithm used.
    :param is_double_ended: Whether the algorithm uses two endpoints
        (NEB, GSM) versus single-ended (growing string, freezing string).
    :param converged: Whether the path search met its own stopping
        criteria. What that means depends on ``method``, and the producer
        states the algorithm's verdict, not a proxy for it:

        * ``neb``: the band's force (and, where used, step) thresholds were
          satisfied, climbing image included for a climbing-image run, and
          the run did not stop on its iteration limit.
        * ``gsm``, ``growing_string``, ``freezing_string``: the string
          finished growing (or freezing) and the program's own convergence
          test on the path, or on the TS node it reports, passed.
        * ``other``: the method's own convergence verdict; describe the
          criterion in ``note``.

        ``true`` means that verdict was observed. ``false`` means it was
        observed to fail. Leave it absent when it was not observed: the
        existence of an output file, a nonzero exit code or a parsed TS
        guess is not a convergence verdict, so none of them justifies
        ``true``.
    :param n_points: Total sampled-point count (consistency check).
    :param selected_ts_point_index: Index of the point selected as the
        TS guess (0-based). Must match a ``points[].point_index``.
    :param climbing_image_index: Optional index of the climbing image in
        NEB-CI runs.
    :param source_endpoint_count: Optional count of endpoint geometries
        consumed by the algorithm (typically 2 for double-ended runs).
    :param zero_energy_reference_hartree: Energy used as relative zero
        for ``relative_energy_kj_mol`` on each point.
    :param note: Optional free-text note.
    :param points: Path samples (images / nodes / path points), unique on
        ``point_index``.
    """

    method: PathSearchMethod
    is_double_ended: bool | None = None
    converged: bool | None = None
    n_points: int | None = Field(default=None, ge=1)
    selected_ts_point_index: int | None = Field(default=None, ge=0)
    climbing_image_index: int | None = Field(default=None, ge=0)
    source_endpoint_count: int | None = Field(default=None, ge=1)
    zero_energy_reference_hartree: float | None = None
    note: str | None = None
    points: list[PathSearchPointPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        """Enforce unique ``point_index``, TS-index / climbing-index
        consistency, and ``n_points`` agreement with the point list."""

        indices = [p.point_index for p in self.points]
        if len(set(indices)) != len(indices):
            raise ValueError("Path-search point_index values must be unique.")
        index_set = set(indices)

        if (
            self.selected_ts_point_index is not None
            and self.selected_ts_point_index not in index_set
        ):
            raise ValueError(
                "selected_ts_point_index must match the point_index of one "
                "of the provided points."
            )

        if (
            self.climbing_image_index is not None
            and self.climbing_image_index not in index_set
        ):
            raise ValueError(
                "climbing_image_index must match the point_index of one "
                "of the provided points."
            )

        if self.n_points is not None and self.n_points != len(self.points):
            raise ValueError(
                f"n_points={self.n_points} does not match the number of "
                f"provided points ({len(self.points)})."
            )
        return self


class CalculationParameterObservation(SchemaBase):
    """Upload-facing payload for one parsed execution-control parameter.

    Mirrors the ``calculation_parameter`` row contract: ``raw_key`` and
    ``raw_value`` are the always-present capture surface, every other field
    is optional enrichment. ``canonical_key`` is best-effort — when the
    parser emits a key that is not yet in ``calculation_parameter_vocab``
    the persistence layer demotes it to ``None`` rather than failing the
    upload.

    :param raw_key: Raw, software-specific key as parsed (e.g. ``"calcfc"``).
    :param raw_value: Raw value as parsed.
    :param canonical_key: Optional canonical key for cross-software queries.
    :param canonical_value: Optional normalized value paired with the key.
    :param section: Optional route-line section (``opt``, ``scf``, ...).
    :param value_type: Optional consumer hint (``bool``, ``int``, ...).
    :param unit: Optional unit string when the value carries dimensionality.
    :param parameter_index: Optional ordering for repeated/positional options.
    """

    raw_key: str = Field(min_length=1)
    raw_value: str
    canonical_key: str | None = None
    canonical_value: str | None = None
    section: str | None = None
    value_type: str | None = None
    unit: str | None = None
    parameter_index: int | None = Field(default=None, ge=0)


class OutputGeometryEntry(SchemaBase):
    """One declared output geometry on a calculation upload payload.

    Each entry carries a geometry payload and the role this geometry plays
    as a calculation output. The list position determines the
    ``output_order`` written to ``calculation_output_geometry`` (1-indexed).
    Producers must declare ``role`` explicitly; defaulting to ``final``
    would silently mis-classify scan iterations, IRC path points, and NEB
    images.
    """

    geometry: GeometryPayload
    role: CalculationGeometryRole


class CalculationWithResultsPayload(CalculationPayload):
    """A calculation with optional typed result blocks.

    Extends ``CalculationPayload`` with opt/freq/sp/irc/path_search/scan
    result fields. Validation enforces that only the result type matching the
    calculation type may be provided.

    :param opt_result: Inline optimisation result (type must be ``opt``).
    :param freq_result: Inline frequency result (type must be ``freq``).
    :param sp_result: Inline single-point result (type must be ``sp``).
    :param composite_result: Inline composite energy (type must be ``composite``,
        and a ``composite`` calculation must carry it). See
        :class:`CompositeResultPayload`.
    :param sp_energy_components: Inline parts of the single point's
        electronic energy (type must be ``sp``).
    :param irc_result: Inline IRC result bundle (type must be ``irc``).
    :param path_search_result: Inline path-search result bundle (type
        must be ``path_search``). Carries NEB, GSM, and other path-based
        TS-search algorithms via ``path_search_result.method``.
    :param scan_result: Inline scan result (type must be ``scan``): the
        stepped coordinates and the points along them. Whether a route accepts
        a ``scan`` calculation at all is that route's own allow-list; this
        field is only where the points go when it does.
    :param parameters: Optional parsed execution-control parameter
        observations. Each becomes one ``calculation_parameter`` row.
    :param parameters_json: Optional JSON snapshot of the parser output
        (debug/traceability only — relational rows are the queryable layer).
    :param parameters_parser_version: Optional version tag of the parser
        that produced the observations.
    :param parameters_extracted_at: Optional timestamp of extraction.
    """

    opt_result: OptResultPayload | None = None
    freq_result: FreqResultPayload | None = None
    sp_result: SPResultPayload | None = None
    composite_result: CompositeResultPayload | None = None
    sp_energy_components: list[SPEnergyComponentPayload] = Field(
        default_factory=list, description=SP_ENERGY_COMPONENTS_DESCRIPTION
    )
    irc_result: IRCResultPayload | None = None
    path_search_result: PathSearchResultPayload | None = None
    # Resolved at the foot of this module: ``fragments.scan`` imports
    # ``CalculationConstraintCreate`` from here, so it cannot be imported above.
    scan_result: "CalculationScanResultCreate | None" = None
    execution_environment: ExecutionEnvironmentManifestPayload | None = None

    scf_stability: SCFStabilityPayload | None = None
    wavefunction_diagnostic: WavefunctionDiagnosticPayload | None = None
    spin_diagnostic: SpinDiagnosticPayload | None = None
    hessian: HessianPayload | None = None

    input_geometries: list[GeometryPayload] = Field(
        default_factory=list,
        description=(
            "Geometries this calculation was run on. When empty, the "
            "workflow falls back to the conformer's reference geometry "
            "for calculation types in {freq, sp}; opt skips. List "
            "order maps to input_order = 1, 2, 3, ... in the database."
        ),
    )

    output_geometries: list[OutputGeometryEntry] = Field(
        default_factory=list,
        description=(
            "Geometries this calculation produced or reported. When "
            "empty, the workflow falls back to the conformer's "
            "reference geometry as a single (role=final, output_order=1) "
            "row for calc types in the narrow set {opt}. Freq, sp, "
            "and all other types get zero rows when the producer "
            "leaves this empty. List order maps to output_order = "
            "1, 2, 3, ... in the database."
        ),
    )

    parameters: list[CalculationParameterObservation] | None = None
    parameters_json: dict | None = None
    parameters_parser_version: str | None = None
    parameters_extracted_at: datetime | None = None

    constraints: list[CalculationConstraintCreate] = Field(
        default_factory=list,
        description=(
            "Coordinate constraints held fixed during this calculation. "
            "Generic across opt, freq, sp, irc, path_search, scan, and any "
            "other constrained run — these are input/provenance metadata and "
            "do not require a result block. For scan calculations, frozen "
            "coordinates may be declared here while the stepped coordinate "
            "is declared on the scan_result.coordinates list."
        ),
    )

    def transition_state_frequency_findings(
        self, *, location: str
    ) -> list[StationaryPointFinding]:
        """Judge this calculation's frequency evidence as a transition state.

        A thin adapter over the single owner in
        :mod:`tckdb_schemas.stationary_point` — it knows payload shapes,
        which that module deliberately does not, and it knows nothing
        about the physics, which is entirely there.
        """
        if self.freq_result is None:
            return []
        return evaluate_transition_state_frequency(
            self.freq_result.n_imag,
            self.freq_result.imag_freq_cm1,
            location=location,
            imaginary_modes=self.freq_result.imaginary_modes(),
            reaction_coordinate_mode_index=(
                self.freq_result.reaction_coordinate_mode_index
            ),
            tau=self.tau_resolution(),
        )

    def frequency_completeness_findings(
        self, *, location: str, fallback_xyz_text: str | None = None
    ) -> list[StationaryPointFinding]:
        """Judge whether this calculation's frequency list is the spectrum.

        A thin adapter over :mod:`tckdb_schemas.frequency_completeness`,
        which owns the arithmetic and knows no payload shapes. What is
        payload-shaped, and lives here, is *which* geometry the frequency
        job ran on: ``input_geometries[0]`` when the producer named one,
        and otherwise the reference geometry the workflow will fall back
        to — the same fallback ``input_geometries``' own description
        documents, so the check counts the atoms the database will end up
        binding this calculation to rather than a different set.

        :param location: Path to the payload element, used verbatim.
        :param fallback_xyz_text: The enclosing conformer's or transition
            state's reference geometry, for the common case of a freq
            calculation that names no input geometry of its own.
        """
        if self.freq_result is None or self.freq_result.modes is None:
            return []
        return evaluate_deposited_frequency_list(
            len(self.freq_result.modes),
            input_geometry_xyz_text=(
                self.input_geometries[0].xyz_text
                if self.input_geometries
                else None
            ),
            fallback_xyz_text=fallback_xyz_text,
            location=location,
        )

    def tau_resolution(self) -> TauResolution:
        """ADR 0012's τ for this calculation, from its own parameters.

        Reads the ``canonical_key``/``canonical_value`` pairs the
        producer deposited. A payload that carries no parameters — the
        common case for a hand-written upload — resolves to the
        conservative "protocol not recorded" row, and says so in the
        stored reason rather than silently picking a number.
        """
        return resolve_tau_from_parameters(
            (observation.canonical_key, observation.canonical_value)
            for observation in (self.parameters or ())
        )

    @model_validator(mode="after")
    def validate_constraint_indices_unique(self) -> Self:
        indices = [c.constraint_index for c in self.constraints]
        if len(set(indices)) != len(indices):
            raise ValueError(
                "Calculation constraint_index values must be unique within a calculation."
            )
        return self

    @model_validator(mode="after")
    def validate_result_matches_type(self) -> Self:
        """Ensure only the result block matching ``self.type`` is set."""
        allowed = {
            CalculationType.opt: "opt_result",
            CalculationType.freq: "freq_result",
            CalculationType.sp: "sp_result",
            CalculationType.irc: "irc_result",
            CalculationType.path_search: "path_search_result",
            CalculationType.scan: "scan_result",
        }
        allowed_field = allowed.get(self.type)
        for field_name in (
            "opt_result",
            "freq_result",
            "sp_result",
            "irc_result",
            "path_search_result",
            "scan_result",
        ):
            value = getattr(self, field_name)
            if value is not None and field_name != allowed_field:
                raise ValueError(
                    f"Result block '{field_name}' is not allowed for "
                    f"calculation type '{self.type.value}'. "
                    f"Expected '{allowed_field}' or no result."
                )
        assert_composite_result_matches_type(self.type, self.composite_result)
        return self

    @model_validator(mode="after")
    def validate_sp_energy_components(self) -> Self:
        """Components sit on a single point and agree with its energy (ADR 0021)."""
        check_sp_energy_components(
            [(c.component, c.value_hartree) for c in self.sp_energy_components],
            calculation_type=self.type,
            electronic_energy_hartree=(
                self.sp_result.electronic_energy_hartree if self.sp_result is not None else None
            ),
        )
        return self

    @model_validator(mode="after")
    def validate_tckdb_origin_metadata(self) -> Self:
        """If ``parameters_json["tckdb_origin"]`` is present, validate
        its shape against :class:`CalculationOriginMetadata`. Absence is
        allowed and means "executed" by default. See DR-0026 for the
        full convention.
        """
        if not self.parameters_json or not isinstance(self.parameters_json, dict):
            return self
        origin_block = self.parameters_json.get("tckdb_origin")
        if origin_block is None:
            return self
        CalculationOriginMetadata.model_validate(origin_block)
        return self


# ``fragments.scan`` needs ``CalculationConstraintCreate`` from this module, so
# the scan result type cannot be named above. Importing the module (not a name
# from it) is safe whichever of the two is imported first: if ``scan`` is the
# one mid-import, this line finds it already in ``sys.modules`` and does
# nothing, and ``scan`` finishes the job itself with the rebuild at its foot.
import tckdb_schemas.fragments.scan  # noqa: E402,F401
