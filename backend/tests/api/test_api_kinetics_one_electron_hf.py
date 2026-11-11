"""A one-electron participant's Hartree-Fock energy stands for a declared correlated kinetics level.

ORCA refuses every correlated method for one electron, and for one electron every correlation term is zero. A kinetics
energy link matches a declared correlated level when the participant has exactly one electron (stored SMILES and
charge), its method is Hartree-Fock in any spin form, and every other identity field equals the declared level's.
The substitution is reported as ``one_electron_hf_substitution``. Each test names the condition it protects.
"""

from __future__ import annotations

import pytest

from app.chemistry.species import electron_count_from_smiles
from tests.api.test_api_bundle_provenance_warnings import _SOFTWARE, _kinetics

_REACTION = "/api/v1/uploads/computed-reaction"
_STANDALONE = "/api/v1/uploads/kinetics"
_F12 = {
    "method": "DLPNO-CCSD(T)-F12",
    "basis": "cc-pVTZ-F12",
    "aux_basis": "aug-cc-pVTZ/C",
    "cabs_basis": "cc-pVTZ-F12-CABS",
}
_HF = {**_F12, "method": "hf"}
_XYZ = {
    "[H]": "1\nH\nH 0.0 0.0 0.0",
    "[2H]": "1\nD\nH 0.0 0.0 0.0",
    "[3H]": "1\nT\nH 0.0 0.0 0.0",
    "[H-]": "1\nHminus\nH 0.0 0.0 0.0",
    "[H+]": "1\nHplus\nH 0.0 0.0 0.0",
    "[H][H]": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74",
    "[2H][2H]": "2\nD2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74",
    "[3H][3H]": "2\nT2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74",
}


_ISOTOPES = {
    "[2H]": {1: 2},
    "[3H]": {1: 3},
    "[2H][2H]": {1: 2, 2: 2},
    "[3H][3H]": {1: 3, 2: 3},
}


def _geometry(key: str, smiles: str) -> dict:
    geometry: dict = {"key": key, "xyz_text": _XYZ[smiles]}
    if smiles in _ISOTOPES:
        geometry["isotopes"] = _ISOTOPES[smiles]
    return geometry


def _species(key: str, smiles: str, charge: int, multiplicity: int, level: dict | None) -> dict:
    species: dict = {
        "key": key,
        "species_entry": {"smiles": smiles, "charge": charge, "multiplicity": multiplicity},
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": _geometry(f"{key}-geom", smiles),
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [],
    }
    if level is not None:
        spin = "restricted" if multiplicity == 1 else "unrestricted"
        species["calculations"].append(
            {
                "key": f"{key}-sp",
                "type": "sp",
                "geometry_key": f"{key}-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": {**level, "spin_treatment": spin},
                "sp_electronic_energy_hartree": -0.5,
            }
        )
    return species


def _bundle(
    declared: dict,
    *,
    left: str = "[H]",
    left_charge: int = 0,
    left_mult: int = 2,
    left_level: dict | None = None,
    right: str = "[H]",
    right_charge: int = 0,
    right_mult: int = 2,
    right_level: dict | None = None,
    product: str = "[H][H]",
    product_level: dict | None = None,
) -> dict:
    """``left + right -> product``: each species links its single point, when it has one."""
    species = [
        _species("a", left, left_charge, left_mult, left_level),
        _species("p", product, 0, 1, product_level if product_level is not None else _F12),
    ]
    reactant_keys = ["a"]
    if right_level is not None or right != left or right_charge != left_charge:
        species.append(_species("b", right, right_charge, right_mult, right_level))
        reactant_keys = ["a", "b"]
    else:
        reactant_keys = ["a", "a"]
    links = [{"calculation_key": f"{s['key']}-sp", "role": "reactant_energy"} for s in species[:1]]
    links += [{"calculation_key": f"{s['key']}-sp", "role": "reactant_energy"} for s in species[2:] if s["calculations"]]
    links.append({"calculation_key": "p-sp", "role": "product_energy"})
    return {
        "species": species,
        "reversible": True,
        "reactant_keys": reactant_keys,
        "product_keys": ["p"],
        "kinetics": [
            _kinetics(
                reactant_keys=reactant_keys,
                product_keys=["p"],
                energy_level_of_theory=declared,
                source_calculations=links,
            )
        ],
    }


def _hf_codes(resp) -> list[dict]:
    return [w for w in resp.json()["warnings"] if w["code"] == "one_electron_hf_substitution"]


def _assert_refused(resp) -> None:
    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == "kinetics_energy_level_contradiction"


# ---------------------------------------------------------------------------
# Accepted, and said
# ---------------------------------------------------------------------------


def test_a_hydrogen_atom_at_hf_under_a_declared_f12_level_is_accepted_and_reported(client):
    """The reproduced defect: an H atom at HF, everything else at DLPNO-CCSD(T)-F12, declared at DLPNO-CCSD(T)-F12."""
    resp = client.post(_REACTION, json=_bundle(dict(_F12), left_level=_HF))
    assert resp.status_code == 201, resp.text[:800]
    (warning,) = _hf_codes(resp)
    assert warning["context"]["cabs_singles_absent"] is True
    assert len(warning["context"]["energy_calculation_refs"]) == 1
    assert warning["context"]["declared_level_of_theory_ref"].startswith("lot_")
    assert "CABS singles" in warning["message"] and "Hartree-Fock" in warning["message"]
    assert warning["field"] == "kinetics[0].energy_level_of_theory"
    assert "missing_level_of_theory_provenance" not in {w["code"] for w in resp.json()["warnings"]}


@pytest.mark.parametrize("method", ["hf", "HF", " Hf "])
def test_the_hf_identity_key_counts_in_any_case(client, method):
    """Branch: the method's identity key is ``hf`` (case and surrounding space are not part of it)."""
    resp = client.post(_REACTION, json=_bundle(dict(_F12), left_level={**_HF, "method": method}))
    assert resp.status_code == 201, resp.text[:800]
    assert len(_hf_codes(resp)) == 1


@pytest.mark.parametrize("method", ["uhf", "rhf", "rohf", "hartree-fock"])
def test_other_hartree_fock_identity_keys_are_not_exempt(client, method):
    """Kills: the HF set widened past the ``hf`` key. The spin form belongs in spin_treatment, not the method."""
    resp = client.post(_REACTION, json=_bundle(dict(_F12), left_level={**_HF, "method": method}))
    _assert_refused(resp)


@pytest.mark.parametrize(
    ("smiles", "product"),
    [("[2H]", "[2H][2H]"), ("[3H]", "[3H][3H]")],
)
def test_deuterium_and_tritium_count_as_one_electron(client, smiles, product):
    """Isotopes are the element: D and T have one electron."""
    resp = client.post(
        _REACTION, json=_bundle(dict(_F12), left=smiles, left_level=_HF, product=product)
    )
    assert resp.status_code == 201, resp.text[:800]
    assert len(_hf_codes(resp)) == 1


def test_a_declared_level_without_f12_carries_no_cabs_note(client):
    """The CABS flag is set by the declared method, not always."""
    level = {"method": "CCSD(T)", "basis": "cc-pVTZ"}
    resp = client.post(
        _REACTION, json=_bundle(level, left_level={**level, "method": "hf"}, product_level=level)
    )
    assert resp.status_code == 201, resp.text[:800]
    (warning,) = _hf_codes(resp)
    assert warning["context"]["cabs_singles_absent"] is False
    assert "CABS" not in warning["message"]


def test_no_warning_when_nothing_is_substituted(client):
    resp = client.post(_REACTION, json=_bundle(dict(_F12), left_level=dict(_F12)))
    assert resp.status_code == 201, resp.text[:800]
    assert _hf_codes(resp) == []


# ---------------------------------------------------------------------------
# Still refused
# ---------------------------------------------------------------------------


def test_the_hydride_ion_at_hf_is_still_a_contradiction(client):
    """Kills: the electron-count condition dropped. H- has two electrons (one atomic number plus charge -1)."""
    bundle = _bundle(
        dict(_F12),
        left="[H-]",
        left_charge=-1,
        left_mult=1,
        left_level=_HF,
        right="[H+]",
        right_charge=1,
        right_mult=1,
        right_level=None,
    )
    _assert_refused(client.post(_REACTION, json=bundle))


def test_a_two_electron_neutral_participant_at_hf_is_still_a_contradiction(client):
    """Kills: the electron-count condition dropped. H2 at HF, the hydrogen atom at the correlated level."""
    resp = client.post(
        _REACTION, json=_bundle(dict(_F12), left_level=dict(_F12), product_level=_HF)
    )
    _assert_refused(resp)


def test_a_hydrogen_atom_at_hf_in_another_basis_is_still_a_contradiction(client):
    """Kills: the every-other-field condition dropped (here the basis)."""
    resp = client.post(
        _REACTION, json=_bundle(dict(_F12), left_level={**_HF, "basis": "cc-pVDZ-F12"})
    )
    _assert_refused(resp)


def test_a_hydrogen_atom_at_hf_with_another_cabs_basis_is_still_a_contradiction(client):
    resp = client.post(
        _REACTION, json=_bundle(dict(_F12), left_level={**_HF, "cabs_basis": "cc-pVDZ-F12-CABS"})
    )
    _assert_refused(resp)


def test_a_hydrogen_atom_at_another_correlated_method_is_still_a_contradiction(client):
    """Only Hartree-Fock is exempt: MP2 on one electron is not a method the rule names."""
    resp = client.post(_REACTION, json=_bundle(dict(_F12), left_level={**_HF, "method": "mp2"}))
    _assert_refused(resp)


def test_a_declared_hartree_fock_level_is_not_widened_to_other_hf_spellings(client):
    """The exemption needs a correlated declared level: HF declared against H at uhf and H2 at hf is a contradiction."""
    resp = client.post(
        _REACTION,
        json=_bundle(dict(_HF), left_level={**_HF, "method": "uhf"}, product_level=dict(_HF)),
    )
    _assert_refused(resp)


# ---------------------------------------------------------------------------
# The standalone route finds the HF calculation as the hydrogen atom's energy
# ---------------------------------------------------------------------------


def _sp(level: dict, multiplicity: int) -> dict:
    spin = "restricted" if multiplicity == 1 else "unrestricted"
    return {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": {**level, "spin_treatment": spin},
        "sp_result": {"electronic_energy_hartree": -0.5},
    }


def _deposit(client, smiles: str, multiplicity: int, level: dict, *, also: dict | None = None) -> None:
    payload = {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
        "geometry": {"xyz_text": _XYZ[smiles]},
        "calculation": _sp(level, multiplicity),
    }
    if also is not None:
        payload["additional_calculations"] = [_sp(also, multiplicity)]
    resp = client.post("/api/v1/uploads/conformers", json=payload)
    assert resp.status_code == 201, resp.text[:500]


def _standalone(declared: dict) -> dict:
    h = {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}
    return {
        "reaction": {
            "reversible": False,
            "reactants": [h, dict(h)],
            "products": [{"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}}],
        },
        "scientific_origin": "computed",
        "energy_level_of_theory": declared,
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
        "n": 0.0,
        "reported_ea": 0.0,
        "reported_ea_units": "kj_mol",
        "tmin_k": 300.0,
        "tmax_k": 2000.0,
        "software_release": {"name": "Arkane", "version": "3.0"},
    }


def test_standalone_the_hartree_fock_calculation_of_the_atom_is_linked_and_reported(client, db_session):
    _deposit(client, "[H]", 2, _HF)
    _deposit(client, "[H][H]", 1, _F12)
    resp = client.post(_STANDALONE, json=_standalone(dict(_F12)))
    assert resp.status_code == 201, resp.text[:800]
    assert len(_hf_codes(resp)) == 1
    from sqlalchemy import select

    from app.db.models.kinetics import KineticsSourceCalculation

    roles = db_session.scalars(
        select(KineticsSourceCalculation.role).where(KineticsSourceCalculation.kinetics_id == resp.json()["id"])
    ).all()
    assert sorted(r.value for r in roles) == ["product_energy", "reactant_energy"]


def test_standalone_without_the_electron_condition_the_atom_would_find_none(client):
    """The two-electron product at HF is not found as an energy at the declared level."""
    _deposit(client, "[H]", 2, _F12)
    _deposit(client, "[H][H]", 1, _HF)
    resp = client.post(_STANDALONE, json=_standalone(dict(_F12)))
    assert resp.status_code == 422
    assert "No SP calculation found" in resp.text


# ---------------------------------------------------------------------------
# The electron count, from what the species stores
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("smiles", "charge", "electrons"),
    [
        ("[H]", 0, 1),
        ("[2H]", 0, 1),
        ("[3H]", 0, 1),
        ("[H-]", -1, 2),
        ("[H+]", 1, 0),
        ("[He+]", 1, 1),
        ("[He]", 0, 2),
        ("[H][H]", 0, 2),
        ("[CH3]", 0, 9),
        ("C", 0, 10),
    ],
)
def test_electron_count_is_atomic_numbers_minus_charge(smiles, charge, electrons):
    assert electron_count_from_smiles(smiles, charge) == electrons


def test_an_unreadable_smiles_has_no_electron_count():
    assert electron_count_from_smiles("not a smiles(", 0) is None


# ---------------------------------------------------------------------------
# Boundaries on stored rows (no producer in the way)
# ---------------------------------------------------------------------------

_DECLARED_ROW = {"method": "dlpno-ccsd(t)-f12", "basis": "cc-pvtz-f12"}


def _level(db_session, tag: str, **fields):
    from app.db.models.level_of_theory import LevelOfTheory

    level = LevelOfTheory(lot_hash=(tag * 64)[:64], **fields)
    db_session.add(level)
    db_session.flush()
    return level


def _energy(db_session, smiles: str, multiplicity: int, level, charge: int = 0):
    from app.db.models.calculation import Calculation
    from app.db.models.common import CalculationType, MoleculeKind, StereoKind
    from app.db.models.species import Species, SpeciesEntry

    species = Species(
        kind=MoleculeKind.molecule,
        smiles=smiles,
        inchi_key="A" * 14 + "-" + "B" * 10 + "-N",
        charge=charge,
        multiplicity=multiplicity,
        stereo_kind=StereoKind.achiral,
    )
    db_session.add(species)
    db_session.flush()
    entry = SpeciesEntry(species_id=species.id)
    db_session.add(entry)
    db_session.flush()
    calc = Calculation(type=CalculationType.sp, species_entry_id=entry.id, lot_id=level.id)
    db_session.add(calc)
    db_session.flush()
    db_session.refresh(calc)
    return calc


def test_a_species_whose_smiles_cannot_be_read_is_not_exempt(db_session):
    """Kills: an unknown composition treated as one electron."""
    from app.services.calculation_levels import kinetics_energy_level_violation

    declared = _level(db_session, "1", **_DECLARED_ROW)
    hf = _level(db_session, "2", **{**_DECLARED_ROW, "method": "hf"})
    energies = [_energy(db_session, "not a smiles(", 2, hf), _energy(db_session, "[H][H]", 1, declared)]
    violation = kinetics_energy_level_violation(energies, declared)
    assert violation is not None and violation.code == "kinetics_energy_level_contradiction"


def test_a_readable_one_electron_species_is_exempt_beside_a_correlated_link(db_session):
    """The control for the row-level tests: the same shape with H is accepted."""
    from app.services.calculation_levels import kinetics_energy_level_violation

    declared = _level(db_session, "1", **_DECLARED_ROW)
    hf = _level(db_session, "2", **{**_DECLARED_ROW, "method": "hf"})
    energies = [_energy(db_session, "[H]", 2, hf), _energy(db_session, "[H][H]", 1, declared)]
    assert kinetics_energy_level_violation(energies, declared) is None


def test_every_link_at_hartree_fock_is_not_exempted(db_session):
    """Kills: the exemption applied with no link at the declared method; the ordinary rule then applies."""
    from app.services.calculation_levels import (
        kinetics_energy_level_violation,
        kinetics_one_electron_hf_substitutions,
    )

    declared = _level(db_session, "1", **_DECLARED_ROW)
    hf = _level(db_session, "2", **{**_DECLARED_ROW, "method": "hf"})
    energies = [_energy(db_session, "[H]", 2, hf), _energy(db_session, "[2H]", 2, hf)]
    assert kinetics_one_electron_hf_substitutions(energies, declared) == []
    violation = kinetics_energy_level_violation(energies, declared)
    assert violation is not None and violation.code == "kinetics_energy_level_contradiction"
    # Declaring the level they all share is accepted, as before.
    assert kinetics_energy_level_violation(energies, hf) is None


def test_a_declared_composite_scheme_is_never_exempted(db_session):
    """Kills: the composite-scheme guard. A scheme level hashes by its definition, not by its columns."""
    from app.db.models.common import CompositeBindingSource
    from app.services.calculation_levels import kinetics_energy_level_violation
    from tests.api.test_api_kinetics_energy_level_spin import _scheme_level

    declared = _scheme_level(
        db_session,
        method="dlpno-ccsd(t)-f12",
        lot_hash="7" * 64,
        source=CompositeBindingSource.declared,
        scheme_name="Label",
        definition_hash="e" * 64,
    )
    hf = _level(db_session, "8", method="hf")
    energies = [_energy(db_session, "[H]", 2, hf), _energy(db_session, "[H][H]", 1, declared)]
    violation = kinetics_energy_level_violation(energies, declared)
    assert violation is not None and violation.code == "kinetics_energy_level_contradiction"


def test_a_restricted_open_atom_beside_unrestricted_radicals_is_still_mixed(db_session):
    """Spin is judged as before: the one-electron atom is a doublet in the open-shell set."""
    from app.db.models.common import SpinTreatment
    from app.services.calculation_levels import kinetics_energy_level_violation

    declared = _level(db_session, "1", **_DECLARED_ROW)
    hf_ro = _level(db_session, "2", **{**_DECLARED_ROW, "method": "hf"}, spin_treatment=SpinTreatment.restricted_open)
    u = _level(db_session, "3", **_DECLARED_ROW, spin_treatment=SpinTreatment.unrestricted)
    energies = [_energy(db_session, "[H]", 2, hf_ro), _energy(db_session, "[CH3]", 2, u)]
    violation = kinetics_energy_level_violation(energies, declared)
    assert violation is not None
    assert violation.context["reason"] == "open_shell_reference_mixed"


# ---------------------------------------------------------------------------
# The declared method must be a wavefunction or multireference method
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["DLPNO-CCSD(T)-F12", "CCSD(T)-F12a", "MP2-F12", "CASPT2", "MRCI+Q"])
def test_a_correlated_wavefunction_or_multireference_declared_method_is_accepted(client, method):
    level = {"method": method, "basis": "cc-pVTZ-F12"}
    resp = client.post(
        _REACTION, json=_bundle(level, left_level={**level, "method": "hf"}, product_level=level)
    )
    assert resp.status_code == 201, resp.text[:800]
    assert len(_hf_codes(resp)) == 1


@pytest.mark.parametrize("method", ["B3LYP", "B2PLYP", "G4"])
def test_a_density_functional_or_composite_declared_method_is_not_exempted(client, method):
    """Kills: any non-HF declared method counting as correlated. The atom's B3LYP energy is not its HF energy."""
    level = {"method": method, "basis": "cc-pVTZ"}
    resp = client.post(
        _REACTION, json=_bundle(level, left_level={**level, "method": "hf"}, product_level=level)
    )
    _assert_refused(resp)


# ---------------------------------------------------------------------------
# /uploads/kinetics: the HF calculation stands in only when nothing matches exactly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spin", [None, "unrestricted"])
def test_standalone_an_atom_with_a_correlated_and_an_hf_single_point_resolves_to_the_correlated_one(
    client, db_session, spin
):
    """Kills: an HF stand-in competing with an exact match (it made the lookup ambiguous)."""
    from sqlalchemy import select

    from app.db.models.calculation import Calculation
    from app.db.models.kinetics import KineticsSourceCalculation

    _deposit(client, "[H]", 2, _F12, also=_HF)
    _deposit(client, "[H][H]", 1, _F12)
    declared = {**_F12, **({"spin_treatment": spin} if spin else {})}
    resp = client.post(_STANDALONE, json=_standalone(declared))
    assert resp.status_code == 201, resp.text[:800]
    assert _hf_codes(resp) == []
    methods = db_session.scalars(
        select(Calculation.lot_id).join(
            KineticsSourceCalculation, KineticsSourceCalculation.calculation_id == Calculation.id
        ).where(KineticsSourceCalculation.kinetics_id == resp.json()["id"])
    ).all()
    from app.db.models.level_of_theory import LevelOfTheory

    levels = {db_session.get(LevelOfTheory, lot_id).method.lower() for lot_id in methods}
    assert levels == {"dlpno-ccsd(t)-f12"}


def test_a_declared_hartree_fock_level_is_not_a_correlated_level(db_session):
    """Kills: the declared-not-HF guard. Declaring HF (spin unknown) with H at hf+unrestricted is no substitution."""
    from app.db.models.common import SpinTreatment
    from app.services.calculation_levels import kinetics_one_electron_hf_substitutions

    declared = _level(db_session, "1", method="hf", basis="cc-pvtz-f12")
    atom = _level(db_session, "2", method="hf", basis="cc-pvtz-f12", spin_treatment=SpinTreatment.unrestricted)
    energies = [_energy(db_session, "[H]", 2, atom), _energy(db_session, "[H][H]", 1, declared)]
    assert kinetics_one_electron_hf_substitutions(energies, declared) == []
