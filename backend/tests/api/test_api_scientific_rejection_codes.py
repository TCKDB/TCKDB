"""The proof that a scientific refusal names itself in the response body.

Every other guard in this repository stops one step short of the thing a
client actually reads. ``test_scientific_check_register`` proves a code is
declared and passed to a coded error; the workflow tests prove the right
deposits are refused. Neither noticed that the ``code`` field of the 422
body said ``validation_error`` for every chemistry refusal TCKDB makes —
because the codes the checks carried were spelled inside their English
sentences (``"... (reaction_mass_balance_failed)."``) where the envelope's
promoter, which requires a ``code: `` prefix, never looked.

So the assertion here is deliberately narrow and deliberately awkward:

    assert response.json()["code"] == "reaction_mass_balance_failed"

**not** ``assert code in response.json()["detail"]``. The second passes on
the broken system. That distinction is the whole point of the file, and it
is why a new ``error_envelope`` code in the register is not accepted until
it is named here — see
``test_every_error_envelope_code_is_proved_end_to_end``.

Each test also asserts the status is 422 and, where the message is a
published one, that ``detail`` still carries the sentence it always did.
Attaching a code was meant to be purely additive: a client matching prose
keeps working, and a client matching ``code`` gets something useful for
the first time.
"""

from __future__ import annotations

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}


def _envelope(response) -> dict:
    """The parsed body, asserted to be a 422 with the standard envelope."""
    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) >= {"code", "detail"}, body
    return body


def _assert_code(response, expected: str) -> dict:
    """The response names *expected* in its ``code`` field, not in its prose.

    A helper rather than a bare assert so the failure message can say what
    was actually reported — which, before this branch, was
    ``validation_error`` for every single one of these.
    """
    body = _envelope(response)
    assert body["code"] == expected, (
        f"expected code={expected!r}, got {body['code']!r}. "
        f"detail={body['detail']!r}"
    )
    return body


# ---------------------------------------------------------------------------
# Conservation across a reaction
# ---------------------------------------------------------------------------


def _reaction_payload(reactants: list[dict], products: list[dict]) -> dict:
    return {
        "reversible": True,
        "reactants": [{"species_entry": entry} for entry in reactants],
        "products": [{"species_entry": entry} for entry in products],
    }


_H_ATOM = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
_METHANE = {"smiles": "C", "charge": 0, "multiplicity": 1}
_H2 = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}
_METHYL = {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}
_HYDROXIDE = {"smiles": "[OH-]", "charge": -1, "multiplicity": 1}
_WATER = {"smiles": "O", "charge": 0, "multiplicity": 1}


class TestConservationAcrossAReaction:
    def test_unbalanced_reaction_names_mass_balance(self, client):
        response = client.post(
            "/api/v1/uploads/reactions",
            json=_reaction_payload([_H_ATOM, _METHANE], [_METHYL]),
        )
        body = _assert_code(response, "reaction_mass_balance_failed")
        # Additive, not a rewrite: the sentence a prose-matching client has
        # always seen is byte-for-byte the one it still sees.
        assert "not element-balanced" in str(body["detail"])

    def test_charge_losing_reaction_names_charge_conservation(self, client):
        # [OH-] + [H] -> H2O drops an electron on the way across. The
        # elemental balance is satisfied, so only the charge law can fire.
        response = client.post(
            "/api/v1/uploads/reactions",
            json=_reaction_payload([_HYDROXIDE, _H_ATOM], [_WATER]),
        )
        body = _assert_code(response, "reaction_charge_not_conserved")
        assert "conserved across a reaction" in str(body["detail"])


# ---------------------------------------------------------------------------
# A structure against its own label
# ---------------------------------------------------------------------------


def _conformer_payload(
    *,
    species_entry: dict,
    xyz_text: str,
    label: str = "conf-a",
) -> dict:
    return {
        "species_entry": species_entry,
        "geometry": {"xyz_text": xyz_text},
        "calculation": {
            "type": "sp",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "label": label,
    }


_METHANE_XYZ = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
_METHYL_XYZ = (
    "4\nmethyl\n"
    "C  0.000  0.000  0.000\n"
    "H  1.079  0.000  0.000\n"
    "H -0.539  0.934  0.000\n"
    "H -0.539 -0.934  0.000"
)


class TestAStructureAgainstItsOwnLabel:
    def test_geometry_of_a_different_molecule_names_composition(self, client):
        response = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                species_entry=_METHANE, xyz_text=_METHYL_XYZ
            ),
        )
        body = _assert_code(response, "species_geometry_composition_mismatch")
        assert "species_geometry_composition_mismatch" in str(body["detail"])

    def test_isotope_labels_that_disagree_name_the_isotope_check(self, client):
        # CH3D by SMILES, all-protium by geometry. Same formula, so the
        # composition check above passes and only the isotope one can fire.
        response = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                species_entry={
                    "smiles": "[2H]C",
                    "charge": 0,
                    "multiplicity": 1,
                },
                xyz_text=_METHANE_XYZ,
            ),
        )
        body = _assert_code(response, "species_geometry_isotope_mismatch")
        assert "Isotope substitution" in str(body["detail"])

    def test_declared_charge_that_the_smiles_denies_names_the_charge_check(
        self, client
    ):
        response = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                species_entry={"smiles": "C", "charge": -1, "multiplicity": 1},
                xyz_text=_METHANE_XYZ,
            ),
        )
        body = _assert_code(response, "species_smiles_charge_mismatch")
        assert "does not match SMILES charge" in str(body["detail"])


# ---------------------------------------------------------------------------
# Stationary points
# ---------------------------------------------------------------------------
#
# These reach the envelope through ``raise_for_blocking_findings``, which
# reports the code the blocking findings agreed on. They are the reason the
# promotion had to be typed rather than textual: the finding's own ``code``
# is the contract, while its ``message`` is a paragraph of advice that will
# be reworded.


def _freq_calc(
    *,
    n_imag: int,
    imag_freq_cm1: float | None = None,
    frequencies: list[float] | None = None,
    reaction_coordinate_mode_index: int | None = None,
    dispositions: dict[int, str] | None = None,
) -> dict:
    result: dict = {"n_imag": n_imag, "imag_freq_cm1": imag_freq_cm1}
    if frequencies is not None:
        dispositions = dispositions or {}
        result["modes"] = [
            {
                "mode_index": index + 1,
                "frequency_cm1": value,
                "is_imaginary": value < 0,
                "imaginary_disposition": dispositions.get(index + 1),
            }
            for index, value in enumerate(frequencies)
        ]
    if reaction_coordinate_mode_index is not None:
        result["reaction_coordinate_mode_index"] = reaction_coordinate_mode_index
    return {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "freq_result": result,
    }


#: H + CH4 -> H2 + CH3, so the saddle point is CH5.
_TS_XYZ = (
    "6\nH...H-CH3 abstraction TS\n"
    "C  0.000  0.000  0.000\n"
    "H -0.510  0.883  0.000\n"
    "H -0.510 -0.883  0.000\n"
    "H  0.000  0.000 -1.090\n"
    "H  0.000  0.000  1.350\n"
    "H  0.000  0.000  2.250"
)


def _transition_state_payload(
    *,
    label: str,
    charge: int = 0,
    xyz_text: str = _TS_XYZ,
    freq: dict | None = None,
) -> dict:
    payload: dict = {
        "reaction": _reaction_payload([_H_ATOM, _METHANE], [_H2, _METHYL]),
        "charge": charge,
        "multiplicity": 2,
        "geometry": {"xyz_text": xyz_text},
        "primary_opt": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "additional_calculations": [freq] if freq is not None else [],
        "label": label,
    }
    return payload


class TestStationaryPoints:
    def test_a_transition_state_with_no_imaginary_mode(self, client):
        response = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                label="ts-none", freq=_freq_calc(n_imag=0)
            ),
        )
        _assert_code(response, "transition_state_no_imaginary_mode")

    def test_extra_imaginary_modes_with_no_designation(self, client):
        response = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                label="ts-ambiguous",
                freq=_freq_calc(n_imag=3, imag_freq_cm1=-1300.0),
            ),
        )
        _assert_code(
            response, "transition_state_reaction_coordinate_not_designated"
        )

    def test_an_undeclared_mode_stiffer_than_the_designated_one(self, client):
        response = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                label="ts-stiff",
                freq=_freq_calc(
                    n_imag=2,
                    imag_freq_cm1=-100.0,
                    frequencies=[-100.0, -1300.0, 800.0],
                    reaction_coordinate_mode_index=1,
                ),
            ),
        )
        _assert_code(
            response, "transition_state_reaction_coordinate_ambiguous"
        )

    def test_a_minimum_with_an_imaginary_mode(self, client):
        payload = _conformer_payload(
            species_entry=_METHANE, xyz_text=_METHANE_XYZ
        )
        payload["calculation"] = _freq_calc(n_imag=1, imag_freq_cm1=-250.0)
        response = client.post("/api/v1/uploads/conformers", json=payload)
        _assert_code(response, "n_imag_contradicts_minimum")


# ---------------------------------------------------------------------------
# Conservation, against the saddle point
# ---------------------------------------------------------------------------


class TestTheSaddlePointAgainstItsReaction:
    def test_a_saddle_point_made_of_other_atoms(self, client):
        # The reaction is CH5; the geometry deposited is CH3.
        response = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                label="ts-wrong-atoms",
                xyz_text=_METHYL_XYZ,
                freq=_freq_calc(n_imag=1, imag_freq_cm1=-1500.0),
            ),
        )
        body = _assert_code(response, "transition_state_composition_mismatch")
        assert "transition_state_composition_mismatch" in str(body["detail"])

    def test_a_saddle_point_at_another_charge(self, client):
        response = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                label="ts-wrong-charge",
                charge=-1,
                freq=_freq_calc(n_imag=1, imag_freq_cm1=-1500.0),
            ),
        )
        body = _assert_code(response, "transition_state_charge_mismatch")
        assert "transition_state_charge_mismatch" in str(body["detail"])


# ---------------------------------------------------------------------------
# Rate coefficients
# ---------------------------------------------------------------------------


class TestRateCoefficients:
    def test_a_units_of_the_wrong_dimensionality(self, client):
        # One reactant, so the rate law is first order and A must be per
        # second. Bimolecular units on it are not an unusual result but a
        # number that cannot mean what it says.
        response = client.post(
            "/api/v1/uploads/kinetics",
            json={
                "reaction": _reaction_payload([_METHANE], [_METHANE]),
                "scientific_origin": "experimental",
                "a": 2.16e8,
                "a_units": "cm3_mol_s",
                "n": 0.0,
                "reported_ea": 14.35,
                "reported_ea_units": "kj_mol",
            },
        )
        body = _assert_code(response, "arrhenius_a_units_molecularity_mismatch")
        assert "is incompatible with" in str(body["detail"])


# ---------------------------------------------------------------------------
# Atom mapping across a reaction
# ---------------------------------------------------------------------------
#
# ``CH3 + H -> CH4`` and its saddle point: small enough to state a whole map
# inline, asymmetric enough that a wrong one is visibly wrong. The bundle
# below is the same shape as
# ``tests/api/scientific/test_api_reaction_atom_map.py``'s, which is the
# only endpoint carrying an ``atom_map`` at all.
#
# These refuse inside a Pydantic model validator rather than in service
# code, so they exercise the other half of the promotion: the exception
# object survives into ``errors()[i]["ctx"]["error"]`` and its ``.code`` is
# read from there. Nothing about the message is parsed, which is what lets
# these sentences stay exactly as they were written.

_XYZ_H = "1\nH\nH 0.0 0.0 0.0"
_XYZ_CH3 = (
    "4\nmethyl\n"
    "C  0.000  0.000  0.000\n"
    "H  1.080  0.000  0.000\n"
    "H -0.540  0.935  0.000\n"
    "H -0.540 -0.935  0.000"
)
_XYZ_NH3 = (
    "4\nammonia\n"
    "N  0.000  0.000  0.000\n"
    "H  1.010  0.000  0.000\n"
    "H -0.505  0.875  0.000\n"
    "H -0.505 -0.875  0.000"
)
_XYZ_CH4 = _METHANE_XYZ
_XYZ_TS = (
    "5\nTS for CH3 + H -> CH4\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.400"
)
#: The reaction's five atoms plus a spare hydrogen, so a complete map of a
#: balanced reaction can still leave a saddle-point atom over.
_XYZ_TS_SPARE_ATOM = (
    "6\nTS with a spare atom\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.400\n"
    "H  0.000  0.000  3.000"
)
_BUNDLE_LOT = {"method": "wb97xd", "basis": "def2tzvp"}


def _bundle_species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {
            "smiles": smiles,
            "charge": 0,
            "multiplicity": multiplicity,
        },
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _BUNDLE_LOT,
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [],
    }


def _bundle(
    atom_map: dict | None = None,
    *,
    species: list[dict] | None = None,
    validation_evidence: list[dict] | None = None,
) -> dict:
    transition_state: dict = {
        "charge": 0,
        "multiplicity": 2,
        "geometry": {"key": "ts-geom", "xyz_text": _XYZ_TS},
        "calculation": {
            "key": "ts-opt",
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _BUNDLE_LOT,
            "opt_converged": True,
        },
        "calculations": [
            {
                "key": "ts-freq",
                "type": "freq",
                "geometry_key": "ts-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _BUNDLE_LOT,
                "freq_n_imag": 1,
                "freq_imag_freq_cm1": -1500.0,
            }
        ],
    }
    if validation_evidence is not None:
        transition_state["calculations"].append(
            {
                "key": "ts-irc",
                "type": "irc",
                "geometry_key": "ts-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _BUNDLE_LOT,
            }
        )
        transition_state["validation_evidence"] = validation_evidence
    bundle: dict = {
        "species": species
        or [
            _bundle_species("ch3", "[CH3]", 2, _XYZ_CH3),
            _bundle_species("h", "[H]", 2, _XYZ_H),
            _bundle_species("ch4", "C", 1, _XYZ_CH4),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
        "transition_state": transition_state,
    }
    if atom_map is not None:
        bundle["atom_map"] = atom_map
    return bundle


def _map(source: str = "declared", **overrides) -> dict:
    atom_map: dict = {
        "source": source,
        "ts_geometry_key": "ts-geom",
        "participants": [
            {
                "side": "reactant",
                "species_key": "ch3",
                "participant_index": 1,
                "geometry_key": "ch3-geom",
                "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4},
            },
            {
                "side": "reactant",
                "species_key": "h",
                "participant_index": 2,
                "geometry_key": "h-geom",
                "atom_to_ts": {1: 5},
            },
            {
                "side": "product",
                "species_key": "ch4",
                "participant_index": 1,
                "geometry_key": "ch4-geom",
                "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
            },
        ],
    }
    atom_map.update(overrides)
    return atom_map


class TestAtomMapping:
    def test_an_element_that_changes_across_the_map(self, client):
        # Ammonia in the methyl's slot: the map's reactant end is now N
        # where its saddle-point end is C.
        species = [
            _bundle_species("ch3", "[NH3]", 2, _XYZ_NH3),
            _bundle_species("h", "[H]", 2, _XYZ_H),
            _bundle_species("ch4", "C", 1, _XYZ_CH4),
        ]
        response = client.post(
            "/api/v1/uploads/computed-reaction",
            json=_bundle(_map(), species=species),
        )
        _assert_code(response, "atom_map_element_not_conserved")

    def test_one_saddle_point_atom_claimed_twice(self, client):
        participants = _map()["participants"]
        participants[1]["atom_to_ts"] = {1: 2}
        response = client.post(
            "/api/v1/uploads/computed-reaction",
            json=_bundle(_map(participants=participants)),
        )
        _assert_code(response, "atom_map_not_a_bijection")

    def test_an_index_the_named_geometry_does_not_have(self, client):
        participants = _map()["participants"]
        participants[0]["atom_to_ts"] = {1: 1, 2: 2, 3: 3, 9: 4}
        response = client.post(
            "/api/v1/uploads/computed-reaction",
            json=_bundle(_map(participants=participants)),
        )
        _assert_code(response, "atom_map_indices_not_geometry_relative")

    def test_a_geometry_the_participant_does_not_own(self, client):
        participants = _map()["participants"]
        participants[0]["geometry_key"] = "ch4-geom"
        response = client.post(
            "/api/v1/uploads/computed-reaction",
            json=_bundle(_map(participants=participants)),
        )
        _assert_code(response, "atom_map_indices_not_geometry_relative")

    def test_a_saddle_point_atom_claimed_by_neither_leg(self, client):
        # A six-atom saddle point on a five-atom reaction. Both legs are
        # complete over every declared participant and the reaction
        # balances, so nothing is missing to explain an atom that comes
        # from nothing and becomes nothing — which is the precondition the
        # rule is gated on.
        bundle = _bundle(_map())
        bundle["transition_state"]["geometry"]["xyz_text"] = _XYZ_TS_SPARE_ATOM
        response = client.post("/api/v1/uploads/computed-reaction", json=bundle)
        _assert_code(response, "atom_map_atoms_unaccounted_for")

    def test_two_legs_that_do_not_cover_the_same_atoms(self, client):
        # Same six-atom saddle point, but now methane's fifth hydrogen
        # lands on the spare atom: each leg is a bijection and the two
        # disagree about which saddle-point atoms the reaction is made of.
        participants = _map()["participants"]
        participants[2]["atom_to_ts"] = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6}
        bundle = _bundle(_map(participants=participants))
        bundle["transition_state"]["geometry"]["xyz_text"] = _XYZ_TS_SPARE_ATOM
        response = client.post("/api/v1/uploads/computed-reaction", json=bundle)
        body = _assert_code(response, "atom_map_atoms_unaccounted_for")
        assert "No species is missing" in str(body["detail"])

    def test_an_inferred_map_that_names_no_algorithm(self, client):
        response = client.post(
            "/api/v1/uploads/computed-reaction",
            json=_bundle(_map(source="inferred")),
        )
        body = _assert_code(response, "atom_map_inferred_requires_note")
        assert "ADR 0011" in str(body["detail"])


# ---------------------------------------------------------------------------
# The atom map against the IRC partition
# ---------------------------------------------------------------------------


_IRC_EVIDENCE_CONTRADICTING_THE_MAP = [
    {
        "kind": "irc",
        "passed": True,
        "rationale": "IRC descends to CH3 + H on the reactant side.",
        "source_calculation_key": "ts-irc",
        # The map says methyl is saddle-point atoms 1-4 and the lone
        # hydrogen is 5. This partition swaps 4 and 5, so both surfaces
        # describe the same saddle point and disagree about one atom.
        "reactant_participant_mapping": {
            "reactant:1": [1, 2, 3, 5],
            "reactant:2": [4],
        },
        "product_participant_mapping": {"product:1": [1, 2, 3, 4, 5]},
    }
]


class TestTheAtomMapAgainstTheIrcPartition:
    def test_a_map_that_contradicts_its_own_irc_partition(self, client):
        response = client.post(
            "/api/v1/uploads/computed-reaction",
            json=_bundle(
                _map(),
                validation_evidence=_IRC_EVIDENCE_CONTRADICTING_THE_MAP,
            ),
        )
        body = _assert_code(response, "atom_map_contradicts_irc_mapping")
        assert "atom_map_contradicts_irc_mapping" in str(body["detail"])

    def test_an_irc_partition_that_hands_a_participant_other_elements(
        self, client
    ):
        # No atom map at all, so the agreement check above cannot fire and
        # only the element check can: the lone hydrogen is handed the
        # saddle point's carbon.
        evidence = [
            {
                "kind": "irc",
                "passed": True,
                "rationale": "IRC descends to CH3 + H on the reactant side.",
                "source_calculation_key": "ts-irc",
                "reactant_participant_mapping": {
                    "reactant:1": [2, 3, 4, 5],
                    "reactant:2": [1],
                },
                "product_participant_mapping": {
                    "product:1": [1, 2, 3, 4, 5]
                },
            }
        ]
        response = client.post(
            "/api/v1/uploads/computed-reaction",
            json=_bundle(validation_evidence=evidence),
        )
        body = _assert_code(
            response, "transition_state_irc_mapping_element_mismatch"
        )
        assert "transition_state_irc_mapping_element_mismatch" in str(
            body["detail"]
        )
