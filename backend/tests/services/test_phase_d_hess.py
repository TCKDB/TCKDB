"""D6: Hess advisory consistency, on records deposited through the real upload path.

Every eligible fixture is made the way a depositor would make it: a
transition-state upload (whose primary opt is the tunneling source
calculation), a kinetics upload carrying a ``tunneling_application`` block,
and one thermo upload per participant with a declared enthalpy basis. The
playground instance held no eligible record on 2026-09-27 (0 tunneling rows,
0 declared thermo), so these fixtures are the only eligible records there are.

Reasons no upload can produce -- the upload path refuses an unbalanced
reaction, a tunneling TS on another reaction entry, an unparseable SMILES --
are exercised by an in-memory edit of an uploaded fixture that is never
flushed; each such case says so.

Hand arithmetic, kJ/mol (D6-1..D6-4 of the Phase D plan):

* D6-1  CH4 + OH -> CH3 + H2O at 298.15 K. dfH: CH4 -74.6, OH 37.4, CH3 147.2,
  H2O -241.8, so dH = (147.2 - 241.8) - (-74.6 + 37.4) = -57.4. Claim
  dE = -54.6 - 0.0, residual = +2.8.
* D6-2  same reaction at 0 K. dfH(0 K): CH4 -66.6, OH 37.1, CH3 150.0,
  H2O -238.9, so dH = (150.0 - 238.9) - (-66.6 + 37.1) = -59.4. Claim
  dE = -60.0, residual = -0.6, identical when both energies are shifted by
  +12345 on the absolute scale.
* D6-3  2 CH3 -> C2H6 at 298.15 K. dfH: CH3 147.2, C2H6 -84.0, so
  dH = -84.0 - 2 * 147.2 = -378.4. Claim dE = -381.0, residual = -2.6.
  Counting CH3 once would give dH = -231.2 and an unbalanced reaction.
* D6-4  D6-1 with every participant as a NASA7 fit whose low branch is
  anchored (a6) at the D6-1 values, so dH(298.15) = -57.4, cross-checked
  against Cantera's own ``delta_standard_enthalpy``.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.schemas.workflows.transition_state_upload import TransitionStateUploadRequest
from app.services.consistency import engine
from app.services.consistency.core import currency, latest_recorded, thermo_inputs
from app.services.consistency.hess import (
    ELEMENT_REFERENCE_COMPILATION,
    ENDPOINT_IDENTITY,
    RUNNER,
    compare_hess,
)
from app.services.consistency.service import compare, current, invoke
from app.workflows.kinetics import persist_kinetics_upload
from app.workflows.thermo import persist_thermo_upload
from app.workflows.transition_state import persist_transition_state_upload

R = 8.31446261815324
T298 = 298.15

_SOFTWARE = {"name": "gaussian", "version": "16", "revision": "C.02"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}

SPECIES = {
    "CH4": ("C", 0, 1), "OH": ("[OH]", 0, 2), "CH3": ("[CH3]", 0, 2), "H2O": ("O", 0, 1),
    "C2H6": ("CC", 0, 1), "OH-": ("[OH-]", -1, 1), "CH3-": ("[CH3-]", -1, 1),
    "CD4": ("[2H]C([2H])([2H])[2H]", 0, 1), "CD3": ("[2H][C]([2H])[2H]", 0, 2), "HDO": ("[2H]O", 0, 1),
    "C2H5": ("C[CH2]", 0, 2), "H": ("[H]", 0, 2), "H2": ("[H][H]", 0, 1),
}
DFH298 = {"CH4": -74.6, "OH": 37.4, "CH3": 147.2, "H2O": -241.8, "C2H6": -84.0}
DFH0 = {"CH4": -66.6, "OH": 37.1, "CH3": 150.0, "H2O": -238.9}
H298_UNCERTAINTY = {"CH4": 0.1, "OH": 0.2, "CH3": 0.3, "H2O": 0.04, "C2H6": 0.5}

_XYZ_ABSTRACTION = """7
CH4 + OH abstraction TS
C  0.000  0.000  0.000
H  0.000  1.027 -0.363
H  0.889 -0.513 -0.363
H -0.889 -0.513 -0.363
H  0.000  0.000  1.300
O  0.000  0.000  2.500
H  0.940  0.000  2.750
"""
_XYZ_RECOMBINATION = """8
CH3 + CH3 recombination TS
C  0.000  0.000  0.000
H  0.000  1.027 -0.363
H  0.889 -0.513 -0.363
H -0.889 -0.513 -0.363
C  0.000  0.000  3.000
H  0.000  1.027  3.363
H  0.889 -0.513  3.363
H -0.889 -0.513  3.363
"""

#: GRI-Mech 3.0 NASA7 shape coefficients (a1..a5, a7 low; b1..b5, b7 high).
#: a6/b6 are re-anchored below so H(298.15) equals DFH298 and H is
#: continuous at t_mid -- synthetic fixtures, not GRI's own enthalpies.
_GRI = {
    "CH4": ([5.14987613, -0.0136709788, 4.91800599e-05, -4.84743026e-08, 1.66693956e-11, -4.64130376],
            [0.074851495, 0.0133909467, -5.73285809e-06, 1.22292535e-09, -1.0181523e-13, 18.437318]),
    "OH": ([3.99201543, -0.00240131752, 4.61793841e-06, -3.88113333e-09, 1.3641147e-12, -0.103925458],
           [3.09288767, 0.000548429716, 1.26505228e-07, -8.79461556e-11, 1.17412376e-14, 4.4766961]),
    "CH3": ([3.6735904, 0.00201095175, 5.73021856e-06, -6.87117425e-09, 2.54385734e-12, 1.60456433],
            [2.28571772, 0.00723990037, -2.98714348e-06, 5.95684644e-10, -4.67154394e-14, 8.48007179]),
    "H2O": ([4.19864056, -0.0020364341, 6.52040211e-06, -5.48797062e-09, 1.77197817e-12, -0.849032208],
            [3.03399249, 0.00217691804, -1.64072518e-07, -9.7041987e-11, 1.68200992e-14, 4.9667701]),
}


def _h_poly(coefficients, temperature):
    """Hand formula H/R - a6 = sum_k a_k T^k / k, k = 1..5."""
    return sum(c * temperature ** k / k for k, c in enumerate(coefficients[:5], start=1))


def nasa7_block(name):
    low, high = _GRI[name]
    a6 = DFH298[name] * 1000.0 / R - _h_poly(low, T298)
    b6 = _h_poly(low, 1000.0) + a6 - _h_poly(high, 1000.0)
    block = {"t_low": 200.0, "t_mid": 1000.0, "t_high": 3500.0}
    block.update({f"a{i}": v for i, v in zip((1, 2, 3, 4, 5, 7), low, strict=True)}, a6=a6)
    block.update({f"b{i}": v for i, v in zip((1, 2, 3, 4, 5, 7), high, strict=True)}, b6=b6)
    return block


# --------------------------------------------------------------------------- #
# Upload-path fixtures
# --------------------------------------------------------------------------- #


def _participant(name):
    smiles, charge, multiplicity = SPECIES[name]
    return {"species_entry": {"smiles": smiles, "charge": charge, "multiplicity": multiplicity}}


def _reaction(reactants, products):
    return {"reversible": True, "reactants": [_participant(n) for n in reactants],
            "products": [_participant(n) for n in products]}


def deposit_rate(session, reactants=("CH4", "OH"), products=("CH3", "H2O"), *, direction="forward",
                 xyz=_XYZ_ABSTRACTION, ts_charge=0, ts_multiplicity=2, lot=_LOT, source=True,
                 tunneling=True, geometry_isotopes=None, **tunneling_fields):
    """TS upload, then a kinetics upload whose tunneling block cites the TS opt."""
    reaction = _reaction(reactants, products)
    ts_entry = persist_transition_state_upload(session, TransitionStateUploadRequest(
        reaction=reaction, charge=ts_charge, multiplicity=ts_multiplicity, geometry={"xyz_text": xyz, "isotopes": geometry_isotopes},
        primary_opt=CalculationWithResultsPayload(type="opt", software_release=_SOFTWARE, level_of_theory=lot),
    ))
    calculation = session.scalar(select(Calculation).where(Calculation.transition_state_entry_id == ts_entry.id))
    block = {
        "model": "eckart", "transition_state_entry_ref": ts_entry.public_ref,
        "source_calculation_ref": calculation.public_ref if source else None,
        "imaginary_frequency_cm1": -1500.0, "reactant_energy_kj_mol": 0.0, "product_energy_kj_mol": -54.6,
        "forward_barrier_kj_mol": 30.0, "reverse_barrier_kj_mol": 84.6,
        "energy_zero_convention": "separated_reactants", "energy_correction_convention": "thermal_enthalpy_298k",
    }
    block.update(tunneling_fields)
    units = {1: "per_s", 2: "cm3_mol_s"}.get(len(reactants))
    kinetics = persist_kinetics_upload(session, KineticsUploadRequest(
        reaction=reaction, scientific_origin="computed", direction=direction, a=1.0e12, a_units=units, n=0.0,
        reported_ea=30.0, reported_ea_units="kj_mol", tmin_k=300.0, tmax_k=2000.0,
        tunneling_application=block if tunneling else None,
    ))
    session.flush()
    session.refresh(kinetics)
    return kinetics


def deposit_thermo(session, name, **fields):
    payload = {"species_entry": _participant(name)["species_entry"], "scientific_origin": "computed"}
    payload.update(fields)
    thermo = persist_thermo_upload(session, ThermoUploadRequest(**payload))
    session.flush()
    return thermo


def declared_thermo(session, name, **extra):
    fields = {"enthalpy_reference_kind": "formation_298k", "h298_kj_mol": DFH298[name],
              "h298_uncertainty_kj_mol": H298_UNCERTAINTY[name]}
    if name in DFH0:
        fields.update(enthalpy_formation_0k_kj_mol=DFH0[name], enthalpy_formation_0k_uncertainty_kj_mol=0.05)
    fields.update(extra)
    return deposit_thermo(session, name, **fields)


def mapping_for(kinetics, thermo_by_name, selection=None):
    """Public-ref mapping ``spe_ref -> thm_ref[:rep]``, matched on the species entry."""
    mapping = {}
    for participant in kinetics.reaction_entry.structure_participants:
        name, thermo = next((n, t) for n, t in thermo_by_name.items() if t.species_entry_id == participant.species_entry_id)
        rep = (selection or {}).get(name)
        mapping[participant.species_entry.public_ref] = thermo.public_ref + (f":{rep}" if rep else "")
    return mapping


def request(kinetics, mapping):
    return {"check": "hess", "target_ref": kinetics.public_ref, "thermo_mapping": mapping}


@contextmanager
def uploads(db_conn):
    with Session(db_conn) as session, session.begin():
        yield session


def payloads(result):
    return [json.loads(f.message) for f in result.findings]


def evaluated(result):
    return [p for p in payloads(result) if p.get("residual_kj_mol") is not None]


def reasons(result):
    return [p["reason"] for p in payloads(result) if "reason" in p]


def abstraction(session, **tunneling_fields):
    kinetics = deposit_rate(session, **tunneling_fields)
    thermo = {name: declared_thermo(session, name) for name in ("CH4", "OH", "CH3", "H2O")}
    return kinetics, thermo


# --------------------------------------------------------------------------- #
# D6-1 .. D6-4
# --------------------------------------------------------------------------- #


def test_d6_1_abstraction_at_298_residual(db_conn):
    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(session)
        result = compare(session, **request(kinetics, mapping_for(kinetics, thermo)))
        found = evaluated(result)
        assert len(found) == 1
        (payload,) = found
        assert payload["reason"] is None and payload["temperature_k"] == 298.15
        assert payload["delta_h_formation_kj_mol"] == pytest.approx(-57.4, abs=1e-9)
        assert payload["delta_e_claim_kj_mol"] == pytest.approx(-54.6, abs=1e-12)
        assert payload["residual_kj_mol"] == pytest.approx(2.8, abs=1e-9)
        terms = payload["terms"]
        assert payload["term_columns"] == ["coefficient", "representation", "h_kj_mol", "uncertainty_kj_mol"]
        assert len(terms) == 4
        # Per-term uncertainties listed as supplied, never combined.
        assert {name: terms[thermo[name].public_ref] for name in thermo} == {
            "CH4": [-1, "h298", -74.6, 0.1], "OH": [-1, "h298", 37.4, 0.2],
            "CH3": [1, "h298", 147.2, 0.3], "H2O": [1, "h298", -241.8, 0.04]}
        assert payload["uncertainty_propagation"] is None
        assert all(p["element_reference_compilation"] == ELEMENT_REFERENCE_COMPILATION for p in payloads(result))
        assert all(p["endpoint_identity"] == ENDPOINT_IDENTITY for p in payloads(result))
        assert len(result.findings) == 6  # one reaction-energy input, four thermo inputs, one evaluation
        assert result.runner == RUNNER and result.target is kinetics


@pytest.mark.parametrize("correction", ["electronic_plus_zpe", "atom_and_bond_corrected"])
@pytest.mark.parametrize("zero,shift", [("separated_reactants", 0.0), ("absolute", 12345.0)])
def test_d6_2_zero_kelvin_residual_is_shift_invariant(db_conn, correction, zero, shift):
    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(
            session, energy_correction_convention=correction, energy_zero_convention=zero,
            reactant_energy_kj_mol=0.0 + shift, product_energy_kj_mol=-60.0 + shift)
        found = evaluated(compare(session, **request(kinetics, mapping_for(kinetics, thermo))))
        assert len(found) == 1
        (payload,) = found
        assert payload["temperature_k"] == 0.0
        assert payload["delta_h_formation_kj_mol"] == pytest.approx(-59.4, abs=1e-9)
        assert payload["delta_e_claim_kj_mol"] == pytest.approx(-60.0, abs=1e-9)
        assert payload["residual_kj_mol"] == pytest.approx(-0.6, abs=1e-9)
        assert len(payload["terms"]) == 4
        assert {tuple(t[1::2]) for t in payload["terms"].values()} == {("formation_0k", 0.05)}


def test_d6_3_recombination_counts_slots(db_conn):
    with uploads(db_conn) as session:
        kinetics = deposit_rate(session, ("CH3", "CH3"), ("C2H6",), xyz=_XYZ_RECOMBINATION, ts_multiplicity=1,
                                product_energy_kj_mol=-381.0, reverse_barrier_kj_mol=411.0)
        thermo = {name: declared_thermo(session, name) for name in ("CH3", "C2H6")}
        assert len(kinetics.reaction_entry.structure_participants) == 3
        found = evaluated(compare(session, **request(kinetics, mapping_for(kinetics, thermo))))
        assert len(found) == 1
        (payload,) = found
        assert {ref: t[0] for ref, t in payload["terms"].items()} == {
            thermo["CH3"].public_ref: -2, thermo["C2H6"].public_ref: 1}
        assert payload["delta_h_formation_kj_mol"] == pytest.approx(-378.4, abs=1e-9)
        assert payload["residual_kj_mol"] == pytest.approx(-2.6, abs=1e-9)


def _nasa_abstraction(session, *, points=False):
    kinetics = deposit_rate(session)
    thermo = {}
    for name in ("CH4", "OH", "CH3", "H2O"):
        extra = {"nasa": nasa7_block(name)}
        if points:
            extra["points"] = [{"temperature_k": T298, "h_kj_mol": DFH298[name]}]
        thermo[name] = declared_thermo(session, name, **extra)
    return kinetics, thermo


def test_d6_4_nasa7_matches_cantera_delta_standard_enthalpy(db_conn):
    ct = engine.cantera()
    species = []
    composition = {"CH4": {"C": 1, "H": 4}, "OH": {"O": 1, "H": 1}, "CH3": {"C": 1, "H": 3}, "H2O": {"H": 2, "O": 1}}
    for name, elements in composition.items():
        block = nasa7_block(name)
        coeffs = [block["t_mid"], *[block[f"b{i}"] for i in range(1, 8)], *[block[f"a{i}"] for i in range(1, 8)]]
        sp = ct.Species(name, elements)
        sp.thermo = ct.NasaPoly2(block["t_low"], block["t_high"], 1e5, coeffs)
        species.append(sp)
    reaction = ct.Reaction(reactants={"CH4": 1, "OH": 1}, products={"CH3": 1, "H2O": 1},
                           rate=ct.ArrheniusRate(1.0, 0.0, 0.0))
    gas = ct.Solution(thermo="ideal-gas", kinetics="gas", species=species, reactions=[reaction])
    gas.TP = T298, 1e5
    cantera_dh = float(gas.delta_standard_enthalpy[0]) / 1e6
    assert cantera_dh == pytest.approx(-57.4, abs=1e-9)

    with uploads(db_conn) as session:
        kinetics, thermo = _nasa_abstraction(session)
        selection = dict.fromkeys(thermo, "nasa7")
        pinned = evaluated(compare(session, **request(kinetics, mapping_for(kinetics, thermo, selection))))
        assert len(pinned) == 1
        assert pinned[0]["delta_h_formation_kj_mol"] == pytest.approx(cantera_dh, abs=1e-9)
        assert pinned[0]["residual_kj_mol"] == pytest.approx(2.8, abs=1e-9)
        assert len(pinned[0]["terms"]) == 4
        assert {tuple(t[1::2]) for t in pinned[0]["terms"].values()} == {("nasa7", None)}
        # Unpinned: h298 and nasa7 on each of four terms, every combination visible.
        result = compare(session, **request(kinetics, mapping_for(kinetics, thermo)))
        found = evaluated(result)
        assert len(found) == 16
        assert len({tuple(t[1] for t in p["terms"].values()) for p in found}) == 16
        assert all(p["residual_kj_mol"] == pytest.approx(2.8, abs=1e-9) for p in found)


def test_exact_point_representation_and_the_combination_cap(db_conn):
    with uploads(db_conn) as session:
        kinetics, thermo = _nasa_abstraction(session, points=True)
        pinned = evaluated(compare(session, **request(
            kinetics, mapping_for(kinetics, thermo, dict.fromkeys(thermo, "point")))))
        assert len(pinned) == 1
        assert pinned[0]["residual_kj_mol"] == pytest.approx(2.8, abs=1e-9)
        # h298, nasa7 and point on four terms is 81 combinations: refused, never truncated.
        with pytest.raises(ValueError, match="64 explicit representation combinations"):
            compare(session, **request(kinetics, mapping_for(kinetics, thermo)))
        with pytest.raises(ValueError, match="unknown enthalpy representation"):
            compare(session, **request(kinetics, mapping_for(kinetics, thermo, {"CH4": "wilhoit"})))


def test_a_combination_count_exactly_at_the_cap_is_evaluated(db_conn, monkeypatch):
    # At most one fit per record, so four participants reach 16 or 81 but never
    # 64 itself; the cap is lowered to 16 to test its boundary.
    from app.services.consistency import hess
    monkeypatch.setattr(hess, "MAX_COMBINATIONS", 16)
    with uploads(db_conn) as session:
        kinetics, thermo = _nasa_abstraction(session)
        found = evaluated(compare(session, **request(kinetics, mapping_for(kinetics, thermo))))
        assert len(found) == 16
        assert all(p["residual_kj_mol"] == pytest.approx(2.8, abs=1e-9) for p in found)
        monkeypatch.setattr(hess, "MAX_COMBINATIONS", 15)
        with pytest.raises(ValueError, match="15 explicit representation combinations"):
            compare(session, **request(kinetics, mapping_for(kinetics, thermo)))


def test_the_shared_basis_is_required_of_fitted_participants_too(db_conn):
    # A NASA fit encodes an enthalpy, so an undeclared fitted participant is
    # refused exactly as an undeclared h298 one is. The upload refuses such a
    # row today; it is set in memory only and rolled back.
    with Session(db_conn) as session:
        transaction = session.begin()
        try:
            kinetics, thermo = _nasa_abstraction(session)
            for name in ("CH4", "OH"):
                thermo[name].h298_kj_mol = None
            thermo["OH"].enthalpy_reference_kind = None
            with session.no_autoflush:
                result = compare(session, **request(kinetics, mapping_for(kinetics, thermo, {"CH4": "nasa7"})))
            assert reasons(result) == ["enthalpy_reference_unrecorded"]
            assert evaluated(result) == []
        finally:
            transaction.rollback()


def test_pressure_never_gates_enthalpy(db_conn):
    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(session)
        assert {t.reference_pressure_bar for t in thermo.values()} == {None}
        thermo["OH"] = declared_thermo(session, "OH", reference_pressure_bar=1.01325)
        found = evaluated(compare(session, **request(kinetics, mapping_for(kinetics, thermo))))
        assert len(found) == 1 and found[0]["residual_kj_mol"] == pytest.approx(2.8, abs=1e-9)


# --------------------------------------------------------------------------- #
# One unavailable case per reason
# --------------------------------------------------------------------------- #


def _rate_case(**fields):
    def build(session):
        return abstraction(session, **fields)
    return build


def _direction(direction):
    def build(session):
        kinetics = deposit_rate(session, direction=direction)
        return kinetics, {name: declared_thermo(session, name) for name in ("CH4", "OH", "CH3", "H2O")}
    return build


def _no_tunneling(session):
    kinetics = deposit_rate(session, tunneling=False)
    return kinetics, {name: declared_thermo(session, name) for name in ("CH4", "OH", "CH3", "H2O")}


def _no_source(session):
    kinetics = deposit_rate(session, source=False)
    return kinetics, {name: declared_thermo(session, name) for name in ("CH4", "OH", "CH3", "H2O")}


def _in_memory_no_level_of_theory(session):
    """Unreachable by upload (a calculation payload requires a level of theory); edited in memory."""
    kinetics, thermo = abstraction(session)
    kinetics.tunneling_applications[0].source_calculation.lot = None
    return kinetics, thermo


def _solvated(session):
    kinetics = deposit_rate(session, lot={**_LOT, "solvent": "water", "solvent_model": "SMD"})
    return kinetics, {name: declared_thermo(session, name) for name in ("CH4", "OH", "CH3", "H2O")}


def _in_memory_ts_on_other_entry(session):
    """Unreachable by upload (the kinetics workflow refuses it); edited in memory, never flushed."""
    kinetics, thermo = abstraction(session)
    other = deposit_rate(session, ("CH3", "CH3"), ("C2H6",), xyz=_XYZ_RECOMBINATION, ts_multiplicity=1)
    ts = kinetics.tunneling_applications[0].transition_state_entry.transition_state
    ts.reaction_entry_id = other.reaction_entry_id
    return kinetics, thermo


def _missing_mapping(session):
    kinetics, thermo = abstraction(session)
    return kinetics, thermo, {"drop": "OH"}


def _wrong_species_mapping(session):
    kinetics, thermo = abstraction(session)
    thermo = dict(thermo)
    return kinetics, thermo, {"swap": ("OH", "CH4")}


def _in_memory_unsupported_role(session):
    """Unreachable by upload (roles are reactant/product by schema); edited in memory."""
    kinetics, thermo = abstraction(session)
    kinetics.reaction_entry.structure_participants[0].role = "catalyst"
    return kinetics, thermo


def _charged(session):
    kinetics = deposit_rate(session, ("CH4", "OH-"), ("CH3-", "H2O"), ts_charge=-1, ts_multiplicity=1)
    return kinetics, {name: declared_thermo(session, name.rstrip("-"), species_entry=_participant(name)["species_entry"])
                      for name in ("CH4", "OH-", "CH3-", "H2O")}


def _isotope(session):
    # The saddle point holds CD4's four deuterons, so its geometry must say so
    # (calculation_geometry_isotope_mismatch); atoms 2-5 are the four hydrogens.
    kinetics = deposit_rate(session, ("CD4", "OH"), ("CD3", "HDO"),
                            geometry_isotopes={2: 2, 3: 2, 4: 2, 5: 2})
    base = {"CD4": "CH4", "OH": "OH", "CD3": "CH3", "HDO": "H2O"}
    return kinetics, {name: declared_thermo(session, base[name], species_entry=_participant(name)["species_entry"])
                      for name in base}


def _in_memory_unusable(session):
    """Unreachable by upload (a SMILES must parse); edited in memory, never flushed."""
    kinetics, thermo = abstraction(session)
    thermo["OH"].species_entry.species.smiles = "*"
    return kinetics, thermo


def _in_memory_unbalanced(session):
    """Unreachable by upload (reaction_mass_balance_failed); a product slot is dropped in memory."""
    kinetics, thermo = abstraction(session)
    participants = kinetics.reaction_entry.structure_participants
    water = next(p for p in participants if p.species_entry_id == thermo["H2O"].species_entry_id)
    participants.remove(water)
    return kinetics, {k: v for k, v in thermo.items() if k != "H2O"}


def _phase(phase):
    def build(session):
        kinetics = deposit_rate(session)
        thermo = {name: declared_thermo(session, name) for name in ("CH4", "OH", "CH3")}
        thermo["H2O"] = declared_thermo(session, "H2O", phase=phase)
        return kinetics, thermo
    return build


def _unrecorded_basis(session):
    kinetics = deposit_rate(session)
    thermo = {name: declared_thermo(session, name) for name in ("CH4", "OH", "CH3")}
    thermo["H2O"] = deposit_thermo(session, "H2O", s298_j_mol_k=188.8)  # no enthalpy content, no declaration
    return kinetics, thermo


def _in_memory_mixed_basis(session):
    """Cannot fire today (one kind exists); a second kind is set in memory, never flushed."""
    kinetics, thermo = abstraction(session)
    thermo["OH"].enthalpy_reference_kind = "some_future_kind"
    return kinetics, thermo


def _enthalpy_unavailable(session):
    kinetics = deposit_rate(session)
    thermo = {name: declared_thermo(session, name) for name in ("CH4", "OH", "CH3")}
    thermo["H2O"] = deposit_thermo(session, "H2O", enthalpy_reference_kind="formation_298k", wilhoit={
        "cp0_j_mol_k": 33.3, "cp_inf_j_mol_k": 58.2, "b_k": 500.0, "a0": 0.0, "a1": 0.0, "a2": 0.0, "a3": 0.0,
        "h0_kj_mol": -251.0, "s0_j_mol_k": 150.0})
    return kinetics, thermo


def _selected_unavailable(session):
    kinetics, thermo = abstraction(session)
    return kinetics, thermo, {"select": {"H2O": "nasa9"}}


def _zero_kelvin_absent(session):
    kinetics = deposit_rate(session, energy_correction_convention="electronic_plus_zpe", product_energy_kj_mol=-60.0)
    thermo = {name: declared_thermo(session, name) for name in ("CH4", "OH", "CH3")}
    thermo["H2O"] = deposit_thermo(session, "H2O", enthalpy_reference_kind="formation_298k", h298_kj_mol=-241.8)
    return kinetics, thermo


def _exact_point_missing(session):
    kinetics, thermo = abstraction(session)
    thermo["H2O"] = declared_thermo(session, "H2O", points=[{"temperature_k": 300.0, "h_kj_mol": -241.7}])
    return kinetics, thermo, {"select": {"H2O": "point"}}


def _isotopologue_entry_mapped_onto_participant(session):
    """Thermo for the CD3 entry (same species row as CH3, different entry) offered for CH3."""
    kinetics, thermo = abstraction(session)
    cd3 = declared_thermo(session, "CH3", species_entry=_participant("CD3")["species_entry"])
    assert cd3.species_entry.species_id == thermo["CH3"].species_entry.species_id
    assert cd3.species_entry_id != thermo["CH3"].species_entry_id
    return kinetics, thermo, {"replace": {"CH3": cd3}}


REASON_CASES = [
    ("no_reaction_level_energy", _no_tunneling),
    ("no_reaction_level_energy", _rate_case(model="wigner", reactant_energy_kj_mol=None, product_energy_kj_mol=None,
                                            forward_barrier_kj_mol=None, reverse_barrier_kj_mol=None)),
    ("tunneling_orientation_not_declared", _direction("reverse")),
    ("tunneling_orientation_not_declared", _direction(None)),
    ("tunneling_transition_state_on_other_reaction_entry", _in_memory_ts_on_other_entry),
    ("energy_zero_convention_not_separated_species", _rate_case(energy_zero_convention="lowest_state")),
    # A pre-reactive complex well stated on the separated-reactants scale: R is not 0.
    ("reaction_energy_not_separated_species", _rate_case(reactant_energy_kj_mol=-8.0, product_energy_kj_mol=-62.6)),
    ("energy_correction_convention_not_declared", _rate_case(model="wigner", energy_correction_convention=None)),
    ("energy_correction_convention_without_thermo_counterpart", _rate_case(energy_correction_convention="electronic_only")),
    ("energy_convention_other", _rate_case(energy_correction_convention="other", convention_note="G4 enthalpy at 0 K")),
    ("reaction_energy_source_untraceable", _no_source),
    ("reaction_energy_source_untraceable", _in_memory_no_level_of_theory),
    ("reaction_energy_solvated_out_of_scope", _solvated),
    ("nonfinite_stored_value", _rate_case(product_energy_kj_mol=float("nan"))),
    ("missing_or_unsupported_participants", _in_memory_unsupported_role),
    ("incomplete_or_incompatible_thermo_mapping", _missing_mapping),
    ("incomplete_or_incompatible_thermo_mapping", _wrong_species_mapping),
    ("incomplete_or_incompatible_thermo_mapping", _isotopologue_entry_mapped_onto_participant),
    ("charged_species_out_of_scope", _charged),
    ("isotope_labelled_species_out_of_scope", _isotope),
    ("unusable_species_composition", _in_memory_unusable),
    ("unbalanced_stoichiometry", _in_memory_unbalanced),
    ("phase_not_recorded", _phase(None)),
    ("non_gas_phase_unsupported", _phase("liquid")),
    ("enthalpy_reference_unrecorded", _unrecorded_basis),
    ("enthalpy_reference_mixed", _in_memory_mixed_basis),
    ("participant_enthalpy_unavailable", _enthalpy_unavailable),
    ("participant_enthalpy_unavailable", _selected_unavailable),
    ("participant_formation_0k_absent", _zero_kelvin_absent),
    ("no_exact_matching_point", _exact_point_missing),
]


def _run_case(session, build):
    built = build(session)
    # In-memory edits (the last step of an in-memory builder) must never flush.
    with session.no_autoflush:
        kinetics, thermo, options = built if len(built) == 3 else (*built, {})
        participants = kinetics.reaction_entry.structure_participants
        by_entry = {t.species_entry_id: t for t in thermo.values()}
        selection = {thermo[name].species_entry_id: rep for name, rep in options.get("select", {}).items()}
        if "drop" in options:
            by_entry.pop(thermo[options["drop"]].species_entry_id)
        if "swap" in options:
            a, b = options["swap"]
            by_entry[thermo[a].species_entry_id] = thermo[b]
        for name, replacement in options.get("replace", {}).items():
            by_entry[thermo[name].species_entry_id] = replacement
        assert participants and by_entry
        return compare_hess(kinetics, by_entry, representations=selection)


def test_reason_cases_cover_every_token():
    tokens = {token for token, _ in REASON_CASES}
    assert len(REASON_CASES) == 30
    assert tokens == {
        "no_reaction_level_energy", "tunneling_orientation_not_declared",
        "tunneling_transition_state_on_other_reaction_entry", "energy_zero_convention_not_separated_species",
        "reaction_energy_not_separated_species",
        "energy_correction_convention_not_declared", "energy_correction_convention_without_thermo_counterpart",
        "energy_convention_other", "reaction_energy_source_untraceable", "reaction_energy_solvated_out_of_scope",
        "nonfinite_stored_value", "missing_or_unsupported_participants", "incomplete_or_incompatible_thermo_mapping",
        "charged_species_out_of_scope", "isotope_labelled_species_out_of_scope", "unusable_species_composition",
        "unbalanced_stoichiometry", "phase_not_recorded", "non_gas_phase_unsupported",
        "enthalpy_reference_unrecorded", "enthalpy_reference_mixed", "participant_enthalpy_unavailable",
        "participant_formation_0k_absent", "no_exact_matching_point",
    }


@pytest.mark.parametrize("expected,build", REASON_CASES, ids=[f"{i}-{t}" for i, (t, _) in enumerate(REASON_CASES)])
def test_each_unavailable_reason(db_conn, expected, build):
    with Session(db_conn) as session:
        transaction = session.begin()
        try:
            result = _run_case(session, build)
            found = reasons(result)
            # Exactly one evaluation finding, carrying exactly this token.
            assert found == [expected]
            assert evaluated(result) == []
            assert all(p["element_reference_compilation"] == ELEMENT_REFERENCE_COMPILATION for p in payloads(result))
            assert all(p["endpoint_identity"] == ENDPOINT_IDENTITY for p in payloads(result))
        finally:
            transaction.rollback()


def test_mapping_reason_names_the_missing_participant(db_conn):
    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(session)
        result = _run_case(session, lambda s: (kinetics, thermo, {"drop": "OH"}))
        (payload,) = [p for p in payloads(result) if p.get("reason")]
        assert payload["missing_participants"] == [thermo["OH"].species_entry.public_ref]
        assert payload["unexpected_mappings"] == [] and payload["mismatched_mappings"] == []


def test_zero_kelvin_rejects_a_representation_selection(db_conn):
    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(session, energy_correction_convention="electronic_plus_zpe")
        with pytest.raises(ValueError, match="only to thermal_enthalpy_298k"):
            compare(session, **request(kinetics, mapping_for(kinetics, thermo, {"CH4": "h298"})))


# --------------------------------------------------------------------------- #
# Persistence, currency and restale
# --------------------------------------------------------------------------- #


def count_reviews(session):
    return session.scalar(select(func.count()).select_from(RecordMachineReviewRow))


def test_dry_run_stages_nothing_and_commit_appends_one_row(db_conn):
    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(session)
        args = request(kinetics, mapping_for(kinetics, thermo))
        before_inputs = [thermo_inputs(t) for t in thermo.values()]
        before = count_reviews(session)
        result, row = invoke(session, **args)
        assert row is None and count_reviews(session) == before
        assert not session.new and not session.dirty
        flushed = []

        def audit(s, *_):
            flushed.extend(type(r).__name__ for r in s.new)
            assert not s.dirty and not s.deleted

        event.listen(session, "before_flush", audit)
        try:
            result, row = invoke(session, commit=True, **args)
            session.flush()
        finally:
            event.remove(session, "before_flush", audit)
        assert flushed == ["RecordMachineReviewRow"]
        assert count_reviews(session) == before + 1
        assert row.record_type.value == "kinetics" and row.record_id == kinetics.id and row.model == RUNNER
        assert all(f.severity.value == "info" for f in result.findings)
        assert [thermo_inputs(t) for t in thermo.values()] == before_inputs
        assert current(session, **args).state.value == "current"
        assert latest_recorded(session, result).id == row.id


def test_hess_rubric_restales_nothing_stored(db_conn):
    """Adding hess_consistency_v1 to the recipe leaves every stored review as it was."""
    from app.services.machine_review.admin_trigger import active_rubric_versions_for_record_type
    from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS
    from app.services.machine_review.rereview import MachineReviewReReviewDecision, plan_record_machine_rereview
    from tests.services.test_phase_d_persistence import _seed_reviewer_review

    assert ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS["hess_consistency_v1"] == "1"
    # The reviewer family's kinetics recipe is filtered to its own rubric.
    assert active_rubric_versions_for_record_type("kinetics") == {"computed_kinetics_v2": "2"}
    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(session)
        digest, prompt, rubrics = _seed_reviewer_review(
            session, record_type="kinetics", record_id=kinetics.id, record_ref=kinetics.public_ref)

        def plan():
            return plan_record_machine_rereview(
                session, record_type="kinetics", record_id=kinetics.id, current_context=digest,
                active_prompt_version=prompt, active_rubric_versions=rubrics)

        assert plan().decision is MachineReviewReReviewDecision.skip_current
        args = request(kinetics, mapping_for(kinetics, thermo))
        result, row = invoke(session, commit=True, **args)
        session.flush()
        assert row is not None
        assert plan().decision is MachineReviewReReviewDecision.skip_current
        assert currency(session, result).state.value == "current"
        assert result.recipe == {"hess_consistency_v1": "1"}


def _material_targets(kinetics, thermo):
    tunneling = kinetics.tunneling_applications[0]
    return {
        "tunneling": tunneling, "kinetics": kinetics, "thermo": thermo["OH"], "lot": tunneling.source_calculation.lot,
        "species": thermo["CH3"].species_entry.species, "entry": thermo["CH3"].species_entry,
        "ts_entry": tunneling.transition_state_entry, "transition_state": tunneling.transition_state_entry.transition_state,
    }


MATERIAL_INPUTS = [
    ("tunneling", "product_energy_kj_mol", -54.0), ("tunneling", "reactant_energy_kj_mol", 0.5),
    ("tunneling", "energy_correction_convention", "electronic_plus_zpe"),
    ("tunneling", "energy_zero_convention", "absolute"), ("tunneling", "source_calculation_id", None),
    ("kinetics", "direction", "reverse"), ("thermo", "h298_kj_mol", 38.0),
    ("thermo", "h298_uncertainty_kj_mol", 1.0), ("thermo", "enthalpy_formation_0k_kj_mol", 37.0),
    ("thermo", "enthalpy_reference_kind", None), ("thermo", "phase", None), ("lot", "solvent", "water"),
    ("species", "charge", 1), ("entry", "isotope_key", "fake"),
    # In-memory only (no upload re-homes a TS); never flushed.
    ("transition_state", "reaction_entry_id", "another_reaction_entry"), ("ts_entry", "multiplicity", 4),
]


@pytest.mark.parametrize("location,field,value", MATERIAL_INPUTS)
def test_every_material_input_changes_live_currency(db_conn, location, field, value):
    with Session(db_conn) as session:
        transaction = session.begin()
        try:
            kinetics, thermo = abstraction(session)
            args = request(kinetics, mapping_for(kinetics, thermo))
            _, row = invoke(session, commit=True, **args)
            session.flush()
            assert current(session, **args).state.value == "current"
            if value == "another_reaction_entry":
                value = kinetics.reaction_entry_id + 10**9
            setattr(_material_targets(kinetics, thermo)[location], field, value)
            with session.no_autoflush:
                live = compare(session, **args)
                assert live.digest.context_hash != row.context_hash
                assert current(session, **args).state.value == "stale"
        finally:
            transaction.rollback()


def test_representation_selection_is_a_hashed_input(db_conn):
    with uploads(db_conn) as session:
        kinetics, thermo = _nasa_abstraction(session)
        unpinned = compare(session, **request(kinetics, mapping_for(kinetics, thermo)))
        pinned = compare(session, **request(kinetics, mapping_for(kinetics, thermo, {"OH": "nasa7"})))
        assert unpinned.digest != pinned.digest
        assert len(evaluated(unpinned)) == 16 and len(evaluated(pinned)) == 8


def test_service_rejects_inputs_hess_does_not_use(db_conn):
    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(session)
        mapping = mapping_for(kinetics, thermo)
        for extra in ({"temperature_grid": [300.0]}, {"comparison_thermo_ref": thermo["OH"].public_ref},
                      {"reverse_kinetics_ref": kinetics.public_ref}):
            with pytest.raises(ValueError, match="hess uses"):
                compare(session, **request(kinetics, mapping), **extra)
        with pytest.raises(ValueError, match="expected a kin_"):
            compare(session, check="hess", target_ref=thermo["OH"].public_ref, thermo_mapping=mapping)


def test_cli_dry_runs_and_commits_once(db_conn, monkeypatch, capsys):
    from app.api import deps
    from scripts import run_consistency_check as cli

    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(session)
        commits = []

        class SessionProxy:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def __getattr__(self, name):
                return getattr(session, name)

            def commit(self):
                commits.append(True)

            def rollback(self):
                pass

        monkeypatch.setattr(deps, "SessionLocal", SessionProxy)
        args = ["--check", "hess", "--target-ref", kinetics.public_ref]
        for entry_ref, thermo_ref in mapping_for(kinetics, thermo, {"CH4": "h298"}).items():
            args += ["--thermo", f"{entry_ref}={thermo_ref}"]
        before = count_reviews(session)
        assert cli.main(args) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["committed"] is False and count_reviews(session) == before and not commits
        found = [json.loads(f["message"]) for f in output["findings"]]
        residuals = [p["residual_kj_mol"] for p in found if p.get("residual_kj_mol") is not None]
        assert len(residuals) == 1 and residuals[0] == pytest.approx(2.8, abs=1e-9)
        assert cli.main([*args, "--commit"]) == 0
        capsys.readouterr()
        assert count_reviews(session) == before + 1 and commits == [True]
        assert cli.main([*args, "--temperature", "300"]) == 1


# --------------------------------------------------------------------------- #
# Review round 1 (#550): endpoints, headroom, Cp-only points
# --------------------------------------------------------------------------- #


def test_separated_reactants_scale_requires_the_reactant_energy_at_zero(db_conn):
    with uploads(db_conn) as session:
        well, thermo = abstraction(session, reactant_energy_kj_mol=-8.0, product_energy_kj_mol=-62.6)
        result = compare(session, **request(well, mapping_for(well, thermo)))
        assert evaluated(result) == [] and reasons(result) == ["reaction_energy_not_separated_species"]
        exact, thermo = abstraction(session, reactant_energy_kj_mol=-0.0, product_energy_kj_mol=-54.6)
        found = evaluated(compare(session, **request(exact, mapping_for(exact, thermo))))
        assert len(found) == 1 and found[0]["residual_kj_mol"] == pytest.approx(2.8, abs=1e-9)


def test_absolute_scale_states_the_unverifiable_endpoint_assumption(db_conn):
    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(session, energy_zero_convention="absolute",
                                       reactant_energy_kj_mol=12337.0, product_energy_kj_mol=12282.4)
        result = compare(session, **request(kinetics, mapping_for(kinetics, thermo)))
        found = evaluated(result)
        assert len(found) == 1 and found[0]["residual_kj_mol"] == pytest.approx(2.8, abs=1e-9)
        assert len(result.findings) == 6
        assert all(p["endpoint_identity"] == ENDPOINT_IDENTITY for p in payloads(result))


_XYZ_DOUBLE_ABSTRACTION = """16
two abstractions, one saddle-shaped fixture
C  0.000  0.000  0.000
H  0.000  1.027 -0.363
H  0.889 -0.513 -0.363
H -0.889 -0.513 -0.363
H  0.000  0.000  1.300
O  0.000  0.000  2.500
H  0.940  0.000  2.750
C  6.000  0.000  0.000
C  7.530  0.000  0.000
H  5.630  1.027  0.000
H  5.630 -0.513  0.889
H  5.630 -0.513 -0.889
H  7.900  1.027  0.000
H  7.900 -0.513  0.889
H  7.900 -0.513 -2.200
H  7.900 -0.513 -3.100
"""

#: Deliberately long floats, so the eight-term row set overflows one finding.
_LONG_DFH298 = {"CH4": -74.60000000000001, "OH": 37.40000000000001, "C2H6": -84.00000000000001,
                "H": 217.99800000000002, "CH3": 147.20000000000002, "H2O": -241.80000000000001,
                "C2H5": 119.86000000000001, "H2": 0.0000000000000001}


def test_a_large_reaction_splits_its_terms_instead_of_overflowing(db_conn):
    from app.services.machine_review.schemas import MachineReviewFinding

    limit = next(m.max_length for m in MachineReviewFinding.model_fields["message"].metadata if hasattr(m, "max_length"))
    assert limit == 1000
    reactants, products = ("CH4", "OH", "C2H6", "H"), ("CH3", "H2O", "C2H5", "H2")
    dh = sum(_LONG_DFH298[n] for n in products) - sum(_LONG_DFH298[n] for n in reactants)
    with uploads(db_conn) as session:
        kinetics = deposit_rate(session, reactants, products, xyz=_XYZ_DOUBLE_ABSTRACTION, ts_multiplicity=1,
                                product_energy_kj_mol=dh + 1.25, reverse_barrier_kj_mol=40.0)
        thermo = {name: deposit_thermo(session, name, enthalpy_reference_kind="formation_298k",
                                       h298_kj_mol=value, h298_uncertainty_kj_mol=0.123456789012345)
                  for name, value in _LONG_DFH298.items()}
        result = compare(session, **request(kinetics, mapping_for(kinetics, thermo)))
        assert all(len(f.message) <= limit for f in result.findings)
        found = evaluated(result)
        assert len(found) == 1
        (payload,) = found
        assert payload["residual_kj_mol"] == pytest.approx(1.25, abs=1e-9)
        # The terms moved to continuation findings, deterministically, and all of them arrived.
        assert "terms" not in payload and payload["term_findings"] >= 1 and payload["combination"] == 0
        parts = [p for p in payloads(result) if "terms_part" in p]
        assert len(parts) == payload["term_findings"]
        assert [p["terms_part"] for p in parts] == list(range(len(parts)))
        terms = {ref: row for p in parts for ref, row in p["terms"].items()}
        assert len(terms) == 8
        assert sum(row[0] * row[2] for row in terms.values()) == pytest.approx(payload["delta_h_formation_kj_mol"])
        assert all(p["endpoint_identity"] == ENDPOINT_IDENTITY for p in payloads(result))
        assert compare(session, **request(kinetics, mapping_for(kinetics, thermo))).findings == result.findings


def test_an_oversized_reason_detail_is_dropped_and_said_so():
    """The detail-list path of the size bound, on the pure helper (no upload makes a list this long)."""
    from app.db.models.kinetics import Kinetics
    from app.services.consistency.hess import _bounded_findings

    target = Kinetics(public_ref="kin_oversized")
    base = {"element_reference_compilation": ELEMENT_REFERENCE_COMPILATION, "endpoint_identity": ENDPOINT_IDENTITY}
    missing = [f"spe_{i:026d}" for i in range(40)]
    payload = {**base, "reason": "incomplete_or_incompatible_thermo_mapping", "missing_participants": missing}
    findings = _bounded_findings(target, payload, ["kin_oversized"], combination=None, base=base,
                                 droppable=("missing_participants",))
    assert len(findings) == 1 and len(findings[0].message) <= 1000
    head = json.loads(findings[0].message)
    assert head["reason"] == "incomplete_or_incompatible_thermo_mapping"
    assert head["detail_omitted"] == "finding_too_large" and "missing_participants" not in head


def test_cp_only_points_are_not_an_enthalpy_representation(db_conn):
    with uploads(db_conn) as session:
        kinetics = deposit_rate(session)
        thermo = {name: declared_thermo(session, name, points=[{"temperature_k": T298, "cp_j_mol_k": 35.0}])
                  for name in ("CH4", "OH", "CH3", "H2O")}
        found = evaluated(compare(session, **request(kinetics, mapping_for(kinetics, thermo))))
        assert len(found) == 1
        assert {row[1] for row in found[0]["terms"].values()} == {"h298"}
        # Pinning the Cp-only points is a visible unavailability, not a silent pass.
        pinned = compare(session, **request(kinetics, mapping_for(kinetics, thermo, {"H2O": "point"})))
        assert evaluated(pinned) == [] and reasons(pinned) == ["participant_enthalpy_unavailable"]


def test_a_declared_energy_level_does_not_change_the_hess_context_hash(db_conn):
    """The declaration is not read by any check, so declaring one restales no stored review (as for D1-D5)."""
    from tests.services.scientific_read._factories import make_lot

    with uploads(db_conn) as session:
        kinetics, thermo = abstraction(session)
        arguments = request(kinetics, mapping_for(kinetics, thermo))
        before = compare(session, **arguments).digest.context_hash
        kinetics.energy_level_of_theory_id = make_lot(session, method="b3lyp", basis="def2svp").id
        session.flush()
        assert compare(session, **arguments).digest.context_hash == before
