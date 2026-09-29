"""``merge_duplicate_levels_of_theory.py``: plan, dry run, commit, refusals.

Runs inside the pytest transaction, so every change rolls back with the
test. The seed is what ``38b06819f099`` leaves behind on a database that
already held two spellings of one level: a *holder* row carrying the
identity-keyed hash, and a *duplicate* row still carrying its pre-#574 hash
and its own calculations.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
from datetime import datetime

import pytest
from sqlalchemy import select
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge
from app.db.models.record_review import RecordReview
from app.services.calculation_resolution import _level_of_theory_hash
from tests.services.scientific_read._factories import (
    make_calculation,
    make_frequency_scale_factor,
    make_species,
    make_species_entry,
    next_inchi_key,
    unique_smiles,
)

_SCRIPT = (
    pathlib.Path(__file__).parents[2]
    / "scripts"
    / "ops"
    / "merge_duplicate_levels_of_theory.py"
)


@pytest.fixture(scope="module")
def merge():
    spec = importlib.util.spec_from_file_location("merge_duplicate_lots", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SessionProxy:
    def __init__(self, session) -> None:
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def commit(self):
        self._session.flush()


def _pre_574_hash(method: str, basis: str) -> str:
    payload = {
        "method": method,
        "basis": basis,
        "aux_basis": None,
        "cabs_basis": None,
        "dispersion": None,
        "solvent": None,
        "solvent_model": None,
        "keywords": None,
        "spin_treatment": "unknown",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _lot(db_session, method: str, basis: str, *, holder: bool) -> LevelOfTheory:
    lot_hash = (
        _level_of_theory_hash(LevelOfTheoryRef(method=method, basis=basis))
        if holder
        else _pre_574_hash(method, basis)
    )
    row = LevelOfTheory(method=method, basis=basis, lot_hash=lot_hash)
    db_session.add(row)
    db_session.flush()
    return row


def _calc(db_session, lot: LevelOfTheory, *, approved: bool = False) -> Calculation:
    entry = make_species_entry(
        db_session,
        make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("LOTM")),
    )
    calc = make_calculation(db_session, species_entry_id=entry.id, lot_id=lot.id)
    if approved:
        _approve(db_session, calc)
    return calc


def _approve(db_session, record, record_type=SubmissionRecordType.calculation) -> None:
    when = datetime(2026, 9, 1)
    curator = AppUser(
        username=f"lotmerge-curator-{record_type.value}-{record.id}",
        role=AppUserRole.curator,
    )
    db_session.add(curator)
    db_session.flush()
    db_session.add(
        RecordReview(
            record_type=record_type,
            record_id=record.id,
            status=RecordReviewStatus.approved,
            reviewed_by=curator.id,
            reviewed_at=when,
            first_approved_at=when,
        )
    )
    db_session.flush()


@pytest.fixture
def seeded(db_session):
    """Three groups: one mergeable, one with an approved calc, one cited by an FSF."""
    s = {}
    # The #572 pair. Psi4's spelling holds the key; Gaussian's is the duplicate.
    s["psi4"] = _lot(db_session, "b3lyp-lotm", "def2-tzvp", holder=True)
    s["gaussian"] = _lot(db_session, "b3lyp-lotm", "def2tzvp", holder=False)
    s["psi4_calc"] = _calc(db_session, s["psi4"])
    s["gaussian_calcs"] = [_calc(db_session, s["gaussian"]) for _ in range(2)]

    s["approved_holder"] = _lot(db_session, "pbe0-lotm", "def2-svp", holder=True)
    s["approved_dup"] = _lot(db_session, "pbe0-lotm", "Def2SVP", holder=False)
    s["approved_calc"] = _calc(db_session, s["approved_dup"], approved=True)

    s["fsf_holder"] = _lot(db_session, "wb97xd-lotm", "cc-pvtz", holder=True)
    s["fsf_dup"] = _lot(db_session, "wb97xd-lotm", "cc-pVTZ", holder=False)
    s["fsf"] = make_frequency_scale_factor(db_session, lot=s["fsf_dup"])

    # A distinct basis that must never be grouped with anything.
    s["star"] = _lot(db_session, "b3lyp-lotm", "6-31G*", holder=True)
    s["star2"] = _lot(db_session, "b3lyp-lotm", "6-31G**", holder=True)
    return s


def _group(plan, holder: LevelOfTheory):
    [group] = [g for g in plan.groups if g.holder and g.holder.row_id == holder.id]
    return group


def test_plan_finds_the_groups_and_their_blockers(merge, db_session, seeded):
    plan = merge.build_plan(db_session)
    ours = [
        g
        for g in plan.groups
        if g.holder and g.holder.row_id in {
            seeded["psi4"].id, seeded["approved_holder"].id, seeded["fsf_holder"].id
        }
    ]
    assert len(ours) == 3

    pair = _group(plan, seeded["psi4"])
    assert [d.row.row_id for d in pair.duplicates] == [seeded["gaussian"].id]
    assert pair.duplicates[0].calculations == 2
    assert pair.blockers() == []

    approved = _group(plan, seeded["approved_holder"])
    assert any(
        f"calculation {seeded['approved_calc'].public_ref} is approved" in b
        for b in approved.blockers()
    )

    cited = _group(plan, seeded["fsf_holder"])
    assert any("frequency_scale_factor.level_of_theory_id" in b for b in cited.blockers())

    grouped = {d.row.row_id for g in plan.groups for d in g.duplicates} | {
        g.holder.row_id for g in plan.groups if g.holder
    }
    assert seeded["star"].id not in grouped
    assert seeded["star2"].id not in grouped


def test_commit_merges_only_the_unblocked_group(merge, db_session, seeded):
    ref_before = seeded["psi4"].public_ref
    gaussian_id = seeded["gaussian"].id
    plan = merge.build_plan(db_session)
    result = merge.commit_plan(db_session, plan)
    db_session.expire_all()

    merged_holders = {g.holder.row_id for g, _ in result.merged}
    assert seeded["psi4"].id in merged_holders
    assert seeded["approved_holder"].id not in merged_holders
    assert seeded["fsf_holder"].id not in merged_holders

    # The Gaussian calculations now cite the Psi4 row; the duplicate is gone.
    for calc in seeded["gaussian_calcs"]:
        assert db_session.get(Calculation, calc.id).lot_id == seeded["psi4"].id
    # Kept as a merged row, not deleted: its ref still resolves.
    merged_row = db_session.get(LevelOfTheory, gaussian_id)
    assert _merged_into(db_session, gaussian_id) == seeded["psi4"].id
    assert merged_row.basis == "def2tzvp"
    holder = db_session.get(LevelOfTheory, seeded["psi4"].id)
    assert (holder.public_ref, holder.basis) == (ref_before, "def2-tzvp")

    # Blocked groups: untouched.
    assert db_session.get(Calculation, seeded["approved_calc"].id).lot_id == seeded["approved_dup"].id
    assert _merged_into(db_session, seeded["approved_dup"].id) is None
    assert _merged_into(db_session, seeded["fsf_dup"].id) is None


def test_an_approval_after_the_plan_keeps_the_group(merge, db_session, seeded):
    plan = merge.build_plan(db_session)
    _approve(db_session, seeded["gaussian_calcs"][0])

    result = merge.commit_plan(db_session, plan)
    db_session.expire_all()

    assert seeded["psi4"].id not in {g.holder.row_id for g, _ in result.merged}
    assert any(g.holder.row_id == seeded["psi4"].id for g, _ in result.kept)
    for calc in seeded["gaussian_calcs"]:
        assert db_session.get(Calculation, calc.id).lot_id == seeded["gaussian"].id


def test_a_group_without_a_holder_is_blocked(merge, db_session):
    # Neither row carries the keyed hash: 38b06819f099 has not run.
    a = _lot(db_session, "m062x-lotm", "Def2TZVP", holder=False)
    b = _lot(db_session, "m062x-lotm", "def2tzvp", holder=False)
    plan = merge.build_plan(db_session)
    [group] = [
        g for g in plan.groups if {d.row.row_id for d in g.duplicates} == {a.id, b.id}
    ]
    assert group.holder is None
    assert "38b06819f099" in group.blockers()[0]


def _run_main(merge, monkeypatch, db_session, argv, *, db_name="tckdb_test_lotmerge"):
    import app.api.deps as deps
    from app.api.config import settings

    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    monkeypatch.setattr(settings, "db_name", db_name)
    return merge.main(argv)


def test_dry_run_prints_the_plan_and_changes_nothing(
    merge, monkeypatch, db_session, seeded, capsys
):
    assert _run_main(merge, monkeypatch, db_session, []) == 0
    out = capsys.readouterr().out
    assert "calculation.lot_id  (rewritten)" in out
    assert "frequency_scale_factor.level_of_theory_id  (blocks a merge)" in out
    assert seeded["gaussian"].public_ref in out
    assert "BLOCKED" in out
    assert "Dry run" in out
    db_session.expire_all()
    assert _merged_into(db_session, seeded["gaussian"].id) is None
    rows = db_session.scalars(
        select(Calculation.lot_id).where(
            Calculation.id.in_([c.id for c in seeded["gaussian_calcs"]])
        )
    ).all()
    assert set(rows) == {seeded["gaussian"].id}


def test_commit_through_main(merge, monkeypatch, db_session, seeded, capsys):
    gaussian_id = seeded["gaussian"].id
    assert _run_main(merge, monkeypatch, db_session, ["--commit"]) == 0
    out = capsys.readouterr().out
    assert f"into {seeded['psi4'].public_ref}: 2 calculation(s) repointed" in out
    db_session.expire_all()
    assert _merged_into(db_session, gaussian_id) == seeded["psi4"].id


def test_commit_refuses_a_deployed_database_name(merge, monkeypatch, db_session, seeded):
    assert (
        _run_main(merge, monkeypatch, db_session, ["--commit"], db_name="tckdb_prod") == 2
    )
    db_session.expire_all()
    assert _merged_into(db_session, seeded["gaussian"].id) is None



# ---------------------------------------------------------------------------
# Review findings on #582: releases cite the duplicate's ref, and accepted
# products cite the moved calculations.
# ---------------------------------------------------------------------------


def test_a_merged_duplicate_ref_still_resolves_to_the_holder(merge, db_session, seeded):
    """A published release freezes ``level_of_theory_ref`` per cited calculation."""
    from app.services.release.records import calculation_provenance
    from app.services.scientific_read.handles import resolve_level_of_theory_handle

    calc_id = seeded["gaussian_calcs"][0].id
    old_ref = seeded["gaussian"].public_ref
    frozen = calculation_provenance(db_session, [calc_id])[calc_id]
    assert frozen["level_of_theory"]["level_of_theory_ref"] == old_ref

    merge.commit_plan(db_session, merge.build_plan(db_session))
    db_session.expire_all()

    assert resolve_level_of_theory_handle(db_session, old_ref) == seeded["psi4"].id


def test_a_calculation_cited_by_an_accepted_thermo_blocks_the_group(
    merge, db_session, seeded
):
    from app.db.models.common import ThermoCalculationRole
    from app.db.models.thermo import ThermoSourceCalculation
    from tests.services.scientific_read._factories import make_thermo_scalar

    calc = seeded["gaussian_calcs"][0]
    thermo = make_thermo_scalar(
        db_session, species_entry=_entry_of(db_session, calc)
    )
    db_session.add(
        ThermoSourceCalculation(
            thermo_id=thermo.id, calculation_id=calc.id, role=ThermoCalculationRole.sp
        )
    )
    db_session.flush()
    _approve(db_session, thermo, SubmissionRecordType.thermo)

    plan = merge.build_plan(db_session)
    blockers = _group(plan, seeded["psi4"]).blockers()
    assert any(thermo.public_ref in b for b in blockers), blockers

    merge.commit_plan(db_session, plan)
    db_session.expire_all()
    assert db_session.get(Calculation, calc.id).lot_id == seeded["gaussian"].id


def test_an_accepted_thermo_two_hops_away_blocks_the_group(merge, db_session, seeded):
    """calculation <- statmech_source_calculation -> statmech <- thermo.statmech_id."""
    from app.db.models.common import StatmechCalculationRole
    from app.db.models.statmech import StatmechSourceCalculation
    from tests.services.scientific_read._factories import make_statmech, make_thermo_scalar

    calc = seeded["gaussian_calcs"][1]
    entry = _entry_of(db_session, calc)
    statmech = make_statmech(db_session, species_entry=entry)
    db_session.add(
        StatmechSourceCalculation(
            statmech_id=statmech.id, calculation_id=calc.id, role=StatmechCalculationRole.opt
        )
    )
    db_session.flush()
    thermo = make_thermo_scalar(db_session, species_entry=entry, statmech_id=statmech.id)
    _approve(db_session, thermo, SubmissionRecordType.thermo)

    blockers = _group(merge.build_plan(db_session), seeded["psi4"]).blockers()
    assert any(thermo.public_ref in b for b in blockers), blockers
    # The statmech itself is not approved and must not be named as accepted.
    assert not any(statmech.public_ref in b and "accepted" in b and thermo.public_ref not in b for b in blockers)


def _entry_of(db_session, calc):
    from app.db.models.species import SpeciesEntry

    return db_session.get(SpeciesEntry, calc.species_entry_id)


def test_a_second_run_finds_nothing_to_merge(merge, db_session, seeded):
    merge.commit_plan(db_session, merge.build_plan(db_session))
    db_session.expire_all()
    again = merge.build_plan(db_session)
    assert not any(
        g.holder and g.holder.row_id == seeded["psi4"].id for g in again.groups
    )


def test_rows_merged_into_a_duplicate_are_re_aimed_at_the_holder(merge, db_session, seeded):
    """A merge is always one hop: nothing may point at a row that is itself merged."""
    earlier = _lot(db_session, "b3lyp-lotm", "DEF2TZVP", holder=False)
    db_session.add(LevelOfTheoryMerge(merged_lot_id=earlier.id, into_lot_id=seeded["gaussian"].id))
    db_session.flush()

    merge.commit_plan(db_session, merge.build_plan(db_session))
    db_session.expire_all()

    assert _merged_into(db_session, earlier.id) == seeded["psi4"].id
    assert _merged_into(db_session, seeded["gaussian"].id) == seeded["psi4"].id


def _merged_into(db_session, lot_id: int) -> int | None:
    return db_session.scalar(
        select(LevelOfTheoryMerge.into_lot_id).where(LevelOfTheoryMerge.merged_lot_id == lot_id)
    )
