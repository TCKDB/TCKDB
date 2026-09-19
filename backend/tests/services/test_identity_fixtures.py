"""Run the frozen identity challenge set through the real resolution services.

The set lives in ``backend/tests/fixtures/identity/`` (README there states
the contract). Each case is resolved with
:func:`app.services.species_resolution.resolve_species_entry`, the same
service every upload path calls, and the relation between the resulting rows
is asserted against the fixture's ``expected`` value. Negative cases assert
the specific refusal.

A false merge (two inputs the fixture says are distinct landing on one row)
and a false split (one identity forking) both fail here, by case id.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter

import pytest

from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.services.species_resolution import resolve_species_entry
from tests.services.identity_fixtures import (
    CLASSES,
    EXPECTED_OUTCOMES,
    FIXTURE_DIR,
    IdentityCase,
    IdentityFixtureError,
    identity_fixture_report,
    load_identity_fixtures,
)

CASES = load_identity_fixtures()


def _payload(item) -> SpeciesEntryIdentityPayload:
    return SpeciesEntryIdentityPayload(
        smiles=item.smiles, charge=item.charge, multiplicity=item.multiplicity
    )


def _geometry(item) -> GeometryPayload | None:
    if item.xyz_text is None:
        return None
    return GeometryPayload(xyz_text=item.xyz_text, isotopes=item.isotopes)


def _resolve(session, case: IdentityCase):
    entries = []
    for item in case.inputs:
        entry = resolve_species_entry(session, _payload(item), geometry=_geometry(item))
        session.flush()
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# The set itself
# ---------------------------------------------------------------------------


def test_every_class_file_is_present_and_non_empty():
    per_class = Counter(case.klass for case in CASES)
    assert set(per_class) == set(CLASSES)
    assert all(per_class[klass] >= 2 for klass in CLASSES), per_class
    assert len(CASES) >= 20


def test_every_expected_outcome_is_exercised():
    outcomes = {case.expected for case in CASES}
    assert outcomes == set(EXPECTED_OUTCOMES)


def test_loader_refuses_an_empty_file(tmp_path):
    for klass in CLASSES:
        (tmp_path / f"{klass}.json").write_text(json.dumps([]))
    with pytest.raises(IdentityFixtureError, match="holds no cases"):
        load_identity_fixtures(tmp_path)


def test_loader_refuses_a_missing_file(tmp_path):
    for klass in CLASSES[:-1]:
        (tmp_path / f"{klass}.json").write_text((FIXTURE_DIR / f"{klass}.json").read_text())
    with pytest.raises(IdentityFixtureError, match="missing fixture file"):
        load_identity_fixtures(tmp_path)


def test_report_counts_what_the_fixtures_contain():
    report = identity_fixture_report(CASES)
    raw_counts = Counter()
    for klass in CLASSES:
        for item in json.loads((FIXTURE_DIR / f"{klass}.json").read_text()):
            raw_counts[(klass, item["expected"])] += 1
    for klass in CLASSES:
        for outcome in EXPECTED_OUTCOMES:
            assert report["by_class"][klass][outcome] == raw_counts[(klass, outcome)], (klass, outcome)
        assert report["by_class"][klass]["total"] == sum(
            raw_counts[(klass, outcome)] for outcome in EXPECTED_OUTCOMES
        )
    assert report["total_cases"] == sum(raw_counts.values()) == len(CASES)
    assert sum(report["by_outcome"].values()) == report["total_cases"]
    # The counts are not all zero and not all in one bucket.
    assert report["by_outcome"]["rejected"] >= 3
    assert report["by_outcome"]["same_species_entry"] >= 5
    assert report["cases_with_geometry"] >= 8


def test_report_is_sensitive_to_the_set_it_is_given():
    full = identity_fixture_report(CASES)
    without_spin = identity_fixture_report([case for case in CASES if case.klass != "spin"])
    assert without_spin["total_cases"] == full["total_cases"] - full["by_class"]["spin"]["total"]
    assert without_spin["by_class"]["spin"]["total"] == 0


# ---------------------------------------------------------------------------
# The relations, through the production services
# ---------------------------------------------------------------------------


def assert_relation(case: IdentityCase, entries) -> None:
    """The fixture's ``expected`` relation, over the rows the inputs resolved to."""
    entry_ids = [entry.id for entry in entries]
    species_ids = [entry.species_id for entry in entries]

    if case.expected == "same_species_entry":
        assert len(set(entry_ids)) == 1, f"{case.id}: false split into entries {entry_ids}"
    elif case.expected == "same_species_distinct_entries":
        assert len(set(species_ids)) == 1, f"{case.id}: expected one species, got {species_ids}"
        assert len(set(entry_ids)) == len(entries), f"{case.id}: false merge of entries {entry_ids}"
    elif case.expected == "distinct_species_entries":
        assert len(set(species_ids)) == len(entries), f"{case.id}: false merge of species {species_ids}"
        assert len(set(entry_ids)) == len(entries)
    else:  # pragma: no cover - the loader already refused it
        raise AssertionError(case.expected)

    if case.stereo_labels is not None:
        assert [entry.stereo_label for entry in entries] == list(case.stereo_labels), case.id
    if case.isotope_keys is not None:
        assert [entry.isotope_key for entry in entries] == list(case.isotope_keys), case.id


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_identity_case(db_session, case: IdentityCase):
    if case.expected == "rejected":
        (item,) = case.inputs
        with pytest.raises(ValueError, match=case.rejection_match):
            resolve_species_entry(db_session, _payload(item), geometry=_geometry(item))
        return

    assert_relation(case, _resolve(db_session, case))


# ---------------------------------------------------------------------------
# The check itself is not vacuous: a wrong expectation fails, by case id
# ---------------------------------------------------------------------------


def _case_with(case: IdentityCase, **changes) -> IdentityCase:
    return dataclasses.replace(case, **changes)


def test_a_false_merge_and_a_false_split_are_both_caught(db_session):
    by_id = {case.id: case for case in CASES}
    merged = by_id["tautomers/notation_variants_are_one_species"]  # resolves to one entry
    split = by_id["spin/methylene_singlet_vs_triplet"]  # resolves to two species

    with pytest.raises(AssertionError, match="false merge of species"):
        assert_relation(_case_with(merged, expected="distinct_species_entries"), _resolve(db_session, merged))
    with pytest.raises(AssertionError, match="false merge of entries"):
        assert_relation(
            _case_with(merged, expected="same_species_distinct_entries"), _resolve(db_session, merged)
        )
    with pytest.raises(AssertionError, match="false split into entries"):
        assert_relation(_case_with(split, expected="same_species_entry"), _resolve(db_session, split))
    with pytest.raises(AssertionError, match="expected one species"):
        assert_relation(_case_with(split, expected="same_species_distinct_entries"), _resolve(db_session, split))

    stereo = by_id["stereo/diazene_cis_vs_trans"]
    with pytest.raises(AssertionError, match=stereo.id):
        assert_relation(_case_with(stereo, stereo_labels=("E", "Z")), _resolve(db_session, stereo))
