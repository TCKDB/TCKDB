"""What an existing (legacy) statmech record establishes for each supported claim, from its stored facts alone.

A statmech row carries no explicit model membership, no energy-reference or included-correction convention and
no application recipe. It can still support a *narrow* claim from facts it does carry. This module is the one
place that says which facts each claim needs and what a stored fact does and does not establish. It is pure: the
inventory feeds it counts, and the assessor (a later stage) feeds it the same facts after live checks, so what the
inventory predicts and what an assessment finds cannot drift apart.

Rules that run through it:

* A requirement is ``present``, ``not_required`` (nothing to establish: an atom has no modes), ``ambiguous`` (something is stored but does not pin the question: several
  geometry alternatives, an unknown projection status) or ``missing``. Nothing is defaulted: an absent scale
  factor is *unknown application*, not unity; an absent optical factor is unknown, not one; a record that does not
  state the translational normalisation of a full partition function is not a full model.
* A profile is ``usable`` only when every requirement is present or not required; ``absent`` when every requirement
  that applies is missing (the record represents nothing of the claim); otherwise ``ambiguous``. A row whose owner kind or scope cannot
  carry the claim at all (a transition state for a stable-species claim, an atom for a vibrational component) is
  ``not_applicable``: that is not a data gap.
* A non-null ``frequency_scale_factor_id`` establishes that a factor of a stated *purpose* is linked to the record. It
  does not establish that the factor was applied to the frequencies a claim uses: application is unknown until a
  declaration states it. The one existing evidence of application is an applied energy correction that carries a
  frequency scale factor on one of the record's own source calculations; it establishes application for the purpose of
  that factor (fundamental, zero-point, enthalpy, entropy or heat capacity), and a factor of another purpose does not
  stand in for it.
* Facts a legacy row cannot hold are missing for every legacy row, by construction: the energy-reference zero and
  the zero-point inclusion convention, and the translational normalisation. Those claims are reachable only
  through a declared model.

``usable`` here means the stored facts do not stand in the way. It is not an assessment: source status, review,
findings and geometry coherence are checked live by the assessor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.services.statmech_selection.vocabulary import OwnerKind

PRESENT = "present"
AMBIGUOUS = "ambiguous"
MISSING = "missing"
#: Nothing to establish: an atom has no modes to scale, a record with no rotor has no rotor to project out.
NOT_REQUIRED = "not_required"

TORSION_BEARING_TREATMENTS = frozenset({"rrho_1d", "rrho_nd", "rrho_1d_nd", "rrho_ad", "rrao"})


class Readiness(str, Enum):
    usable = "represented_and_usable"
    ambiguous = "represented_but_ambiguous"
    absent = "absent"
    not_applicable = "not_applicable"


#: The claims a legacy record can be asked about. ``canonical_model`` and ``within_basin_thermal`` are split by
#: observable: a scale factor is calibrated for a purpose, and the purposes are not interchangeable.
PROFILES: tuple[str, ...] = (
    "rotational_component",
    "vibrational_component",
    "zpe_component",
    "within_basin_thermal.entropy",
    "within_basin_thermal.heat_capacity",
    "canonical_model.internal_partition",
    "canonical_model.full_partition",
    "conventional_ts_transverse_model",
)


@dataclass(frozen=True)
class LegacyFacts:
    """The stored facts of one statmech record (counts and presence, never an id)."""

    owner_kind: OwnerKind
    scientific_origin: str
    rigid_rotor_kind: str | None = None
    is_linear: bool | None = None
    n_rotational_constants: int = 0
    external_symmetry: int | None = None
    optical_isomers: int | None = None
    statmech_treatment: str | None = None
    uses_projected_frequencies: bool | None = None
    scale_factor_kind: str | None = None
    has_scale_factor: bool = False
    #: Purposes of the frequency scale factors an applied energy correction carries on the record's own source
    #: calculations: the recorded evidence that a factor of that purpose was applied.
    applied_scale_kinds: frozenset[str] = frozenset()
    n_electronic_levels: int = 0
    n_torsions: int = 0
    n_torsions_without_kind: int = 0
    n_torsions_hindered_without_scan: int = 0
    n_torsions_invalidated: int = 0
    #: Source calculations that carry a frequency or Hessian result, whatever their role or job type.
    n_curvature_sources: int = 0
    #: Distinct geometries across the geometry-bearing source calculations (opt, freq, composite).
    n_source_geometries: int = 0
    #: ``none`` / ``single`` / ``multiple`` structure determinations that name one of the sources (TS owners).
    determination_tie: str = "none"
    reaction_coordinate_designated: bool | None = None
    extra: dict[str, int] = field(default_factory=dict)

    @property
    def is_atom(self) -> bool:
        return self.rigid_rotor_kind == "atom"

    @property
    def torsion_bearing(self) -> bool:
        return self.n_torsions > 0 or self.statmech_treatment in TORSION_BEARING_TREATMENTS


def _rotor(f: LegacyFacts) -> str:
    if f.is_atom:
        return PRESENT
    n = f.n_rotational_constants
    kind = f.rigid_rotor_kind
    if kind is None and f.is_linear is True and n >= 1:
        return PRESENT
    if kind is None:
        return MISSING if n == 0 and f.is_linear is None else AMBIGUOUS
    needed = {"linear": 1, "spherical_top": 1, "symmetric_top": 2, "asymmetric_top": 3}[kind]
    if n >= needed:
        return PRESENT
    return MISSING if n == 0 else AMBIGUOUS


def _external_symmetry(f: LegacyFacts) -> str:
    if f.is_atom:
        return NOT_REQUIRED
    return PRESENT if f.external_symmetry is not None else MISSING


def _geometry(f: LegacyFacts) -> str:
    if f.is_atom or f.scientific_origin != "computed":
        return NOT_REQUIRED
    if f.n_source_geometries == 1:
        return PRESENT
    return AMBIGUOUS if f.n_source_geometries > 1 else MISSING


def _curvature(f: LegacyFacts) -> str:
    if f.is_atom:
        return NOT_REQUIRED
    return PRESENT if f.n_curvature_sources >= 1 else MISSING


def _mode_treatment(f: LegacyFacts) -> str:
    if f.is_atom:
        return NOT_REQUIRED
    return PRESENT if f.statmech_treatment is not None else MISSING


def _scale(f: LegacyFacts, *, kind: str) -> str:
    """Whether a factor of purpose ``kind`` was applied.

    Present on recorded application; ambiguous when a factor is linked (or applied for another purpose) but its
    application for this purpose is not stated; missing otherwise.
    """
    if f.is_atom:
        return NOT_REQUIRED
    if kind in f.applied_scale_kinds:
        return PRESENT
    if f.has_scale_factor or f.applied_scale_kinds:
        return AMBIGUOUS
    return MISSING


def _projection(f: LegacyFacts) -> str:
    if f.is_atom or not f.torsion_bearing:
        return NOT_REQUIRED
    return PRESENT if f.uses_projected_frequencies is not None else AMBIGUOUS


def _rotor_treatment(f: LegacyFacts) -> str:
    if f.is_atom or not f.torsion_bearing:
        return NOT_REQUIRED
    if f.n_torsions == 0:
        return MISSING
    if f.n_torsions_invalidated or f.n_torsions_without_kind or f.n_torsions_hindered_without_scan:
        return AMBIGUOUS
    return PRESENT


def _optical(f: LegacyFacts) -> str:
    return PRESENT if f.optical_isomers is not None else MISSING


def _electronic(f: LegacyFacts) -> str:
    return PRESENT if f.n_electronic_levels >= 1 else MISSING


def _tie(f: LegacyFacts) -> str:
    return {"single": PRESENT, "multiple": AMBIGUOUS}.get(f.determination_tie, MISSING)


def _reaction_coordinate(f: LegacyFacts) -> str:
    return PRESENT if f.reaction_coordinate_designated is True else MISSING


#: The scale-factor purpose each claim needs. A ZPE-calibrated factor is not an entropy or heat-capacity factor, and
#: neither is a fundamental-frequency factor; the purposes are not interchangeable proof.
SCALE_PURPOSE: dict[str, str] = {
    "vibrational_component": "fundamental",
    "zpe_component": "zpe",
    "within_basin_thermal.entropy": "entropy",
    "within_basin_thermal.heat_capacity": "heat_capacity",
    "canonical_model.internal_partition": "fundamental",
    "canonical_model.full_partition": "fundamental",
    "conventional_ts_transverse_model": "fundamental",
}


def requirements(profile: str, f: LegacyFacts) -> dict[str, str]:
    """``{requirement: present|not_required|ambiguous|missing}`` for one claim. Deterministic and total."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    if profile == "rotational_component":
        return {"rotor": _rotor(f), "external_symmetry": _external_symmetry(f), "geometry": _geometry(f)}
    scale = _scale(f, kind=SCALE_PURPOSE[profile])
    if profile == "vibrational_component":
        return {
            "curvature_source": _curvature(f),
            "geometry": _geometry(f),
            "mode_treatment": _mode_treatment(f),
            "scale_application": scale,
            "projection_status": _projection(f),
        }
    if profile == "zpe_component":
        return {
            "curvature_source": _curvature(f),
            "scale_application": scale,
            "energy_reference_convention": MISSING,
            "included_corrections": MISSING,
        }
    internal = {
        "rotor": _rotor(f),
        "external_symmetry": _external_symmetry(f),
        "optical_factor": _optical(f),
        "electronic_degeneracy": _electronic(f),
        "curvature_source": _curvature(f),
        "geometry": _geometry(f),
        "mode_treatment": _mode_treatment(f),
        "scale_application": scale,
        "projection_status": _projection(f),
        "rotor_treatment": _rotor_treatment(f),
    }
    if profile == "canonical_model.full_partition":
        return {**internal, "translational_normalization": MISSING, "energy_reference_convention": MISSING}
    if profile == "conventional_ts_transverse_model":
        # A chiral saddle point exists, so the optical factor stays a requirement here too.
        return {**internal, "reaction_coordinate_designation": _reaction_coordinate(f), "determination_tie": _tie(f)}
    return internal


def _owner_fits(profile: str, owner: OwnerKind) -> bool:
    if profile == "conventional_ts_transverse_model":
        return owner is OwnerKind.transition_state_entry
    if profile.startswith(("within_basin_thermal", "canonical_model")):
        return owner is OwnerKind.species_entry
    return True


def _scope_fits(profile: str, f: LegacyFacts) -> bool:
    """An atom has no vibrational component to qualify (the request contract refuses that scope too)."""
    return not (profile == "vibrational_component" and f.is_atom)


def classify(profile: str, f: LegacyFacts) -> tuple[Readiness, dict[str, str]]:
    """The readiness of one legacy record for one claim, with the per-requirement states behind it."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    if not _owner_fits(profile, f.owner_kind) or not _scope_fits(profile, f):
        return Readiness.not_applicable, {}
    states = requirements(profile, f)
    values = set(states.values()) - {NOT_REQUIRED}
    if values <= {PRESENT}:
        return Readiness.usable, states
    if values == {MISSING}:
        return Readiness.absent, states
    return Readiness.ambiguous, states
