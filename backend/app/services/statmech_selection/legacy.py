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
  that applies is missing (the record represents nothing of the claim); otherwise ``ambiguous``. A row whose owner kind cannot carry the
  claim at all is ``not_applicable``: an owner mismatch is not a data gap.
* A non-null ``frequency_scale_factor_id`` establishes that a factor of a stated kind was applied (value 1.0 is an
  explicit "unscaled"); null establishes nothing.
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
    not_applicable = "not_applicable_owner_kind"


#: The claims a legacy record can be asked about. ``canonical_model`` is split by observable.
PROFILES: tuple[str, ...] = (
    "rotational_component",
    "vibrational_component",
    "zpe_component",
    "within_basin_thermal",
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


def _scale(f: LegacyFacts, *, kind: str | None = None) -> str:
    if f.is_atom:
        return NOT_REQUIRED
    if not f.has_scale_factor:
        return MISSING
    if kind is not None and f.scale_factor_kind != kind:
        return AMBIGUOUS  # a factor was applied, but not of the purpose this claim needs
    return PRESENT


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


def requirements(profile: str, f: LegacyFacts) -> dict[str, str]:
    """``{requirement: present|ambiguous|missing}`` for one claim. Deterministic and total."""
    if profile == "rotational_component":
        return {"rotor": _rotor(f), "external_symmetry": _external_symmetry(f), "geometry": _geometry(f)}
    if profile == "vibrational_component":
        return {
            "curvature_source": _curvature(f),
            "geometry": _geometry(f),
            "mode_treatment": _mode_treatment(f),
            "scale_application": _scale(f),
            "projection_status": _projection(f),
        }
    if profile == "zpe_component":
        return {
            "curvature_source": _curvature(f),
            "scale_application": _scale(f, kind="zpe"),
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
        "scale_application": _scale(f),
        "projection_status": _projection(f),
        "rotor_treatment": _rotor_treatment(f),
    }
    if profile in ("within_basin_thermal", "canonical_model.internal_partition"):
        return internal
    if profile == "canonical_model.full_partition":
        return {**internal, "translational_normalization": MISSING, "energy_reference_convention": MISSING}
    if profile == "conventional_ts_transverse_model":
        return {
            **{k: v for k, v in internal.items() if k != "optical_factor"},
            "reaction_coordinate_designation": _reaction_coordinate(f),
            "determination_tie": _tie(f),
        }
    raise ValueError(f"unknown profile {profile!r}")


def _owner_fits(profile: str, owner: OwnerKind) -> bool:
    if profile == "conventional_ts_transverse_model":
        return owner is OwnerKind.transition_state_entry
    if profile in ("within_basin_thermal", "canonical_model.internal_partition", "canonical_model.full_partition"):
        return owner is OwnerKind.species_entry
    return True


def classify(profile: str, f: LegacyFacts) -> tuple[Readiness, dict[str, str]]:
    """The readiness of one legacy record for one claim, with the per-requirement states behind it."""
    if not _owner_fits(profile, f.owner_kind):
        return Readiness.not_applicable, {}
    states = requirements(profile, f)
    values = set(states.values()) - {NOT_REQUIRED}
    if values <= {PRESENT}:
        return Readiness.usable, states
    if values == {MISSING}:
        return Readiness.absent, states
    return Readiness.ambiguous, states
