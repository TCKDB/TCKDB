"""End-to-end upload tests against real ARC-generated payloads.

Each scenario directory under ``backend/tests/fixtures/arc_runs/`` carries
the minimal upstream artifacts from a real ARC run:

    arc_runs/<scenario>/
        output.yml          ARC's scientific summary (reference only)
        input.yml           ARC project config (reference only)
        tckdb_payloads/
            <kind>/<name>.payload.json   what we POST
            <kind>/<name>.meta.json      endpoint + recorded response

Tests POST every ``.payload.json`` to the endpoint recorded in its sibling
``.meta.json`` and hard-fail on any non-matching status code. The intent is
that genuine ARC data must always upsert cleanly: when the upload schema
evolves and breaks a fixture, the failure is the signal to regenerate
the fixtures from the original ARC run directories (kept outside the repo).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ARC_RUNS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arc_runs"


def _discover_payloads() -> list[tuple[str, Path, Path]]:
    cases: list[tuple[str, Path, Path]] = []
    if not ARC_RUNS_DIR.exists():
        return cases
    for scenario_dir in sorted(p for p in ARC_RUNS_DIR.iterdir() if p.is_dir()):
        payloads_root = scenario_dir / "tckdb_payloads"
        if not payloads_root.exists():
            continue
        for payload_file in sorted(payloads_root.rglob("*.payload.json")):
            meta_file = payload_file.with_name(
                payload_file.name.replace(".payload.json", ".meta.json")
            )
            case_id = f"{scenario_dir.name}/{payload_file.relative_to(payloads_root)}"
            cases.append((case_id, payload_file, meta_file))
    return cases


_PAYLOAD_CASES = _discover_payloads()


def _normalize_endpoint(endpoint: str) -> str:
    if endpoint.startswith("/api/"):
        return endpoint
    return f"/api/v1{endpoint}"


@pytest.mark.parametrize(
    "case_id,payload_file,meta_file",
    _PAYLOAD_CASES,
    ids=[c[0] for c in _PAYLOAD_CASES],
)
def test_arc_run_payload_uploads_cleanly(
    case_id: str,
    payload_file: Path,
    meta_file: Path,
    client,
) -> None:
    """Real ARC payloads must upsert cleanly against the current schema."""
    if not meta_file.exists():
        pytest.fail(f"{case_id}: missing companion .meta.json at {meta_file}")

    with meta_file.open() as fh:
        meta = json.load(fh)
    with payload_file.open() as fh:
        payload = json.load(fh)

    url = _normalize_endpoint(meta["endpoint"])
    expected_body = meta.get("response_body") or {}

    resp = client.post(url, json=payload)

    assert 200 <= resp.status_code < 300, (
        f"{case_id}: POST {url} returned {resp.status_code}, expected 2xx.\n"
        f"response: {resp.text[:2000]}"
    )

    body = resp.json()

    # IDs are DB-state-dependent and will differ from the recorded values;
    # assert the response shape and the load-bearing scalar counts instead.
    if "type" in expected_body:
        assert body.get("type") == expected_body["type"], (
            f"{case_id}: response type mismatch — "
            f"expected {expected_body['type']!r}, got {body.get('type')!r}"
        )
    if "species_count" in expected_body:
        assert body.get("species_count") == expected_body["species_count"], (
            f"{case_id}: species_count mismatch — "
            f"expected {expected_body['species_count']}, got {body.get('species_count')}"
        )
    for list_key in (
        "species_entry_ids",
        "kinetics_ids",
        "thermo_ids",
        "statmech_ids",
        "transport_ids",
    ):
        if list_key in expected_body:
            assert isinstance(body.get(list_key), list), (
                f"{case_id}: {list_key} missing or not a list in response"
            )
            assert len(body[list_key]) == len(expected_body[list_key]), (
                f"{case_id}: {list_key} length mismatch — "
                f"expected {len(expected_body[list_key])}, got {len(body.get(list_key, []))}"
            )


def test_arc_runs_aggregate_conformer_consolidation(client, db_session) -> None:
    """Upload every scenario; verify same species across runs consolidates.

    The load-bearing invariant is: independent ARC runs that compute the same
    chemical species (matched by InChI key) must share a single
    ``conformer_group``, with one or more ``conformer_observation`` rows
    accumulating across uploads. Methyl radical and methane appear in four
    scenarios each in the curated fixture set, so each must end up with at
    least four observations under a single group.
    """
    from sqlalchemy import func, select

    from app.db.models.species import (
        ConformerGroup,
        ConformerObservation,
        Species,
        SpeciesEntry,
    )

    assert _PAYLOAD_CASES, "no ARC run fixtures discovered"
    for case_id, payload_file, meta_file in _PAYLOAD_CASES:
        with meta_file.open() as fh:
            meta = json.load(fh)
        with payload_file.open() as fh:
            payload = json.load(fh)
        url = _normalize_endpoint(meta["endpoint"])
        resp = client.post(url, json=payload)
        assert 200 <= resp.status_code < 300, (
            f"{case_id}: {resp.status_code}\n{resp.text[:500]}"
        )

    # Standard InChI keys for the two species that overlap across four
    # scenarios in the curated fixture set. Cross-referenced in MANIFEST.yml.
    overlap_targets = {
        "methyl_radical": ("WCYWZMWISLQXQU-UHFFFAOYSA-N", 4),
        "methane":        ("VNWKTOKETHGBQD-UHFFFAOYSA-N", 4),
    }

    for label, (inchi_key, expected_scenario_count) in overlap_targets.items():
        species = db_session.scalar(
            select(Species).where(Species.inchi_key == inchi_key)
        )
        assert species is not None, (
            f"{label} ({inchi_key}) should exist after uploads"
        )
        entries = db_session.scalars(
            select(SpeciesEntry).where(SpeciesEntry.species_id == species.id)
        ).all()
        assert entries, f"{label}: Species has no SpeciesEntry"

        obs_count = db_session.scalar(
            select(func.count(ConformerObservation.id))
            .join(
                ConformerGroup,
                ConformerObservation.conformer_group_id == ConformerGroup.id,
            )
            .where(ConformerGroup.species_entry_id.in_([e.id for e in entries]))
        )
        assert obs_count >= expected_scenario_count, (
            f"{label}: expected >={expected_scenario_count} conformer_observations "
            f"(one per contributing scenario), got {obs_count}"
        )

        # Consolidation: all entries for this Species should share at most
        # one conformer_group each (uniqueness is enforced by
        # uq_conformer_group_species_entry_id, but we assert it explicitly
        # to make the invariant visible in the test).
        group_count = db_session.scalar(
            select(func.count(ConformerGroup.id))
            .where(ConformerGroup.species_entry_id.in_([e.id for e in entries]))
        )
        assert group_count == len(entries), (
            f"{label}: expected one conformer_group per species_entry "
            f"({len(entries)}), got {group_count}"
        )
