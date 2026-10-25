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
    narrowing_of,
)
from app.services.relative_conformer_energy.manifest import build_comparison_manifest
from app.services.structure_selection.manifest import ReplayError, canonical
from app.services.structure_selection.models import CoverageRequirement, RepeatPolicy, SelectionBounds
from tests.services.relative_conformer_energy.test_service import conformer
from tests.services.scientific_read._factories import make_geometry, make_lot
from tests.services.structure_selection._support import make_world
from tests.services.structure_selection.test_service import build_basin

ANCHOR = "cg_anchor"
KNOWN = CoverageRequirement.known_values
ALL = CoverageRequirement.all_requested_members


def side(
    anchor: str,
    members: dict[str, tuple[float | None, str | None]],
    *,
    anchor_value: float | None = -76.4,
    anchor_geometry: str | None = "geom_a",
):
    """One protocol's answer: ``{ref: (relative kJ/mol or None, geometry of the value or None)}`` plus the reference.

    A value is present exactly when its relative energy is; its geometry is the one it was evaluated at.
    """
    has_anchor = anchor_value is not None
    rows = [
        {
            "conformer_ref": anchor,
            "relative_energy_kj_mol": 0.0 if has_anchor else None,
            "value_hartree": anchor_value,
            "value_uncertainty_hartree": None,
            "value_geometry_ref": anchor_geometry if has_anchor else None,
        }
    ]
    rows += [
        {
            "conformer_ref": ref,
            "relative_energy_kj_mol": kj,
            "value_hartree": -76.0 if kj is not None else None,
            "value_uncertainty_hartree": 0.001 if kj is not None else None,
            "value_geometry_ref": geometry if kj is not None else None,
        }
        for ref, (kj, geometry) in members.items()
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


G = "geom_a"


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


def test_a_tie_for_the_largest_deviation_goes_to_the_lowest_conformer_ref():
    # |+2| ties |-2|; the members are listed highest-ref first to show the answer does not follow list order.
    cand, ref = three({"d": 3.0, "c": -1.0, "b": 3.0}, {"d": 1.0, "c": 1.0, "b": 1.0})  # d +2, c -2, b +2
    stats = run(cand, ref)["statistics"]
    assert stats["max_abs_deviation_kj_mol"] == 2.0 and stats["max_abs_deviation_conformer_ref"] == "cg_b"


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
    cand = side(ANCHOR, {"cg_b": (3.0, "geom_a"), "cg_c": (3.0, "geom_a"), "cg_d": (3.0, None)})
    ref = side(ANCHOR, {"cg_b": (1.0, "geom_a"), "cg_c": (1.0, "geom_z"), "cg_d": (1.0, "geom_a")})
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


def with_anchor_missing(members, **kw):
    """An answer whose reference conformer has no value, so the engine gives every member a value but no difference."""
    answer = side(ANCHOR, dict.fromkeys(members, (1.0, G)), anchor_value=None, **kw)
    for row in answer["members"]:
        if row["conformer_ref"] != ANCHOR:
            row["relative_energy_kj_mol"] = None  # the member has its value; only the anchor is missing
    return answer


def test_a_reference_conformer_missing_on_one_side_is_named_once_and_blames_no_member():
    cand = with_anchor_missing(["cg_b", "cg_c"])
    ref = side(ANCHOR, {"cg_b": (1.0, G), "cg_c": (2.0, G)})
    known = run(cand, ref, coverage=KNOWN)
    assert known["reference_conformer"]["problem"] == "reference_conformer_value_missing"
    assert known["reference_conformer"]["candidate_value_present"] is False
    assert known["reference_conformer"]["reference_value_present"] is True
    # Both members have a value on both sides: what is missing is the anchor, and the pairs say exactly that.
    assert {p["excluded_reason"] for p in known["pairs"]} == {"reference_conformer_value_missing"}
    assert (
        known["outcome"] == "no_pairs" and known["statistics"]["n"] == 0 and known["statistics"]["msd_kj_mol"] is None
    )
    # A member that itself lacks a value keeps its own reason.
    cand_missing = with_anchor_missing(["cg_b"])
    cand_missing["members"].append(side(ANCHOR, {"cg_c": (None, None)})["members"][1])
    mixed = {p["conformer_ref"]: p["excluded_reason"] for p in run(cand_missing, ref)["pairs"]}
    assert mixed == {"cg_b": "reference_conformer_value_missing", "cg_c": "candidate_value_missing"}


def test_a_complete_answer_is_incomplete_when_the_reference_conformer_is_missing_even_with_no_other_member():
    # The only member is the reference conformer, so no pair is excluded: the anchor clause alone makes this incomplete.
    cand = side(ANCHOR, {}, anchor_value=None)
    ref = side(ANCHOR, {})
    out = run(cand, ref, coverage=ALL)
    assert out["pairs"] == [] and out["reference_conformer"]["problem"] == "reference_conformer_value_missing"
    assert out["outcome"] == "incomplete_pairing" and out["statistics"] is None
    assert run(cand, ref, coverage=KNOWN)["outcome"] == "no_pairs"
    # With members, a complete answer is incomplete as well, and its statistics are null.
    members = with_anchor_missing(["cg_b"])
    full = run(members, side(ANCHOR, {"cg_b": (1.0, G)}), coverage=ALL)
    assert full["outcome"] == "incomplete_pairing" and full["statistics"] is None


# --- 1: the reference conformer's geometry -------------------------------------------------------------------------


def test_same_geometry_needs_the_reference_conformer_at_one_geometry_on_both_sides():
    # The member sits at geom_B on both sides, but the anchor it is measured from is at geom_A1 against geom_A2.
    cand = side(ANCHOR, {"cg_b": (3.0, "geom_B")}, anchor_geometry="geom_A1")
    ref = side(ANCHOR, {"cg_b": (1.0, "geom_B")}, anchor_geometry="geom_A2")
    strict = run(cand, ref, pairing=Pairing.same_geometry)
    assert strict["reference_conformer"]["problem"] == "reference_conformer_geometry_differs"
    assert strict["reference_conformer"]["candidate_geometry_ref"] == "geom_A1"
    assert strict["reference_conformer"]["reference_geometry_ref"] == "geom_A2"
    assert [p["excluded_reason"] for p in strict["pairs"]] == ["reference_conformer_geometry_differs"]
    assert (
        strict["outcome"] == "no_pairs"
        and strict["statistics"]["n"] == 0
        and strict["statistics"]["mad_kj_mol"] is None
    )
    assert run(cand, ref, pairing=Pairing.same_geometry, coverage=ALL)["outcome"] == "incomplete_pairing"
    # The same data under same_basin compares: the anchor's geometry matters only when the pairing says so.
    loose = run(cand, ref, pairing=Pairing.same_basin)
    assert loose["reference_conformer"]["problem"] is None and loose["statistics"]["n"] == 1


def test_same_geometry_with_an_unrecorded_reference_conformer_geometry_has_no_usable_pair():
    cand = side(ANCHOR, {"cg_b": (3.0, "geom_B")}, anchor_geometry=None)
    ref = side(ANCHOR, {"cg_b": (1.0, "geom_B")})
    out = run(cand, ref, pairing=Pairing.same_geometry)
    assert out["reference_conformer"]["problem"] == "reference_conformer_geometry_not_recorded"
    assert [p["excluded_reason"] for p in out["pairs"]] == ["reference_conformer_geometry_not_recorded"]
    assert out["statistics"]["n"] == 0


def test_a_value_tied_to_a_different_geometry_than_the_other_sides_is_excluded_even_when_every_ref_is_shared():
    cand = side(ANCHOR, {"cg_b": (3.0, "geom_B1")})
    ref = side(ANCHOR, {"cg_b": (1.0, "geom_B2")})
    pair = run(cand, ref, pairing=Pairing.same_geometry)["pairs"][0]
    assert pair["excluded_reason"] == "geometry_differs"
    # The echo is the geometry each value came from, one each.
    assert (pair["candidate_geometry_ref"], pair["reference_geometry_ref"]) == ("geom_B1", "geom_B2")


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
# The boundary: no selection kernel, no decision
# ---------------------------------------------------------------------------

_FORBIDDEN = ("selection_kernel",)
_FORBIDDEN_NAMES = {"Edge", "decide_alternatives", "decide_structures", "decide_graph"}


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


def _comparison_path() -> dict[str, Path]:
    """The modules the comparison runs through, by file."""
    from app.services.relative_conformer_energy import manifest, public, service

    return {m.__name__: Path(m.__file__) for m in (compare_module, service, manifest, public)}


def _names_used(path: Path) -> set[str]:
    """Every identifier the module uses or imports by name (``Name``, ``Attribute`` and import aliases)."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out |= {a.name.rsplit(".", 1)[-1] for a in node.names} | {a.asname for a in node.names if a.asname}
    return out


def test_no_comparison_path_module_imports_the_selection_kernel_or_names_a_decision():
    """Direct imports and names only.

    This reads the four modules the comparison runs through (``compare``, ``service``, ``manifest``, ``public``). It
    does **not** follow imports transitively: the certificate legitimately comes through ``structure_selection.public``
    and the assessment, which sit beside the rules, so a transitive scan would be both wrong and unreadable. What it
    pins is that none of these modules imports the selection kernel or calls a decision function: a comparison ranks nothing.
    """
    modules = _comparison_path()
    assert len(modules) == 4
    for name, path in modules.items():
        imported = _imported_modules(path)
        assert imported, f"the scan read no import in {name}, so it proves nothing"
        assert not [m for m in imported if any(bad in m for bad in _FORBIDDEN)], name
        assert not (_FORBIDDEN_NAMES & _names_used(path)), name
    assert "app.services.structure_selection.models" in _imported_modules(
        modules["app.services.relative_conformer_energy.compare"]
    )


def test_the_name_scan_catches_a_decision_call_in_any_form(tmp_path):
    for source in (
        "from x import decide_alternatives\n",
        "import x\nx.decide_structures(1)\n",
        "e = decide_graph(1)\n",
        "from x import Foo as Edge\n",
    ):
        path = tmp_path / "m.py"
        path.write_text(source)
        assert _FORBIDDEN_NAMES & _names_used(path), source


def test_the_boundary_scan_catches_a_forbidden_import(tmp_path):
    bad = tmp_path / "m.py"
    bad.write_text(
        "from app.services.selection_kernel import decide_alternatives\n"
    )
    found = [m for m in _imported_modules(bad) if any(b in m for b in _FORBIDDEN)]
    assert found  # the scan is not vacuous: it flags the import a ranking would need


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
    # The reference conformer is at another geometry on the candidate side, so no relative energy can be paired.
    assert strict.comparison["reference_conformer"]["problem"] == "reference_conformer_geometry_differs"
    assert {p["excluded_reason"] for p in strict.comparison["pairs"]} == {"reference_conformer_geometry_differs"}
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
    other_recipe = RelativeEnergyRequest(
        level=lot,
        recipe={
            "version": 1,
            "source": {"origin": "producer_declared", "producer": "arc"},
            "spin_treatment": {"state": "known", "value": "restricted"},
        },
        **shared,
    )
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
    assert manifest["format"] == {"name": "relative_conformer_energy_comparison_manifest", "version": 4}
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


def test_a_version_one_comparison_manifest_is_refused_as_an_unknown_format(db_session):
    """Kills: bumping only the single-run manifest, so a comparison recorded under the old shape still replays."""
    world, cand_lot, (a, *_) = two_level_world(db_session)
    manifest = run_service(db_session, world, request_for(world, a, cand_lot, world.lot)).manifest
    old = copy.deepcopy(manifest)
    old["format"] = {"name": "relative_conformer_energy_comparison_manifest", "version": 1}
    with pytest.raises(ReplayError) as raised:
        replay_comparison(_resealed(old))
    assert raised.value.code == "unsupported_manifest_format"
    assert "unknown manifest format" in str(raised.value)


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


# ---------------------------------------------------------------------------
# The geometry a value came from; the identical-protocol rule; the combined manifest bound
# ---------------------------------------------------------------------------


def test_pairing_uses_the_geometry_each_value_came_from_not_the_set_over_its_repeats(db_session):
    world = make_world(db_session)
    cand_lot = make_lot(db_session, method="b3lyp")
    anchor, anchor_obs = conformer(world, -76.400)
    build_basin(
        world,
        energy=-76.500,
        observation=anchor_obs,
        lot=cand_lot,
        target=StructureDeterminationTargetKind.conformer_basin,
    )
    group, obs = conformer(world, None)
    b1, b2 = make_geometry(db_session), make_geometry(db_session)
    basin = {"observation": obs, "target": StructureDeterminationTargetKind.conformer_basin}
    # Two repeats of the basin at each level, at B1 and B2. The administrative representative is the latest stored, so
    # the candidate's value comes from B2 and the reference protocol's from B1, although both have the set {B1, B2}.
    build_basin(world, energy=-76.503, lot=cand_lot, geometry=b1, **basin)
    build_basin(world, energy=-76.513, lot=cand_lot, geometry=b2, **basin)
    build_basin(world, energy=-76.401, geometry=b2, **basin)
    build_basin(world, energy=-76.411, geometry=b1, **basin)
    world.settle()
    both = {"repeat_policy": RepeatPolicy.administrative_representative}
    strict = run_service(
        db_session, world, request_for(world, anchor, cand_lot, world.lot, pairing=Pairing.same_geometry, **both)
    )
    members = {name: {m.conformer_ref: m for m in r.result.members} for name, r in strict.runs.items()}
    cand_member, ref_member = members["candidate"][group.public_ref], members["reference"][group.public_ref]
    assert (
        set(cand_member.evaluated_geometry_refs)
        == set(ref_member.evaluated_geometry_refs)
        == {
            b1.public_ref,
            b2.public_ref,
        }
    )  # the set over the repeats is the same: a pairing that read it would pass this pair
    assert cand_member.value_geometry_ref == b2.public_ref and ref_member.value_geometry_ref == b1.public_ref
    pair = {p["conformer_ref"]: p for p in strict.comparison["pairs"]}[group.public_ref]
    assert pair["excluded_reason"] == "geometry_differs" and pair["deviation_kj_mol"] is None
    assert (pair["candidate_geometry_ref"], pair["reference_geometry_ref"]) == (b2.public_ref, b1.public_ref)
    loose = run_service(db_session, world, request_for(world, anchor, cand_lot, world.lot, **both))
    assert {p["conformer_ref"]: p["excluded_reason"] for p in loose.comparison["pairs"]}[group.public_ref] is None


def test_repeats_that_agree_on_a_value_but_not_on_a_geometry_tie_it_to_none(db_session):
    world = make_world(db_session)
    anchor, anchor_obs = conformer(world, -76.400)
    group, obs = conformer(world, None)
    b1, b2 = make_geometry(db_session), make_geometry(db_session)
    basin = {"observation": obs, "target": StructureDeterminationTargetKind.conformer_basin}
    build_basin(world, energy=-76.401, geometry=b1, **basin)
    build_basin(world, energy=-76.401, geometry=b2, **basin)
    world.settle()
    request = RelativeEnergyRequest(
        reference_conformer_ref=anchor.public_ref,
        energy_kind=EnergyKind.electronic_energy,
        level=LevelSelector(level_of_theory_ref=world.lot.public_ref),
        certificate=Certificate.local_minimum_any_surface,
    )
    from app.services.relative_conformer_energy import run_relative_energies

    result = run_relative_energies(db_session, entry_id=world.entry.id, request=request, require_snapshot=False).result
    member = {m.conformer_ref: m for m in result.members}[group.public_ref]
    assert member.value_hartree == -76.401 and member.value_geometry_ref is None  # never borrowed from either record
    assert set(member.evaluated_geometry_refs) == {b1.public_ref, b2.public_ref}


def test_the_identical_protocol_rule_compares_what_the_recipes_narrow_to():
    lot = LevelSelector(level_of_theory_ref="lot_x")
    shared = {
        "reference_conformer_ref": "cg_a",
        "energy_kind": EnergyKind.electronic_energy,
        "certificate": Certificate.local_minimum_any_surface,
    }
    source = {"origin": "producer_declared", "producer": "arc"}

    def pair(a, b):
        return ComparisonRequest(
            candidate=RelativeEnergyRequest(level=lot, recipe=a, **shared),
            reference=RelativeEnergyRequest(level=lot, recipe=b, **shared),
            pairing=Pairing.same_basin,
        )

    unknown = {"version": 1, "source": source, "spin_treatment": {"state": "unknown"}}
    restricted = {"version": 1, "source": source, "spin_treatment": {"state": "known", "value": "restricted"}}
    assert narrowing_of(unknown) == narrowing_of(None) == ()
    for a, b in (
        (unknown, None),
        (None, unknown),
        (restricted, {**restricted, "source": {"origin": "producer_declared", "producer": "other"}}),
        (restricted, {**restricted, "supporting_dois": ["10.1000/182"]}),
    ):
        with pytest.raises(CodedValueError) as raised:
            pair(a, b)
        assert raised.value.code == CODE_IDENTICAL_PROTOCOLS
    pair(restricted, None)  # a known fact narrows: a real second protocol
    pair(restricted, {**restricted, "spin_treatment": {"state": "known", "value": "unrestricted"}})


def test_the_combined_comparison_manifest_is_held_to_the_size_bound(db_session):
    world, cand_lot, (a, *_) = two_level_world(db_session)
    request = request_for(world, a, cand_lot, world.lot)
    run_ = run_service(db_session, world, request)
    one = len(canonical(run_.runs["candidate"].manifest).encode())
    combined = len(canonical(run_.manifest).encode())
    assert combined > one  # the combined record is bigger than either run, so a per-run bound cannot protect it
    runs = {name: r.manifest for name, r in run_.runs.items()}
    kwargs = {"profile": run_.manifest["read_profile"], "snapshot_isolation": run_.manifest["snapshot_isolation"]}
    build_comparison_manifest(request, runs, run_.comparison, bounds=SelectionBounds(manifest_bytes=combined), **kwargs)
    with pytest.raises(CodedValueError) as raised:
        build_comparison_manifest(
            request, runs, run_.comparison, bounds=SelectionBounds(manifest_bytes=combined - 1), **kwargs
        )
    assert raised.value.code == "structure_selection_manifest_too_large"
    assert raised.value.context["size"] == combined and raised.value.context["limit"] == combined - 1
    # Each run fits under that limit on its own: it is the combination that is refused.
    assert one < combined - 1


def test_a_comparison_manifest_embedding_runs_of_another_version_is_refused_not_crashed(db_session):
    world, cand_lot, (a, *_) = two_level_world(db_session)
    manifest = run_service(db_session, world, request_for(world, a, cand_lot, world.lot)).manifest
    old = copy.deepcopy(manifest)
    for name in ("candidate", "reference"):
        run_ = old["runs"][name]
        for member in run_["result"]["members"]:
            del member["value_geometry_ref"]
        run_["result"]["relative_energy_version"] = "1"
        run_["versions"]["relative_energy"] = "1"
        old["runs"][name] = _resealed(run_)
    with pytest.raises(ReplayError) as raised:  # not a KeyError from the pairing
        replay_comparison(_resealed(old))
    assert raised.value.code == "inconsistent_manifest"


def test_an_answer_that_lists_no_member_at_all_has_no_reference_conformer_value_and_no_pair():
    # The engine's own no_members outcome: the entry has no visible group, so even the named reference is not listed.
    empty = side(ANCHOR, {})
    empty["members"] = []
    empty["outcome"] = "no_members"
    for pairing in (Pairing.same_basin, Pairing.same_geometry):
        out = run(empty, copy.deepcopy(empty), pairing=pairing)
        anchor = out["reference_conformer"]
        assert anchor["problem"] == "reference_conformer_value_missing"
        assert (anchor["candidate_value_present"], anchor["reference_value_present"]) == (False, False)
        assert (anchor["candidate_geometry_ref"], anchor["reference_geometry_ref"]) == (None, None)
        assert out["pairs"] == [] and out["outcome"] == "no_pairs" and out["statistics"]["n"] == 0
    assert run(empty, copy.deepcopy(empty), coverage=ALL)["outcome"] == "incomplete_pairing"
    # One side lists the member and the other does not: the absent side has no value and no geometry, never a crash.
    one_sided = {p["conformer_ref"]: p for p in run(side(ANCHOR, {"cg_b": (3.0, G)}), empty)["pairs"]}
    assert one_sided["cg_b"]["excluded_reason"] == "reference_protocol_value_missing"
    assert one_sided["cg_b"]["reference_geometry_ref"] is None


def test_the_comparison_writer_stamps_the_registered_format_and_the_reader_refuses_a_float_bool_or_missing_one(db_session):
    from app.services.relative_conformer_energy.manifest import COMPARISON_MANIFEST_FORMAT, MANIFEST_FORMATS
    from tests.services._format_cases import bad_format_manifests

    world, cand_lot, (a, *_) = two_level_world(db_session)
    manifest = run_service(db_session, world, request_for(world, a, cand_lot, world.lot)).manifest
    current = MANIFEST_FORMATS.current(COMPARISON_MANIFEST_FORMAT).version
    assert manifest["format"] == {"name": COMPARISON_MANIFEST_FORMAT, "version": current} and type(current) is int
    for tampered in bad_format_manifests(manifest, current):
        with pytest.raises(ReplayError, match="unknown manifest format"):
            replay_comparison(tampered)
