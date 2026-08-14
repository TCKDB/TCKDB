"""The proof that a frequency list is checked against its own geometry.

`ADR 0012 <../../../docs/adr/0012-imaginary-modes-are-judged-by-magnitude-not-counted.md>`_
§"What a record must carry" requires "the complete signed unrounded
frequency list, never filtered", and until this branch nothing asked.
A depositor could send three modes, all imaginary, with ``n_imag = 3``
and pass every check TCKDB had, ``freq_n_imag_disagrees_with_modes``
included — that rule compares the imaginary count against ``n_imag``,
and a list of nothing but imaginary modes satisfies it exactly.

Why it matters beyond tidiness: `paper/18__TCKDB/3_results.tex` reports
recovering a harmonic spectrum from a stored Hessian and comparing it
against the independently stored frequency lists, agreeing to within
0.045 cm-1. A subset agrees on every mode it contains and is silent
about the ones it omits, which reads as agreement. The completeness of
the stored list is load-bearing for that claim.

Two bounds, two tiers, and the split is the design rather than an
accident of when each was written.

The **floor** — fewer modes than the smallest complete spectrum the
geometry admits — is **warn**. The two arguments are set out in
``tckdb_schemas.frequency_completeness``; the one this file makes
executable is the second, which is that ``modes = null`` is accepted and
must stay accepted, so a block's cheapest workaround is deleting the
frequency list — turning a partial list into no list at all.

The **ceiling** — more modes than ``3N`` — is **block**. ``3N`` is the
total number of Cartesian degrees of freedom, the six rigid-body modes
included, so no harmonic analysis of that geometry produces more at any
level of theory, on any grid, in any coordinate system: there is no
correct deposit to refuse, and no filtering that produces *extra* modes,
so neither of the floor's arguments reaches it. It warned in 0.29.0
pending a scientific-check register entry and now has one
(``CHECK_FREQ_LIST_WITHIN_GEOMETRY_DEGREES_OF_FREEDOM``).

So the tests below assert ``201`` and read a warning out of the body for
everything on the floor side, and ``422`` with a named ``code`` for
everything past the ceiling. The end-to-end proof that the code reaches
the ``code`` field of the response body — as opposed to appearing inside
its prose — lives in ``test_api_scientific_rejection_codes.py``, which is
where the register's guard requires it.
"""

from __future__ import annotations

from tckdb_schemas.frequency_completeness import (
    W_FREQ_LIST_EXCEEDS_GEOMETRY,
    W_FREQ_LIST_INCOMPLETE,
)

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}

#: Water, non-linear: 3 atoms, 3N - 6 = 3 vibrational modes.
_WATER_XYZ = (
    "3\nwater\n"
    "O  0.000000  0.000000  0.117300\n"
    "H  0.000000  0.757200 -0.469200\n"
    "H  0.000000 -0.757200 -0.469200"
)
_WATER_SPECTRUM = [1595.0, 3657.0, 3756.0]

#: Carbon dioxide, linear: 3 atoms, 3N - 5 = 4 vibrational modes. The
#: case a naive ``3N - 6`` rule would flag as an over-count, or a naive
#: "exactly 3N - 6" rule as a mismatch.
_CO2_XYZ = (
    "3\ncarbon dioxide\n"
    "C  0.000000  0.000000  0.000000\n"
    "O  0.000000  0.000000  1.162000\n"
    "O  0.000000  0.000000 -1.162000"
)
_CO2_SPECTRUM = [667.0, 667.0, 1333.0, 2349.0]


def _freq_calc(frequencies: list[float] | None, *, n_imag: int = 0) -> dict:
    result: dict = {"n_imag": n_imag, "zpe_hartree": 0.02}
    if frequencies is not None:
        result["modes"] = [
            {
                "mode_index": index + 1,
                "frequency_cm1": value,
                "is_imaginary": value < 0,
            }
            for index, value in enumerate(frequencies)
        ]
    return {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "freq_result": result,
    }


def _conformer_payload(
    *,
    smiles: str,
    xyz_text: str,
    frequencies: list[float] | None,
    label: str,
    multiplicity: int = 1,
    molecule_kind: str | None = None,
) -> dict:
    species_entry: dict = {
        "smiles": smiles,
        "charge": 0,
        "multiplicity": multiplicity,
    }
    if molecule_kind is not None:
        species_entry["molecule_kind"] = molecule_kind
    return {
        "species_entry": species_entry,
        "geometry": {"xyz_text": xyz_text},
        "calculation": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "additional_calculations": [_freq_calc(frequencies)],
        "label": label,
    }


def _post(client, payload: dict):
    resp = client.post("/api/v1/uploads/conformers", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post_refused(client, payload: dict) -> dict:
    """Post a payload the ceiling must refuse, and return the 422 body.

    Separate from :func:`_post` rather than parameterised on status,
    because a helper that accepts either would let a refusal quietly
    become an acceptance and vice versa.
    """
    resp = client.post("/api/v1/uploads/conformers", json=payload)
    assert resp.status_code == 422, resp.text
    return resp.json()


def _completeness_warnings(body: dict) -> list[dict]:
    return [
        w
        for w in body["warnings"]
        if w["code"] in {W_FREQ_LIST_INCOMPLETE, W_FREQ_LIST_EXCEEDS_GEOMETRY}
    ]


class TestACompleteListIsAccepted:
    def test_water_depositing_its_three_modes_draws_no_completeness_warning(
        self, client
    ):
        body = _post(
            client,
            _conformer_payload(
                smiles="O",
                xyz_text=_WATER_XYZ,
                frequencies=_WATER_SPECTRUM,
                label="water-complete",
            ),
        )
        assert _completeness_warnings(body) == []

    def test_water_depositing_all_nine_eigenvalues_is_also_complete(self, client):
        """ADR 0012 asks for the six translation/rotation eigenvalues too.

        "the six translation/rotation eigenvalues must be present so
        contamination is directly assessable". A record carrying all
        ``3N`` is the most complete record the ADR describes, so the
        upper bound is ``3N`` and not ``3N - 6`` — a rule that refused
        this would refuse exactly what the ADR asks for.
        """
        body = _post(
            client,
            _conformer_payload(
                smiles="O",
                xyz_text=_WATER_XYZ,
                frequencies=[0.1, 0.2, 0.3, 12.0, 15.0, 18.0, *_WATER_SPECTRUM],
                label="water-with-rigid-body-block",
            ),
        )
        assert _completeness_warnings(body) == []


class TestATruncatedListSaysSo:
    def test_water_depositing_one_of_three_modes_is_accepted_and_flagged(
        self, client
    ):
        """The same geometry, one mode deposited instead of three.

        Accepted — 201, the record is stored — and flagged, which is the
        whole tier decision made visible: the evidence survives, and a
        consumer is told the list is not the spectrum.
        """
        body = _post(
            client,
            _conformer_payload(
                smiles="O",
                xyz_text=_WATER_XYZ,
                frequencies=[3756.0],
                label="water-truncated",
            ),
        )
        warnings = _completeness_warnings(body)
        assert [w["code"] for w in warnings] == [W_FREQ_LIST_INCOMPLETE]
        assert "1 mode(s)" in warnings[0]["message"]
        assert "at least 3" in warnings[0]["message"]

    def test_the_attack_this_branch_closes_is_reported(self, client):
        """Only the imaginary modes deposited, ``n_imag`` agreeing exactly.

        The record ``freq_n_imag_disagrees_with_modes`` cannot see: the
        count and the list agree, because everything in the list is
        imaginary, so PR #160's rule is satisfied byte for byte. It is
        deposited against a van der Waals complex because that is the
        one declared kind whose imaginary modes are accepted rather than
        refused — which is exactly the corner where a subset could hide.
        """
        payload = _conformer_payload(
            smiles="O",
            xyz_text=_WATER_XYZ,
            frequencies=[-40.0, -18.0],
            label="water-imaginary-only",
        )
        payload["species_entry"]["species_entry_kind"] = "vdw_complex"
        payload["additional_calculations"][0]["freq_result"]["n_imag"] = 2
        body = _post(client, payload)
        assert [w["code"] for w in _completeness_warnings(body)] == [
            W_FREQ_LIST_INCOMPLETE
        ]


class TestAnOverlongListIsRefused:
    """The other bound, and the other tier.

    Kept as its own class rather than sitting beside the truncation tests,
    because the two are different judgements about different records and
    reading them under one heading is how the tiers get conflated again.
    """

    def test_a_list_longer_than_the_geometry_allows_is_refused(self, client):
        """Ten modes on a three-atom geometry: nine degrees of freedom exist.

        Arithmetically impossible rather than merely short, so it gets its
        own code *and* its own tier — the usual cause is a calculation
        attached to the wrong geometry, which is a different repair from
        "deposit the rest of the list" and a different judgement about
        whether the record can be stored at all.
        """
        body = _post_refused(
            client,
            _conformer_payload(
                smiles="O",
                xyz_text=_WATER_XYZ,
                frequencies=[float(100 * n) for n in range(1, 11)],
                label="water-overlong",
            ),
        )
        assert body["code"] == W_FREQ_LIST_EXCEEDS_GEOMETRY
        # Nothing was written, so there is no warning to carry either: the
        # blocking tier owns the fact it refuses.
        assert "warnings" not in body


class TestALinearMoleculeIsNotFlagged:
    def test_carbon_dioxide_depositing_3n_minus_5_is_accepted_silently(
        self, client
    ):
        """The case a naive ``3N - 6`` rule breaks.

        CO2 has four vibrational modes, not three, and no linearity
        determination happens anywhere in this check — the floor is the
        weakest bound that is certainly true, so a collinear geometry
        clears it without being asked whether it is collinear.
        """
        body = _post(
            client,
            _conformer_payload(
                smiles="O=C=O",
                xyz_text=_CO2_XYZ,
                frequencies=_CO2_SPECTRUM,
                label="co2-linear",
            ),
        )
        assert _completeness_warnings(body) == []

    def test_hydrogen_molecule_deposits_its_single_mode(self, client):
        """Two atoms are collinear by definition, so ``N = 2`` is exact.

        ``3N - 6`` is zero here and would accept an empty list; the
        floor for a diatomic is therefore stated as ``3N - 5 = 1``,
        which is a fact about two points rather than a measurement.
        """
        body = _post(
            client,
            _conformer_payload(
                smiles="[H][H]",
                xyz_text="2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.741",
                frequencies=[4401.0],
                label="h2-complete",
            ),
        )
        assert _completeness_warnings(body) == []

    def test_a_diatomic_depositing_an_empty_list_is_flagged(self, client):
        body = _post(
            client,
            _conformer_payload(
                smiles="[H][H]",
                xyz_text="2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.741",
                frequencies=[],
                label="h2-empty",
            ),
        )
        assert [w["code"] for w in _completeness_warnings(body)] == [
            W_FREQ_LIST_INCOMPLETE
        ]


class TestSpeciesWithNoModesToDeposit:
    def test_a_single_atom_depositing_no_modes_is_accepted(self, client):
        """A hydrogen atom has zero vibrational modes, and an empty list
        is the complete spectrum for it."""
        body = _post(
            client,
            _conformer_payload(
                smiles="[H]",
                xyz_text="1\nH atom\nH 0.0 0.0 0.0",
                frequencies=[],
                label="h-atom-empty",
                multiplicity=2,
            ),
        )
        assert _completeness_warnings(body) == []

    def test_a_single_atom_reporting_four_modes_is_refused(self, client):
        """Three translations exist, so ``3N = 3`` is the ceiling.

        A fourth entry describes motion the geometry does not have, and a
        one-atom geometry is the sharpest form of the case: there is no
        vibrational mode to be partially reported, so a non-empty list of
        length four cannot be a filtered anything.
        """
        body = _post_refused(
            client,
            _conformer_payload(
                smiles="[H]",
                xyz_text="1\nH atom\nH 0.0 0.0 0.0",
                frequencies=[10.0, 20.0, 30.0, 40.0],
                label="h-atom-overlong",
                multiplicity=2,
            ),
        )
        assert body["code"] == W_FREQ_LIST_EXCEEDS_GEOMETRY

    def test_a_single_atom_reporting_its_three_translations_is_accepted(
        self, client
    ):
        """``3N = 3`` exactly: on the ceiling, so accepted.

        The boundary that keeps the refusal above from being an off-by-one
        on the smallest geometry TCKDB accepts.
        """
        body = _post(
            client,
            _conformer_payload(
                smiles="[H]",
                xyz_text="1\nH atom\nH 0.0 0.0 0.0",
                frequencies=[0.1, 0.2, 0.3],
                label="h-atom-at-ceiling",
                multiplicity=2,
            ),
        )
        assert _completeness_warnings(body) == []


#: H + CH4 -> H2 + CH3, so the saddle point is CH5: 6 atoms, non-linear,
#: 3N - 6 = 12 vibrational modes of which one is the reaction coordinate.
_CH5_XYZ = (
    "6\nH...H-CH3 abstraction TS\n"
    "C  0.000  0.000  0.000\n"
    "H -0.510  0.883  0.000\n"
    "H -0.510 -0.883  0.000\n"
    "H  0.000  0.000 -1.090\n"
    "H  0.000  0.000  1.350\n"
    "H  0.000  0.000  2.250"
)
_CH5_SPECTRUM = [
    -1300.0,
    -42.0,
    -13.0,
    550.0,
    1100.0,
    1180.0,
    1400.0,
    1450.0,
    1500.0,
    3000.0,
    3100.0,
    3150.0,
]


def _transition_state_payload(frequencies: list[float], *, label: str) -> dict:
    return {
        "reaction": {
            "reversible": True,
            "reactants": [
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1}},
            ],
            "products": [
                {
                    "species_entry": {
                        "smiles": "[H][H]",
                        "charge": 0,
                        "multiplicity": 1,
                    }
                },
                {
                    "species_entry": {
                        "smiles": "[CH3]",
                        "charge": 0,
                        "multiplicity": 2,
                    }
                },
            ],
        },
        "charge": 0,
        "multiplicity": 2,
        "geometry": {"xyz_text": _CH5_XYZ},
        "primary_opt": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "additional_calculations": [
            {
                "type": "freq",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_result": {
                    "n_imag": 3,
                    "imag_freq_cm1": -1300.0,
                    "reaction_coordinate_mode_index": 1,
                    "modes": [
                        {
                            "mode_index": index + 1,
                            "frequency_cm1": value,
                            "is_imaginary": value < 0,
                            "imaginary_disposition": (
                                {2: "torsion", 3: "rigid_body_residue"}.get(
                                    index + 1
                                )
                            ),
                        }
                        for index, value in enumerate(frequencies)
                    ],
                },
            }
        ],
        "label": label,
    }


class TestTheTransitionStateADR0012WasWrittenAbout:
    """ADR 0012's motivating record, whole and truncated.

    -1300, -42, -13 on a six-atom saddle point. Accepted either way, and
    ADR 0012's extra-imaginary-mode warning fires either way; what
    changes is whether the record also says its frequency list is not
    the spectrum.
    """

    def test_the_complete_twelve_mode_spectrum_draws_no_completeness_warning(
        self, client
    ):
        resp = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(_CH5_SPECTRUM, label="ts-complete"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert _completeness_warnings(body) == []
        # The ADR 0012 judgement is unchanged and still present.
        assert any(
            w["code"] == "transition_state_extra_imaginary_modes_below_tau"
            for w in body["warnings"]
        ), body["warnings"]

    def test_only_the_imaginary_modes_is_accepted_and_says_it_is_partial(
        self, client
    ):
        """The exact payload the premise named: three modes, all
        imaginary, ``n_imag = 3``. Every pre-existing check passes it."""
        resp = client.post(
            "/api/v1/uploads/transition-states",
            json=_transition_state_payload(
                _CH5_SPECTRUM[:3], label="ts-imaginary-only"
            ),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        warnings = _completeness_warnings(body)
        assert [w["code"] for w in warnings] == [W_FREQ_LIST_INCOMPLETE]
        assert "3 mode(s)" in warnings[0]["message"]
        assert "at least 12" in warnings[0]["message"]


class TestSpeciesWithNoAtomsAndSpeciesWithUnknownAtoms:
    """What the atomless and lumped kinds actually do, shown not assumed."""

    def test_an_electron_never_reaches_this_check_because_it_carries_no_geometry(
        self, client
    ):
        """``ATOMLESS_MOLECULE_KINDS`` closes the door before this rule.

        ``raise_for_atomless_structure`` refuses a geometry deposited
        against ``molecule_kind: electron`` — definitional, blocking, and
        older than this branch. So an electron reaches the completeness
        check with no geometry to count, and the check is unreachable for
        it rather than wrong about it. Asserted on the status and the
        code rather than on prose, because the word "electron" appears in
        the payload and would be echoed back by any refusal.
        """
        payload = _conformer_payload(
            smiles="e",
            xyz_text="1\nnot a thing\nH 0.0 0.0 0.0",
            frequencies=[100.0],
            label="electron-with-geometry",
            multiplicity=2,
        )
        payload["species_entry"]["molecule_kind"] = "electron"
        payload["species_entry"]["charge"] = -1
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 422, resp.text

    def test_a_pseudo_species_is_refused_by_the_conformer_route_before_this_check(
        self, client
    ):
        """The lumped kind does not reach the conformer route at all.

        ``pseudo`` is deliberately absent from
        ``ATOMLESS_MOLECULE_KINDS`` — a lumped construct's composition is
        unknown rather than empty, so its geometry is not a
        contradiction the way an electron's is. It is nevertheless out of
        reach here, because ``app/chemistry/species.py:577`` refuses any
        non-``molecule`` kind on this route outright. So the lumped case
        this check might misjudge, where a mode count genuinely is not a
        function of an atom count, is not reachable through a conformer
        deposit; it is named in
        ``tckdb_schemas.frequency_completeness`` as one reason the rule
        warns rather than blocks, and this test records that the reason
        is currently hypothetical on this route rather than pretending it
        was exercised.
        """
        payload = _conformer_payload(
            smiles="O",
            xyz_text=_WATER_XYZ,
            frequencies=[1595.0],
            label="pseudo-truncated",
        )
        payload["species_entry"]["molecule_kind"] = "pseudo"
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == (
            "Conformer upload currently supports only molecule species"
        )


class TestAbsenceIsStillAccepted:
    def test_omitting_modes_entirely_draws_no_completeness_warning(self, client):
        """The load-bearing asymmetry, restated as a test.

        ``modes = null`` is incompleteness that says so, and it stays
        accepted and unflagged. This is exactly why the short-list rule
        cannot block: a block here would make deleting the list the
        cheapest way past it, and the deleted list is the one this
        record shows TCKDB has always accepted.
        """
        body = _post(
            client,
            _conformer_payload(
                smiles="O",
                xyz_text=_WATER_XYZ,
                frequencies=None,
                label="water-no-modes",
            ),
        )
        assert _completeness_warnings(body) == []
