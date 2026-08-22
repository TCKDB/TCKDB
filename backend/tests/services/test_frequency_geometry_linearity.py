"""The proof that "3N-5 modes" is judged against the geometry's *shape*.

``tckdb_schemas.frequency_completeness`` compares a deposited frequency
list against ``3N - 6``, the weakest bound that is certainly true for any
geometry with that many atoms. It has to be: linearity is never
determined in the wire package, and since ``3N - 5 > 3N - 6`` a linear
molecule clears a ``3N - 6`` floor without anyone having to choose a
collinearity tolerance.

The residue that leaves is one mode wide, and it runs in **both**
directions. This file is about both:

* a **non-linear** molecule depositing exactly ``3N - 5`` modes — one
  spurious extra, or one rigid-body mode left in the list — sits inside
  the accepted band and passes silently;
* a **linear** molecule depositing exactly ``3N - 6`` modes is one
  vibration short, and the wire floor cannot see it either: ``3N - 6``
  *is* that floor and it warns strictly below, so the deposit lands
  exactly on the accepted line.

The second direction has a cause the first does not, and it is the one
worth naming: a linear molecule's bending modes are **doubly
degenerate**. CO2's four vibrations are two stretches and one bend
counted twice, so a parser that de-duplicates equal frequencies emits
three — exactly ``3N - 6``. ``TestThePayloadAdapter`` deposits that
list.

Every test here uses a real molecule, and both kinds are present on
purpose. A file testing only bent geometries could not tell this check
from a rule that flags every ``3N - 5`` deposit; a file testing only
linear ones could not tell it from a rule that flags every ``3N - 6``
one. The pair is the test, in each direction, and
``TestTheTwoDirectionsDoNotOverlap`` pins that no deposit can collect
both.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.frequency_geometry_linearity import (
    BENT_MIN_TRANSVERSE_RATIO,
    COLLINEAR_MAX_TRANSVERSE_RATIO,
    W_FREQ_LIST_BENT_COUNT_FOR_LINEAR_GEOMETRY,
    W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY,
    GeometryLinearity,
    calculation_linearity_warnings,
    classify_xyz_linearity,
    evaluate_deposited_frequency_list_linearity,
    evaluate_frequency_list_linearity,
    transverse_extent_ratio,
)

# ---------------------------------------------------------------------------
# Real geometries, both kinds
# ---------------------------------------------------------------------------

#: Water. Bent, 104.5°. 3 atoms: 3N-6 = 3 vibrations, 3N-5 = 4.
WATER_XYZ = (
    "3\nwater\n"
    "O  0.000000  0.000000  0.117300\n"
    "H  0.000000  0.757200 -0.469200\n"
    "H  0.000000 -0.757200 -0.469200"
)

#: Carbon dioxide. Linear. 3 atoms: 3N-5 = 4 vibrations, one *more*
#: than water's three despite the identical atom count.
CO2_XYZ = (
    "3\ncarbon dioxide\n"
    "C  0.000000  0.000000  0.000000\n"
    "O  0.000000  0.000000  1.162000\n"
    "O  0.000000  0.000000 -1.162000"
)

#: Hydrogen cyanide. Linear, and unlike CO2 not symmetric — so a check
#: that accidentally keyed on symmetry rather than collinearity would
#: part company with reality here.
HCN_XYZ = (
    "3\nhydrogen cyanide\n"
    "H  0.000000  0.000000 -1.064000\n"
    "C  0.000000  0.000000  0.000000\n"
    "N  0.000000  0.000000  1.156000"
)

#: Acetylene. Linear, 4 atoms: 3N-5 = 7 vibrations. Four atoms rather
#: than three, so the arithmetic is exercised away from the smallest case.
ACETYLENE_XYZ = (
    "4\nacetylene\n"
    "C  0.000000  0.000000  0.601000\n"
    "C  0.000000  0.000000 -0.601000\n"
    "H  0.000000  0.000000  1.663000\n"
    "H  0.000000  0.000000 -1.663000"
)

#: Methanol. Bent and three-dimensional, 6 atoms: 3N-6 = 12 vibrations,
#: 3N-5 = 13.
METHANOL_XYZ = (
    "6\nmethanol\n"
    "C  -0.047130   0.664390   0.000000\n"
    "O  -0.047130  -0.758550   0.000000\n"
    "H  -1.093000   0.969790   0.000000\n"
    "H   0.437050   1.064220   0.890410\n"
    "H   0.437050   1.064220  -0.890410\n"
    "H   0.863080  -1.049960   0.000000"
)


def _bent_co2_xyz(x: float, y: float) -> str:
    """CO2 with its two oxygens pulled off the axis by ``y``."""
    return (
        "3\nbent carbon dioxide\n"
        "C  0.000000  0.000000  0.000000\n"
        f"O  {x:.6f}  {y:.6f}  0.000000\n"
        f"O  {-x:.6f}  {y:.6f}  0.000000"
    )


#: CO2 bent by a thousandth of a degree — ordinary optimiser convergence
#: noise, and the case that rules out reusing
#: ``LINEARITY_SINGULAR_VALUE_TOLERANCE``.
CO2_179_999_XYZ = _bent_co2_xyz(1.161000, 0.000010)

#: CO2 at 179°: genuinely between the two thresholds.
CO2_179_XYZ = _bent_co2_xyz(1.161956, 0.010140)

#: CO2 at 178° — the brief's example of a genuinely ambiguous geometry.
CO2_178_XYZ = _bent_co2_xyz(1.161823, 0.020280)

#: CO2 at 170°: bent past any tolerance argument.
CO2_170_XYZ = _bent_co2_xyz(1.157578, 0.101275)


class TestTheGeometricMeasure:
    def test_an_exactly_collinear_set_has_no_transverse_extent(self):
        coordinates = np.array([[0.0, 0.0, -1.0], [0.0, 0.0, 0.0], [0.0, 0.0, 2.0]])
        assert transverse_extent_ratio(coordinates) == pytest.approx(0.0, abs=1e-12)

    def test_the_measure_is_invariant_under_rotation_and_translation(self):
        """Collinearity is a property of the shape, not of the frame.

        A geometry deposited in a rotated or shifted frame is the same
        molecule, so a check that answered differently for the two would
        be reporting the file's orientation, not the chemistry.
        """
        parsed = classify_xyz_linearity(WATER_XYZ)
        angle = 0.7
        rotation = np.array(
            [
                [np.cos(angle), -np.sin(angle), 0.0],
                [np.sin(angle), np.cos(angle), 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
        water = np.array(
            [
                [0.0, 0.0, 0.1173],
                [0.0, 0.7572, -0.4692],
                [0.0, -0.7572, -0.4692],
            ]
        )
        moved = water @ rotation.T + np.array([3.0, -4.0, 5.0])
        assert transverse_extent_ratio(moved) == pytest.approx(
            parsed.transverse_ratio, rel=1e-9
        )

    def test_a_single_point_has_no_answer(self):
        coordinates = np.array([[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]])
        assert transverse_extent_ratio(coordinates) is None


class TestClassification:
    @pytest.mark.parametrize(
        "name,xyz",
        [
            ("co2", CO2_XYZ),
            ("hcn", HCN_XYZ),
            ("acetylene", ACETYLENE_XYZ),
            ("co2 bent by 0.001 degrees", CO2_179_999_XYZ),
        ],
    )
    def test_linear_molecules_are_classified_linear(self, name, xyz):
        assert classify_xyz_linearity(xyz).verdict is GeometryLinearity.linear, name

    @pytest.mark.parametrize(
        "name,xyz",
        [
            ("water", WATER_XYZ),
            ("methanol", METHANOL_XYZ),
            ("co2 bent to 170 degrees", CO2_170_XYZ),
        ],
    )
    def test_bent_molecules_are_classified_bent(self, name, xyz):
        assert classify_xyz_linearity(xyz).verdict is GeometryLinearity.bent, name

    @pytest.mark.parametrize(
        "name,xyz",
        [("co2 at 179 degrees", CO2_179_XYZ), ("co2 at 178 degrees", CO2_178_XYZ)],
    )
    def test_near_linear_molecules_are_left_undetermined(self, name, xyz):
        """The band between the thresholds, and why it exists.

        A bond angle of 178° is genuinely ambiguous — quasi-linear
        molecules are real, and any single cutoff mis-sorts some of them.
        The check answers "undetermined" rather than picking a side, so
        no depositor is told confidently that their record is wrong on
        the strength of two degrees.
        """
        assert (
            classify_xyz_linearity(xyz).verdict is GeometryLinearity.undetermined
        ), name

    def test_the_thresholds_leave_a_band_between_them(self):
        assert COLLINEAR_MAX_TRANSVERSE_RATIO < BENT_MIN_TRANSVERSE_RATIO

    def test_water_is_bent_by_an_order_of_magnitude_more_than_the_threshold(self):
        """The threshold is nowhere near anything real, which is the point."""
        ratio = classify_xyz_linearity(WATER_XYZ).transverse_ratio
        assert ratio > 10 * BENT_MIN_TRANSVERSE_RATIO

    def test_an_unparseable_geometry_is_not_spoken_about(self):
        assert (
            classify_xyz_linearity("not an xyz block").verdict
            is GeometryLinearity.undetermined
        )

    def test_a_diatomic_is_not_answered_here(self):
        """Two atoms are collinear by definition and already have an exact
        count from ``minimum_complete_mode_count``. Answering a question
        nobody asks would invite a caller to rely on it."""
        diatomic = "2\nHF\nH 0.0 0.0 0.0\nF 0.0 0.0 0.917"
        assert (
            classify_xyz_linearity(diatomic).verdict
            is GeometryLinearity.undetermined
        )


class TestTheWarningFiresOnlyForBentGeometries:
    def test_water_with_four_modes_is_flagged(self):
        """Four modes is 3N-5: the count a *linear* triatomic has.

        The whole residue in one case. The wire-side floor is ``3N - 6``
        = 3 and the ceiling ``3N`` = 9, so four sits comfortably inside
        the accepted band and nothing said anything before this check.
        """
        warnings = evaluate_frequency_list_linearity(4, WATER_XYZ, location="x")
        assert [w.code for w in warnings] == [
            W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY
        ]

    def test_carbon_dioxide_with_four_modes_is_not_flagged(self):
        """The same atom count, the same mode count, the opposite verdict.

        This is the test that separates the new check from a rule that
        simply flags ``3N - 5``: CO2 genuinely has four vibrations.
        """
        assert evaluate_frequency_list_linearity(4, CO2_XYZ, location="x") == []

    def test_hydrogen_cyanide_with_four_modes_is_not_flagged(self):
        assert evaluate_frequency_list_linearity(4, HCN_XYZ, location="x") == []

    def test_acetylene_with_seven_modes_is_not_flagged(self):
        assert evaluate_frequency_list_linearity(7, ACETYLENE_XYZ, location="x") == []

    def test_methanol_with_thirteen_modes_is_flagged(self):
        warnings = evaluate_frequency_list_linearity(13, METHANOL_XYZ, location="x")
        assert [w.code for w in warnings] == [
            W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY
        ]

    def test_methanol_with_its_twelve_modes_is_not_flagged(self):
        assert evaluate_frequency_list_linearity(12, METHANOL_XYZ, location="x") == []

    def test_a_near_linear_geometry_is_never_flagged(self):
        """178° with four modes: silent, because the verdict is silent.

        A warning here would be a confident-sounding claim resting on a
        two-degree bend, which no tolerance can support.
        """
        assert evaluate_frequency_list_linearity(4, CO2_178_XYZ, location="x") == []


class TestItStaysOutOfTheOtherChecksWay:
    @pytest.mark.parametrize("n_modes", [0, 1, 2, 3, 5, 6, 7, 8, 9, 10])
    def test_only_the_linear_count_is_ever_reported(self, n_modes):
        """Every other length belongs to the floor, the ceiling, or nobody.

        A payload cannot collect this warning *and* a completeness one
        for the same list: water's floor is 3 and its ceiling 9, and this
        check speaks only at 4.
        """
        assert evaluate_frequency_list_linearity(n_modes, WATER_XYZ, location="x") == []

    def test_no_list_is_not_a_wrong_list(self):
        assert evaluate_frequency_list_linearity(None, WATER_XYZ, location="x") == []

    def test_no_geometry_means_no_opinion(self):
        assert evaluate_frequency_list_linearity(4, None, location="x") == []


class TestTheMessage:
    def test_it_names_the_code_the_counts_and_the_measure(self):
        warning = evaluate_frequency_list_linearity(
            4, WATER_XYZ, location="calculations[0].freq_result.modes"
        )[0]
        assert warning.field == "calculations[0].freq_result.modes"
        assert W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY in warning.message
        assert "4 modes" in warning.message
        assert "3N-6 = 3" in warning.message
        assert "accepted and flagged" in warning.message

    def test_it_carries_no_database_identifiers(self):
        """DR-0028 Requirement 2: nothing a depositor cannot act on."""
        warning = evaluate_frequency_list_linearity(
            4, WATER_XYZ, location="calculations[0].freq_result.modes"
        )[0]
        assert "_id" not in warning.message
        assert "id=" not in warning.message


class TestGeometryResolution:
    def test_the_calculations_own_geometry_wins_over_the_fallback(self):
        """Same rule as the wire-side completeness check, restated.

        The two must count the same atoms to be talking about the same
        record. Here the calculation names a bent geometry while the
        enclosing conformer's reference is linear: the calculation's own
        is the one the frequency job ran on.
        """
        warnings = evaluate_deposited_frequency_list_linearity(
            4,
            input_geometry_xyz_text=WATER_XYZ,
            fallback_xyz_text=CO2_XYZ,
            location="x",
        )
        assert [w.code for w in warnings] == [
            W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY
        ]

    def test_the_fallback_is_used_when_the_calculation_names_none(self):
        warnings = evaluate_deposited_frequency_list_linearity(
            4,
            input_geometry_xyz_text=None,
            fallback_xyz_text=WATER_XYZ,
            location="x",
        )
        assert [w.code for w in warnings] == [
            W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY
        ]

    def test_the_linear_fallback_keeps_a_linear_deposit_silent(self):
        assert (
            evaluate_deposited_frequency_list_linearity(
                4,
                input_geometry_xyz_text=None,
                fallback_xyz_text=CO2_XYZ,
                location="x",
            )
            == []
        )


class TestThePayloadAdapter:
    def _calc(self, frequencies: list[float] | None):
        from app.schemas.fragments.calculation import CalculationWithResultsPayload

        freq_result: dict = {"n_imag": 0}
        if frequencies is not None:
            freq_result["modes"] = [
                {
                    "mode_index": index + 1,
                    "frequency_cm1": value,
                    "is_imaginary": False,
                }
                for index, value in enumerate(frequencies)
            ]
        return CalculationWithResultsPayload.model_validate(
            {
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "freq_result": freq_result,
            }
        )

    def test_a_bent_conformer_geometry_flags_its_freq_calculation(self):
        warnings = calculation_linearity_warnings(
            self._calc([1595.0, 3657.0, 3756.0, 12.0]),
            location="calculations[1].freq_result.modes",
            fallback_xyz_text=WATER_XYZ,
        )
        assert [w.code for w in warnings] == [
            W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY
        ]

    def test_a_linear_conformer_geometry_does_not(self):
        assert (
            calculation_linearity_warnings(
                self._calc([667.0, 667.0, 1333.0, 2349.0]),
                location="calculations[1].freq_result.modes",
                fallback_xyz_text=CO2_XYZ,
            )
            == []
        )

    def test_a_calculation_with_no_frequency_list_is_silent(self):
        assert (
            calculation_linearity_warnings(
                self._calc(None),
                location="calculations[1].freq_result.modes",
                fallback_xyz_text=WATER_XYZ,
            )
            == []
        )


class TestTheMirrorWarningFiresOnlyForLinearGeometries:
    """The other direction: a linear geometry one vibration short.

    Nothing reported this before. The wire-side floor for an N-atom
    geometry is ``3N - 6`` and it warns strictly below, so a linear
    molecule depositing exactly ``3N - 6`` sits on the accepted line and
    passed in silence — while a consumer recomputing a partition
    function from it got a number rather than an error.
    """

    def test_carbon_dioxide_with_three_modes_is_flagged(self):
        """Three modes is 3N-6: the count a *bent* triatomic has.

        CO2 has four vibrations. The wire floor is 3 and warns below 3,
        the ceiling is 9, so this deposit tripped nothing at all.
        """
        warnings = evaluate_frequency_list_linearity(3, CO2_XYZ, location="x")
        assert [w.code for w in warnings] == [
            W_FREQ_LIST_BENT_COUNT_FOR_LINEAR_GEOMETRY
        ]

    def test_water_with_three_modes_is_not_flagged(self):
        """The same atom count, the same mode count, the opposite verdict.

        This is what separates the check from a rule that flags every
        ``3N - 6`` deposit: water genuinely has three vibrations, and it
        is the single most common molecule in the database.
        """
        assert evaluate_frequency_list_linearity(3, WATER_XYZ, location="x") == []

    def test_hydrogen_cyanide_with_three_modes_is_flagged(self):
        """Linear but not symmetric.

        A check that keyed on symmetry rather than collinearity would
        part company with reality here, so HCN is tested in both
        directions rather than only the silent one.
        """
        warnings = evaluate_frequency_list_linearity(3, HCN_XYZ, location="x")
        assert [w.code for w in warnings] == [
            W_FREQ_LIST_BENT_COUNT_FOR_LINEAR_GEOMETRY
        ]

    def test_acetylene_with_six_modes_is_flagged(self):
        """Four atoms, so the arithmetic runs away from the smallest case."""
        warnings = evaluate_frequency_list_linearity(6, ACETYLENE_XYZ, location="x")
        assert [w.code for w in warnings] == [
            W_FREQ_LIST_BENT_COUNT_FOR_LINEAR_GEOMETRY
        ]

    def test_acetylene_with_its_seven_modes_is_not_flagged(self):
        assert evaluate_frequency_list_linearity(7, ACETYLENE_XYZ, location="x") == []

    def test_methanol_with_its_twelve_modes_is_not_flagged(self):
        """3N-6 on a bent molecule is simply correct."""
        assert evaluate_frequency_list_linearity(12, METHANOL_XYZ, location="x") == []

    def test_a_near_linear_geometry_is_never_flagged(self):
        """178 degrees with three modes: silent, because the verdict is.

        Symmetric with the bent direction. A warning here would rest a
        confident claim on a two-degree bend, and quasi-linear molecules
        are exactly where that claim would be wrong.
        """
        assert evaluate_frequency_list_linearity(3, CO2_178_XYZ, location="x") == []


class TestItStaysOutOfTheOtherChecksWayInTheMirrorDirection:
    @pytest.mark.parametrize("n_modes", [0, 1, 2, 4, 5, 6, 7, 8, 9, 10])
    def test_only_the_bent_count_is_ever_reported(self, n_modes):
        """Every other length belongs to the floor, the ceiling, or nobody.

        CO2's floor is 3 and its ceiling 9; this check speaks only at 3,
        which is the one length the floor cannot reach because the floor
        warns strictly below itself. So a payload cannot collect this
        warning and a completeness one for the same list.
        """
        assert evaluate_frequency_list_linearity(n_modes, CO2_XYZ, location="x") == []

    def test_no_list_is_not_a_wrong_list(self):
        assert evaluate_frequency_list_linearity(None, CO2_XYZ, location="x") == []

    def test_no_geometry_means_no_opinion(self):
        assert evaluate_frequency_list_linearity(3, None, location="x") == []


class TestTheTwoDirectionsDoNotOverlap:
    """No deposit may collect both codes, and each must reach one.

    The two counts differ by one and each is admitted only under the
    opposite verdict, so exclusivity is structural rather than a
    convention. Asserted anyway: a future edit that made the verdict
    check non-exclusive would otherwise produce two contradictory
    warnings on one list, telling a depositor their molecule is both too
    long and too short.
    """

    @pytest.mark.parametrize(
        "xyz",
        [WATER_XYZ, CO2_XYZ, HCN_XYZ, ACETYLENE_XYZ, METHANOL_XYZ, CO2_178_XYZ],
    )
    def test_no_length_on_any_geometry_yields_both(self, xyz):
        for n_modes in range(0, 25):
            codes = {
                w.code
                for w in evaluate_frequency_list_linearity(n_modes, xyz, location="x")
            }
            assert len(codes) <= 1, f"{n_modes} modes produced {codes}"

    def test_both_codes_are_reachable(self):
        """Guard the guard.

        The exclusivity test above passes vacuously if neither code can
        ever fire, which is precisely the shape of a check that has
        quietly stopped checking.
        """
        seen = set()
        for xyz in (WATER_XYZ, CO2_XYZ, HCN_XYZ, ACETYLENE_XYZ, METHANOL_XYZ):
            for n_modes in range(0, 25):
                seen.update(
                    w.code
                    for w in evaluate_frequency_list_linearity(
                        n_modes, xyz, location="x"
                    )
                )
        assert seen == {
            W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY,
            W_FREQ_LIST_BENT_COUNT_FOR_LINEAR_GEOMETRY,
        }


class TestTheMirrorMessage:
    def _warning(self):
        return evaluate_frequency_list_linearity(
            3, CO2_XYZ, location="calculations[0].freq_result.modes"
        )[0]

    def test_it_names_the_code_the_counts_and_the_measure(self):
        warning = self._warning()
        assert warning.field == "calculations[0].freq_result.modes"
        assert W_FREQ_LIST_BENT_COUNT_FOR_LINEAR_GEOMETRY in warning.message
        assert "3 modes" in warning.message
        assert "3N-5 = 4" in warning.message
        assert "accepted and flagged" in warning.message

    def test_it_names_the_cause_a_depositor_can_act_on(self):
        """The degenerate bending pair, named explicitly.

        This is the whole reason the case is a code of its own rather
        than an extension of ``freq_list_incomplete_for_geometry``, whose
        message argues from partial Hessians and frozen-atom regions and
        would send a depositor looking in the wrong place.
        """
        message = self._warning().message
        assert "degenerate" in message
        assert "de-duplicates" in message

    def test_it_carries_no_database_identifiers(self):
        """DR-0028 Requirement 2: nothing a depositor cannot act on."""
        message = self._warning().message
        assert "_id" not in message
        assert "id=" not in message


class TestTheMirrorPayloadAdapter:
    def _calc(self, frequencies: list[float] | None):
        from app.schemas.fragments.calculation import CalculationWithResultsPayload

        freq_result: dict = {"n_imag": 0}
        if frequencies is not None:
            freq_result["modes"] = [
                {
                    "mode_index": index + 1,
                    "frequency_cm1": value,
                    "is_imaginary": False,
                }
                for index, value in enumerate(frequencies)
            ]
        return CalculationWithResultsPayload.model_validate(
            {
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "freq_result": freq_result,
            }
        )

    def test_a_deduplicated_degenerate_pair_is_flagged(self):
        """The real defect, deposited as it actually arrives.

        CO2's spectrum is [667, 667, 1333, 2349] — the bend is doubly
        degenerate and appears twice. A parser that collapses equal
        frequencies emits three modes, which is what this deposits.
        """
        warnings = calculation_linearity_warnings(
            self._calc([667.0, 1333.0, 2349.0]),
            location="calculations[1].freq_result.modes",
            fallback_xyz_text=CO2_XYZ,
        )
        assert [w.code for w in warnings] == [
            W_FREQ_LIST_BENT_COUNT_FOR_LINEAR_GEOMETRY
        ]

    def test_the_intact_degenerate_pair_is_silent(self):
        """The same molecule, correctly deposited. Both 667s present."""
        assert (
            calculation_linearity_warnings(
                self._calc([667.0, 667.0, 1333.0, 2349.0]),
                location="calculations[1].freq_result.modes",
                fallback_xyz_text=CO2_XYZ,
            )
            == []
        )

    def test_the_calculations_own_linear_geometry_wins_over_a_bent_fallback(self):
        warnings = evaluate_deposited_frequency_list_linearity(
            3,
            input_geometry_xyz_text=CO2_XYZ,
            fallback_xyz_text=WATER_XYZ,
            location="x",
        )
        assert [w.code for w in warnings] == [
            W_FREQ_LIST_BENT_COUNT_FOR_LINEAR_GEOMETRY
        ]

    def test_a_calculation_with_no_frequency_list_is_silent(self):
        assert (
            calculation_linearity_warnings(
                self._calc(None),
                location="calculations[1].freq_result.modes",
                fallback_xyz_text=CO2_XYZ,
            )
            == []
        )
