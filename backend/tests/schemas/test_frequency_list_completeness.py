"""The arithmetic of "is this frequency list the spectrum?", pinned.

The API tests in ``tests/api/test_api_frequency_list_completeness.py``
prove the rule reaches a depositor through the real routes. This file
pins the numbers it is built on, including the boundaries — a completeness
rule is exactly as good as its ``3N - 6`` versus ``3N - 5`` versus ``3N``
edges, and every one of those has a way of being wrong that no
end-to-end test would notice.

The seam tests at the foot prove the rule is wired into all four upload
request models, not only the one that happened to get a demonstration.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tckdb_schemas.frequency_completeness import (
    W_FREQ_LIST_EXCEEDS_GEOMETRY,
    W_FREQ_LIST_INCOMPLETE,
    atom_count_of_xyz,
    evaluate_frequency_list_completeness,
    maximum_mode_count,
    minimum_complete_mode_count,
)
from tckdb_schemas.stationary_point import ValidationTier
from tckdb_schemas.workflows import (
    ComputedReactionUploadRequest,
    ComputedSpeciesUploadRequest,
    ConformerUploadRequest,
    TransitionStateUploadRequest,
)

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}

_WATER_XYZ = (
    "3\nwater\n"
    "O  0.000000  0.000000  0.117300\n"
    "H  0.000000  0.757200 -0.469200\n"
    "H  0.000000 -0.757200 -0.469200"
)


class TestTheBounds:
    @pytest.mark.parametrize(
        ("n_atoms", "expected"),
        [
            # A single atom has three translations and no vibrations.
            (1, 0),
            # Two atoms are collinear by definition, so N = 2 is exact
            # rather than a lower bound: 3N - 5 = 1. Stating it as
            # 3N - 6 = 0 would accept an empty list for every diatomic.
            (2, 1),
            # From three atoms on, linearity is a measurement this check
            # declines to make, so the floor is 3N - 6 and a collinear
            # geometry clears it with a mode to spare.
            (3, 3),
            (6, 12),
            (15, 39),
        ],
    )
    def test_minimum_complete_mode_count(self, n_atoms: int, expected: int):
        assert minimum_complete_mode_count(n_atoms) == expected

    @pytest.mark.parametrize(("n_atoms", "expected"), [(1, 3), (3, 9), (6, 18)])
    def test_maximum_is_three_n(self, n_atoms: int, expected: int):
        assert maximum_mode_count(n_atoms) == expected

    def test_a_linear_triatomic_clears_the_floor_without_being_asked(self):
        """CO2 has 3N - 5 = 4 modes and the floor for N = 3 is 3.

        The property the whole design rests on: no collinearity
        tolerance is chosen anywhere, because ``3N - 5 > 3N - 6``.
        """
        assert 4 > minimum_complete_mode_count(3)
        assert evaluate_frequency_list_completeness(4, 3, location="x") == []


class TestTheJudgement:
    def test_a_complete_spectrum_is_silent(self):
        assert evaluate_frequency_list_completeness(3, 3, location="x") == []

    def test_the_full_three_n_eigenvalue_set_is_silent(self):
        """ADR 0012 asks for the six rigid-body eigenvalues as well."""
        assert evaluate_frequency_list_completeness(9, 3, location="x") == []

    def test_one_short_of_the_floor_is_reported(self):
        findings = evaluate_frequency_list_completeness(2, 3, location="x")
        assert [f.code for f in findings] == [W_FREQ_LIST_INCOMPLETE]
        assert findings[0].tier is ValidationTier.warn
        assert findings[0].structural_flag is True

    def test_one_past_the_ceiling_is_reported_under_a_different_code(self):
        findings = evaluate_frequency_list_completeness(10, 3, location="x")
        assert [f.code for f in findings] == [W_FREQ_LIST_EXCEEDS_GEOMETRY]

    def test_one_past_the_ceiling_blocks_where_one_short_of_the_floor_warns(
        self,
    ):
        """The asymmetry, asserted as one fact rather than two.

        Written as a single test because the two tiers are only
        defensible together: the floor cannot distinguish a filtered list
        from a genuinely shorter one and ``modes = null`` is accepted, so
        blocking it would pay a depositor to delete the list; nothing
        filters modes *in*, so neither argument reaches the ceiling. A
        change that promoted or demoted one of them alone should fail
        here, which two separate assertions in two separate tests would
        let through one at a time.
        """
        over = evaluate_frequency_list_completeness(10, 3, location="x")
        under = evaluate_frequency_list_completeness(2, 3, location="x")
        assert over[0].tier is ValidationTier.block
        assert under[0].tier is ValidationTier.warn
        # The structural flag keeps an *accepted* record out of default
        # queries and bulk exports. A refused payload leaves no record to
        # keep out of anything, so the blocking finding carries none --
        # the same as every other blocking finding in ``stationary_point``.
        assert over[0].structural_flag is False
        assert under[0].structural_flag is True

    def test_the_refusal_says_why_it_is_a_refusal(self):
        """The message has to carry the argument, not only the arithmetic.

        A depositor who reads "cannot produce that many modes" and
        nothing else will look for the switch that turns the rule off.
        The sentence names the reason there is none: filtering makes a
        spectrum shorter, never longer.
        """
        message = evaluate_frequency_list_completeness(10, 3, location="x")[
            0
        ].message
        assert "carries 10 modes" in message
        assert "only 9 degrees of freedom" in message
        assert "shorter, never longer" in message
        assert message.isascii(), (
            "a message that reaches a response body and a log must be "
            "ASCII; an em dash in one rolled back a whole upload against a "
            "SQL_ASCII database on 2026-08-04 (see "
            "backend/scripts/check_runtime_ascii.py)"
        )

    def test_the_location_is_used_verbatim(self):
        findings = evaluate_frequency_list_completeness(
            1, 3, location="species['a'].calculations['f'].freq_result.modes"
        )
        assert findings[0].location == (
            "species['a'].calculations['f'].freq_result.modes"
        )
        assert findings[0].message.startswith(findings[0].location + ":")

    @pytest.mark.parametrize(
        ("n_modes", "n_atoms"),
        [(None, 3), (3, None), (None, None)],
    )
    def test_absence_reports_nothing(self, n_modes, n_atoms):
        """Neither half alone is a claim about the other.

        ``modes = null`` is incompleteness that says so and stays
        accepted; a payload with no geometry cannot be asked the
        question at all.
        """
        assert (
            evaluate_frequency_list_completeness(
                n_modes, n_atoms, location="x"
            )
            == []
        )


class TestCountingAtoms:
    def test_a_well_formed_block_counts(self):
        assert atom_count_of_xyz(_WATER_XYZ) == 3

    def test_none_counts_to_none(self):
        assert atom_count_of_xyz(None) is None

    @pytest.mark.parametrize(
        "xyz_text",
        [
            "not a number\ncomment\nH 0 0 0",
            "5\ncomment\nH 0 0 0",
            "",
        ],
    )
    def test_an_uncountable_block_declines_rather_than_guessing(self, xyz_text):
        """A geometry that cannot be parsed is another check's refusal.

        ``parse_xyz_elements`` owns that contract and raises
        ``atom_map_geometry_unparseable``; reporting the same defect here
        in different words would give a depositor two problems to fix
        where there is one.
        """
        assert atom_count_of_xyz(xyz_text) is None


# ---------------------------------------------------------------------------
# Seams: every published upload request model asks the question
# ---------------------------------------------------------------------------


def _freq_result(frequencies: list[float]) -> dict:
    return {
        "n_imag": 0,
        "modes": [
            {
                "mode_index": index + 1,
                "frequency_cm1": value,
                "is_imaginary": False,
            }
            for index, value in enumerate(frequencies)
        ],
    }


def _conformer_request(frequencies: list[float]) -> ConformerUploadRequest:
    return ConformerUploadRequest(
        species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
        geometry={"xyz_text": _WATER_XYZ},
        calculation={
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        additional_calculations=[
            {
                "type": "freq",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_result": _freq_result(frequencies),
            }
        ],
    )


def _computed_species_request(
    frequencies: list[float],
) -> ComputedSpeciesUploadRequest:
    return ComputedSpeciesUploadRequest(
        species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
        conformers=[
            {
                "key": "c1",
                "geometry": {"xyz_text": _WATER_XYZ},
                "primary_calculation": {
                    "key": "c1_opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                },
                "additional_calculations": [
                    {
                        "key": "c1_freq",
                        "type": "freq",
                        "software_release": _SOFTWARE,
                        "level_of_theory": _LOT,
                        "freq_result": _freq_result(frequencies),
                    }
                ],
            }
        ],
    )


_TS_XYZ = (
    "4\nsaddle\n"
    "C  0.000  0.000  0.000\n"
    "H  0.000  0.000  1.100\n"
    "H  1.030  0.000 -0.360\n"
    "H -0.510  0.890 -0.360"
)


def _ts_freq_result(frequencies: list[float]) -> dict:
    return {
        "n_imag": 1,
        "imag_freq_cm1": frequencies[0],
        "modes": [
            {
                "mode_index": index + 1,
                "frequency_cm1": value,
                "is_imaginary": value < 0,
            }
            for index, value in enumerate(frequencies)
        ],
    }


def _transition_state_request(
    frequencies: list[float],
) -> TransitionStateUploadRequest:
    return TransitionStateUploadRequest(
        reaction={
            "reversible": True,
            "reactants": [
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}},
            ],
            "products": [
                {"species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1}},
            ],
        },
        charge=0,
        multiplicity=1,
        geometry={"xyz_text": _TS_XYZ},
        primary_opt={
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        additional_calculations=[
            {
                "type": "freq",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_result": _ts_freq_result(frequencies),
            }
        ],
    )


def _computed_reaction_request(
    frequencies: list[float],
) -> ComputedReactionUploadRequest:
    return ComputedReactionUploadRequest(
        species=[
            {
                "key": "s1",
                "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
                "conformers": [
                    {
                        "key": "s1_c1",
                        "geometry": {"key": "s1_geom", "xyz_text": _WATER_XYZ},
                        "calculation": {
                            "key": "s1_opt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT,
                        },
                    }
                ],
                "calculations": [
                    {
                        "key": "s1_freq",
                        "type": "freq",
                        "geometry_key": "s1_geom",
                        "software_release": _SOFTWARE,
                        "level_of_theory": _LOT,
                        "freq_n_imag": 0,
                        "freq_frequencies_cm1": frequencies,
                    }
                ],
            },
            {
                "key": "s2",
                "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
                "conformers": [
                    {
                        "key": "s2_c1",
                        "geometry": {"key": "s2_geom", "xyz_text": _WATER_XYZ},
                        "calculation": {
                            "key": "s2_opt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT,
                        },
                    }
                ],
            },
        ],
        reactant_keys=["s1"],
        product_keys=["s2"],
    )


_COMPLETE = [1595.0, 3657.0, 3756.0]
_TRUNCATED = [3756.0]
_TS_COMPLETE = [-1300.0, 500.0, 900.0, 1200.0, 1500.0, 3000.0]
_TS_TRUNCATED = [-1300.0]

#: Water, ``3N = 9``: the whole mass-weighted Hessian spectrum, the six
#: near-zero rigid-body eigenvalues ADR 0012 asks for included. Sits
#: exactly on the ceiling and must be accepted.
_AT_CEILING = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 1595.0, 3657.0, 3756.0]
#: One past it, which no harmonic analysis of three atoms can produce.
_OVERLONG = [*_AT_CEILING, 4000.0]

#: The same pair for the four-atom saddle point (``3N = 12``). The first
#: entry stays imaginary so ``freq_n_imag_disagrees_with_modes`` and the
#: reaction-coordinate contract are both satisfied and the completeness
#: bound is the only rule in play.
_TS_AT_CEILING = [
    -1300.0, 0.1, 0.2, 0.3, 0.4, 0.5, 500.0, 900.0, 1200.0, 1500.0, 3000.0, 3100.0
]
_TS_OVERLONG = [*_TS_AT_CEILING, 3200.0]


class TestEveryPublishedRequestModelAsksTheQuestion:
    """One parametrised proof per published upload request body.

    Without this, a rule wired into one model and forgotten in three
    would look fully tested — which is the shape of the defect the
    completeness rule itself exists to catch.
    """

    @pytest.mark.parametrize(
        ("build", "complete", "truncated"),
        [
            (_conformer_request, _COMPLETE, _TRUNCATED),
            (_computed_species_request, _COMPLETE, _TRUNCATED),
            (_transition_state_request, _TS_COMPLETE, _TS_TRUNCATED),
            (_computed_reaction_request, _COMPLETE, _TRUNCATED),
        ],
        ids=[
            "conformers",
            "computed-species",
            "transition-states",
            "computed-reaction",
        ],
    )
    def test_a_complete_list_is_silent_and_a_truncated_one_is_not(
        self, build, complete, truncated
    ):
        assert [
            f.code
            for f in build(complete).stationary_point_findings()
            if f.code == W_FREQ_LIST_INCOMPLETE
        ] == []
        assert [
            f.code
            for f in build(truncated).stationary_point_findings()
            if f.code == W_FREQ_LIST_INCOMPLETE
        ] == [W_FREQ_LIST_INCOMPLETE]

    @pytest.mark.parametrize(
        ("build", "overlong"),
        [
            (_conformer_request, _OVERLONG),
            (_computed_species_request, _OVERLONG),
            (_transition_state_request, _TS_OVERLONG),
            (_computed_reaction_request, _OVERLONG),
        ],
        ids=[
            "conformers",
            "computed-species",
            "transition-states",
            "computed-reaction",
        ],
    )
    def test_a_list_past_the_ceiling_refuses_the_request(self, build, overlong):
        """Every published model refuses, not merely reports.

        The parametrisation above proves each model *collects* the
        finding. That is not the same claim once the ceiling blocks: a
        model could collect findings and never call
        ``raise_for_blocking_findings``, in which case the ceiling would
        be reported to the route as a warning-shaped finding that
        ``stationary_point_warnings`` silently drops -- a refusal that
        refuses nothing, and invisible from either side on its own. So
        each seam is asserted at the raise.
        """
        with pytest.raises(ValidationError) as exc:
            build(overlong)
        assert W_FREQ_LIST_EXCEEDS_GEOMETRY in str(exc.value)

    @pytest.mark.parametrize(
        ("build", "at_ceiling"),
        [
            (_conformer_request, _AT_CEILING),
            (_computed_species_request, _AT_CEILING),
            (_transition_state_request, _TS_AT_CEILING),
            (_computed_reaction_request, _AT_CEILING),
        ],
        ids=[
            "conformers",
            "computed-species",
            "transition-states",
            "computed-reaction",
        ],
    )
    def test_a_list_exactly_at_the_ceiling_is_accepted_by_every_model(
        self, build, at_ceiling
    ):
        """``3N`` exactly, through each seam, accepted and unflagged.

        The complement of the test above, and it is what keeps the
        refusal from being one mode too eager on any one seam. ADR 0012
        asks for the six translation/rotation eigenvalues alongside the
        spectrum, so this list -- the whole mass-weighted Hessian
        spectrum -- is the most complete record it describes.
        """
        assert [
            f.code
            for f in build(at_ceiling).stationary_point_findings()
            if f.code in {W_FREQ_LIST_EXCEEDS_GEOMETRY, W_FREQ_LIST_INCOMPLETE}
        ] == []


# ---------------------------------------------------------------------------
# Seams: the bundle-local transition states
# ---------------------------------------------------------------------------
#
# Added because a mutation that deleted the computed-reaction transition
# state's call left every other test in this file green. The request-level
# parametrisation above reaches each bundle's *species* side only, so a
# transition state is a separate seam and needs a separate proof — which
# is exactly the "a subset agrees on everything it contains" failure this
# whole branch is about, in test coverage rather than in frequency lists.


def _bundle_ts_payload(frequencies: list[float]) -> dict:
    return {
        "charge": 0,
        "multiplicity": 1,
        "geometry": {"key": "ts_geom", "xyz_text": _TS_XYZ},
        "calculation": {
            "key": "ts_opt",
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "calculations": [
            {
                "key": "ts_freq",
                "type": "freq",
                "geometry_key": "ts_geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_n_imag": 1,
                "freq_imag_freq_cm1": frequencies[0],
                "freq_frequencies_cm1": frequencies,
            }
        ],
    }


def _computed_reaction_ts(frequencies: list[float]):
    from tckdb_schemas.workflows.computed_reaction_upload import (
        BundleTransitionStateIn,
    )

    return BundleTransitionStateIn(**_bundle_ts_payload(frequencies))


def _network_pdep_ts(frequencies: list[float]):
    from app.schemas.workflows.network_pdep_upload import TransitionStateIn

    return TransitionStateIn(
        key="ts1", micro_reaction_key="mr1", **_bundle_ts_payload(frequencies)
    )


class TestTheBundleTransitionStateSeams:
    @pytest.mark.parametrize(
        "build",
        [_computed_reaction_ts, _network_pdep_ts],
        ids=["computed-reaction-ts", "network-pdep-ts"],
    )
    def test_a_complete_list_is_silent_and_a_truncated_one_is_not(self, build):
        assert [
            f.code
            for f in build(_TS_COMPLETE).stationary_point_findings()
            if f.code == W_FREQ_LIST_INCOMPLETE
        ] == []
        assert [
            f.code
            for f in build(_TS_TRUNCATED).stationary_point_findings()
            if f.code == W_FREQ_LIST_INCOMPLETE
        ] == [W_FREQ_LIST_INCOMPLETE]

    @pytest.mark.parametrize(
        "build",
        [_computed_reaction_ts, _network_pdep_ts],
        ids=["computed-reaction-ts", "network-pdep-ts"],
    )
    def test_the_ceiling_refuses_and_the_ceiling_itself_does_not(self, build):
        with pytest.raises(ValidationError) as exc:
            build(_TS_OVERLONG)
        assert W_FREQ_LIST_EXCEEDS_GEOMETRY in str(exc.value)
        assert [
            f.code
            for f in build(_TS_AT_CEILING).stationary_point_findings()
            if f.code in {W_FREQ_LIST_EXCEEDS_GEOMETRY, W_FREQ_LIST_INCOMPLETE}
        ] == []


def _network_pdep_species(frequencies: list[float]):
    from app.schemas.workflows.network_pdep_upload import NetworkSpeciesIn

    return NetworkSpeciesIn(
        key="w1",
        species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
        conformers=[
            {
                "key": "w1_c1",
                "geometry": {"key": "w1_geom", "xyz_text": _WATER_XYZ},
                "calculation": {
                    "key": "w1_opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                },
            }
        ],
        calculations=[
            {
                "key": "w1_freq",
                "type": "freq",
                "geometry_key": "w1_geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_n_imag": 0,
                "freq_frequencies_cm1": frequencies,
            }
        ],
    )


def _product_upload_calculations(frequencies: list[float]) -> list[dict]:
    """One keyed inline freq calculation naming the geometry it ran on.

    The statmech / thermo / transport requests carry a product and its
    evidence and never a conformer, so there is no reference geometry to
    fall back to — ``input_geometries`` is the only way the completeness
    question is answerable on these three routes, and this fixture is the
    proof that it is.
    """
    return [
        {
            "key": "f1",
            "calculation": {
                "type": "freq",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "input_geometries": [{"xyz_text": _WATER_XYZ}],
                "freq_result": _freq_result(frequencies),
            },
        }
    ]


def _statmech_request(frequencies: list[float]):
    from app.schemas.workflows.statmech_upload import StatmechUploadRequest

    return StatmechUploadRequest(
        species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
        calculations=_product_upload_calculations(frequencies),
    )


def _thermo_request(frequencies: list[float]):
    from app.schemas.workflows.thermo_upload import ThermoUploadRequest

    return ThermoUploadRequest(
        species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
        h298_kj_mol=-241.8,
        s298_j_mol_k=188.8,
        calculations=_product_upload_calculations(frequencies),
    )


def _transport_request(frequencies: list[float]):
    from app.schemas.workflows.transport_upload import TransportUploadRequest

    return TransportUploadRequest(
        species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
        sigma_angstrom=2.641,
        epsilon_over_k_k=809.1,
        calculations=_product_upload_calculations(frequencies),
    )


class TestTheProductUploadSeam:
    """statmech, thermo and transport share one traversal and one proof."""

    @pytest.mark.parametrize(
        "build",
        [_statmech_request, _thermo_request, _transport_request],
        ids=["statmech", "thermo", "transport"],
    )
    def test_a_complete_list_is_silent_and_a_truncated_one_is_not(self, build):
        assert [
            f.code
            for f in build(_COMPLETE).stationary_point_findings()
            if f.code == W_FREQ_LIST_INCOMPLETE
        ] == []
        assert [
            f.code
            for f in build(_TRUNCATED).stationary_point_findings()
            if f.code == W_FREQ_LIST_INCOMPLETE
        ] == [W_FREQ_LIST_INCOMPLETE]

    @pytest.mark.parametrize(
        "build",
        [_statmech_request, _thermo_request, _transport_request],
        ids=["statmech", "thermo", "transport"],
    )
    def test_the_ceiling_refuses_and_the_ceiling_itself_does_not(self, build):
        with pytest.raises(ValidationError) as exc:
            build(_OVERLONG)
        assert W_FREQ_LIST_EXCEEDS_GEOMETRY in str(exc.value)
        assert [
            f.code
            for f in build(_AT_CEILING).stationary_point_findings()
            if f.code in {W_FREQ_LIST_EXCEEDS_GEOMETRY, W_FREQ_LIST_INCOMPLETE}
        ] == []

    def test_a_calculation_naming_no_geometry_is_not_judged(self):
        """No geometry, no question — and no false alarm either."""
        from app.schemas.workflows.statmech_upload import StatmechUploadRequest

        request = StatmechUploadRequest(
            species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
            calculations=[
                {
                    "key": "f1",
                    "calculation": {
                        "type": "freq",
                        "software_release": _SOFTWARE,
                        "level_of_theory": _LOT,
                        "freq_result": _freq_result(_TRUNCATED),
                    },
                }
            ],
        )
        assert [
            f.code
            for f in request.stationary_point_findings()
            if f.code == W_FREQ_LIST_INCOMPLETE
        ] == []


class TestTheNetworkWellSeam:
    def test_a_complete_list_is_silent_and_a_truncated_one_is_not(self):
        assert [
            f.code
            for f in _network_pdep_species(_COMPLETE).stationary_point_findings()
            if f.code == W_FREQ_LIST_INCOMPLETE
        ] == []
        assert [
            f.code
            for f in _network_pdep_species(_TRUNCATED).stationary_point_findings()
            if f.code == W_FREQ_LIST_INCOMPLETE
        ] == [W_FREQ_LIST_INCOMPLETE]

    def test_the_ceiling_refuses_and_the_ceiling_itself_does_not(self):
        with pytest.raises(ValidationError) as exc:
            _network_pdep_species(_OVERLONG)
        assert W_FREQ_LIST_EXCEEDS_GEOMETRY in str(exc.value)
        assert [
            f.code
            for f in _network_pdep_species(_AT_CEILING).stationary_point_findings()
            if f.code in {W_FREQ_LIST_EXCEEDS_GEOMETRY, W_FREQ_LIST_INCOMPLETE}
        ] == []
