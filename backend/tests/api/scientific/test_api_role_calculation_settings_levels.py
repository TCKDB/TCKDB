"""The settings behind a level belong to a calculation that SUPPLIED that level, under every review profile.

Each test names the mutation it kills. The curated profile hides rejected and deprecated calculations (ADR 0023): a level
is nulled only when all its suppliers are hidden, and the settings must then follow the same suppliers, not whichever
calculation of the role happens to be first.
"""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    StatmechCalculationRole,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.thermo import ThermoSourceCalculation
from tests.api.test_api_composite_program_run import _composite, _deposit_conformer, _thermo_payload
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_statmech_source_calculation,
    make_calculation,
    make_lot,
    make_software_release,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)

R = StatmechCalculationRole
INCLUDE = "calculation_settings,source_calculations"


def _world(db_session):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("RLV")))
    return entry, make_lot(db_session, method="b3lyp", basis="def2tzvp"), make_lot(db_session, method="wb97xd", basis="def2tzvp")


def _calc(db_session, entry, ctype, lot, *, status=None):
    release = make_software_release(db_session, name="orca", version="6.0.0")
    calc = make_calculation(db_session, type=ctype, species_entry_id=entry.id, lot_id=lot.id, software_release_id=release.id)
    if status is not None:
        set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id, status=status)
    return calc


def _statmech(db_session, entry, links):
    sm = make_statmech(db_session, species_entry=entry)
    for calc, role in links:
        attach_statmech_source_calculation(db_session, statmech=sm, calculation=calc, role=role)
    # Approve last: an approved statmech is immutable, so its links must exist first.
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=sm.id, status=RecordReviewStatus.approved)
    return sm


def _read(client, sm, profile):
    r = client.get(f"/api/v1/scientific/statmech/{sm.public_ref}", params={"include": INCLUDE, "profile": profile})
    assert r.status_code == 200, r.text
    return r.json()["record"]


def _by_role(record):
    return {item["role"]: item for item in record["calculation_settings"]}


def test_curated_attaches_a_supplier_of_the_derived_level_not_the_first_shown_calculation(client, db_session):
    """opt A (level X, rejected), opt B (level Y), opt C (level X). Curated geometry is X (C still supplies it), so the
    settings must be C's. Kills: taking the first shown calculation of the role regardless of level (that is B, level Y)."""
    entry, x, y = _world(db_session)
    a = _calc(db_session, entry, CalculationType.opt, x, status=RecordReviewStatus.rejected)
    b = _calc(db_session, entry, CalculationType.opt, y)
    c = _calc(db_session, entry, CalculationType.opt, x)
    sm = _statmech(db_session, entry, [(a, R.opt), (b, R.opt), (c, R.opt)])

    curated = _read(client, sm, "curated")
    assert curated["levels"]["geometry"]["method"].lower() == "b3lyp"
    assert _by_role(curated)["geometry"]["calculation_ref"] == c.public_ref
    exploratory = _read(client, sm, "exploratory")
    assert _by_role(exploratory)["geometry"]["calculation_ref"] == a.public_ref


def test_a_level_whose_only_suppliers_are_withheld_is_omitted_but_an_unlinked_level_is_listed(client, db_session):
    """Kills: omitting any level when some calculation of the role is hidden (a level nothing was ever linked for must
    still say 'No calculation linked', including when the only optimisation is hidden and never carried frequencies),
    and not omitting one whose every supplier is hidden."""
    entry, x, _ = _world(db_session)
    opt = _calc(db_session, entry, CalculationType.opt, x)
    freq = _calc(db_session, entry, CalculationType.freq, x, status=RecordReviewStatus.rejected)
    with_freq = _statmech(db_session, entry, [(opt, R.opt), (freq, R.freq)])
    opt_only = _statmech(db_session, entry, [(opt, R.opt)])

    hidden = _by_role(_read(client, with_freq, "curated"))
    assert list(hidden) == ["geometry", "energy"]
    assert list(_by_role(_read(client, with_freq, "exploratory"))) == ["geometry", "frequency", "energy"]
    unlinked = _by_role(_read(client, opt_only, "curated"))
    assert unlinked["frequency"]["absent_reason"] == "not_linked" and unlinked["frequency"]["calculation_ref"] is None

    # A hidden optimisation that never carried frequencies supplied no frequency level, so that level is not omitted.
    hidden_opt = _calc(db_session, entry, CalculationType.opt, x, status=RecordReviewStatus.rejected)
    only_hidden = _statmech(db_session, entry, [(hidden_opt, R.opt)])
    roles = _by_role(_read(client, only_hidden, "curated"))
    assert "geometry" not in roles
    assert (roles["frequency"]["calculation_ref"], roles["frequency"]["absent_reason"]) == (None, "not_linked")


def test_frequency_comes_from_an_opt_only_when_that_opt_carries_frequencies(client, db_session):
    """Kills: offering any optimisation as the frequency calculation."""
    entry, x, _ = _world(db_session)
    carrying = _calc(db_session, entry, CalculationType.opt, x)
    attach_freq_result(db_session, calculation=carrying, frequencies_cm1=[100.0, 200.0])
    plain = _calc(db_session, entry, CalculationType.opt, x)
    with_freq = _statmech(db_session, entry, [(carrying, R.opt)])
    without = _statmech(db_session, entry, [(plain, R.opt)])

    assert _by_role(_read(client, with_freq, "exploratory"))["frequency"]["calculation_ref"] == carrying.public_ref
    none = _by_role(_read(client, without, "exploratory"))["frequency"]
    assert (none["calculation_ref"], none["absent_reason"]) == (None, "not_linked")


def test_a_composite_recipe_level_is_marked_and_the_composite_answers_the_energy(client):
    """Kills: attaching the composite calculation to the geometry or frequency level, or filling them from elsewhere."""
    resp = _deposit_conformer(client, primary=_composite())
    assert resp.status_code == 201, resp.text
    composite_id = resp.json()["primary_calculation"]["calculation_id"]
    thermo = client.post("/api/v1/uploads/thermo", json=_thermo_payload([(composite_id, "composite")]))
    assert thermo.status_code == 201, thermo.text
    entry_id = thermo.json()["species_entry_id"]

    read = client.get(f"/api/v1/scientific/species-entries/{entry_id}/thermo", params={"include": "calculation_settings"})
    roles = _by_role(read.json()["records"][0])
    assert (roles["geometry"]["calculation_ref"], roles["geometry"]["absent_reason"]) == (None, "composite_recipe")
    assert (roles["frequency"]["calculation_ref"], roles["frequency"]["absent_reason"]) == (None, "composite_recipe")
    assert roles["energy"]["calculation_type"] == "composite" and roles["energy"]["calculation_ref"]


def test_thermo_follows_the_same_suppliers_under_curated(client, db_session):
    """Same repro on the thermo read. Kills: a thermo-only copy of the pick rule."""
    entry, x, y = _world(db_session)
    a = _calc(db_session, entry, CalculationType.opt, x, status=RecordReviewStatus.rejected)
    b = _calc(db_session, entry, CalculationType.opt, y)
    c = _calc(db_session, entry, CalculationType.opt, x)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    for calc in (a, b, c):
        db_session.add(ThermoSourceCalculation(thermo_id=thermo.id, calculation_id=calc.id, role=ThermoCalculationRole.opt))
    db_session.flush()
    set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id, status=RecordReviewStatus.approved)

    r = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo",
        params={"include": "calculation_settings", "profile": "curated"},
    )
    assert r.status_code == 200, r.text
    assert _by_role(r.json()["records"][0])["geometry"]["calculation_ref"] == c.public_ref


def test_statmech_search_and_detail_carry_the_section(client, db_session):
    entry, x, _ = _world(db_session)
    opt = _calc(db_session, entry, CalculationType.opt, x)
    sm = _statmech(db_session, entry, [(opt, R.opt)])
    search = client.get("/api/v1/scientific/statmech/search", params={"species_entry_ref": entry.public_ref, "include": "calculation_settings"})
    assert search.status_code == 200, search.text
    assert _by_role(search.json()["records"][0])["geometry"]["calculation_ref"] == opt.public_ref
    detail = _read(client, sm, "exploratory")
    assert _by_role(detail)["geometry"]["calculation_ref"] == opt.public_ref
