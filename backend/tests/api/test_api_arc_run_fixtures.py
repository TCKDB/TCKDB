"""End-to-end upload tests against real ARC-generated payloads.

Each scenario directory under ``backend/tests/fixtures/arc_runs/`` carries
the minimal upstream artifacts from a real ARC run:

    arc_runs/<scenario>/
        output.yml          ARC's scientific summary (reference only)
        input.yml           ARC project config (reference only)
        tckdb_payloads/
            <kind>/<name>.payload.json   what we POST
            <kind>/<name>.meta.json      endpoint + recorded response
        tckdb_payloads_trimmed_264/      see NOTE.md inside; task #264 only

Tests POST every ``.payload.json`` under ``tckdb_payloads/`` to the endpoint
recorded in its sibling ``.meta.json``. The intent is that genuine ARC data
must always upsert cleanly *or* be refused for a documented, deliberate
reason: when the upload schema evolves and a fixture starts failing for any
other reason, that failure is the signal to regenerate the fixtures from the
original ARC run directories (kept outside the repo).

Five payloads are a documented exception (task #264): they carry a
``transition_state``-side ``bac_total`` of value ``0.0`` with no
components, which is exactly the false shape
``backend/alembic/versions/a55cc983501a_repair_false_zero_ts_bac_totals.py``
and ``assert_bac_total_has_required_components`` exist to refuse. The
payloads are kept byte-for-byte as ARC produced them -- they are the only
in-repo evidence of what a real producer actually emits, defect included --
and this file asserts the refusal directly against them rather than
silently dropping or editing them. ``tckdb_payloads_trimmed_264/`` carries
a derived copy of each (everything unchanged except that one entry) for the
one test below whose subject has nothing to do with this defect and needs
the upload to succeed; see the ``NOTE.md`` beside each trimmed copy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ARC_RUNS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arc_runs"

#: Task #264. Real ARC payloads that carry a componentless, zero-valued
#: transition-state ``bac_total`` and are therefore refused on purpose,
#: mapped to the ``bac_total.applied_energy_corrections`` index the
#: refusal names. Keyed on the same ``case_id`` shape
#: ``_discover_payloads`` produces, so a payload dropped from the fixture
#: set (regenerated fixtures, say) surfaces as an unknown-key ``KeyError``
#: here rather than this dict silently going stale.
_KNOWN_REFUSED_TS_BAC_TOTAL: dict[str, int] = {
    "neb_1/computed_reaction/i-C3H7-n-C3H7.payload.json": 1,
    "reaction_1/computed_reaction/CHO-CH4-CH2O-CH3.payload.json": 1,
    "rotor_scan_2/computed_reaction/rxn_209_C-rxn_209_CO-O-rxn_209_-CH3-rxn_209_COO.payload.json": 1,
    "rotor_scan_3/computed_reaction/rxn_210_CC-rxn_210_-H-rxn_210_C-CH2-rxn_210_-H-H.payload.json": 1,
    "rotor_scan_6/computed_reaction/rxn_392_CO-rxn_392_-CH-C-rxn_392_C-O-rxn_392_C-C.payload.json": 1,
}


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


def _trimmed_payload_path(case_id: str, payload_file: Path) -> Path:
    """The task-264 trimmed copy for a known-refused case, or the original.

    ``tckdb_payloads_trimmed_264/`` sits beside ``tckdb_payloads/``, not
    nested under it, specifically so ``_discover_payloads``'s ``rglob``
    never picks trimmed copies up as their own parametrized cases.
    """
    if case_id not in _KNOWN_REFUSED_TS_BAC_TOTAL:
        return payload_file
    tckdb_payloads_dir = payload_file.parents[1]
    assert tckdb_payloads_dir.name == "tckdb_payloads", tckdb_payloads_dir
    trimmed = (
        tckdb_payloads_dir.parent
        / "tckdb_payloads_trimmed_264"
        / payload_file.relative_to(tckdb_payloads_dir)
    )
    assert trimmed.exists(), f"missing trimmed fixture: {trimmed}"
    return trimmed


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
    """Real ARC payloads must upsert cleanly, or be refused on purpose.

    Every case is one of two claims: it upserts against the current
    schema (the general rule), or it is one of the five documented task
    #264 exceptions and is refused with exactly the code the false shape
    it carries earns. Nothing here is expected to fail for any other
    reason.
    """
    if not meta_file.exists():
        pytest.fail(f"{case_id}: missing companion .meta.json at {meta_file}")

    with meta_file.open() as fh:
        meta = json.load(fh)
    with payload_file.open() as fh:
        payload = json.load(fh)

    url = _normalize_endpoint(meta["endpoint"])

    resp = client.post(url, json=payload)

    if case_id in _KNOWN_REFUSED_TS_BAC_TOTAL:
        aec_index = _KNOWN_REFUSED_TS_BAC_TOTAL[case_id]
        assert resp.status_code == 422, (
            f"{case_id}: POST {url} returned {resp.status_code}, expected 422 "
            f"(task #264: this payload's transition_state carries a "
            f"componentless, zero-valued bac_total, which is now refused).\n"
            f"response: {resp.text[:2000]}"
        )
        body = resp.json()
        assert body.get("code") == "bac_total_requires_components", body
        assert body.get("context", {}).get("target_kind") == "transition_state_entry", body
        assert (
            f"transition_state.applied_energy_corrections[{aec_index}]"
            in body.get("context", {}).get("field", "")
        ), body
        return

    expected_body = meta.get("response_body") or {}

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

    Consolidation is not what task #264's five documented refusals are
    about, so this test uploads the trimmed copy for those five cases
    (see ``_trimmed_payload_path`` and each's ``NOTE.md``) and the
    original, untouched payload for everything else -- every upload here
    must still succeed.
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
        with _trimmed_payload_path(case_id, payload_file).open() as fh:
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
