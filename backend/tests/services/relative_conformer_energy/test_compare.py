"""The descriptive benchmark comparison of two stored protocols: statistics, pairing, claims, boundary, replay.

The pure tests build the two answers (``RelativeEnergyResult.to_dict()`` shape) directly, so each pins one rule of the
comparison and nothing else. The database tests run the whole service over two levels of one entry.
"""

from __future__ import annotations

import ast
import copy
import math
from pathlib import Path

import pytest

from app.api.error_contract import CodedValueError
from app.db.models.common import StructureDeterminationTargetKind
from app.services.relative_conformer_energy import (
    Certificate,
    EnergyKind,
    LevelSelector,
    RelativeEnergyRequest,
    replay_comparison,
    replay_comparison_matches,
    run_comparison,
)
from app.services.relative_conformer_energy import compare as compare_module
from app.services.relative_conformer_energy.compare import (
    CODE_IDENTICAL_PROTOCOLS,
    EXCLUDED_REASONS,
    MISSING_EVIDENCE,
    ComparisonRequest,
    Pairing,
    compare_results,
)
from app.services.structure_selection.manifest import ReplayError
from app.services.structure_selection.models import CoverageRequirement
from tests.services.relative_conformer_energy.test_service import conformer
from tests.services.scientific_read._factories import make_geometry, make_lot
from tests.services.structure_selection._support import make_world
from tests.services.structure_selection.test_service import build_basin

ANCHOR = "cg_anchor"
KNOWN = CoverageRequirement.known_values
ALL = CoverageRequirement.all_requested_members


def side(anchor: str, members: dict[str, tuple[float | None, tuple[str, ...]]], *, anchor_value: float | None = -76.4):
    """One protocol's answer: ``{ref: (relative kJ/mol or None, evaluated geometry refs)}`` plus the named reference."""
    rows = [
        {
            "conformer_ref": anchor,
            "relative_energy_kj_mol": 0.0 if anchor_value is not None else None,
            "value_uncertainty_hartree": None,
            "evaluated_geometry_refs": ["geom_a"] if anchor_value is not None else [],
        }
    ]
    rows += [
        {
            "conformer_ref": ref,
            "relative_energy_kj_mol": kj,
            "value_uncertainty_hartree": 0.001 if kj is not None else None,
            "evaluated_geometry_refs": list(geoms),
        }
        for ref, (kj, geoms) in members.items()
    ]
    return {
        "outcome": "relative_energies",
        "level": {
            "level_of_theory_ref": "lot_x",
            "composite_scheme_ref": None,
            "recipe_cohort_id": None,
            "recipe_facts": [],
        },
        "reference": {"conformer_ref": anchor, "value_hartree": anchor_value},
        "coverage": {},
        "unresolved": [],
        "members": rows,
    }


G = ("geom_a",)


def run(cand, ref, *, pairing=Pairing.same_basin, coverage=KNOWN, anchor=ANCHOR):
    return compare_results(cand, ref, pairing=pairing, coverage_requirement=coverage, reference_conformer_ref=anchor)


def three(cand_vals, ref_vals):
    return (
        side(ANCHOR, {f"cg_{k}": (v, G) for k, v in cand_vals.items()}),
        side(ANCHOR, {f"cg_{k}": (v, G) for k, v in ref_vals.items()}),
    )


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def test_synthetic_deviations_give_exact_statistics_and_the_member_of_the_maximum():
    cand, ref = three({"b": 3.0, "c": -1.0, "d": 4.0}, {"b": 1.0, "c": 1.0, "d": 1.0})  # deviations 2, -2, 3
    out = run(cand, ref)
    stats = out["statistics"]
    # The anchor's own zero is not a pair: n is 3, not 4, and the means are over three deviations.
    assert out["outcome"] == "compared" and stats["n"] == 3
    assert stats["msd_kj_mol"] == pytest.approx(1.0)
    assert stats["mad_kj_mol"] == pytest.approx(7 / 3)
    assert stats["rmsd_kj_mol"] == pytest.approx(math.sqrt(17 / 3))
    assert stats["max_abs_deviation_kj_mol"] == 3.0 and stats["max_abs_deviation_conformer_ref"] == "cg_d"
    assert [p["conformer_ref"] for p in out["pairs"]] == ["cg_b", "cg_c", "cg_d"]
    assert ANCHOR not in {p["conformer_ref"] for p in out["pairs"]}
    assert stats["reference_conformer_ref"] == ANCHOR and stats["sign_convention"] == "candidate minus reference"
    assert "another reference conformer" in stats["reference_conformer_dependence"]


def test_the_deviation_is_candidate_minus_reference_and_keeps_its_sign():
    cand, ref = three({"b": 5.0}, {"b": 2.0})
    pair = run(cand, ref)["pairs"][0]
    assert pair["deviation_kj_mol"] == pytest.approx(3.0)
    assert pair["candidate_kj_mol"] == 5.0 and pair["reference_kj_mol"] == 2.0
    swapped = run(ref, cand)["pairs"][0]
    assert swapped["deviation_kj_mol"] == pytest.approx(-3.0)


def test_a_pair_absent_on_one_side_is_excluded_with_a_reason_and_is_not_a_zero():
    cand, ref = three({"b": 3.0, "c": None, "d": 4.0}, {"b": 1.0, "c": 1.0, "d": None})
    out = run(cand, ref)
    by = {p["conformer_ref"]: p for p in out["pairs"]}
    assert by["cg_c"]["excluded_reason"] == "candidate_value_missing" and by["cg_c"]["deviation_kj_mol"] is None
    assert (
        by["cg_d"]["excluded_reason"] == "reference_protocol_value_missing" and by["cg_d"]["deviation_kj_mol"] is None
    )
    assert out["statistics"]["n"] == 1 and out["statistics"]["msd_kj_mol"] == pytest.approx(2.0)
    assert {p["excluded_reason"] for p in out["pairs"]} <= EXCLUDED_REASONS | {None}


def test_a_member_missing_from_both_sides_is_excluded_for_neither():
    cand, ref = three({"b": None}, {"b": None})
    assert run(cand, ref)["pairs"][0]["excluded_reason"] == "neither_protocol_has_value"


def test_a_member_listed_by_only_one_answer_is_excluded_not_dropped():
    cand = side(ANCHOR, {"cg_b": (3.0, G), "cg_only": (1.0, G)})
    ref = side(ANCHOR, {"cg_b": (1.0, G)})
    by = {p["conformer_ref"]: p for p in run(cand, ref)["pairs"]}
    assert by["cg_only"]["excluded_reason"] == "reference_protocol_value_missing"


def test_same_geometry_excludes_a_pair_evaluated_at_different_geometries_and_same_basin_keeps_it():
    cand = side(ANCHOR, {"cg_b": (3.0, ("geom_a",)), "cg_c": (3.0, ("geom_a",)), "cg_d": (3.0, ())})
    ref = side(ANCHOR, {"cg_b": (1.0, ("geom_a",)), "cg_c": (1.0, ("geom_z",)), "cg_d": (1.0, ("geom_a",))})
    strict = {p["conformer_ref"]: p for p in run(cand, ref, pairing=Pairing.same_geometry)["pairs"]}
    assert strict["cg_b"]["excluded_reason"] is None
    assert strict["cg_c"]["excluded_reason"] == "geometry_differs" and strict["cg_c"]["deviation_kj_mol"] is None
    assert strict["cg_d"]["excluded_reason"] == "geometry_not_recorded"
    assert run(cand, ref, pairing=Pairing.same_geometry)["statistics"]["n"] == 1
    loose = run(cand, ref, pairing=Pairing.same_basin)
    assert loose["statistics"]["n"] == 3 and all(p["excluded_reason"] is None for p in loose["pairs"])


def test_very_different_mads_still_license_no_superiority_and_name_all_seven_missing_prerequisites():
    near, far = three({"b": 0.1, "c": 0.2}, {"b": 0.0, "c": 0.0}), three({"b": 50.0, "c": 60.0}, {"b": 0.0, "c": 0.0})
    for cand, ref in (near, far):
        out = run(cand, ref)
        claims = out["claims"]
        assert claims["kind"] == "descriptive"
        assert claims["superiority"] == "not_licensed" and claims["ranking"] == "not_licensed"
        assert claims["missing_evidence"] == ["E1", "E2", "E3", "E4", "E5", "E6", "E7"] == list(MISSING_EVIDENCE)
        assert "does not show that either protocol is more accurate" in claims["statement"]
    assert run(*far)["statistics"]["mad_kj_mol"] > 100 * run(*near)["statistics"]["mad_kj_mol"]


def test_a_complete_answer_with_one_exclusion_is_incomplete_pairing_with_no_statistics():
    cand, ref = three({"b": 3.0, "c": None}, {"b": 1.0, "c": 1.0})
    subset = run(cand, ref, coverage=KNOWN)
    assert subset["outcome"] == "compared" and subset["statistics"]["n"] == 1
    complete = run(cand, ref, coverage=ALL)
    assert complete["outcome"] == "incomplete_pairing" and complete["statistics"] is None
    assert [p["excluded_reason"] for p in complete["pairs"]] == [None, "candidate_value_missing"]


def test_a_missing_reference_conformer_value_makes_a_complete_answer_incomplete():
    cand = side(ANCHOR, {"cg_b": (None, ())}, anchor_value=None)
    ref = side(ANCHOR, {"cg_b": (None, ())}, anchor_value=None)
    assert run(cand, ref, coverage=ALL)["outcome"] == "incomplete_pairing"


def test_no_pair_means_every_statistic_is_null_not_zero():
    out = run(side(ANCHOR, {}), side(ANCHOR, {}))
    assert out["outcome"] == "no_pairs" and out["pairs"] == []
    stats = out["statistics"]
    assert stats["n"] == 0
    figures = [k for k in stats if k.endswith("_kj_mol") or k == "max_abs_deviation_conformer_ref"]
    assert figures and all(stats[k] is None for k in figures)
    cand, ref = three({"b": None}, {"b": 1.0})
    assert run(cand, ref)["statistics"]["msd_kj_mol"] is None


def test_two_stored_uncertainties_are_echoed_and_never_combined():
    cand, ref = three({"b": 3.0}, {"b": 1.0})
    pair = run(cand, ref)["pairs"][0]
    assert pair["candidate_uncertainty_hartree"] == 0.001 and pair["reference_uncertainty_hartree"] == 0.001
    assert not any("uncertainty" in k and k.startswith(("deviation", "combined")) for k in pair)


# ---------------------------------------------------------------------------
# The boundary: no selection kernel, no rules
# ---------------------------------------------------------------------------

_FORBIDDEN = ("app.services.structure_selection.rules", "selection_kernel")


def _imported_modules(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            out.add(base)
            out |= {f"{base}.{a.name}" for a in node.names}
    return out


def test_the_comparison_module_imports_nothing_from_the_rules_or_the_selection_kernel():
    imported = _imported_modules(Path(compare_module.__file__))
    assert imported, "the scan read no import, so it proves nothing"
    assert not [m for m in imported if any(bad in m for bad in _FORBIDDEN)]
    assert "app.services.structure_selection.models" in imported  # the scan does see this module's real imports


def test_the_boundary_scan_catches_a_forbidden_import(tmp_path):
    bad = tmp_path / "m.py"
    bad.write_text(
        "from app.services.structure_selection import rules\nfrom app.services.selection_kernel import Edge\n"
    )
    found = [m for m in _imported_modules(bad) if any(b in m for b in _FORBIDDEN)]
    assert found  # the scan is not vacuous: it flags the import a wired rule edge would need


# ---------------------------------------------------------------------------
# Against real rows
# ---------------------------------------------------------------------------


def request_for(world, reference, candidate_lot, reference_lot, **changes):
    base = {
        "reference_conformer_ref": reference.public_ref,
        "energy_kind": EnergyKind.electronic_energy,
        "certificate": Certificate.local_minimum_any_surface,
    }
    base.update(changes)
    pairing = base.pop("pairing", Pairing.same_basin)
    return ComparisonRequest(
        candidate=RelativeEnergyRequest(level=LevelSelector(level_of_theory_ref=candidate_lot.public_ref), **base),
        reference=RelativeEnergyRequest(level=LevelSelector(level_of_theory_ref=reference_lot.public_ref), **base),
        pairing=pairing,
    )


def two_level_world(db_session, *, other_geometry: bool = False):
    """Three conformers, each with a value at the world's level (the reference protocol) and one at another level.

    Reference protocol (world level): a -76.400, b -76.401, c -76.398. Candidate (b3lyp): a -76.500, b -76.503, c -76.499.
    """
    world = make_world(db_session)
    cand_lot = make_lot(db_session, method="b3lyp")
    groups = []
    for ref_energy, cand_energy in ((-76.400, -76.500), (-76.401, -76.503), (-76.398, -76.499)):
        group, obs = conformer(world, ref_energy)
        geometry = make_geometry(db_session) if other_geometry else None
        build_basin(
            world,
            energy=cand_energy,
            observation=obs,
            lot=cand_lot,
            geometry=geometry,
            target=StructureDeterminationTargetKind.conformer_basin,
        )
        groups.append(group)
    world.settle()
    return world, cand_lot, groups


def run_service(db_session, world, request):
    return run_comparison(db_session, entry_id=world.entry.id, request=request, require_snapshot=False)


def test_the_service_compares_two_levels_over_one_population(db_session):
    world, cand_lot, (a, b, c) = two_level_world(db_session)
    run_ = run_service(db_session, world, request_for(world, a, cand_lot, world.lot))
    stats = run_.comparison["statistics"]
    # b: candidate -0.003 vs reference -0.001; c: candidate +0.001 vs reference +0.002 (both relative to a, in hartree).
    dev_b = (-0.003 - -0.001) * 2625.4996394798254
    dev_c = (0.001 - 0.002) * 2625.4996394798254
    assert stats["n"] == 2 and stats["msd_kj_mol"] == pytest.approx((dev_b + dev_c) / 2)
    assert run_.comparison["claims"]["superiority"] == "not_licensed"
    assert set(run_.runs) == {"candidate", "reference"}
    assert (
        run_.runs["candidate"].manifest["population"]["groups"]
        == run_.runs["reference"].manifest["population"]["groups"]
    )


def test_the_same_data_against_two_anchors_gives_different_statistics_and_each_echoes_its_anchor(db_session):
    world, cand_lot, (a, b, c) = two_level_world(db_session)
    from_a = run_service(db_session, world, request_for(world, a, cand_lot, world.lot))
    from_b = run_service(db_session, world, request_for(world, b, cand_lot, world.lot))
    assert from_a.comparison["statistics"]["reference_conformer_ref"] == a.public_ref
    assert from_b.comparison["statistics"]["reference_conformer_ref"] == b.public_ref
    assert from_a.comparison["statistics"]["msd_kj_mol"] != pytest.approx(from_b.comparison["statistics"]["msd_kj_mol"])
    assert from_a.manifest["request"]["shared"]["reference_conformer_ref"] == a.public_ref
    assert {p["conformer_ref"] for p in from_b.comparison["pairs"]} == {a.public_ref, c.public_ref}


def test_same_geometry_excludes_conformers_whose_two_values_sit_at_different_geometries(db_session):
    world, cand_lot, (a, b, c) = two_level_world(db_session, other_geometry=True)
    strict = run_service(db_session, world, request_for(world, a, cand_lot, world.lot, pairing=Pairing.same_geometry))
    assert {p["excluded_reason"] for p in strict.comparison["pairs"]} == {"geometry_differs"}
    assert strict.comparison["statistics"]["n"] == 0 and strict.comparison["outcome"] == "no_pairs"
    loose = run_service(db_session, world, request_for(world, a, cand_lot, world.lot, pairing=Pairing.same_basin))
    assert loose.comparison["statistics"]["n"] == 2


def test_a_complete_answer_over_a_conformer_with_no_value_at_a_level_is_incomplete(db_session):
    world, cand_lot, (a, b, c) = two_level_world(db_session)
    conformer(world, None)
    world.settle()
    known = run_service(db_session, world, request_for(world, a, cand_lot, world.lot))
    assert known.comparison["outcome"] == "compared" and known.comparison["statistics"]["n"] == 2
    complete = run_service(db_session, world, request_for(world, a, cand_lot, world.lot, coverage=ALL))
    assert complete.comparison["outcome"] == "incomplete_pairing" and complete.comparison["statistics"] is None


def test_a_protocol_is_not_compared_with_itself():
    lot = LevelSelector(level_of_theory_ref="lot_x")
    shared = {
        "reference_conformer_ref": "cg_a",
        "energy_kind": EnergyKind.electronic_energy,
        "certificate": Certificate.local_minimum_any_surface,
    }
    one = RelativeEnergyRequest(level=lot, **shared)
    with pytest.raises(CodedValueError) as raised:
        ComparisonRequest(
            candidate=one, reference=RelativeEnergyRequest(level=lot, **shared), pairing=Pairing.same_basin
        )
    assert raised.value.code == CODE_IDENTICAL_PROTOCOLS
    other_recipe = RelativeEnergyRequest(level=lot, recipe={"x": 1}, **shared)
    ComparisonRequest(candidate=one, reference=other_recipe, pairing=Pairing.same_basin)  # a recipe makes it a protocol


def test_requests_that_differ_in_more_than_their_protocol_are_refused():
    shared = {"energy_kind": EnergyKind.electronic_energy, "certificate": Certificate.local_minimum_any_surface}
    a = RelativeEnergyRequest(
        reference_conformer_ref="cg_a", level=LevelSelector(level_of_theory_ref="lot_x"), **shared
    )
    b = RelativeEnergyRequest(
        reference_conformer_ref="cg_b", level=LevelSelector(level_of_theory_ref="lot_y"), **shared
    )
    with pytest.raises(ValueError):
        ComparisonRequest(candidate=a, reference=b, pairing=Pairing.same_basin)


# ---------------------------------------------------------------------------
# Manifest and replay
# ---------------------------------------------------------------------------


def test_the_comparison_manifest_replays_and_holds_both_runs_and_the_pairing(db_session):
    world, cand_lot, (a, *_) = two_level_world(db_session)
    manifest = run_service(db_session, world, request_for(world, a, cand_lot, world.lot)).manifest
    assert manifest["manifest_format"] == "relative_conformer_energy_comparison_manifest"
    assert manifest["request"]["pairing"] == "same_basin"
    assert manifest["runs"]["candidate"]["request"]["level"]["level_of_theory_ref"] == cand_lot.public_ref
    assert manifest["runs"]["reference"]["request"]["level"]["level_of_theory_ref"] == world.lot.public_ref
    assert manifest["integrity"]["algorithm"] == "sha256" and "checksum" in manifest["integrity"]["note"].lower()
    assert replay_comparison_matches(manifest)
    assert replay_comparison(manifest)["statistics"] == manifest["result"]["statistics"]


def _resealed(manifest):
    from app.services.structure_selection.manifest import digest

    body = {k: v for k, v in manifest.items() if k != "integrity"}
    return {**body, "integrity": {**manifest["integrity"], "manifest_sha256": digest(body)}}


def test_replay_recomputes_and_never_trusts_a_recorded_flag(db_session):
    world, cand_lot, (a, *_) = two_level_world(db_session)
    manifest = run_service(db_session, world, request_for(world, a, cand_lot, world.lot)).manifest
    plain = copy.deepcopy(manifest)
    plain["result"]["statistics"]["mad_kj_mol"] = 0.0  # edited, checksum now wrong
    with pytest.raises(ReplayError) as raised:
        replay_comparison(plain)
    assert raised.value.code == "manifest_checksum_mismatch"
    # Recomputing the checksum (anyone can) does not help: the recorded figure is not what the replayed answers give.
    with pytest.raises(ReplayError) as raised:
        replay_comparison(_resealed(plain))
    assert raised.value.code == "result_mismatch"
    flagged = copy.deepcopy(manifest)
    flagged["result"]["claims"]["superiority"] = "licensed"
    with pytest.raises(ReplayError) as raised:
        replay_comparison(_resealed(flagged))
    assert raised.value.code == "result_mismatch"
    swapped = copy.deepcopy(manifest)
    swapped["runs"]["candidate"], swapped["runs"]["reference"] = (
        swapped["runs"]["reference"],
        swapped["runs"]["candidate"],
    )
    with pytest.raises(ReplayError) as raised:
        replay_comparison(_resealed(swapped))
    assert raised.value.code == "inconsistent_manifest"
