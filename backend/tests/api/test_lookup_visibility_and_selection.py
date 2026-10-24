"""``/api/v1/lookup/*``: the scientific reads' visibility, profile and display order; selection modes that say what they did.

Before this change the lookup routes were public, applied no review filter and no read profile, listed in id order, and
``selection=lowest_energy`` across mixed levels of theory added a warning and returned a winner. Now:

* rejected and deprecated records are hidden by default (counted in ``hidden_by_default``); ``?profile=curated``
  withholds everything below the approved floor and counts none of it;
* records are listed in the display order the response echoes;
* a ``selection`` mode labels what it did (``selected_by``), refuses ``lowest_energy`` across levels, returns every
  member of an exact tie, and never lets a record the profile hides take part.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models.common import CalculationType, RecordReviewStatus, SubmissionRecordType
from tests.services.scientific_read._factories import (
    attach_opt_result,
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transport,
    next_inchi_key,
    set_review,
)

S = RecordReviewStatus
LOOKUP = "/api/v1/lookup"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
#: row 0 rejected, row 1 deprecated, then three visible (one approved).
HIDING = [S.rejected, S.deprecated, S.approved, S.not_reviewed, S.under_review]


def _entry(db_session, prefix="LK"):
    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key(prefix))
    return species, make_species_entry(db_session, species)


def _review(db_session, record_type, rows, statuses=HIDING):
    for row, status in zip(rows, statuses, strict=True):
        if status is not S.not_reviewed:
            set_review(db_session, record_type=record_type, record_id=row.id, status=status)


def _seed_thermo(db_session):
    _species, entry = _entry(db_session)
    rows = [make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-1.0 - i) for i in range(5)]
    _review(db_session, SubmissionRecordType.thermo, rows)
    return rows, f"{LOOKUP}/thermo", {"species_entry_id": entry.id}


def _seed_statmech(db_session):
    _species, entry = _entry(db_session)
    rows = [make_statmech(db_session, species_entry=entry) for _ in range(5)]
    _review(db_session, SubmissionRecordType.statmech, rows)
    return rows, f"{LOOKUP}/statmech", {"species_entry_id": entry.id}


def _seed_transport(db_session):
    _species, entry = _entry(db_session)
    rows = [make_transport(db_session, species_entry=entry) for _ in range(5)]
    _review(db_session, SubmissionRecordType.transport, rows)
    return rows, f"{LOOKUP}/transport", {"species_entry_id": entry.id}


def _seed_calculations(db_session):
    _species, entry = _entry(db_session)
    rows = [make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id) for _ in range(5)]
    _review(db_session, SubmissionRecordType.calculation, rows)
    return rows, f"{LOOKUP}/calculations", {"species_entry_id": entry.id}


def _seed_kinetics(db_session):
    rs = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("LKR"))
    ps = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("LKP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    rows = [make_kinetics(db_session, reaction_entry=entry, a=1e-12 * (i + 1)) for i in range(5)]
    _review(db_session, SubmissionRecordType.kinetics, rows)
    return rows, f"{LOOKUP}/kinetics", {"reaction_entry_id": entry.id}


SEEDS = {
    "thermo": _seed_thermo,
    "statmech": _seed_statmech,
    "transport": _seed_transport,
    "calculations": _seed_calculations,
    "kinetics": _seed_kinetics,
}


def _ids(body):
    return [r["id"] for r in body["results"]]


@pytest.mark.parametrize("case", sorted(SEEDS))
def test_a_lookup_hides_rejected_and_deprecated_by_default_and_counts_them(case, client, db_session):
    rows, url, params = SEEDS[case](db_session)
    body = client.get(url, params=params).json()
    assert sorted(_ids(body)) == sorted(rows[i].id for i in (2, 3, 4))
    assert body["hidden_by_default"]["counts"] == {"rejected": 1, "deprecated": 1}
    assert body["hidden_by_default"]["include_with"]["rejected"] == "include_rejected=true"
    lifted = client.get(url, params={**params, "include_rejected": "true", "include_deprecated": "true"}).json()
    assert sorted(_ids(lifted)) == sorted(r.id for r in rows)
    assert lifted["hidden_by_default"] is None


@pytest.mark.parametrize("case", sorted(SEEDS))
def test_a_curated_lookup_withholds_below_floor_records_and_counts_none_of_them(case, client, db_session):
    """Kills: a lookup that ignores the profile (before, every record showed); a count of what the floor withheld."""
    rows, url, params = SEEDS[case](db_session)
    body = client.get(
        url, params={**params, "profile": "curated", "include_rejected": "true", "include_deprecated": "true"}
    ).json()
    assert _ids(body) == [rows[2].id]
    assert body["profile"] == "curated"
    assert body["hidden_by_default"] is None
    assert '"counts"' not in json.dumps(body)


def test_lookup_results_are_in_the_display_order_it_echoes(client, db_session):
    """Review rank, then deposition newest first, then id descending: the seed makes id order disagree with all three."""
    from tests.api.test_browse_order_echo import ROW_SPECS, skew

    _species, entry = _entry(db_session)
    rows = [make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-1.0 - i) for i in range(5)]
    skew(db_session, rows, ROW_SPECS)
    for row, (status, _age) in zip(rows, ROW_SPECS, strict=True):
        if status is not S.not_reviewed:
            set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=row.id, status=status)
    body = client.get(f"{LOOKUP}/thermo", params={"species_entry_id": entry.id}).json()
    assert [(k["key"], k["direction"]) for k in body["order"]["keys"]] == [
        ("review_rank", "asc"),
        ("created_at", "desc"),
        ("id", "desc"),
    ]
    assert body["order"]["requested_by"] is None
    # approved (row 1), then the unreviewed tier newest first: 0 (1d), 2 (3d, under_review ranks equal), 4 and 3 (5d, id desc)
    assert _ids(body) == [rows[i].id for i in (1, 0, 2, 4, 3)]
    assert _ids(body) != sorted(_ids(body))


# --- selection modes ----------------------------------------------------------------------------------------------


def _opt(db_session, entry, lot, energy, *, age_days=0, status=None):
    calc = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id, lot_id=lot.id)
    attach_opt_result(db_session, calculation=calc, final_energy_hartree=energy)
    calc.created_at = T0 - timedelta(days=age_days)
    db_session.flush()
    if status is not None:
        set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id, status=status)
    return calc


def _calc_ids(body):
    return [r["id"] for r in body["results"] if r["resource_type"] == "calculation"]


def test_lowest_energy_across_levels_of_theory_is_refused_and_names_the_levels(client, db_session):
    """Kills: warning and returning a winner (the old behaviour)."""
    _species, entry = _entry(db_session)
    lot_a = make_lot(db_session, method="b3lyp", basis="6-31g")
    lot_b = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    _opt(db_session, entry, lot_a, -100.0)
    _opt(db_session, entry, lot_b, -90.0)
    response = client.get(
        f"{LOOKUP}/calculations", params={"species_entry_id": entry.id, "selection": "lowest_energy"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "lowest_energy_across_levels"
    methods = sorted(level["method"] for level in body["context"]["levels"])
    assert methods == ["b3lyp", "wb97xd"]
    assert all(level["level_of_theory_ref"] for level in body["context"]["levels"])
    assert "filter to one level" in body["detail"] and "select endpoint" in body["detail"]
    # Filtering to one level answers it.
    ok = client.get(
        f"{LOOKUP}/calculations",
        params={"species_entry_id": entry.id, "selection": "lowest_energy", "method": "wb97xd", "basis": "def2tzvp"},
    )
    assert ok.status_code == 200
    assert len(_calc_ids(ok.json())) == 1


def test_lowest_energy_within_one_level_is_labelled_as_the_requests_mode(client, db_session):
    _species, entry = _entry(db_session)
    lot = make_lot(db_session)
    _opt(db_session, entry, lot, -90.0)
    low = _opt(db_session, entry, lot, -100.0)
    body = client.get(
        f"{LOOKUP}/calculations", params={"species_entry_id": entry.id, "selection": "lowest_energy"}
    ).json()
    assert _calc_ids(body) == [low.id]
    selected_by = body["selected_by"]
    assert selected_by["mode"] == "lowest_energy"
    assert "not by a scientific preference" in selected_by["statement"]
    assert selected_by["groups"] == [
        {"species_entry_id": entry.id, "entry_listed": None, "candidates": 2, "chosen": 1, "not_compared": 0, "tied": False}
    ]
    assert "selected_by" in client.get(f"{LOOKUP}/calculations", params={"species_entry_id": entry.id}).json()
    assert client.get(f"{LOOKUP}/calculations", params={"species_entry_id": entry.id}).json()["selected_by"] is None


def test_an_exact_tie_returns_every_tied_record_and_says_so(client, db_session):
    """Kills: picking one of the tied records by id."""
    _species, entry = _entry(db_session)
    lot = make_lot(db_session)
    tied = [_opt(db_session, entry, lot, -100.0), _opt(db_session, entry, lot, -100.0)]
    _opt(db_session, entry, lot, -50.0)
    body = client.get(
        f"{LOOKUP}/calculations", params={"species_entry_id": entry.id, "selection": "lowest_energy"}
    ).json()
    assert sorted(_calc_ids(body)) == sorted(c.id for c in tied)
    group = body["selected_by"]["groups"][0]
    assert group["tied"] is True and group["chosen"] == 2 and group["candidates"] == 3


@pytest.mark.parametrize("mode,expected_age", [("latest", 1), ("earliest", 30)])
def test_latest_and_earliest_choose_by_deposition_time_only(mode, expected_age, client, db_session):
    _species, entry = _entry(db_session)
    lot = make_lot(db_session)
    calcs = {age: _opt(db_session, entry, lot, -100.0 + age, age_days=age) for age in (1, 10, 30)}
    body = client.get(f"{LOOKUP}/calculations", params={"species_entry_id": entry.id, "selection": mode}).json()
    assert _calc_ids(body) == [calcs[expected_age].id]
    assert body["selected_by"]["mode"] == mode
    assert body["selected_by"]["groups"][0]["tied"] is False


def test_a_record_the_profile_hides_cannot_win_a_selection(client, db_session):
    """The lowest energy is on a rejected calculation (exploratory default) / an unreviewed one (curated): that record
    is simply absent, so the next one wins. Kills: comparing records the profile hides."""
    _species, entry = _entry(db_session)
    lot = make_lot(db_session)
    _opt(db_session, entry, lot, -200.0, status=S.rejected)  # lowest, but rejected
    _opt(db_session, entry, lot, -150.0)  # unreviewed
    approved = _opt(db_session, entry, lot, -100.0, status=S.approved)
    other = _opt(db_session, entry, lot, -120.0, status=S.approved)

    exploratory = client.get(
        f"{LOOKUP}/calculations", params={"species_entry_id": entry.id, "selection": "lowest_energy"}
    ).json()
    assert len(_calc_ids(exploratory)) == 1
    assert exploratory["results"][0]["summary"]["energy_hartree"] == -150.0

    curated = client.get(
        f"{LOOKUP}/calculations",
        params={"species_entry_id": entry.id, "selection": "lowest_energy", "profile": "curated"},
    ).json()
    assert _calc_ids(curated) == [other.id]  # -120 beats -100; -150 and -200 are absent
    assert curated["selected_by"]["groups"][0]["candidates"] == 2
    assert approved.id not in _calc_ids(curated)


def test_the_species_calculation_lookup_searches_every_entry_and_selects_within_each(client, db_session):
    """Kills: the silent pick of the species' first entry. Entries are different structures, so a mode applies within
    each and their energies are never compared."""
    species = make_species(db_session, smiles="C=O", inchi_key="WSFSSNUMVMOOMR-UHFFFAOYSA-N")
    first = make_species_entry(db_session, species, stereo_label="a")
    second = make_species_entry(db_session, species, stereo_label="b")
    lot = make_lot(db_session)
    a = _opt(db_session, first, lot, -100.0)
    _opt(db_session, first, lot, -90.0)
    b = _opt(db_session, second, lot, -50.0)
    params = {"smiles": "C=O", "charge": 0, "multiplicity": 1, "type": "opt", "method": lot.method, "basis": lot.basis}
    everything = client.get(f"{LOOKUP}/species-calculation", params=params).json()
    assert len(_calc_ids(everything)) == 3
    assert [r["id"] for r in everything["results"] if r["resource_type"] == "species_entry"] != []
    chosen = client.get(f"{LOOKUP}/species-calculation", params={**params, "selection": "lowest_energy"}).json()
    assert sorted(_calc_ids(chosen)) == sorted([a.id, b.id])
    assert {g["species_entry_id"] for g in chosen["selected_by"]["groups"]} == {first.id, second.id}


def test_an_unknown_selection_mode_is_a_validation_error(client, db_session):
    _species, entry = _entry(db_session)
    response = client.get(f"{LOOKUP}/calculations", params={"species_entry_id": entry.id, "selection": "best"})
    assert response.status_code == 422


def test_identity_counts_follow_the_visibility_of_the_records_they_count(client, db_session):
    species = make_species(db_session, smiles="C=O", inchi_key="WSFSSNUMVMOOMR-UHFFFAOYSA-N")
    entry = make_species_entry(db_session, species)
    thermos = [make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-1.0 - i) for i in range(3)]
    set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=thermos[0].id, status=S.rejected)
    set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=thermos[1].id, status=S.approved)
    set_review(db_session, record_type=SubmissionRecordType.species_entry, record_id=entry.id, status=S.approved)
    params = {"smiles": "C=O", "charge": 0, "multiplicity": 1}

    def count(**extra):
        body = client.get(f"{LOOKUP}/species", params={**params, **extra}).json()
        return next(r for r in body["results"] if r["resource_type"] == "species_entry")["summary"]["thermo_count"]

    assert count() == 2
    assert count(include_rejected="true") == 3
    assert count(profile="curated") == 1
