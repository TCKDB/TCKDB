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

import pytest

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
        assert body["detail"] == (
            "Reaction is not element-balanced (reaction_mass_balance_failed)."
        )
        # The message says the reaction does not balance; the context says
        # what it is short of, without anyone parsing a sentence for it.
        assert body["context"] == {
            "reactants": {"C": 1, "H": 5},
            "products": {"C": 1, "H": 3},
        }

    def test_charge_losing_reaction_names_charge_conservation(self, client):
        # [OH-] + [H] -> H2O drops an electron on the way across. The
        # elemental balance is satisfied, so only the charge law can fire.
        response = client.post(
            "/api/v1/uploads/reactions",
            json=_reaction_payload([_HYDROXIDE, _H_ATOM], [_WATER]),
        )
        body = _assert_code(response, "reaction_charge_not_conserved")
        assert "conserved across a reaction" in str(body["detail"])

    def test_the_same_entry_on_both_sides_names_the_null_reaction(self, client):
        entry = {"smiles": "C/C=C/C", "charge": 0, "multiplicity": 1}
        response = client.post(
            "/api/v1/uploads/reactions",
            json=_reaction_payload([entry], [entry]),
        )
        body = _assert_code(response, "reaction_entry_null_reaction")
        assert "id=" not in str(body["detail"])

    def test_an_isomerisation_between_two_entries_of_one_species_is_accepted(self, client):
        e = {"smiles": "C/C=C/C", "charge": 0, "multiplicity": 1}
        z = {"smiles": "C/C=C\\C", "charge": 0, "multiplicity": 1}
        response = client.post(
            "/api/v1/uploads/reactions",
            json=_reaction_payload([z], [e]),
        )
        assert response.status_code == 201, response.text


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
_D2O_XYZ = (
    "3\nheavy water\n"
    "O  0.000  0.000  0.117\n"
    "D  0.000  0.757 -0.469\n"
    "D  0.000 -0.757 -0.469"
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

    def test_a_calculation_geometry_of_another_molecule_names_its_own_code(
        self, client
    ):
        """The conformer geometry is right; the calculation's is not.

        Deliberately a *different* code from the check above, and this is the
        payload that shows why one would not do: the species entry's own
        structure is correct methane, so ``species_geometry_composition_mismatch``
        cannot fire, and the only thing wrong is a geometry attached to a
        calculation. A client repairing that edits a calculation's
        ``input_geometries``; a client repairing the other edits the
        conformer. Different fields, different repairs, different codes.
        """
        payload = _conformer_payload(
            species_entry=_METHANE, xyz_text=_METHANE_XYZ
        )
        payload["calculation"]["input_geometries"] = [{"xyz_text": _METHYL_XYZ}]
        response = client.post("/api/v1/uploads/conformers", json=payload)
        body = _assert_code(response, "calculation_geometry_composition_mismatch")
        assert "calculation_geometry_composition_mismatch" in str(body["detail"])

    def test_a_calculation_geometry_of_another_isotopologue_names_its_own_code(
        self, client
    ):
        """Right formula, right conformer, wrong isotopes on the calculation.

        Composition reads D and [2H] as H, so only the calculation-level
        isotope check can refuse this; a different code from
        ``species_geometry_isotope_mismatch``, which is repaired on the
        conformer rather than on a calculation.
        """
        payload = _conformer_payload(species_entry=_METHANE, xyz_text=_METHANE_XYZ)
        payload["calculation"]["input_geometries"] = [
            {"xyz_text": _METHANE_XYZ, "isotopes": {2: 2}}
        ]
        response = client.post("/api/v1/uploads/conformers", json=payload)
        body = _assert_code(response, "calculation_geometry_isotope_mismatch")
        assert "calculation_geometry_isotope_mismatch" in str(body["detail"])

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

    def test_a_d_geometry_under_a_protium_species_names_the_isotope_check(
        self, client
    ):
        """``D`` declares deuterium: heavy water cannot be filed under ``O``."""
        response = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(species_entry=_WATER, xyz_text=_D2O_XYZ),
        )
        body = _assert_code(response, "species_geometry_isotope_mismatch")
        assert body["context"]["geometry_substitutions"] == "2Hx2"

    def test_a_d_geometry_under_a_2H_species_is_accepted(self, client):
        response = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                species_entry={"smiles": "[2H]O[2H]", "charge": 0, "multiplicity": 1},
                xyz_text=_D2O_XYZ,
            ),
        )
        assert response.status_code == 201, response.text

    def test_an_isotopes_entry_against_the_d_spelling_names_the_symbol_conflict(
        self, client
    ):
        payload = _conformer_payload(
            species_entry={"smiles": "[2H]O[2H]", "charge": 0, "multiplicity": 1},
            xyz_text=_D2O_XYZ,
        )
        payload["geometry"]["isotopes"] = {"2": 3}
        body = _assert_code(
            client.post("/api/v1/uploads/conformers", json=payload),
            "geometry_isotope_symbol_conflict",
        )
        assert body["context"]["implied_mass_number"] == 2
        assert body["context"]["declared_mass_number"] == 3

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

    def test_a_kind_the_stored_identity_denies_names_the_kind_check(
        self, client, db_session
    ):
        """An ordinary deposit must not silently inherit ``pseudo``.

        A pseudo-species carries a free-text ``smiles`` -- it has no
        atom-resolved structure to canonicalise -- so nothing stops one from
        being registered under a string that is also a real molecule's
        canonical SMILES. Species identity is ``(smiles, charge,
        multiplicity)`` and excludes ``kind``, so the next ordinary deposit of
        that molecule resolves onto the pseudo row. Before the check it took
        the stored kind without a word, and ``pseudo`` is what makes
        ``validate_reaction_elemental_balance`` and its charge twin decline to
        judge a reaction -- so mass balance switched itself off for methane and
        no surface recorded it.
        """
        from app.db.models.common import MoleculeKind, StereoKind
        from app.db.models.species import Species

        db_session.add(
            Species(
                kind=MoleculeKind.pseudo,
                smiles="C",
                inchi_key="PSEUDOLUMPEDCH-UHFFFAOYSA-N",
                charge=0,
                multiplicity=1,
                stereo_kind=StereoKind.achiral,
            )
        )
        db_session.flush()

        response = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                species_entry=_METHANE, xyz_text=_METHANE_XYZ
            ),
        )
        body = _assert_code(response, "species_kind_conflict")
        assert "already stored as molecule_kind" in str(body["detail"])

    def test_marks_and_geometry_that_describe_different_stereoisomers_name_the_stereo_check(
        self, client
    ):
        """``C/C=C/C`` is trans-2-butene; a cis geometry under it is one molecule described twice."""
        from tests.services.test_stereo_descriptor import embed_xyz

        response = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                species_entry={"smiles": "C/C=C/C", "charge": 0, "multiplicity": 1},
                xyz_text=embed_xyz("C/C=C\\C"),
            ),
        )
        body = _assert_code(response, "species_stereo_contradiction")
        assert body["context"]["sources"] == ["geometry", "SMILES stereo marks"]
        assert "id=" not in str(body["detail"])


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
# A frequency result against its own frequency list
# ---------------------------------------------------------------------------
#
# Unlike every other entry above, this one asserts nothing about the
# potential energy surface. It says only that the scalar the ESS printed
# and the mode rows deposited beside it must answer the same question the
# same way -- a contract between two fields of one record, which is the
# narrow thing the blocking-tier rule reserves the blocking tier for.


class TestAFrequencyResultAgainstItsOwnModes:
    def test_a_scalar_that_outruns_the_frequency_list(self, client):
        """``n_imag = 3`` beside one imaginary row is refused by name.

        The failure this guards: accepted, the record's cheap summary
        claims three imaginary modes while its evidence table shows one,
        a reader who trusts the summary and a reader who reads the modes
        get different answers about the same row, and neither is told.
        """
        response = client.post(
            "/api/v1/uploads/conformers",
            json={
                **_conformer_payload(
                    species_entry=_METHANE, xyz_text=_METHANE_XYZ
                ),
                "calculation": _freq_calc(
                    n_imag=3,
                    imag_freq_cm1=-1300.0,
                    frequencies=[-1300.0, 800.0, 1600.0],
                ),
            },
        )
        body = _assert_code(response, "freq_n_imag_disagrees_with_modes")
        # The sentence 0.27.0 emitted is byte-for-byte the sentence a
        # prose-matching client still sees; the code is new information
        # beside it, not a rewrite of it.
        assert (
            "n_imag=3 does not match imaginary mode count 1 in modes."
            in str(body["detail"])
        )
        # What it is short of, without anyone parsing that sentence.
        assert body["context"] == {
            "n_imag": 3,
            "imaginary_mode_count": 1,
            "mode_count": 3,
        }

    def test_the_same_scalar_with_no_frequency_list_is_accepted(self, client):
        """Absence is not disagreement, and the rule must not conflate them.

        Identical ``n_imag`` to the test above, with the frequency list
        omitted rather than contradicting it. A depositor who uploads no
        per-mode data has an incomplete record, not a false one, and the
        read API already says so by reporting
        ``n_imag_at_or_above_tau = null`` rather than ``0`` for exactly
        this state. It is the asymmetry that makes the check above safe
        to block on, and a rule written "mode rows must exist" would
        refuse this deposit.

        Ridden on a van der Waals complex because that is the one
        declared kind for which several imaginary modes are an
        expectation rather than a contradiction, so nothing else in the
        payload can be what refuses it.
        """
        payload = _conformer_payload(
            species_entry={**_METHANE, "species_entry_kind": "vdw_complex"},
            xyz_text=_METHANE_XYZ,
            label="vdw-scalar-only",
        )
        payload["additional_calculations"] = [
            _freq_calc(n_imag=3, imag_freq_cm1=-22.0)
        ]
        response = client.post("/api/v1/uploads/conformers", json=payload)
        assert response.status_code == 201, response.text
        assert "n_imag_higher_order_saddle" in {
            warning["code"] for warning in response.json()["warnings"]
        }

    def test_a_frequency_list_that_agrees_is_deposited(self, client):
        """The same three imaginary modes, declared, are accepted.

        the imaginary-mode protocol's own motivating record -- a reaction coordinate at
        -1300 with -42 and -13 beside it -- with the scalar agreeing with
        the list, the barrier designated and the two extras declared. The
        blocking tier above has to let this through or it is the
        ``n_imag == 1`` gate under another name.
        """
        response = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                label="ts-declared-extras",
                freq=_freq_calc(
                    n_imag=3,
                    imag_freq_cm1=-1300.0,
                    frequencies=[-1300.0, -42.0, -13.0, 900.0],
                    reaction_coordinate_mode_index=1,
                    dispositions={2: "torsion", 3: "rigid_body_residue"},
                ),
            ),
        )
        assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# A frequency list against the geometry it is attached to
# ---------------------------------------------------------------------------
#
# The one bound in ``tckdb_schemas.frequency_completeness`` that refuses.
# ``3N`` is the dimension of the nuclear coordinate space, so a list longer
# than it is not a short spectrum -- it is not a spectrum of that geometry
# under any harmonic treatment, grid, coordinate system or level of theory.
# The *floor* (``freq_list_incomplete_for_geometry``) stays advisory, and
# the last test here is what proves the promotion did not drag it along:
# a filtered list has several honest explanations and ``modes = null`` is
# accepted, so blocking on the floor would pay a depositor to delete the
# list. Nothing filters modes *in*, so the ceiling has no such reading.


def _water_conformer(frequencies: list[float] | None, *, label: str) -> dict:
    """A water conformer depositing *frequencies* as its own spectrum.

    Water because ``3N = 9`` and ``3N - 6 = 3`` are far enough apart that
    the floor, the ceiling and the space between them are three visibly
    different lists rather than three off-by-ones.
    """
    payload = _conformer_payload(
        species_entry=_WATER,
        xyz_text=(
            "3\nwater\n"
            "O  0.000000  0.000000  0.117300\n"
            "H  0.000000  0.757200 -0.469200\n"
            "H  0.000000 -0.757200 -0.469200"
        ),
        label=label,
    )
    payload["additional_calculations"] = [
        _freq_calc(n_imag=0, frequencies=frequencies)
    ]
    return payload


class TestAFrequencyListAgainstItsGeometry:
    def test_ten_modes_on_a_nine_degree_of_freedom_geometry(self, client):
        """The refusal, named in the ``code`` field.

        Water has nine Cartesian degrees of freedom in total. A tenth
        mode describes motion the geometry does not have, so the list and
        the geometry are not records of the same molecule -- and no
        re-run, tighter grid or different coordinate system changes that,
        which is why this refuses where the floor warns.
        """
        response = client.post(
            "/api/v1/uploads/conformers",
            json=_water_conformer(
                [float(100 * n) for n in range(1, 11)], label="water-overlong"
            ),
        )
        body = _assert_code(
            response, "freq_list_exceeds_geometry_degrees_of_freedom"
        )
        # The arithmetic a depositor needs, in the sentence rather than
        # left for them to work out.
        assert "carries 10 modes" in str(body["detail"])
        assert "only 9 degrees of freedom" in str(body["detail"])
        # Which element of the payload, without parsing the sentence.
        assert body["context"] == {
            "locations": ["additional_calculations[0].freq_result.modes"]
        }

    def test_exactly_three_n_is_accepted_because_the_ceiling_is_inclusive(
        self, client
    ):
        """Nine modes on water: the whole mass-weighted Hessian spectrum.

        the imaginary-mode protocol §"What a record must carry" asks for the six
        translation/rotation eigenvalues *as well as* the spectrum, "so
        contamination is directly assessable". That record sits exactly on
        the ceiling, so a strict comparison would refuse the most complete
        deposit the rule describes. This is the assertion that keeps the
        refusal above narrow.
        """
        response = client.post(
            "/api/v1/uploads/conformers",
            json=_water_conformer(
                [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 1595.0, 3657.0, 3756.0],
                label="water-all-nine",
            ),
        )
        assert response.status_code == 201, response.text

    def test_a_linear_molecule_at_its_ceiling_is_accepted(self, client):
        """Carbon dioxide, nine modes, ``3N = 9``.

        Linearity is never determined by this check -- doing so would mean
        choosing a collinearity tolerance at validation time -- so the
        molecule most likely to be mis-bounded by a rule written around
        ``3N - 6`` is asserted directly. Its vibrational count is
        ``3N - 5 = 4``, one *more* than ``3N - 6``, and its full
        eigenvalue set is nine.
        """
        payload = _conformer_payload(
            species_entry={"smiles": "O=C=O", "charge": 0, "multiplicity": 1},
            xyz_text=(
                "3\ncarbon dioxide\n"
                "C  0.000000  0.000000  0.000000\n"
                "O  0.000000  0.000000  1.162000\n"
                "O  0.000000  0.000000 -1.162000"
            ),
            label="co2-all-nine",
        )
        payload["additional_calculations"] = [
            _freq_calc(
                n_imag=0,
                frequencies=[
                    0.1, 0.2, 0.3, 0.4, 0.5, 667.0, 667.0, 1333.0, 2349.0
                ],
            )
        ]
        response = client.post("/api/v1/uploads/conformers", json=payload)
        assert response.status_code == 201, response.text

    def test_a_short_list_still_only_warns(self, client):
        """The floor did not become a block by accident.

        Two modes where water has three. Advisory on purpose: a
        partial-Hessian job, a frozen-atom or ONIOM Hessian and a lumped
        participant all produce fewer modes and are honest records of what
        was computed, TCKDB carries no field saying which, and
        ``modes = null`` is accepted -- so a block here would pay a
        depositor to delete the list, which the imaginary-mode protocol §"Why not refuse,
        when refusing is cheaper" calls worse than no rule. Asserted
        beside the refusal above because a promotion applied one bound too
        widely is invisible from the blocking side.
        """
        response = client.post(
            "/api/v1/uploads/conformers",
            json=_water_conformer([1595.0, 3756.0], label="water-short"),
        )
        assert response.status_code == 201, response.text
        assert "freq_list_incomplete_for_geometry" in {
            warning["code"] for warning in response.json()["warnings"]
        }


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

    def test_the_same_mistake_on_a_sibling_a_factor_reports_the_same_code(
        self, client
    ):
        """A PLOG term's A-units are the same refusal as the main line's.

        The main-line check calls ``validate_a_units_for_molecularity``
        directly; every sibling A-factor (a ``multi_arrhenius`` term, a PLOG
        entry, a falloff k0) goes through ``_validate_a_units_named``, which
        prefixes the field so the client learns *which* term failed. That
        wrapper must not re-raise a plain ``ValueError``: because
        ``CodedValueError`` is a ``ValueError``, the declared code would be
        destroyed at the raise site -- before any promotion rule could see
        it. The same chemistry error would then arrive under two different
        contracts depending on where in the payload it was written.

        The main line here is deliberately *correct* (``per_s`` for one
        reactant), so the only failure is the PLOG entry's: with more than
        one validation failure the envelope falls back by design, and this
        would then pass for the wrong reason.
        """
        response = client.post(
            "/api/v1/uploads/kinetics",
            json={
                "reaction": _reaction_payload([_METHANE], [_METHANE]),
                "scientific_origin": "experimental",
                "model_kind": "plog",
                "a": 2.16e8,
                "a_units": "per_s",
                "n": 0.0,
                "reported_ea": 14.35,
                "reported_ea_units": "kj_mol",
                "plog_entries": [
                    {
                        "entry_index": 1,
                        "pressure_bar": 1.0,
                        "a": 1.0e10,
                        # Order-2 units on a unimolecular reaction.
                        "a_units": "cm3_mol_s",
                        "n": 0.0,
                        "ea_kj_mol": 0.0,
                    }
                ],
            },
        )
        body = _assert_code(response, "arrhenius_a_units_molecularity_mismatch")
        detail = str(body["detail"])
        # The prose is unchanged: the field prefix the wrapper has always
        # added, then the check's own sentence, verbatim.
        assert "plog_entries[1].a_units: " in detail
        assert "is incompatible with" in detail
        # And the field name did not become the code. That would be the
        # fabrication -- a leading token published as though a client
        # could branch on it.
        assert body["code"] != "plog_entries[1].a_units"
        assert body["code"] != "a_units"
        # The structured facts survive the re-raise, and name the term.
        assert body["context"]["field"] == "plog_entries[1].a_units"
        assert body["context"]["molecularity"] == 1


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
        # Ammonia in the methyl's slot: the map's reactant end is N
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
        # Same six-atom saddle point, but methane's fifth hydrogen
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

    def test_the_structured_facts_reach_the_envelope_context(self, client):
        """``context`` is the half of the contract that is not prose.

        A wire-schema refusal is reported as a Pydantic error list, so its
        structured facts would be reachable only by parsing the sentence
        that named them. They are lifted to the envelope's own ``context``,
        which is where a service-raised refusal has always put them — so a
        client reads one place regardless of which side of the boundary
        refused it.
        """
        participants = _map()["participants"]
        participants[0]["atom_to_ts"] = {1: 1, 2: 2, 3: 3, 9: 4}
        response = client.post(
            "/api/v1/uploads/computed-reaction",
            json=_bundle(_map(participants=participants)),
        )
        body = _assert_code(response, "atom_map_indices_not_geometry_relative")
        assert body["context"] == {
            "atom_index": 9,
            "geometry_key": "ch3-geom",
            "geometry_atom_count": 4,
        }


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

        # The two compositions that disagree, and the two things they belong
        # to. A ``*_mismatch`` names neither side by construction
        # (``Shape.relationship`` in ``app.api.code_catalogue``), so
        # the only machine-readable fact here would be that *something* did
        # not match. Asserted key by key, not as "context is non-empty" --
        # that would pass against a handler stuffing in a constant.
        context = body["context"]
        # ``reactant:1`` is CH3 and the partition hands it the saddle
        # point's four hydrogens, so it is the first slot to disagree.
        assert context["role"] == "reactant", context
        assert context["participant_key"] == "reactant:1", context
        assert context["participant_index"] == 1, context
        assert context["ts_atom_indices"] == [2, 3, 4, 5], context
        assert context["assigned_composition"] == {"H": 4}, context
        assert context["declared_composition"] == {"C": 1, "H": 3}, context
        assert context["declared_smiles"] == "[CH3]", context
        assert context["field"] == "transition_state.validation_evidence", context

        # No row ids reach a client: every fact here
        # is either the depositor's own payload coordinate or an element
        # count.
        assert not any(
            key == "id" or key.endswith(("_id", "_ids")) for key in context
        ), context


# ---------------------------------------------------------------------------
# Transition-state energy ordering against the stored energies
# ---------------------------------------------------------------------------


class TestStatedEnergyAgainstStoredEnergy:
    def test_the_637_fixture_names_its_code_in_the_envelope(self, client):
        """-40.20 Eh stated from a single point that stores -40.9 Eh."""
        from tests.api.test_api_ts_contract_621 import (
            _bundle,
            _energy_ordering,
            _post_bundle,
        )

        payload = _bundle([_energy_ordering()])
        for calculation in payload["transition_state"]["calculations"]:
            if calculation["key"] == "ts-sp":
                calculation["sp_electronic_energy_hartree"] = -40.9
        body = _assert_code(
            _post_bundle(client, payload), "ts_energy_ordering_stated_energy_mismatch"
        )
        context = body["context"]
        assert context["participant"] == "ts", context
        assert context["energy_kind"] == "electronic", context
        assert context["stated_hartree"] == -40.20, context
        assert context["stored_hartree"] == -40.9, context
        assert context["field"] == "transition_state.validation_evidence[0].energies[0]", context
        assert not any(key == "id" or key.endswith(("_id", "_ids")) for key in context), context


# ---------------------------------------------------------------------------
# The type of calculation an energy is cited to
# ---------------------------------------------------------------------------


class TestEnergySourceType:
    def test_an_e0_cited_to_a_single_point_names_its_code_in_the_envelope(self, client):
        from tests.api.test_api_ts_contract_621 import _bundle, _energy_ordering, _post_bundle

        ordering = _energy_ordering()
        e0_of_ts = next(e for e in ordering["energies"] if e["participant"] == "ts" and e["energy_kind"] == "e0")
        e0_of_ts["source_calculation_key"] = "ts-sp"
        body = _assert_code(
            _post_bundle(client, _bundle([ordering])), "ts_energy_ordering_source_type_mismatch"
        )
        context = body["context"]
        assert context["energy_kind"] == "e0", context
        assert context["actual_calculation_type"] == "sp", context
        assert context["accepted_calculation_types"] == ["composite", "freq"], context
        assert not any(key == "id" or key.endswith(("_id", "_ids")) for key in context), context

    def test_a_network_energy_cited_to_a_scan_names_its_code_in_the_envelope(self, client):
        from tests.api.test_api_network_pdep_energy_source_types import _PDEP_URL, _payload, _set_state_energy

        payload = _payload()
        _set_state_energy(payload, "ethyl_scan", "electronic_only")
        body = _assert_code(client.post(_PDEP_URL, json=payload), "network_energy_source_type_mismatch")
        context = body["context"]
        assert context["actual_calculation_type"] == "scan", context
        assert context["field"] == "solve.state_energies[0].source_calculation_key", context
        assert not any(key == "id" or key.endswith(("_id", "_ids")) for key in context), context


# ---------------------------------------------------------------------------
# A network state energy against the sum of its per-participant sources
# ---------------------------------------------------------------------------


class TestNetworkStateEnergySum:
    def test_a_state_energy_one_term_short_names_its_code_in_the_envelope(self, client):
        """The entrance state is ethyl + O2 (-230.0 Eh); the payload states ethyl alone."""
        from app.chemistry.units import HARTREE_TO_KJ_MOL
        from tests.api.test_api_network_pdep_state_energy_sources import _payload

        payload = _payload()
        payload["solve"]["state_energies"][0]["energy_kj_mol"] = -79.8 * HARTREE_TO_KJ_MOL
        body = _assert_code(
            client.post("/api/v1/uploads/networks/pdep", json=payload),
            "network_state_energy_sum_mismatch",
        )
        context = body["context"]
        assert context["field"] == "solve.state_energies[0].energy_kj_mol", context
        assert context["state_key"] == "entrance", context
        assert not any(key == "id" or key.endswith(("_id", "_ids")) for key in context), context


class TestARecordedHessianMethodAgainstItsInputDeck:
    def test_a_recorded_method_the_attached_deck_contradicts_is_refused_by_name(self, client):
        """``hessian_method_contradicts_input_deck``: recorded analytic, deck says NumFreq.

        The failure this guards: judged against whichever statement the
        reader happened to pick, a mode at 40 cm-1 is noise at tau 50 and real
        curvature at tau 30, and the record keeps both statements.
        """
        import base64

        deck = "! B3LYP def2-SVP NumFreq\n* xyz 0 1\nC 0 0 0\n*\n"
        payload = {
            "species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1},
            "conformers": [
                {
                    "key": "c0",
                    "geometry": {"xyz_text": _METHANE_XYZ},
                    "primary_calculation": {
                        "key": "opt0",
                        "type": "opt",
                        "software_release": {"name": "ORCA", "version": "6.0.1"},
                        "level_of_theory": {"method": "B3LYP", "basis": "def2-SVP"},
                        "opt_result": {"converged": True},
                        "parameters": [
                            {
                                "raw_key": "freq",
                                "raw_value": "analytic",
                                "canonical_key": "freq.hessian_method",
                                "canonical_value": "analytic",
                            }
                        ],
                        "artifacts": [
                            {
                                "kind": "input",
                                "filename": "job.in",
                                "content_base64": base64.b64encode(deck.encode()).decode(),
                            }
                        ],
                    },
                }
            ],
        }
        response = client.post("/api/v1/uploads/computed-species", json=payload)
        body = _assert_code(response, "hessian_method_contradicts_input_deck")
        assert body["context"]["recorded"] == "analytic"
        assert body["context"]["input_deck"] == "finite_difference_gradient"


# ---------------------------------------------------------------------------
# Codes declared in declarations.py
# ---------------------------------------------------------------------------


class TestAFrequencyListNumbersItsModesOnce:
    def test_two_rows_with_one_mode_index_are_refused_by_name(self, client):
        """``mode_index`` is the row's identity in the list; two rows with one index say which mode is which nowhere."""
        calc = _freq_calc(n_imag=0, frequencies=[800.0, 1600.0])
        calc["freq_result"]["modes"][1]["mode_index"] = 1
        response = client.post(
            "/api/v1/uploads/conformers",
            json={
                **_conformer_payload(species_entry=_METHANE, xyz_text=_METHANE_XYZ),
                "calculation": calc,
            },
        )
        body = _assert_code(response, "freq_mode_index_not_unique")
        assert body["context"]["duplicate_mode_indices"] == [1]


class TestABathGasThatIsNotACompositionIsRefusedByName:
    @pytest.mark.parametrize(
        ("case", "code"),
        [
            ("high_pressure_limit", "kinetics_bath_gas_at_high_pressure_limit"),
            ("sum_above_one", "kinetics_bath_gas_mole_fraction_sum"),
            ("one_species_twice", "kinetics_bath_gas_duplicate_species"),
        ],
    )
    def test_the_response_carries_the_code(self, client, case, code):
        from tests.api.test_api_kinetics_bath_gas import _APPARENT, HE, KINETICS, N2, _standalone

        overrides = {
            "high_pressure_limit": {
                "pressure_context": "high_p_limit",
                "bath_gas": [{"species_entry": HE, "mole_fraction": 1.0}],
            },
            "sum_above_one": {
                **_APPARENT,
                "bath_gas": [{"species_entry": HE, "mole_fraction": 0.8}, {"species_entry": N2, "mole_fraction": 0.5}],
            },
            "one_species_twice": {
                **_APPARENT,
                "bath_gas": [{"species_entry": HE, "mole_fraction": 0.5}, {"species_entry": HE, "mole_fraction": 0.5}],
            },
        }[case]
        response = client.post(KINETICS, json=_standalone(**overrides))
        assert response.status_code == 422, response.text
        assert response.json()["code"] == code


#: Envelope codes whose end-to-end proof already lives next to the feature's other tests, where the payload builders
#: are. Each is a refusal posted through the test client whose ``code`` is asserted. The guard in
#: ``test_scientific_check_register.py`` requires every envelope code to be named in this file; the test below makes
#: the naming mean something, by opening the cited file and finding a test that posts a request and reads the code.
PROVED_IN: dict[str, str] = {
    "network_channel_kind_mismatch": "workflows/test_network_channel_kind.py",
    "network_state_duplicate_composition": "workflows/test_network_pdep_duplicate_states.py",
    "network_state_duplicate_participant": "workflows/test_network_pdep_duplicate_states.py",
    "network_energy_source_subject_mismatch": "api/test_api_network_pdep_energy_source_subjects.py::test_a_well_cannot_cite_another_wells_single_point",
    "thermo_energy_level_contradiction": "api/test_api_composite_role_consistency.py",
    "statmech_energy_level_contradiction": "api/test_api_statmech_thermo_levels.py",
    "kinetics_energy_level_contradiction": "api/test_api_kinetics_declared_energy_level.py",
    "thermo_sp_geometry_mismatch": "api/test_api_statmech_thermo_levels.py",
    "statmech_sp_geometry_mismatch": "api/test_api_statmech_thermo_levels.py",
    "scf_stability_source_geometry_mismatch": "api/test_api_bundle_extras_622_sources.py",
    "calculation_geometry_conformer_mismatch": "api/test_api_calculation_geometry_conformer_agreement.py",
    "calculation_input_geometry_mismatch": "api/test_api_calculation_geometry_conformer_agreement.py",
    "composite_e0_inconsistent": "api/test_api_composite_program_run.py::test_the_two_arithmetic_checks_block_at_the_api",
    "composite_terms_do_not_sum": "api/test_api_composite_program_run.py::test_the_two_arithmetic_checks_block_at_the_api",
    "composite_total_mismatch": "api/test_api_composite_assembled_review.py",
    "composite_input_level_mismatch": "api/test_api_composite_assembled.py",
    "composite_input_geometry_mismatch": "api/test_api_composite_assembled.py",
    "sp_energy_component_total_mismatch": "api/test_api_sp_energy_components.py",
    "sp_energy_components_do_not_sum": "api/test_api_sp_energy_components.py",
    "kinetics_declaration_contradicts_record": "api/test_api_kinetics_declarations.py",
    "kinetics_determination_mismatch": "api/test_api_kinetics_declarations.py",
    "statmech_model_declaration_mismatch": "api/test_api_statmech_model_declaration.py",
    "structure_determination_mismatch": "api/test_api_structure_declarations.py",
    "enthalpy_declaration_absent": "api/test_enthalpy_reference.py",
    "enthalpy_declaration_without_content": "api/test_enthalpy_reference.py",
    "enthalpy_quantity_not_storable_here": "api/test_enthalpy_reference.py",
    "enthalpy_reference_kind_unrecognized": "api/test_enthalpy_reference.py",
    "unsupported_reaction_molecularity": "api/test_api_untested_refusals_tier_de.py",
    "atom_map_without_transition_state": "api/test_api_untested_refusals_tier_de.py",
    "atom_map_participant_not_declared": "api/test_api_untested_refusals_tier_de.py",
    "thermo_declared_by_target_incoherent": "api/test_api_thermo_declarations.py",
    "thermo_declared_by_record_incoherent": "api/test_api_thermo_declarations.py",
    "scf_stability_source_cycle": "api/test_api_bundle_extras_622_sources.py",
    "transition_state_calculation_species_geometry": "api/test_api_calculation_geometry_conformer_agreement.py",
    "thermo_source_role_type_mismatch": "api/test_api_upload_key_and_role_contracts.py",
    "statmech_source_role_type_mismatch": "api/test_api_upload_key_and_role_contracts.py",
    "lowest_energy_across_levels": "api/test_lookup_visibility_and_selection.py",
    "unsafe_lowest_energy_comparison": "api/test_api_untested_refusals_tier_a.py",
    "relative_conformer_energy_compare_identical_protocols": "api/scientific/test_api_relative_conformer_energies.py",
    "thermo_energy_sp_and_composite_linked": "api/test_api_composite_role_consistency.py",
    "statmech_energy_sp_and_composite_linked": "api/test_api_composite_role_consistency.py",
    "thermo_energy_level_ambiguous": "api/test_api_composite_role_consistency.py",
    "applied_energy_correction_source_calculation_owner_mismatch": "api/test_api_bundle_ownership_codes.py",
    "bac_total_requires_components": "api/test_api_bac_total_requires_components.py",
    "composite_input_owner_mismatch": "api/test_api_composite_assembled.py",
    "kinetics_interpretation_conformer_selection_owner_mismatch": "api/test_api_statmech_citation_ownership.py",
    "kinetics_interpretation_statmech_owner_mismatch": "api/test_api_statmech_citation_ownership.py",
    "kinetics_protocol_calculation_owner_mismatch": "api/test_api_kinetics_declarations.py",
    "observation_identity_hint_conflict": "api/test_api_observation_identity_attach.py",
    "observation_identity_target_not_ground_state_minimum": "api/test_api_observation_identity_attach.py",
    "scf_stability_source_calculation_owner_mismatch": "api/test_api_bundle_extras_622_sources.py",
    "scf_stability_source_conformer_unanchored": "api/test_api_bundle_extras_622_sources.py",
    "sp_energy_component_duplicate": "api/test_api_sp_energy_components.py",
    "statmech_energy_level_ambiguous": "api/test_api_statmech_thermo_levels.py",
    "statmech_energy_level_requires_sp": "api/test_api_statmech_thermo_levels.py",
    "statmech_role_duplicate": "api/test_api_statmech_thermo_levels.py",
    "thermo_energy_level_requires_sp": "api/test_api_statmech_thermo_levels.py",
    "thermo_role_duplicate": "api/test_api_statmech_thermo_levels.py",
    "statmech_source_calculation_owner_mismatch": "api/test_api_bundle_ownership_codes.py",
    "statmech_torsion_scan_calculation_owner_mismatch": "api/test_api_bundle_torsion_scan_ownership.py",
    "thermo_protocol_calculation_owner_mismatch": "api/test_api_thermo_declarations.py",
    "thermo_source_calculation_owner_mismatch": "api/test_api_uploads.py",
    "thermo_statmech_owner_mismatch": "api/test_api_statmech_citation_ownership.py",
    "transport_source_calculation_owner_mismatch": "api/test_api_bundle_extras_622.py",
}




_HTTP_METHODS = frozenset({"post", "put", "patch", "get", "delete"})
_HTTP_HELPERS = ("_refused", "_post", "post_json", "_deposit", "_upload")

def _cites(source: str, code: str, named: str | None = None) -> list[str]:
    """Names of the tests in ``source`` that prove ``code`` over HTTP.

    A test proves it when, in executable code (not the docstring or comments), a request is sent and the
    response ``code`` is compared for equality (or membership) with this code, directly, through a
    parametrised value, or through a module helper that compares a response ``code`` with its parameter. It
    must take the ``client`` fixture and must not be skipped or expected to fail. ``named`` limits the search
    to one test.
    """
    import ast

    tree = ast.parse(source)
    spellings = {code} | {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value == code
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    def mentions(node: ast.AST, extra: frozenset[str] = frozenset()) -> bool:
        return any(
            (isinstance(n, ast.Constant) and n.value == code)
            or (isinstance(n, ast.Name) and (n.id in spellings or n.id in extra))
            for n in ast.walk(node)
        )

    def is_code_key(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and node.value == "code"

    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("test_")}

    def reads_a_response_code_directly(node: ast.AST) -> bool:
        return any(
            (isinstance(n, ast.Subscript) and is_code_key(n.slice))
            or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
                and n.args and is_code_key(n.args[0]))
            for n in ast.walk(node)
        )

    #: Module helpers that return a response's ``code`` (``def code(response): return response.json()["code"]``).
    code_readers = {
        name
        for name, func in functions.items()
        if any(isinstance(n, ast.Return) and reads_a_response_code_directly(n) for n in ast.walk(func))
    }

    def reads_response_code(node: ast.AST) -> bool:
        return reads_a_response_code_directly(node) or any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in code_readers
            for n in ast.walk(node)
        )

    def equality_with_code(compare: ast.Compare, extra: frozenset[str]) -> bool:
        if not all(isinstance(op, (ast.Eq, ast.In)) for op in compare.ops):
            return False
        sides = [compare.left, *compare.comparators]
        reading = [side for side in sides if reads_response_code(side)]
        return bool(reading) and any(mentions(side, extra) for side in sides if side not in reading)

    def helper_compares_its_parameter(helper: ast.FunctionDef) -> bool:
        params = frozenset(arg.arg for arg in [*helper.args.args, *helper.args.kwonlyargs])
        return any(
            isinstance(n, ast.Compare)
            and all(isinstance(op, (ast.Eq, ast.In)) for op in n.ops)
            and any(reads_response_code(side) for side in [n.left, *n.comparators])
            and any(
                mentions(side, params) or (isinstance(side, ast.Name) and side.id in params)
                for side in [n.left, *n.comparators]
            )
            for n in ast.walk(helper)
        )

    def proves(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        body = list(func.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]  # the docstring is prose
        scope = ast.Module(body=body, type_ignores=[])
        if any("skip" in ast.unparse(d) or "xfail" in ast.unparse(d) for d in func.decorator_list):
            return False
        if "client" not in {arg.arg for arg in [*func.args.args, *func.args.kwonlyargs]}:
            return False
        # A parametrised value that carries the code counts as naming it where the body compares that value.
        parametrised = (
            frozenset(arg.arg for arg in [*func.args.args, *func.args.kwonlyargs])
            if any(mentions(decorator) for decorator in func.decorator_list)
            else frozenset()
        )
        sends = False
        proved = False
        for node in ast.walk(scope):
            if isinstance(node, ast.Call):
                callee = node.func
                if isinstance(callee, ast.Attribute) and callee.attr in _HTTP_METHODS:
                    sends = True
                if isinstance(callee, ast.Name) and callee.id.startswith(_HTTP_HELPERS):
                    sends = True
                if isinstance(callee, ast.Name) and callee.id in functions:
                    helper = functions[callee.id]
                    arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
                    if helper_compares_its_parameter(helper) and any(mentions(a) for a in arguments):
                        proved = True
                    if any(
                        isinstance(n, ast.Compare) and equality_with_code(n, frozenset()) for n in ast.walk(helper)
                    ):
                        proved = True
            if isinstance(node, ast.Compare) and equality_with_code(node, parametrised):
                proved = True
        return sends and proved

    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            if named is not None and node.name != named:
                continue
            if proves(node):
                found.append(node.name)
    return found


def test_every_code_cited_as_proved_elsewhere_has_a_test_that_posts_and_reads_it():
    """Each cited file has a test that sends a request and compares the response code with the cited code."""
    from pathlib import Path

    tests_root = Path(__file__).resolve().parents[1]
    assert len(PROVED_IN) >= 30, "the map lost its content, so this test would pass vacuously"
    unproved = {}
    for code, citation in sorted(PROVED_IN.items()):
        relative, _, named_test = citation.partition("::")
        path = tests_root / relative
        assert path.exists(), f"{code}: {relative} does not exist"
        if not _cites(path.read_text(), code, named_test or None):
            unproved[code] = citation
    assert not unproved, (
        "These codes are cited as proved in another file, but no test there sends a request and compares the "
        f"response code with the code (not skipped, with the client fixture): {unproved}. "
        "Write one here, or fix the citation."
    )


class TestTheCitationGuardIsNotVacuous:
    """The guard that vouches for the citations must refuse the shapes it exists to refuse."""

    GOOD = (
        "def test_x(client):\n"
        '    body = client.post("/u", json={}).json()\n'
        '    assert body["code"] == "some_code"\n'
    )

    def test_a_real_proof_is_accepted(self):
        assert _cites(self.GOOD, "some_code") == ["test_x"]

    def test_a_mention_in_a_docstring_or_comment_is_not_a_proof(self):
        source = (
            "def test_x(client):\n"
            '    """Refuses some_code."""\n'
            "    # some_code\n"
            '    body = client.post("/u", json={}).json()\n'
            '    assert body["code"] == "another_code"\n'
        )
        assert _cites(source, "some_code") == []

    def test_a_skipped_or_xfailed_test_is_not_a_proof(self):
        assert _cites("import pytest\n@pytest.mark.skip\n" + self.GOOD, "some_code") == []
        assert _cites("import pytest\n@pytest.mark.xfail\n" + self.GOOD, "some_code") == []

    def test_a_test_that_sends_nothing_or_reads_nothing_is_not_a_proof(self):
        no_request = 'def test_x(client):\n    body = {"code": "some_code"}\n    assert body["code"] == "some_code"\n'
        no_read = 'def test_x(client):\n    client.post("/u", json={"k": "some_code"})\n'
        assert _cites(no_request, "some_code") == []
        assert _cites(no_read, "some_code") == []

    def test_a_message_contains_assertion_is_not_a_proof(self):
        source = (
            "def test_x(client):\n"
            '    resp = client.post("/u", json={})\n'
            '    assert "some_code" in resp.text\n'
            "    resp.content.decode()\n"
        )
        assert _cites(source, "some_code") == []

    def test_a_negative_comparison_is_not_a_proof(self):
        source = (
            "def test_x(client):\n"
            '    body = client.post("/u", json={}).json()\n'
            '    assert body["code"] != "some_code"\n'
        )
        assert _cites(source, "some_code") == []

    def test_a_mention_only_in_the_payload_is_not_a_proof(self):
        source = (
            "def test_x(client):\n"
            '    body = client.post("/u", json={"k": "some_code"}).json()\n'
            '    assert body["code"] == "another_code"\n'
        )
        assert _cites(source, "some_code") == []

    def test_a_helper_that_compares_its_parameter_with_the_response_code_is_a_proof(self):
        source = (
            "def _assert_code(resp, expected):\n"
            '    assert resp.json()["code"] == expected\n'
            "def test_x(client):\n"
            '    _assert_code(client.post("/u", json={}), "some_code")\n'
        )
        assert _cites(source, "some_code") == ["test_x"]

    def test_the_citation_that_was_wrong_in_the_first_draft_is_refused(self):
        """``thermo_sp_geometry_mismatch`` was cited to a file whose tests do not prove it."""
        from pathlib import Path

        old = Path(__file__).resolve().parents[1] / "api/test_api_bundle_extras_622_sources.py"
        assert _cites(old.read_text(), "thermo_sp_geometry_mismatch") == []
