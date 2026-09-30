"""Typed kinetics evidence blocks shared by every route that deposits a rate.

A rate coefficient is not reproducible without knowing which partition
functions it was built from (``KineticsInterpretationAssignmentUpload``) and
which tunneling correction was applied to it
(``KineticsTunnelingApplicationUpload``). These models, and the two
cross-field checks below, are the single definition of that evidence. Both
``KineticsUploadRequest`` (``POST /uploads/kinetics``) and ``BundleKineticsIn``
(``POST /uploads/computed-reaction``) use them, so the two routes cannot
disagree about what a valid interpretation or tunneling block is.

Every reference here is a *public ref* of a record deposited earlier; no
database id is accepted (the "no FK ids in upload schemas" rule). The
``transition_state_entry_ref``/``statmech_ref`` of a record created by the
same request cannot be known to the depositor in advance, so a request can
only cite records that already exist.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Self

from pydantic import Field, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import (
    EnergyCorrectionConvention,
    EnergyZeroConvention,
    KineticsDegeneracyInterpretation,
    KineticsEnsemblePolicy,
    KineticsStandardStateConvention,
    TunnelingModel,
)
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload
from tckdb_schemas.utils import normalize_optional_text

#: Description of ``t0_k`` on every route that carries it, so the two routes
#: cannot word the convention differently.
T0_K_DESCRIPTION = (
    "Reference temperature T0 of the Arrhenius expression, in K, meaning "
    "k = A * (T / T0)**n * exp(-Ea / (R * T)). Defaults to 1 K, which is "
    "the plain k = A * T**n * exp(-Ea / (R * T)) form. It applies to the "
    "scalar a, n and reported_ea of this record (for a falloff rate, to the "
    "high-pressure-limit Arrhenius; the low-pressure limit, PLOG entries, "
    "sum-of-Arrhenius terms and Chebyshev surfaces are always at 1 K)."
)


class ConformerSelectionContentRef(SchemaBase):
    """Content-first locator for a conformer-selection interpretation.

    Set the optional conformer_group_ref (a cg_ public ref, as returned by
    GET /scientific/conformer-groups) when one species entry owns more than
    one conformer group. Without it the other three fields can describe
    several stored selections at once, and the upload is refused with
    ambiguous_conformer_selection_locator listing the refs to choose from.
    """

    species_entry: SpeciesEntryIdentityPayload
    selection_kind: str = Field(min_length=1)
    assignment_scheme_ref: str | None = Field(default=None, min_length=1)
    # Optional, always: every payload valid before this field existed is
    # still valid, and this widens the locator rather than tightening it.
    #
    # A public ref and not a label. One species entry owning several
    # groups is normal -- ``resolve_conformer_group`` mints
    # ``conformer_1``, ``conformer_2``, ... for each basin it does not
    # recognise -- and each may carry its own ``lowest_energy`` selection
    # under no assignment scheme, which
    # ``uq_conformer_selection_conformer_group_id`` permits because it is
    # per-group. But ``uq_conformer_group_species_entry_id`` does *not*
    # declare ``postgresql_nulls_not_distinct``, so a species entry may
    # also own arbitrarily many *unlabelled* groups, and among those a
    # label field would discriminate nothing at all.
    #
    # A public ref is not a database FK id and this is not the "no FK IDs
    # in upload schemas" rule being bent: it is the opaque, stable handle
    # the read API already hands a client for a conformer group, so
    # citing one is a client naming a record it can see.
    conformer_group_ref: str | None = Field(default=None, min_length=1)


class KineticsInterpretationAssignmentUpload(SchemaBase):
    """Exact statmech/conformer/TS interpretation for one rate role.

    The three convention fields are machine tokens, not free text: a rate
    coefficient is not reproducible without knowing how conformers were
    combined, which standard state the partition functions use, and how
    symmetry was counted. ``other`` on any of them requires
    ``convention_note``.
    """

    role: str = Field(pattern="^(reactant|product|transition_state)$")
    participant_index: int | None = Field(default=None, ge=1)
    statmech_ref: str = Field(min_length=1)
    conformer_selection: ConformerSelectionContentRef | None = None
    transition_state_entry_ref: str | None = Field(default=None, min_length=1)
    ensemble_policy: KineticsEnsemblePolicy
    standard_state_convention: KineticsStandardStateConvention
    degeneracy_interpretation: KineticsDegeneracyInterpretation
    convention_note: str | None = None

    @model_validator(mode="after")
    def validate_role_shape(self) -> Self:
        if self.role == "transition_state" and self.transition_state_entry_ref is None:
            raise ValueError("transition_state interpretation requires transition_state_entry_ref.")
        if self.role != "transition_state" and self.transition_state_entry_ref is not None:
            raise ValueError("transition_state_entry_ref is only valid for role='transition_state'.")
        if self.role == "transition_state" and self.participant_index is not None:
            raise ValueError("participant_index is only valid for reactant/product interpretations.")
        if self.role != "transition_state" and self.participant_index is None:
            raise ValueError("reactant/product interpretations require participant_index.")
        if self.role == "transition_state" and self.conformer_selection is not None:
            # ``kinetics_interpretation_subject_shape`` requires a NULL
            # conformer_selection_id for a TS subject. Without this check the
            # id reached the INSERT and surfaced as an IntegrityError at flush
            # (a 500) instead of a 422. Conformer selection is a species-side
            # curation overlay; a TS candidate is chosen through
            # transition_state_selection, not through a conformer group.
            raise ValueError(
                "conformer_selection is only valid for reactant/product "
                "interpretations; a transition state is selected through its "
                "own transition-state selection, not a conformer group."
            )
        return self

    @model_validator(mode="after")
    def validate_other_requires_note(self) -> Self:
        self.convention_note = normalize_optional_text(self.convention_note)
        if (
            KineticsEnsemblePolicy.other
            in {self.ensemble_policy}
            or self.standard_state_convention == KineticsStandardStateConvention.other
            or self.degeneracy_interpretation == KineticsDegeneracyInterpretation.other
        ) and self.convention_note is None:
            raise ValueError(
                "convention_note is required when an interpretation convention is 'other'."
            )
        return self


class KineticsTunnelingApplicationUpload(SchemaBase):
    """Typed tunneling inputs/results, linked by public TS handle.

    :param model: The correction family. ``other`` is accepted only when the
        deposit is still replayable: it must carry a ``model_identifier``
        machine token naming the actual correction plus a result-artifact
        locator, so a reader who does not recognise the model can still see
        what was computed and by what.
    :param source_calculation_ref: The calculation the energies/barriers below
        were read from. Without it the numbers this block returns are
        untraceable.
    """

    model: TunnelingModel
    model_identifier: str | None = Field(
        default=None, pattern=r"^[a-z0-9]+(_[a-z0-9]+)*$"
    )
    transition_state_entry_ref: str = Field(min_length=1)
    source_calculation_ref: str | None = Field(default=None, min_length=1)
    # Signed normal-mode frequency: a TS imaginary mode is negative cm^-1.
    imaginary_frequency_cm1: float | None = None
    frequency_sign_convention: str = "negative_imaginary_cm1"
    reactant_energy_kj_mol: float | None = None
    product_energy_kj_mol: float | None = None
    # Barriers are signed relative to ``energy_zero_convention``; a submerged
    # barrier is legitimately negative.
    forward_barrier_kj_mol: float | None = None
    reverse_barrier_kj_mol: float | None = None
    energy_zero_convention: EnergyZeroConvention | None = None
    energy_correction_convention: EnergyCorrectionConvention | None = None
    convention_note: str | None = None
    sct_path_integral_artifact_calculation_ref: str | None = None
    sct_path_integral_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result_artifact_calculation_ref: str | None = Field(default=None, min_length=1)
    result_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_model_inputs(self) -> Self:
        self.convention_note = normalize_optional_text(self.convention_note)
        if (self.result_artifact_calculation_ref is None) != (self.result_artifact_sha256 is None):
            raise ValueError("result artifact requires both calculation ref and SHA-256.")
        if self.model == TunnelingModel.none:
            raise ValueError("tunneling_application.model must be a correction model, not 'none'.")
        if (self.sct_path_integral_artifact_calculation_ref is None) != (self.sct_path_integral_artifact_sha256 is None):
            raise ValueError("SCT path artifact requires both calculation ref and SHA-256.")
        if self.frequency_sign_convention != "negative_imaginary_cm1":
            raise ValueError("frequency_sign_convention must be 'negative_imaginary_cm1'.")
        if self.model in {TunnelingModel.wigner, TunnelingModel.eckart, TunnelingModel.sct} and self.imaginary_frequency_cm1 is None:
            raise ValueError("Wigner/Eckart tunneling requires imaginary_frequency_cm1.")
        if self.imaginary_frequency_cm1 is not None and self.imaginary_frequency_cm1 >= 0:
            raise ValueError("imaginary_frequency_cm1 must be negative under negative_imaginary_cm1.")
        if self.model == TunnelingModel.eckart and any(
            value is None for value in (self.reactant_energy_kj_mol, self.product_energy_kj_mol, self.forward_barrier_kj_mol, self.reverse_barrier_kj_mol, self.energy_zero_convention, self.energy_correction_convention)
        ):
            raise ValueError("Eckart tunneling requires reactant/product energies, forward/reverse barriers, and energy conventions.")
        if self.model == TunnelingModel.sct and self.sct_path_integral_artifact_calculation_ref is None:
            raise ValueError("SCT tunneling requires a path-integral artifact.")
        # An unrecognised correction is only useful if a reader can identify
        # it and re-derive it. Naming it 'other' and supplying nothing else is
        # an unfalsifiable claim, not evidence.
        if self.model == TunnelingModel.other:
            if self.model_identifier is None:
                raise ValueError(
                    "tunneling_application.model='other' requires model_identifier, "
                    "a machine token naming the correction actually applied "
                    "(e.g. 'zero_curvature_tunneling')."
                )
            if self.result_artifact_calculation_ref is None:
                raise ValueError(
                    "tunneling_application.model='other' requires a result artifact "
                    "(calculation ref + SHA-256) so the correction stays replayable."
                )
        elif self.model_identifier is not None:
            raise ValueError(
                "model_identifier is only valid when model='other'; the named "
                "models are already identified by 'model'."
            )
        if (
            self.energy_zero_convention == EnergyZeroConvention.other
            or self.energy_correction_convention == EnergyCorrectionConvention.other
        ) and self.convention_note is None:
            raise ValueError(
                "convention_note is required when an energy convention is 'other'."
            )
        return self


def default_tunneling_model_from_application(data):
    """Fill the ``tunneling_model`` label from the evidence block at parse time.

    Shared *before*-validator body. Normalisation belongs before validation:
    doing it in an after-validator mutated an already-constructed model, so
    the parsed object silently diverged from the validated one.
    """
    if not isinstance(data, dict):
        return data
    application = data.get("tunneling_application")
    if application is None or data.get("tunneling_model") is not None:
        return data
    model = (
        application.get("model")
        if isinstance(application, dict)
        else getattr(application, "model", None)
    )
    if model is not None:
        data = {**data, "tunneling_model": model}
    return data


def check_tunneling_declaration_agrees(
    tunneling_model: Enum | str | None,
    tunneling_application: KineticsTunnelingApplicationUpload | None,
) -> None:
    """Cross-check the tunneling label against its evidence block.

    ``tunneling_model`` is a *label*: a reported attribute of the rate, of
    the same kind a mechanism file or a paper's methods section carries.
    A literature rate whose authors state "Eckart tunneling was applied"
    genuinely has no imaginary frequency, no barriers and no artifact for
    the depositor to attach, and neither does a rate imported from a
    CHEMKIN mechanism. Demanding typed evidence for a label would force
    exactly the invention this schema exists to prevent, so its absence is
    reported as an upload warning instead.

    ``tunneling_application`` is *evidence*. When present it must be
    internally complete (enforced on that model) and must agree with the
    label it is offered for.
    """
    # Compared by value: the standalone route declares ``tunneling_model`` with
    # the backend's ``TunnelingModel`` and the evidence block uses this
    # package's, two enums with the same members.
    declared = getattr(tunneling_model, "value", tunneling_model)
    if tunneling_application is not None and declared != tunneling_application.model.value:
        raise ValueError("tunneling_application.model must match tunneling_model.")


def check_interpretation_set(
    assignments: Sequence[KineticsInterpretationAssignmentUpload],
    *,
    n_reactants: int,
    n_products: int,
    has_tunneling_application: bool,
) -> None:
    """An interpretation set, once offered, must be complete.

    Supplying ``interpretation_assignments`` is the claim "this rate was
    built from these partition functions in this database". A *partial*
    set is worse than none: it looks like provenance while leaving the
    unnamed participants entirely unaccounted for. So the completeness
    requirement attaches to that claim, not to ``scientific_origin``.

    ``scientific_origin='computed'`` means only "this number came from a
    calculation" — it does not mean the calculation's partition functions
    live here. A rate read out of a CHEMKIN mechanism, or an Arkane TST
    result deposited without its statmech, is computed in origin and
    carries no assignments; rejecting it would lose a real record. Its
    lack of assignments is reported as an upload warning instead.

    ``participant_index`` is bounded here rather than only at the
    persistence seam, so an out-of-range slot is a 422 and not a late
    500-shaped failure.
    """
    for assignment in assignments:
        if assignment.participant_index is None:
            continue
        limit = n_reactants if assignment.role == "reactant" else n_products
        if assignment.participant_index > limit:
            raise ValueError(
                f"interpretation_assignments {assignment.role}:"
                f"{assignment.participant_index} is outside the declared "
                f"{assignment.role} list (1..{limit})."
            )
    subjects = [
        "transition_state" if assignment.role == "transition_state" else f"{assignment.role}:{assignment.participant_index}"
        for assignment in assignments
    ]
    if len(subjects) != len(set(subjects)):
        raise ValueError("interpretation_assignments must be unique by role and participant_index.")
    if not assignments:
        return
    required = {f"reactant:{i}" for i in range(1, n_reactants + 1)} | {
        f"product:{i}" for i in range(1, n_products + 1)
    }
    # A tunneling correction is applied *to* a transition state, so the
    # rate's TS partition function must be named too.
    if has_tunneling_application:
        required.add("transition_state")
    missing = sorted(required - set(subjects))
    if missing:
        raise ValueError(
            "computed kinetics requires one interpretation_assignment per "
            f"reaction subject; missing: {missing}."
        )
