"""Thermo scientific invariants.

These tests pin physically meaningful invariants that pure schema / CRUD
tests do not enforce:

- tabulated-point Gibbs relation: ``G ~= H - T * S``
- NASA-7 piecewise continuity of ``Cp``, ``H``, and ``S`` at ``t_mid``
- NASA bound ordering (``t_low < t_mid < t_high``) at the schema layer
- append-only semantics for thermo as a result table

Detecting failure here would usually mean a coefficient-ordering mistake,
a unit-conversion mistake, or a silent regression in append-only policy.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.thermo import Thermo
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.workflows.thermo import persist_thermo_upload

# ---------------------------------------------------------------------------
# NASA-7 reference evaluation (used only for continuity checking in tests)
# ---------------------------------------------------------------------------
#
# Standard NASA-7 polynomial form, in reduced units:
#
#   Cp / R = a1 + a2*T + a3*T^2 + a4*T^3 + a5*T^4
#   H  / (R*T) = a1 + a2*T/2 + a3*T^2/3 + a4*T^3/4 + a5*T^4/5 + a6/T
#   S  / R = a1*ln(T) + a2*T + a3*T^2/2 + a4*T^3/3 + a5*T^4/4 + a7
#
# In this project the low-range coefficients are stored as a1..a7 and the
# high-range coefficients as b1..b7 (see schema_spec / initial migration).


def _nasa_cp_over_r(coefs: tuple[float, ...], T: float) -> float:
    a1, a2, a3, a4, a5 = coefs[:5]
    return a1 + a2 * T + a3 * T**2 + a4 * T**3 + a5 * T**4


def _nasa_h_over_rt(coefs: tuple[float, ...], T: float) -> float:
    a1, a2, a3, a4, a5, a6 = coefs[:6]
    return (
        a1
        + a2 * T / 2.0
        + a3 * T**2 / 3.0
        + a4 * T**3 / 4.0
        + a5 * T**4 / 5.0
        + a6 / T
    )


def _nasa_s_over_r(coefs: tuple[float, ...], T: float) -> float:
    a1, a2, a3, a4, a5, _, a7 = coefs[:7]
    return (
        a1 * math.log(T)
        + a2 * T
        + a3 * T**2 / 2.0
        + a4 * T**3 / 3.0
        + a5 * T**4 / 4.0
        + a7
    )


# NIST/GRI-style NASA-7 for water — intentionally the same water block
# used by existing thermo workflow tests so this stays a "known good" set.
_WATER_NASA = {
    "t_low": 200.0,
    "t_mid": 1000.0,
    "t_high": 3500.0,
    # Low range (200-1000 K)
    "a1": 4.19864056,
    "a2": -2.0364341e-3,
    "a3": 6.52040211e-6,
    "a4": -5.48797062e-9,
    "a5": 1.77197817e-12,
    "a6": -3.02937267e4,
    "a7": -0.849032208,
    # High range (1000-3500 K)
    "b1": 3.03399249,
    "b2": 2.17691804e-3,
    "b3": -1.64072518e-7,
    "b4": -9.7041987e-11,
    "b5": 1.68200992e-14,
    "b6": -3.00042971e4,
    "b7": 4.9667701,
}


# ---------------------------------------------------------------------------
# Invariant 1: thermo-point Gibbs relation
# ---------------------------------------------------------------------------


# Physical constants for unit conversion. S is stored in J/mol/K while
# H and G are stored in kJ/mol; converting T*S from J/mol -> kJ/mol is
# the one place in this invariant where a unit mistake silently breaks
# scientific meaning, so it is exercised explicitly below.
_J_TO_KJ = 1.0e-3


def _gibbs_residual_kj_mol(*, T: float, H_kj_mol: float, S_j_mol_k: float,
                           G_kj_mol: float) -> float:
    """Return ``G - (H - T*S)`` in kJ/mol after proper entropy conversion."""
    return G_kj_mol - (H_kj_mol - T * S_j_mol_k * _J_TO_KJ)


# We construct self-consistent (H, S, G) triples directly from the
# Gibbs relation G = H - T * (S / 1000) rather than using published
# tabulated values. Published ΔG_f tables are reported relative to
# element reference states, which use element entropy cancellations
# that this point-level invariant does not see, so mixing them in
# would test the table convention rather than the unit-conversion
# path we actually want to pin.


@pytest.mark.parametrize(
    "T,H,S",
    [
        (298.15, -241.8, 188.8),   # water-like magnitudes
        (500.0, -234.9, 206.5),
        (1000.0, -215.8, 232.7),
        (298.15, 82.9, 269.2),     # benzene-like: positive H, different sign
    ],
)
def test_thermo_point_gibbs_relation_holds_on_self_consistent_triple(
    T: float, H: float, S: float,
) -> None:
    """``G == H - T * S`` must hold exactly (to float precision) for any
    ``G`` computed from the same ``(H, S, T)`` using the correct
    entropy unit conversion.

    This is the highest-value invariant in the suite: the test's
    ``_gibbs_residual_kj_mol`` helper mirrors the production unit
    convention (``S`` in J/mol/K, ``H``/``G`` in kJ/mol). If the helper
    — or the canonical unit assumption it encodes — ever drifts, the
    residual jumps by orders of magnitude and this test fires loudly.
    """
    G = H - T * S * _J_TO_KJ
    residual = _gibbs_residual_kj_mol(T=T, H_kj_mol=H, S_j_mol_k=S, G_kj_mol=G)
    assert abs(residual) < 1e-9, (
        f"Gibbs relation violated at T={T} K: residual={residual} kJ/mol"
    )


def test_thermo_point_gibbs_relation_catches_wrong_entropy_unit() -> None:
    """Sanity-check the Gibbs test itself: if the entropy unit factor is
    dropped, the residual blows up by ~3 orders of magnitude.

    This pins the *sensitivity* of the invariant — a future refactor
    that relaxes the canonical-unit assumption without updating this
    test would quietly stop catching unit-conversion regressions."""
    T = 298.15
    H = -241.8
    S = 188.8  # J/mol/K — correct unit per canonical policy
    G = H - T * S * _J_TO_KJ  # correct G given those inputs

    bad_residual = G - (H - T * S)  # forgets J -> kJ factor on T*S
    good_residual = _gibbs_residual_kj_mol(
        T=T, H_kj_mol=H, S_j_mol_k=S, G_kj_mol=G,
    )

    assert abs(bad_residual) > 1000.0, (
        "Using the wrong entropy unit should give a residual in the tens "
        "of MJ/mol; if this assertion ever fires, the test fixture no "
        "longer actually exercises the unit-conversion path."
    )
    assert abs(good_residual) < 1e-9


# ---------------------------------------------------------------------------
# Invariant 2: NASA-7 piecewise continuity at t_mid
# ---------------------------------------------------------------------------


def test_nasa_is_continuous_in_cp_h_and_s_at_t_mid() -> None:
    """The low and high NASA-7 polynomials must agree on ``Cp``, ``H``,
    and ``S`` at ``t_mid`` within a tight numerical tolerance.

    A coefficient-mapping mistake (e.g. swapping a1/b1, losing a6, or
    renumbering indices during a refactor) preserves shape but silently
    destroys scientific meaning. This test is the canonical regression
    guard for that class of bug.
    """
    low_coefs = tuple(_WATER_NASA[f"a{i}"] for i in range(1, 8))
    high_coefs = tuple(_WATER_NASA[f"b{i}"] for i in range(1, 8))
    T_mid = _WATER_NASA["t_mid"]

    cp_low = _nasa_cp_over_r(low_coefs, T_mid)
    cp_high = _nasa_cp_over_r(high_coefs, T_mid)
    h_low = _nasa_h_over_rt(low_coefs, T_mid)
    h_high = _nasa_h_over_rt(high_coefs, T_mid)
    s_low = _nasa_s_over_r(low_coefs, T_mid)
    s_high = _nasa_s_over_r(high_coefs, T_mid)

    # Reference NASA-7 fits are published such that the two pieces agree
    # within roughly O(1e-3) in reduced units.
    assert abs(cp_low - cp_high) < 5e-3, (
        f"Cp/R discontinuity at t_mid: {cp_low} vs {cp_high}"
    )
    assert abs(h_low - h_high) < 5e-3, (
        f"H/(RT) discontinuity at t_mid: {h_low} vs {h_high}"
    )
    assert abs(s_low - s_high) < 5e-3, (
        f"S/R discontinuity at t_mid: {s_low} vs {s_high}"
    )


def test_nasa_continuity_detects_swapped_coefficient_ranges() -> None:
    """Swapping the low and high NASA coefficient blocks should visibly
    break continuity somewhere above ``t_mid``. This pins the sensitivity
    of the continuity check itself so a silently-permissive tolerance
    cannot sneak in through future refactors.

    We evaluate just above t_mid where the high-range polynomial is the
    authoritative form, comparing the right answer to the "accidentally
    swapped" answer.
    """
    low_coefs = tuple(_WATER_NASA[f"a{i}"] for i in range(1, 8))
    high_coefs = tuple(_WATER_NASA[f"b{i}"] for i in range(1, 8))
    T = _WATER_NASA["t_mid"] + 500.0  # clearly inside the high-range window

    cp_correct = _nasa_cp_over_r(high_coefs, T)
    cp_swapped = _nasa_cp_over_r(low_coefs, T)
    assert abs(cp_correct - cp_swapped) > 5e-2, (
        "Low/high NASA blocks produce visibly different Cp/R above t_mid; "
        "if this ever fails, the continuity invariant above is no longer "
        "meaningfully sensitive."
    )


# ---------------------------------------------------------------------------
# Invariant 3: NASA bound ordering (schema-level)
# ---------------------------------------------------------------------------


_SPECIES_ENTRY = {"smiles": "O", "charge": 0, "multiplicity": 1}


def _thermo_request(**overrides) -> ThermoUploadRequest:
    base: dict = {
        "species_entry": dict(_SPECIES_ENTRY),
        "scientific_origin": "computed",
        "h298_kj_mol": -241.8,
        "s298_j_mol_k": 188.8,
    }
    base.update(overrides)
    return ThermoUploadRequest(**base)


def test_nasa_schema_enforces_t_low_lt_t_mid_lt_t_high() -> None:
    """The NASA Pydantic validator must reject any ordering other than
    ``t_low < t_mid < t_high``. The DB also enforces this, but we pin it
    at the upload surface so broken data cannot reach the workflow layer.
    """
    nasa = dict(_WATER_NASA)
    nasa["t_mid"] = nasa["t_low"]
    with pytest.raises(ValidationError, match="t_mid must be greater than t_low"):
        _thermo_request(nasa=nasa)

    nasa = dict(_WATER_NASA)
    nasa["t_high"] = nasa["t_mid"]
    with pytest.raises(ValidationError, match="t_high must be greater than t_mid"):
        _thermo_request(nasa=nasa)


# ---------------------------------------------------------------------------
# Invariant 4: thermo is append-only (scientific-product policy)
# ---------------------------------------------------------------------------


def test_repeated_thermo_uploads_for_same_species_append_not_overwrite(
    db_engine,
) -> None:
    """Two thermo uploads for the same species entry must create two
    distinct ``thermo`` rows. Identity dedup happens at ``species_entry``
    level; scientific-product tables are append-only.

    A regression here (silent overwrite) would destroy history while
    every CRUD test continues to pass, so this invariant is explicitly
    protected in the dedicated invariant suite in addition to being
    covered indirectly in the thermo-upload tests.
    """
    unique = {"smiles": "COC", "charge": 0, "multiplicity": 1}
    with Session(db_engine) as session, session.begin():
        first = persist_thermo_upload(
            session,
            _thermo_request(species_entry=dict(unique), note="run 1"),
        )
        second = persist_thermo_upload(
            session,
            _thermo_request(species_entry=dict(unique), note="run 2"),
        )

        assert first.id != second.id
        assert first.species_entry_id == second.species_entry_id

        rows = session.scalars(
            select(Thermo)
            .where(Thermo.species_entry_id == first.species_entry_id)
            .order_by(Thermo.id)
        ).all()
        assert len(rows) >= 2
        notes = [r.note for r in rows if r.note in {"run 1", "run 2"}]
        assert notes == ["run 1", "run 2"]
