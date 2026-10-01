"""Wire-level rules for transition-state validation evidence (#621).

These run against the wire package alone, without the backend, because the
rules they pin are the ones a producer can be held to before it sends
anything: which kinds exist, which fields belong to which kind, and which
passes the record's own numbers contradict. The backend's tests exercise the
same rules over HTTP and add what needs a database (ownership of a source
calculation, the stored shape).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tckdb_schemas.enums import MoleculeKind
from tckdb_schemas.fragments.calculation import IRCPointPayload, IRCResultPayload
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
    validate_ts_evidence_set,
)

_XYZ = "3\nts\nH 0 0 0\nH 0 0 1\nH 0 0 2"
_MOLECULE = MoleculeKind.molecule
_ELECTRON = MoleculeKind.electron


def _energy(participant: str, kind: str, value: float, key: str = "c") -> dict:
    return {
        "participant": participant,
        "energy_kind": kind,
        "energy_hartree": value,
        "source_calculation_key": key,
    }


def _ordering(**overrides) -> dict:
    record: dict = {
        "kind": "energy_ordering",
        "passed": True,
        "rationale": "TS above both wells",
        "energies": [
            _energy("ts", "electronic", -1.0),
            _energy("reactant:1", "electronic", -1.5),
            _energy("reactant:2", "electronic", -0.25),
            _energy("product:1", "electronic", -1.8),
        ],
    }
    record.update(overrides)
    return record


def _mode(**overrides) -> dict:
    record: dict = {
        "kind": "imaginary_mode",
        "passed": True,
        "rationale": "one imaginary mode",
        "imaginary_frequency_count": 1,
        "imaginary_frequency_cm1": -1200.0,
    }
    record.update(overrides)
    return record


def _set(*records: dict, reactants=(_MOLECULE, _MOLECULE), products=(_MOLECULE,)) -> None:
    validate_ts_evidence_set(
        [TransitionStateValidationEvidenceIn(**record) for record in records],
        subject_label="ts",
        xyz_text=_XYZ,
        reactant_kinds=list(reactants),
        product_kinds=list(products),
    )


class TestKinds:
    def test_the_three_kinds_validate(self):
        for record in (
            {"kind": "irc", "passed": True, "rationale": "r"},
            _ordering(),
            _mode(),
        ):
            TransitionStateValidationEvidenceIn(**record)

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(ValidationError):
            TransitionStateValidationEvidenceIn(**_mode(kind="nmd"))

    def test_at_most_one_record_per_kind(self):
        with pytest.raises(ValueError, match="at most one evidence record of each kind"):
            _set(_mode(), _mode())

    def test_one_of_each_kind_is_fine(self):
        _set({"kind": "irc", "passed": True, "rationale": "r"}, _ordering(), _mode())

    @pytest.mark.parametrize(
        "record",
        [
            {"kind": "irc", "passed": True, "rationale": "r", "energies": []},
            {"kind": "irc", "passed": True, "rationale": "r", "mode_displacement_agrees": True},
            {"kind": "imaginary_mode", "passed": True, "rationale": "r",
             "reactant_participant_mapping": {"reactant:1": [1]},
             "product_participant_mapping": {"product:1": [1]}},
            {"kind": "energy_ordering", "passed": True, "rationale": "r",
             "imaginary_frequency_count": 1},
        ],
    )
    def test_a_field_is_refused_on_a_kind_it_does_not_describe(self, record):
        with pytest.raises(ValidationError):
            TransitionStateValidationEvidenceIn(**record)


class TestEnergyOrdering:
    def test_each_energy_says_what_kind_it_is(self):
        with pytest.raises(ValidationError):
            TransitionStateValidationEvidenceIn(
                **_ordering(energies=[_energy("ts", "total", -1.0)])
            )

    def test_each_energy_names_its_source(self):
        record = _ordering()
        del record["energies"][0]["source_calculation_key"]
        with pytest.raises(ValidationError):
            TransitionStateValidationEvidenceIn(**record)

    @pytest.mark.parametrize("participant", ["TS", "reactant:0", "reactant", "product:1x", "reactants:1"])
    def test_a_malformed_participant_is_refused(self, participant):
        with pytest.raises(ValidationError):
            TransitionStateValidationEvidenceIn(
                **_ordering(energies=[_energy(participant, "electronic", -1.0)])
            )

    def test_the_record_carries_no_source_of_its_own(self):
        with pytest.raises(ValidationError, match="names its source calculations per energy"):
            TransitionStateValidationEvidenceIn(**_ordering(source_calculation_key="c"))

    def test_a_well_is_needed_on_each_side(self):
        with pytest.raises(ValidationError, match="at least one product"):
            TransitionStateValidationEvidenceIn(
                **_ordering(energies=[_energy("ts", "electronic", -1.0), _energy("reactant:1", "electronic", -2.0)])
            )

    def test_one_energy_per_slot(self):
        record = _ordering()
        record["energies"].append(_energy("ts", "electronic", -0.5))
        with pytest.raises(ValidationError, match="more than one"):
            TransitionStateValidationEvidenceIn(**record)

    def test_a_pass_the_energies_contradict_is_refused_for_each_side(self):
        # reactants sum to -1.75, so a saddle point at -1.8 is below them.
        with pytest.raises(ValueError, match="at or below the reactant side"):
            _set(_ordering(energies=[
                _energy("ts", "electronic", -1.8),
                _energy("reactant:1", "electronic", -1.5),
                _energy("reactant:2", "electronic", -0.25),
                _energy("product:1", "electronic", -3.0),
            ]))
        with pytest.raises(ValueError, match="at or below the product side"):
            _set(_ordering(energies=[
                _energy("ts", "electronic", -1.0),
                _energy("reactant:1", "electronic", -2.0),
                _energy("reactant:2", "electronic", -0.25),
                _energy("product:1", "electronic", -0.5),
            ]))

    def test_the_same_numbers_marked_failed_are_accepted(self):
        _set(_ordering(passed=False, energies=[
            _energy("ts", "electronic", -1.8),
            _energy("reactant:1", "electronic", -1.5),
            _energy("reactant:2", "electronic", -0.25),
            _energy("product:1", "electronic", -3.0),
        ]))

    def test_the_two_energy_kinds_are_held_separately(self):
        """A passing electronic ordering does not excuse a failing E0 one."""
        energies = _ordering()["energies"] + [
            _energy("ts", "e0", -1.0),
            _energy("reactant:1", "e0", -0.5),
            _energy("reactant:2", "e0", -0.25),
            _energy("product:1", "e0", -3.0),
        ]
        with pytest.raises(ValueError, match="'e0' energies"):
            _set(_ordering(energies=energies))

    def test_a_passing_ordering_covers_every_participant_with_atoms(self):
        record = _ordering()
        record["energies"] = [e for e in record["energies"] if e["participant"] != "reactant:2"]
        with pytest.raises(ValueError, match="omit reactant:2"):
            _set(record)

    def test_an_undeclared_participant_is_refused(self):
        record = _ordering()
        record["energies"].append(_energy("reactant:3", "electronic", -1.0))
        with pytest.raises(ValueError, match="does not declare"):
            _set(record)

    def test_a_free_electron_has_no_energy_to_give(self):
        record = _ordering()
        record["energies"].append(_energy("reactant:3", "electronic", -1.0))
        with pytest.raises(ValueError, match="has no atoms"):
            _set(record, reactants=(_MOLECULE, _MOLECULE, _ELECTRON))

    def test_a_free_electron_need_not_be_covered(self):
        _set(_ordering(), reactants=(_MOLECULE, _MOLECULE, _ELECTRON))


class TestImaginaryMode:
    def test_every_field_is_optional(self):
        TransitionStateValidationEvidenceIn(kind="imaginary_mode", passed=True, rationale="r")

    def test_the_frequency_is_negative(self):
        with pytest.raises(ValidationError):
            TransitionStateValidationEvidenceIn(**_mode(imaginary_frequency_cm1=1200.0))
        with pytest.raises(ValidationError):
            TransitionStateValidationEvidenceIn(**_mode(imaginary_frequency_cm1=0.0))

    def test_the_count_is_not_negative(self):
        with pytest.raises(ValidationError):
            TransitionStateValidationEvidenceIn(**_mode(imaginary_frequency_count=-1))

    def test_a_pass_with_no_imaginary_mode_is_refused(self):
        record = _mode(imaginary_frequency_count=0)
        del record["imaginary_frequency_cm1"]
        with pytest.raises(ValidationError, match="found no imaginary mode"):
            TransitionStateValidationEvidenceIn(**record)

    def test_a_failed_record_may_report_a_zero_count(self):
        record = _mode(passed=False, imaginary_frequency_count=0)
        del record["imaginary_frequency_cm1"]
        assert TransitionStateValidationEvidenceIn(**record).imaginary_frequency_count == 0

    def test_a_frequency_beside_a_zero_count_is_a_contradiction(self):
        with pytest.raises(ValidationError, match="count is 0"):
            TransitionStateValidationEvidenceIn(**_mode(passed=False, imaginary_frequency_count=0))

    def test_an_unassessed_displacement_is_none_and_not_false(self):
        record = TransitionStateValidationEvidenceIn(**_mode())
        assert record.mode_displacement_agrees is None
        assert TransitionStateValidationEvidenceIn(
            **_mode(mode_displacement_agrees=False, passed=False)
        ).mode_displacement_agrees is False


class TestIrcResultMayLeaveThingsUnstated:
    def test_nothing_is_required(self):
        result = IRCResultPayload()
        assert (result.direction, result.has_forward, result.has_reverse) == (None, None, None)

    def test_points_survive_an_unstated_direction(self):
        result = IRCResultPayload(
            points=[IRCPointPayload(point_index=0, direction="forward"), IRCPointPayload(point_index=1)]
        )
        assert len(result.points) == 2
        assert result.has_forward is None

    def test_a_stated_false_against_a_point_in_that_direction_is_refused(self):
        with pytest.raises(ValidationError, match="has_forward must be true"):
            IRCResultPayload(has_forward=False, points=[IRCPointPayload(point_index=0, direction="forward")])
        with pytest.raises(ValidationError, match="has_reverse must be true"):
            IRCResultPayload(has_reverse=False, points=[IRCPointPayload(point_index=0, direction="reverse")])

    def test_a_stated_false_is_kept_and_distinct_from_unstated(self):
        result = IRCResultPayload(has_forward=True, has_reverse=False)
        assert result.has_forward is True
        assert result.has_reverse is False
        assert IRCResultPayload(has_forward=True).has_reverse is None


@pytest.mark.parametrize(
    "first",
    ["tckdb_schemas.fragments.scan", "tckdb_schemas.fragments.calculation", "tckdb_schemas"],
)
def test_the_scan_result_field_resolves_whichever_module_is_imported_first(first):
    """``scan`` and ``calculation`` import each other's names, so order matters.

    ``scan_result`` on ``CalculationWithResultsPayload`` is a string reference
    resolved once ``scan`` has defined its result type. A fresh interpreter per
    first import is the only way to see an ordering that another test in the
    same process has already settled.
    """
    import subprocess
    import sys

    code = (
        f"import {first}\n"
        "from tckdb_schemas.fragments.calculation import CalculationWithResultsPayload as C\n"
        "assert C.__pydantic_complete__\n"
        "assert 'CalculationScanResultCreate' in str(C.model_fields['scan_result'].annotation)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-1500:]


class TestFiniteAndPlausibleValues:
    """Checked on the model itself, where no later rule can stand in for them."""

    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
    def test_a_non_finite_energy_is_refused(self, value):
        with pytest.raises(ValidationError, match="energy_hartree"):
            TransitionStateValidationEvidenceIn(
                **_ordering(passed=False, energies=[_energy("ts", "electronic", value)])
            )

    @pytest.mark.parametrize("value", [1e-9, 12.5])
    def test_a_positive_energy_is_refused(self, value):
        with pytest.raises(ValidationError, match="energy_hartree"):
            TransitionStateValidationEvidenceIn(
                **_ordering(passed=False, energies=[_energy("ts", "electronic", value)])
            )

    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
    def test_a_non_finite_imaginary_frequency_is_refused(self, value):
        with pytest.raises(ValidationError, match="imaginary_frequency_cm1"):
            TransitionStateValidationEvidenceIn(**_mode(imaginary_frequency_cm1=value))

    def test_zero_is_accepted_because_the_bare_proton_has_exactly_zero_energy(self):
        """``[H+]`` has atoms and no electrons, so its energy is exactly 0 Eh."""
        record = _ordering(
            energies=[
                _energy("ts", "electronic", -1.0),
                _energy("reactant:1", "electronic", -1.5),
                _energy("reactant:2", "electronic", 0.0),  # the proton
                _energy("product:1", "electronic", -1.8),
            ]
        )
        _set(record)  # a passing ordering with a zero-energy participant

    def test_zero_does_not_open_the_door_to_non_finite_values(self):
        with pytest.raises(ValidationError):
            TransitionStateValidationEvidenceIn(
                **_ordering(passed=False, energies=[_energy("ts", "electronic", float("-inf"))])
            )
