"""ADR 0020 conformance, measured over the real ARC fixture corpus.

Every other test in ``test_scan_coordinate_conformance.py`` builds its own
geometry from a NeRF placement. This file is the check the sign-bug review
that fixed :func:`app.services.scan_coordinate_conformance.dihedral_deg`
asked for directly: run the conformance check over the 36 real
``scan_result`` series already committed to this repository under
``backend/tests/fixtures/arc_runs/*/tckdb_payloads/**/*.payload.json``, and
assert the classification and implied constant the register's own reasoning
promises -- not a number chosen to make this pass.

These are the same fixtures ``tests/api/test_api_arc_run_fixtures.py``
exercises for upload-contract conformance; this file uploads the subset that
carries at least one ``scan_result`` and asks a different question of the
rows that land: does ``coordinate_value`` match the geometry it was
deposited beside.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType
from app.services.scan_coordinate_conformance import (
    CoordinateSeriesConformance,
    PointStatus,
    SeriesClassification,
    build_scan_coordinate_conformance_report,
)

ARC_RUNS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arc_runs"


def _count_scan_results(obj: object) -> int:
    """Recursively count non-empty ``scan_result`` keys anywhere in a payload."""
    count = 0
    if isinstance(obj, dict):
        if obj.get("scan_result"):
            count += 1
        for value in obj.values():
            count += _count_scan_results(value)
    elif isinstance(obj, list):
        for value in obj:
            count += _count_scan_results(value)
    return count


def _discover_scan_payloads() -> list[tuple[str, Path, Path]]:
    """Every ``.payload.json`` under ``arc_runs`` that carries a scan_result."""
    cases: list[tuple[str, Path, Path]] = []
    if not ARC_RUNS_DIR.exists():
        return cases
    for scenario_dir in sorted(p for p in ARC_RUNS_DIR.iterdir() if p.is_dir()):
        payloads_root = scenario_dir / "tckdb_payloads"
        if not payloads_root.exists():
            continue
        for payload_file in sorted(payloads_root.rglob("*.payload.json")):
            data = json.loads(payload_file.read_text())
            if _count_scan_results(data) == 0:
                continue
            meta_file = payload_file.with_name(
                payload_file.name.replace(".payload.json", ".meta.json")
            )
            case_id = f"{scenario_dir.name}/{payload_file.name}"
            cases.append((case_id, payload_file, meta_file))
    return cases


_SCAN_PAYLOAD_CASES = _discover_scan_payloads()


def _normalize_endpoint(endpoint: str) -> str:
    if endpoint.startswith("/api/"):
        return endpoint
    return f"/api/v1{endpoint}"


def test_the_fixture_corpus_carries_36_scan_series() -> None:
    """Guard the guard. If this drifts, the fixtures changed and every
    number this file asserts needs re-measuring, not trusting."""
    total = sum(
        _count_scan_results(json.loads(payload_file.read_text()))
        for _, payload_file, _ in _SCAN_PAYLOAD_CASES
    )
    assert total == 36, (
        f"expected 36 scan_result blocks across the arc_runs fixtures, found "
        f"{total}. The numbers this test asserts below were measured "
        "against 36 and need re-deriving against the new count."
    )


def test_adr_0020_conformance_over_the_real_arc_corpus(client) -> None:
    """The one test that matters most for the sign fix.

    Uploads all 36 real ARC scan series through the actual upload path
    (the same endpoints ``test_api_arc_run_fixtures.py`` posts to), then
    runs the ADR 0020 conformance check over every resulting scan
    calculation and asserts the classification tally.

    With the ``dihedral_deg`` sign bug in place, none of these 36 series
    classified ``consistent_with_legacy_relative_axis`` -- the check could
    not recognise ADR 0019's relative-sweep shape in real data at all,
    because the recomputed "expected" dihedral was the negative of the
    true one, so ``stored - expected`` was never close to a constant
    anywhere. With the sign fixed, 33 of the 36 series classified
    ``consistent_with_legacy_relative_axis`` outright; two more (the
    series whose own ``start_value`` is 180.0, landing exactly on the
    +/-180 branch cut) needed the circular-mean/spread fix in
    :func:`app.services.scan_coordinate_conformance.classify_series` to be
    recognised as the same pattern instead of reading as
    ``no_pattern_detected``.

    Measured result, asserted below rather than merely described: **35 of
    36** classify ``consistent_with_legacy_relative_axis``. The 36th has no
    stored geometry on any of its 46 points -- ``n_not_applicable == 46``,
    ``n_not_checkable == 0`` -- and reports ``insufficient_data``,
    correctly not a verdict either way and not a sign-bug symptom. Which
    fixture it is is not named here: ``public_ref`` is assigned per
    upload and is not stable across runs of this test, so
    ``non_legacy`` below is asserted to have exactly one entry rather than
    a specific identity pinned in prose that the next run would falsify.
    """
    assert _SCAN_PAYLOAD_CASES, "fixture discovery found nothing -- this test would pass vacuously"

    for case_id, payload_file, meta_file in _SCAN_PAYLOAD_CASES:
        assert meta_file.exists(), f"{case_id}: missing companion .meta.json"
        meta = json.loads(meta_file.read_text())
        payload = json.loads(payload_file.read_text())
        url = _normalize_endpoint(meta["endpoint"])
        response = client.post(url, json=payload)
        assert 200 <= response.status_code < 300, (
            f"{case_id}: POST {url} returned {response.status_code}, expected 2xx.\n"
            f"response: {response.text[:2000]}"
        )

    db_session = client._db_session
    scan_calculations = db_session.scalars(
        select(Calculation).where(Calculation.type == CalculationType.scan)
    ).all()
    assert scan_calculations, "no scan calculations were persisted by the uploads above"

    tally: Counter[str] = Counter()
    non_legacy: list[str] = []
    non_legacy_series: list[CoordinateSeriesConformance] = []
    n_series = 0
    for calc in scan_calculations:
        for series in build_scan_coordinate_conformance_report(db_session, calc.id):
            n_series += 1
            tally[series.classification.value] += 1
            if series.classification is not SeriesClassification.consistent_with_legacy_relative_axis:
                n_deviating_points = sum(
                    1 for p in series.points if p.status is PointStatus.deviates
                )
                non_legacy_series.append(series)
                non_legacy.append(
                    f"{calc.public_ref} coordinate_index={series.coordinate_index} "
                    f"kind={series.kind.value} -> {series.classification.value} "
                    f"(n_points={series.n_points}, deviating={n_deviating_points}, "
                    f"not_applicable={series.n_not_applicable}, "
                    f"not_checkable={series.n_not_checkable})"
                )

    assert n_series == 36, (
        f"expected 36 scan coordinate series from the uploaded corpus, got "
        f"{n_series}: {dict(tally)}"
    )

    assert tally["consistent_with_legacy_relative_axis"] == 35, (
        "expected 35 of 36 real ARC scan series to classify as "
        "consistent_with_legacy_relative_axis (33 directly, 2 more via the "
        "circular-mean fix for series whose start_value lands on the "
        "+/-180 branch cut); got "
        f"{tally['consistent_with_legacy_relative_axis']}. Tally: "
        f"{dict(tally)}\nNon-legacy series:\n" + "\n".join(non_legacy)
    )

    # The one series that is not consistent_with_legacy_relative_axis is
    # pinned to its exact, measured shape -- not just counted -- so a
    # regression that turns it (or any other series) into a *different*
    # non-legacy classification (no_pattern_detected, in particular, which
    # is what a sign error produces) fails loudly here instead of hiding
    # behind an unchanged "35 of 36" total.
    assert len(non_legacy) == 1, non_legacy
    the_one_exception = non_legacy_series[0]
    assert the_one_exception.classification is SeriesClassification.insufficient_data, non_legacy
    assert the_one_exception.n_not_applicable == the_one_exception.n_points
    assert the_one_exception.n_not_checkable == 0
    assert all(p.status is PointStatus.not_applicable for p in the_one_exception.points)
