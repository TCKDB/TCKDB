"""Loader and report for the frozen identity challenge set.

The fixtures live in ``backend/tests/fixtures/identity/`` (see its README for
the contract and the file format). This module is import-only support: the
test that runs the cases is ``test_identity_fixtures.py``; a paper generator
can import :func:`identity_fixture_report` to count what the set contains.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "identity"

#: One file per class, in report order.
CLASSES: tuple[str, ...] = (
    "tautomers",
    "stereo",
    "isotopologues",
    "spin",
    "charge",
    "conformers",
)

EXPECTED_OUTCOMES: tuple[str, ...] = (
    "same_species_entry",
    "distinct_species_entries",
    "same_species_distinct_entries",
    "rejected",
)


class IdentityFixtureError(ValueError):
    """A fixture file is malformed; the set must not be trusted."""


@dataclass(frozen=True)
class IdentityInput:
    smiles: str
    charge: int
    multiplicity: int
    xyz_text: str | None = None
    isotopes: dict[int, int] | None = None


@dataclass(frozen=True)
class IdentityCase:
    id: str
    klass: str
    description: str
    inputs: tuple[IdentityInput, ...]
    expected: str
    reason: str
    rejection_match: str | None = None
    stereo_labels: tuple[str | None, ...] | None = None
    isotope_keys: tuple[str | None, ...] | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


def _parse_input(case_id: str, item: dict[str, Any]) -> IdentityInput:
    for key in ("smiles", "charge", "multiplicity"):
        if key not in item:
            raise IdentityFixtureError(f"{case_id}: input lacks {key!r}")
    geometry = item.get("geometry")
    xyz_text = None
    isotopes = None
    if geometry is not None:
        xyz_text = geometry.get("xyz_text")
        if not xyz_text:
            raise IdentityFixtureError(f"{case_id}: geometry lacks xyz_text")
        if geometry.get("isotopes"):
            isotopes = {int(k): int(v) for k, v in geometry["isotopes"].items()}
    return IdentityInput(
        smiles=str(item["smiles"]),
        charge=int(item["charge"]),
        multiplicity=int(item["multiplicity"]),
        xyz_text=xyz_text,
        isotopes=isotopes,
    )


def _parse_case(klass: str, item: dict[str, Any]) -> IdentityCase:
    case_id = item.get("id")
    if not case_id or not str(case_id).startswith(f"{klass}/"):
        raise IdentityFixtureError(f"{klass}: case id {case_id!r} must start with {klass + '/'!r}")
    expected = item.get("expected")
    if expected not in EXPECTED_OUTCOMES:
        raise IdentityFixtureError(f"{case_id}: expected {expected!r} not in {EXPECTED_OUTCOMES}")
    inputs = tuple(_parse_input(case_id, entry) for entry in item.get("inputs", []))
    rejection_match = None
    if expected == "rejected":
        if len(inputs) != 1:
            raise IdentityFixtureError(f"{case_id}: a rejected case has exactly one input")
        rejection_match = (item.get("rejection") or {}).get("match")
        if not rejection_match:
            raise IdentityFixtureError(f"{case_id}: a rejected case must state rejection.match")
    elif len(inputs) < 2:
        raise IdentityFixtureError(f"{case_id}: a relation needs at least two inputs")
    for positional in ("stereo_labels", "isotope_keys"):
        values = item.get(positional)
        if values is not None and len(values) != len(inputs):
            raise IdentityFixtureError(f"{case_id}: {positional} must align with inputs")
    if not item.get("reason"):
        raise IdentityFixtureError(f"{case_id}: every case states its reason")
    return IdentityCase(
        id=str(case_id),
        klass=klass,
        description=str(item.get("description", "")),
        inputs=inputs,
        expected=str(expected),
        reason=str(item["reason"]),
        rejection_match=rejection_match,
        stereo_labels=None if item.get("stereo_labels") is None else tuple(item["stereo_labels"]),
        isotope_keys=None if item.get("isotope_keys") is None else tuple(item["isotope_keys"]),
        raw=item,
    )


def load_identity_fixtures(fixture_dir: Path = FIXTURE_DIR) -> list[IdentityCase]:
    """Load every class file; refuse an empty file, a missing file or an empty union."""
    cases: list[IdentityCase] = []
    for klass in CLASSES:
        path = fixture_dir / f"{klass}.json"
        if not path.is_file():
            raise IdentityFixtureError(f"missing fixture file {path}")
        items = json.loads(path.read_text())
        if not isinstance(items, list) or not items:
            raise IdentityFixtureError(f"{path.name} holds no cases")
        cases.extend(_parse_case(klass, item) for item in items)
    if not cases:
        raise IdentityFixtureError("the identity fixture set is empty")
    ids = Counter(case.id for case in cases)
    duplicates = sorted(case_id for case_id, count in ids.items() if count > 1)
    if duplicates:
        raise IdentityFixtureError(f"duplicate case ids: {duplicates}")
    return cases


def identity_fixture_report(cases: list[IdentityCase] | None = None) -> dict[str, Any]:
    """Counts by class and expected outcome, for the paper generators.

    The report describes what the fixture set *contains*; whether the services
    reproduce each expectation is the test's verdict, not this function's.
    """
    if cases is None:
        cases = load_identity_fixtures()
    by_class: dict[str, dict[str, int]] = {}
    for klass in CLASSES:
        counts = Counter(case.expected for case in cases if case.klass == klass)
        by_class[klass] = {outcome: counts.get(outcome, 0) for outcome in EXPECTED_OUTCOMES}
        by_class[klass]["total"] = sum(counts.values())
    by_outcome = Counter(case.expected for case in cases)
    return {
        "schema": "tckdb.identity_fixture_report.v1",
        "classes": list(CLASSES),
        "outcomes": list(EXPECTED_OUTCOMES),
        "by_class": by_class,
        "by_outcome": {outcome: by_outcome.get(outcome, 0) for outcome in EXPECTED_OUTCOMES},
        "total_cases": len(cases),
        "total_inputs": sum(len(case.inputs) for case in cases),
        "cases_with_geometry": sum(1 for case in cases if any(i.xyz_text for i in case.inputs)),
    }
