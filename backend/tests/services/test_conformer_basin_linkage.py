"""Conformer basins are the connected components of the optimised structures of an entry.

Before this rule a basin was matched against one stored reference, the first structure deposited into it, so the
partition of a set of structures depended on the order they were uploaded in. Here every test goes through the real
upload workflow (``persist_conformer_upload``), because the defect lives in how the live path and the recompute
cooperate, not in the matcher alone.

The structures are RDKit geometries with one dihedral set, so no number here is a claim about energies: the subject
is only which torsion differences connect two structures.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.basin_matching import BasinMatchParams, compare_basins
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType, ThermoTargetKind
from app.db.models.record_review import RecordReview
from app.db.models.species import (
    ConformerGroup,
    ConformerGroupMergePending,
    ConformerObservation,
    ConformerSelection,
)
from app.db.models.thermo import Thermo
from app.schemas.workflows.conformer_upload import ConformerUploadRequest
from app.services.conformer_reassignment import reassign_conformer_groups
from app.services.conformer_resolution import TORSION_BASIN_PARAMS, compute_fingerprint_for_observation
from app.services.record_review import ensure_record_review, set_record_review_status
from app.workflows.conformer import persist_conformer_upload
from tests.services.scientific_read._factories import make_thermo_scalar
from tests.services.test_basin_matching import with_dihedral

_BUTANE = "CCCC"
_BUTANE_DIHEDRAL = "[CH3][CH2][CH2][CH3]"
_ETHANOL = "CCO"
_ETHANOL_DIHEDRAL = "[#1][OX2][CH2][CH3]"
_PARAMS = dict(TORSION_BASIN_PARAMS)
_MATCH = BasinMatchParams.from_scheme_params(_PARAMS)


@dataclass(frozen=True)
class Structure:
    name: str
    atoms: tuple
    calc_type: str = "opt"
    #: The depositor's statement about convergence of the optimisation (``None``: not stated).
    converged: bool | None = None


def butane(name: str, degrees: float, calc_type: str = "opt", converged: bool | None = None) -> Structure:
    return Structure(name, with_dihedral(_BUTANE, _BUTANE_DIHEDRAL, degrees), calc_type, converged)


def ethanol(name: str, degrees: float, calc_type: str = "opt") -> Structure:
    return Structure(name, with_dihedral(_ETHANOL, _ETHANOL_DIHEDRAL, degrees, seed=5), calc_type)


def upload_request(smiles: str, structure: Structure) -> ConformerUploadRequest:
    atoms = structure.atoms
    lines = [str(len(atoms)), structure.name] + [f"{s} {x:.6f} {y:.6f} {z:.6f}" for s, x, y, z in atoms]
    calculation: dict = {
        "type": structure.calc_type,
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
    }
    if structure.converged is not None:
        calculation["opt_result"] = {"converged": structure.converged}
    return ConformerUploadRequest(
        species_entry={"smiles": smiles, "charge": 0, "multiplicity": 1},
        geometry={"xyz_text": "\n".join(lines)},
        calculation=calculation,
    )


def deposit_observations(session: Session, smiles: str, structures) -> dict[str, int]:
    """Upload each structure in the given order through the real workflow; return the observation id of each, by name."""
    observation_of: dict[str, int] = {}
    for structure in structures:
        outcome = persist_conformer_upload(session, upload_request(smiles, structure))
        session.flush()
        observation_of[structure.name] = outcome.observation.id
    session.expire_all()
    return observation_of


def deposit(session: Session, smiles: str, structures) -> dict[str, int]:
    """Upload each structure in the given order; return the group each one is filed under, by name."""
    observation_of = deposit_observations(session, smiles, structures)
    return {
        name: session.get(ConformerObservation, observation_id).conformer_group_id
        for name, observation_id in observation_of.items()
    }


def partition(group_of: dict[str, int]) -> frozenset[frozenset[str]]:
    """The basins as sets of structure names: what an order of deposition must not change."""
    by_group: dict[int, set[str]] = {}
    for name, group in group_of.items():
        by_group.setdefault(group, set()).add(name)
    return frozenset(frozenset(names) for names in by_group.values())


def components(smiles: str, structures) -> frozenset[frozenset[str]]:
    """Connected components of the structures under pairwise ``compare_basins``: the answer, computed independently."""
    mapped = {}
    for s in structures:
        fingerprint, coords = compute_fingerprint_for_observation(smiles, s.atoms, _PARAMS)
        assert fingerprint is not None
        mapped[s.name] = coords
    parent = {s.name: s.name for s in structures}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in itertools.combinations(structures, 2):
        if compare_basins(smiles, mapped[a.name], mapped[b.name], _MATCH).same_basin:
            parent[find(a.name)] = find(b.name)
    out: dict[str, set[str]] = {}
    for s in structures:
        out.setdefault(find(s.name), set()).add(s.name)
    return frozenset(frozenset(v) for v in out.values())


def partitions_over_all_orders(db_conn, smiles: str, structures, *, limit: int | None = None):
    """``{order: partition}`` for every order of deposit (or the first ``limit``), each in its own rolled-back transaction."""
    result = {}
    orders = itertools.permutations(structures)
    for order in itertools.islice(orders, limit) if limit else orders:
        with Session(db_conn) as session:
            session.begin()
            try:
                result[tuple(s.name for s in order)] = partition(deposit(session, smiles, order))
            finally:
                session.rollback()
    return result


# ---------------------------------------------------------------------------
# The partition is a function of the set of structures, not of the order they arrived in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("start", [0.0, 60.0], ids=["0/18/36", "60/78/96"])
def test_three_structures_18_degrees_apart_make_one_basin_in_every_order(db_conn, start: float) -> None:
    """The n-butane table of the report: three partitions from one set before, one after."""
    structures = [butane("A", start), butane("B", start + 18.0), butane("C", start + 36.0)]
    seen = partitions_over_all_orders(db_conn, _BUTANE, structures)
    assert len(seen) == 6
    assert set(seen.values()) == {frozenset({frozenset({"A", "B", "C"})})}, seen


def test_the_partition_equals_the_connected_components_in_every_order_of_four_butane_structures(db_conn) -> None:
    structures = [butane("A", 0.0), butane("B", 18.0), butane("C", 36.0), butane("D", 100.0)]
    expected = components(_BUTANE, structures)
    assert expected == frozenset({frozenset({"A", "B", "C"}), frozenset({"D"})}), expected  # the oracle itself
    seen = partitions_over_all_orders(db_conn, _BUTANE, structures)
    assert len(seen) == 24
    assert set(seen.values()) == {expected}, {k: sorted(map(sorted, v)) for k, v in seen.items() if v != expected}


def test_the_partition_equals_the_connected_components_in_every_order_of_ethanol_rotamers(db_conn) -> None:
    structures = [ethanol("trans", 180.0), ethanol("gauche", 60.0), ethanol("near_trans", 170.0)]
    expected = components(_ETHANOL, structures)
    assert expected == frozenset({frozenset({"trans", "near_trans"}), frozenset({"gauche"})})
    seen = partitions_over_all_orders(db_conn, _ETHANOL, structures)
    assert len(seen) == 6
    assert set(seen.values()) == {expected}


def test_drift_inside_the_tolerance_is_one_basin_in_every_order(db_conn) -> None:
    """All pairs within 20 degrees: the case the old rule already got right, and must keep getting right."""
    structures = [butane("A", 65.0), butane("B", 72.0), butane("C", 58.0)]
    seen = partitions_over_all_orders(db_conn, _BUTANE, structures)
    assert set(seen.values()) == {frozenset({frozenset({"A", "B", "C"})})}


# ---------------------------------------------------------------------------
# Only converged optimised structures define a basin
# ---------------------------------------------------------------------------


def test_a_single_point_between_two_optimised_structures_is_filed_but_does_not_join_them(db_conn) -> None:
    """A at 0 and C at 36 are 36 degrees apart; an sp at 18 is near both but is not an optimised structure."""
    with Session(db_conn) as session:
        session.begin()
        try:
            groups = deposit(
                session, _BUTANE, [butane("A", 0.0), butane("C", 36.0), butane("sp_between", 18.0, "sp")]
            )
            assert groups["A"] != groups["C"], "an sp must not bridge two optimised basins"
            assert groups["sp_between"] in (groups["A"], groups["C"]), "it is still filed into a basin"
            assert len(set(groups.values())) == 2
        finally:
            session.rollback()


def test_an_optimised_structure_between_them_does_join_them(db_conn) -> None:
    """The same geometry as an opt does bridge: the difference between the two tests is the calculation type alone."""
    with Session(db_conn) as session:
        session.begin()
        try:
            groups = deposit(session, _BUTANE, [butane("A", 0.0), butane("C", 36.0), butane("B", 18.0)])
            assert len(set(groups.values())) == 1, groups
        finally:
            session.rollback()


def test_the_order_of_a_non_defining_deposit_does_not_change_the_optimised_partition(db_conn) -> None:
    structures = [butane("A", 0.0), butane("C", 36.0), butane("sp_between", 18.0, "sp")]
    for order in itertools.permutations(structures):
        with Session(db_conn) as session:
            session.begin()
            try:
                groups = deposit(session, _BUTANE, order)
                assert groups["A"] != groups["C"], [s.name for s in order]
                assert groups["sp_between"] in (groups["A"], groups["C"]), [s.name for s in order]
            finally:
                session.rollback()


# ---------------------------------------------------------------------------
# A bridging upload merges two groups; what a group records about itself
# ---------------------------------------------------------------------------


def _curator(session) -> AppUser:
    actor = AppUser(username=f"basin-{uuid4().hex[:10]}", role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    return actor


def _review_status(session, group_id: int):
    session.expire_all()
    return session.scalar(
        select(RecordReview.status).where(
            RecordReview.record_type == SubmissionRecordType.conformer_group, RecordReview.record_id == group_id
        )
    )


def _begin_review(session, group_id: int) -> None:
    ensure_record_review(session, record_type=SubmissionRecordType.conformer_group, record_id=group_id)
    set_record_review_status(
        session,
        record_type=SubmissionRecordType.conformer_group,
        record_id=group_id,
        status=RecordReviewStatus.under_review,
        actor=_curator(session),
    )


def test_a_bridging_upload_merges_the_younger_group_into_the_older_and_keeps_the_emptied_row(db_conn) -> None:
    with Session(db_conn) as session:
        session.begin()
        try:
            first = deposit_observations(session, _BUTANE, [butane("A", 0.0), butane("C", 36.0)])
            older, younger = (session.get(ConformerObservation, first[n]).conformer_group_id for n in ("A", "C"))
            assert older != younger and older < younger
            _begin_review(session, older)
            _begin_review(session, younger)
            assert _review_status(session, older) is RecordReviewStatus.under_review
            assert _review_status(session, younger) is RecordReviewStatus.under_review

            second = deposit_observations(session, _BUTANE, [butane("B", 18.0)])
            session.expire_all()
            groups = {n: session.get(ConformerObservation, i).conformer_group_id for n, i in {**first, **second}.items()}
            assert set(groups.values()) == {older}, groups  # all three are in the OLDER group
            survivor, emptied = session.get(ConformerGroup, older), session.get(ConformerGroup, younger)
            # the emptied row is kept, with nothing to match against and a pointer forward
            assert emptied is not None
            assert emptied.merged_into_conformer_group_id == older
            assert emptied.representative_fingerprint_json is None and emptied.representative_coords_json is None
            assert emptied.representative_observation_id is None
            assert survivor.merged_into_conformer_group_id is None
            assert not session.scalars(
                select(ConformerObservation).where(ConformerObservation.conformer_group_id == younger)
            ).all()
            # both reviews were reset, and the survivor's representative is the middle structure
            assert _review_status(session, older) is RecordReviewStatus.not_reviewed
            assert _review_status(session, younger) is RecordReviewStatus.not_reviewed
            assert survivor.representative_observation_id == second["B"]
        finally:
            session.rollback()


def test_a_bridging_upload_moves_a_thermo_target_and_selection_of_the_younger_group(db_conn) -> None:
    """Nothing here is approved, so the live upload makes the merge itself and its dependents follow."""
    with Session(db_conn) as session:
        session.begin()
        try:
            first = deposit_observations(session, _BUTANE, [butane("A", 0.0), butane("C", 36.0)])
            older, younger = (session.get(ConformerObservation, first[n]).conformer_group_id for n in ("A", "C"))
            entry = session.get(ConformerGroup, younger).species_entry
            thermo = make_thermo_scalar(session, species_entry=entry)
            thermo.thermodynamic_target_kind = ThermoTargetKind.single_conformer
            thermo.target_conformer_group_id = younger
            session.add(ConformerSelection(conformer_group_id=younger, selection_kind="lowest_energy"))
            session.flush()
            thermo_id = thermo.id

            deposit_observations(session, _BUTANE, [butane("B", 18.0)])
            session.expire_all()
            assert session.get(Thermo, thermo_id).target_conformer_group_id == older
            assert [s.conformer_group_id for s in session.scalars(select(ConformerSelection))] == [older]
            assert session.scalars(select(ConformerGroupMergePending)).all() == []  # nothing was owed
        finally:
            session.rollback()


# ---------------------------------------------------------------------------
# The representative is the medoid, derived from the set; a wide basin is reported, never split
# ---------------------------------------------------------------------------


def test_the_representative_is_the_middle_structure_in_every_order(db_conn) -> None:
    structures = [butane("lo", 60.0), butane("mid", 70.0), butane("hi", 80.0)]
    for order in itertools.permutations(structures):
        with Session(db_conn) as session:
            session.begin()
            try:
                observations = deposit_observations(session, _BUTANE, order)
                group = session.get(
                    ConformerGroup, session.get(ConformerObservation, observations["mid"]).conformer_group_id
                )
                assert {session.get(ConformerObservation, i).conformer_group_id for i in observations.values()} == {
                    group.id
                }
                assert group.representative_observation_id == observations["mid"], [s.name for s in order]
                assert group.basin_is_clique is True
                assert group.basin_span_deg == pytest.approx(20.0, abs=1.0)
                # the stored fingerprint and coordinates are the representative's own
                representative = session.get(ConformerObservation, group.representative_observation_id)
                assert group.representative_fingerprint_json == representative.torsion_fingerprint_json
                assert group.representative_coords_json == representative.mapped_coords_json
            finally:
                session.rollback()


def test_a_chain_of_optimised_structures_is_one_wide_basin_and_is_not_split(db_conn) -> None:
    structures = [butane("A", 0.0), butane("B", 18.0), butane("C", 36.0)]
    for order in itertools.permutations(structures):
        with Session(db_conn) as session:
            session.begin()
            try:
                observations = deposit_observations(session, _BUTANE, order)
                groups = {session.get(ConformerObservation, i).conformer_group_id for i in observations.values()}
                assert len(groups) == 1, [s.name for s in order]
                group = session.get(ConformerGroup, groups.pop())
                assert group.basin_is_clique is False, [s.name for s in order]
                assert group.basin_span_deg == pytest.approx(36.0, abs=1.0)
                assert group.representative_observation_id == observations["B"]
            finally:
                session.rollback()


def test_drift_inside_the_tolerance_is_a_clique(db_conn) -> None:
    with Session(db_conn) as session:
        session.begin()
        try:
            observations = deposit_observations(
                session, _BUTANE, [butane("A", 65.0), butane("B", 72.0), butane("C", 58.0)]
            )
            group = session.get(
                ConformerGroup, session.get(ConformerObservation, observations["A"]).conformer_group_id
            )
            assert group.basin_is_clique is True and group.basin_span_deg is not None
        finally:
            session.rollback()


# ---------------------------------------------------------------------------
# Defining members, and the structures that are only filed
# ---------------------------------------------------------------------------


def test_an_optimisation_stated_unconverged_is_filed_but_does_not_bridge(db_conn) -> None:
    with Session(db_conn) as session:
        session.begin()
        try:
            groups = deposit(
                session,
                _BUTANE,
                [butane("A", 0.0), butane("C", 36.0), butane("partial", 18.0, "opt", converged=False)],
            )
            assert groups["A"] != groups["C"]
            assert groups["partial"] in (groups["A"], groups["C"])
        finally:
            session.rollback()


def test_a_composite_primary_with_a_geometry_defines_a_basin_like_an_opt(db_conn) -> None:
    from app.services.conformer_basin_store import is_defining_calculation

    assert is_defining_calculation("opt", has_final_geometry=True, converged=None)
    assert is_defining_calculation("opt", has_final_geometry=True, converged=True)
    assert is_defining_calculation("composite", has_final_geometry=True, converged=None)
    assert not is_defining_calculation("opt", has_final_geometry=True, converged=False)
    assert not is_defining_calculation("opt", has_final_geometry=False, converged=None)
    for kind in ("sp", "freq", "scan", "irc"):
        assert not is_defining_calculation(kind, has_final_geometry=True, converged=True), kind


def test_a_provisional_basin_has_no_defining_member_until_an_optimised_deposit_joins_it(db_conn) -> None:
    with Session(db_conn) as session:
        session.begin()
        try:
            first = deposit_observations(session, _BUTANE, [butane("sp_a", 0.0, "sp"), butane("sp_b", 120.0, "sp")])
            group_a, group_b = (session.get(ConformerObservation, first[n]).conformer_group_id for n in ("sp_a", "sp_b"))
            assert group_a != group_b, "two single points far apart are two provisional basins"
            provisional = session.get(ConformerGroup, group_a)
            assert provisional.basin_is_clique is None and provisional.basin_span_deg is None
            assert provisional.representative_observation_id == first["sp_a"]

            second = deposit_observations(session, _BUTANE, [butane("opt_a", 10.0)])
            session.expire_all()
            assert session.get(ConformerObservation, second["opt_a"]).conformer_group_id == group_a
            joined = session.get(ConformerGroup, group_a)
            assert joined.representative_observation_id == second["opt_a"]  # now the optimised one stands for it
            assert joined.basin_is_clique is True
        finally:
            session.rollback()


# ---------------------------------------------------------------------------
# The live path and the recompute are one rule
# ---------------------------------------------------------------------------


def _is_a_no_op(report) -> bool:
    ignored = {"entries_examined", "observations_examined"}
    return not any(v for k, v in report.__dict__.items() if isinstance(v, int) and k not in ignored)


@pytest.mark.parametrize("seed", range(6))
def test_the_recompute_agrees_with_every_state_the_live_path_reaches(db_conn, seed: int) -> None:
    """After each upload of a random sequence the recompute changes nothing: both decide "same basin" one way."""
    rng = random.Random(seed)
    degrees = rng.sample([0.0, 10.0, 18.0, 36.0, 54.0, 72.0, 100.0, 118.0, 150.0, 190.0, 215.0, 235.0], 8)
    structures = [butane(f"s{i}", d, "opt" if rng.random() < 0.8 else "sp") for i, d in enumerate(degrees)]
    with Session(db_conn) as session:
        session.begin()
        try:
            for structure in structures:
                deposit_observations(session, _BUTANE, [structure])
                entry_id = session.scalar(select(ConformerGroup.species_entry_id))
                report = reassign_conformer_groups(
                    session, repair_revision="test-live-agrees", species_entry_ids=[entry_id]
                )
                assert _is_a_no_op(report), (seed, structure.name, report.summary())
        finally:
            session.rollback()


@pytest.mark.parametrize("seed", range(5))
def test_no_observation_ever_changes_group_except_into_an_older_one(db_conn, seed: int) -> None:
    """Monotonicity of the live path over random sequences of optimised deposits."""
    rng = random.Random(100 + seed)
    # 0 and 36 are two groups; 18, deposited after both, bridges them. The rest is random.
    others = rng.sample([60.0, 90.0, 102.0, 114.0, 150.0, 200.0, 212.0, 224.0, 300.0], 6)
    degrees = [0.0, 36.0, *others]
    degrees.insert(rng.randint(2, len(degrees)), 18.0)
    with Session(db_conn) as session:
        session.begin()
        try:
            seen: dict[int, int] = {}
            moves = 0
            for i, d in enumerate(degrees):
                deposit_observations(session, _BUTANE, [butane(f"s{i}", d)])
                session.expire_all()
                for observation in session.scalars(select(ConformerObservation)):
                    previous = seen.get(observation.id)
                    if previous is not None and previous != observation.conformer_group_id:
                        assert observation.conformer_group_id < previous, (seed, i, observation.id)
                        moves += 1
                    seen[observation.id] = observation.conformer_group_id
            assert moves, "the sequence never merged two groups, so nothing was checked"
        finally:
            session.rollback()


# ---------------------------------------------------------------------------
# Linkage does not depend on which of two structures came first, even under the cap
# ---------------------------------------------------------------------------


def test_linkage_is_symmetric_even_when_the_cap_truncates_the_symmetry_set() -> None:
    from app.chemistry.basin_linkage import PairTable
    from app.chemistry.basin_matching import build_symmetry_model, compare_basins_symmetric
    from tests.services.test_basin_matching import atoms_of, embed, shuffled

    smiles = "CC(C)(C)C(C)(C)C"
    capped_params = dict(_PARAMS, max_symmetry_mappings=4)
    match = BasinMatchParams.from_scheme_params(capped_params)
    build_symmetry_model.cache_clear()
    try:
        base = atoms_of(embed(smiles))
        structures = {}
        for seed in range(12):
            _fp, coords = compute_fingerprint_for_observation(smiles, shuffled(base, seed), capped_params)
            structures[seed] = coords
        asymmetric = [
            (i, j)
            for i, j in itertools.permutations(structures, 2)
            if compare_basins(smiles, structures[i], structures[j], match).same_basin
            != compare_basins(smiles, structures[j], structures[i], match).same_basin
        ]
        assert asymmetric, "no pair is direction-dependent under this cap, so the test would verify nothing"
        table = PairTable(smiles, match, {i: structures[i] for i in structures})
        for i, j in asymmetric:
            assert compare_basins_symmetric(smiles, structures[i], structures[j], match).same_basin
            assert compare_basins_symmetric(smiles, structures[j], structures[i], match).same_basin
            assert table.linked(i, j) and table.linked(j, i)
    finally:
        build_symmetry_model.cache_clear()


def test_a_structure_that_defines_nothing_is_filed_into_the_basin_it_matches_best(db_conn) -> None:
    for degrees, nearer in ((8.0, "A"), (28.0, "C")):
        with Session(db_conn) as session:
            session.begin()
            try:
                groups = deposit(
                    session, _BUTANE, [butane("A", 0.0), butane("C", 36.0), butane("sp", degrees, "sp")]
                )
                assert groups["sp"] == groups[nearer], (degrees, groups)
                assert groups["A"] != groups["C"]
            finally:
                session.rollback()
