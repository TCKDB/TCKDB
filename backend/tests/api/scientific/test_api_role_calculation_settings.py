"""``include=calculation_settings`` on thermo and statmech reads: the settings behind the geometry, frequency and energy levels.

Each level lists the calculation the record itself links for it and that calculation's grid and SCF convergence with
their evidence state (built by the same code as the calculation read). A level with no linked calculation is absent,
never filled from a sibling record or an unlinked calculation.
"""

from __future__ import annotations

import hashlib

import pytest

from app.db.models.calculation import CalculationArtifact, CalculationParameter, CalculationParameterVocab
from app.db.models.common import (
    ArtifactKind,
    CalculationType,
    ParameterSource,
    ParameterStatementKind,
    StatmechCalculationRole,
    ThermoCalculationRole,
)
from app.db.models.thermo import ThermoSourceCalculation
from tests.services.scientific_read._factories import (
    attach_statmech_source_calculation,
    make_calculation,
    make_lot,
    make_software_release,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    next_inchi_key,
)

_PARAMS = "calculation_settings"


def _entry(db_session):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("RSET")))


def _calc(db_session, entry, ctype, *, software="orca", version="6.0.0", log=False, method="b3lyp"):
    lot = make_lot(db_session, method=method, basis="def2tzvp")
    release = make_software_release(db_session, name=software, version=version) if software else None
    calc = make_calculation(
        db_session,
        type=ctype,
        species_entry_id=entry.id,
        lot_id=lot.id,
        software_release_id=release.id if release else None,
    )
    if log:
        sha = hashlib.sha256(f"log-{calc.id}".encode()).hexdigest()
        db_session.add(
            CalculationArtifact(
                calculation_id=calc.id, kind=ArtifactKind.output_log, uri=f"s3://t/{sha}", sha256=sha, bytes=3, filename="job.log"
            )
        )
        db_session.flush()
    return calc


def _param(db_session, calc, key, value, *, reported=False, typed=False):
    if db_session.get(CalculationParameterVocab, key) is None:
        db_session.add(CalculationParameterVocab(canonical_key=key))
        db_session.flush()
    db_session.add(
        CalculationParameter(
            calculation_id=calc.id,
            raw_key=key,
            raw_value=value,
            canonical_key=key,
            canonical_value=value,
            source=ParameterSource.upload if typed else ParameterSource.parser,
            artifact_kind=None if typed else ArtifactKind.output_log,
            statement_kind=None if typed else (ParameterStatementKind.reported if reported else ParameterStatementKind.requested),
        )
    )
    db_session.flush()


def _link_thermo(db_session, thermo, calc, role):
    db_session.add(ThermoSourceCalculation(thermo_id=thermo.id, calculation_id=calc.id, role=role))
    db_session.flush()


def _thermo_levels(client, entry, **params):
    qs = "&".join(f"{k}={v}" for k, v in {"include": _PARAMS, **params}.items())
    body = client.get(f"/api/v1/scientific/species-entries/{entry.id}/thermo?{qs}").json()
    return body["records"]


def _by_role(block):
    return {item["role"]: item for item in block}


def _states(item):
    return {s["setting"]: s["state"] for s in item["settings"]}


@pytest.fixture
def three_levels(db_session):
    """An optimisation, a frequency and a single point, each in a different evidence state."""
    entry = _entry(db_session)
    opt = _calc(db_session, entry, CalculationType.opt, log=True)
    _param(db_session, opt, "grid.angular_max", "302", reported=True)
    freq = _calc(db_session, entry, CalculationType.freq, log=True)
    sp = _calc(db_session, entry, CalculationType.sp, software=None)
    _param(db_session, sp, "grid.quality", "ultrafine", typed=True)
    return entry, opt, freq, sp


def test_thermo_lists_each_level_with_its_own_calculation_and_state(client, db_session, three_levels):
    entry, opt, freq, sp = three_levels
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    _link_thermo(db_session, thermo, opt, ThermoCalculationRole.opt)
    _link_thermo(db_session, thermo, freq, ThermoCalculationRole.freq)
    _link_thermo(db_session, thermo, sp, ThermoCalculationRole.sp)

    [record] = _thermo_levels(client, entry)
    roles = _by_role(record["calculation_settings"])
    assert list(roles) == ["geometry", "frequency", "energy"]
    # Distinct calculation per level: swapping the role mapping makes these refs (and the states) differ.
    assert roles["geometry"]["calculation_ref"] == opt.public_ref
    assert roles["frequency"]["calculation_ref"] == freq.public_ref
    assert roles["energy"]["calculation_ref"] == sp.public_ref
    assert roles["geometry"]["calculation_type"] == "opt"
    assert roles["frequency"]["calculation_type"] == "freq"
    assert roles["energy"]["calculation_type"] == "sp"
    assert all(item["absent_reason"] is None for item in roles.values())
    # opt: the log reports the grid; an ORCA opt has an assumed SCF default and the log is present.
    assert _states(roles["geometry"]) == {"grid": "reported", "scf_convergence": "assumed_default"}
    assert roles["geometry"]["settings"][0]["reported"] == [{"key": "grid.angular_max", "value": "302"}]
    # freq: nothing stated, a log exists: the grid default is assumed, ORCA documents no frequency-job SCF default.
    assert _states(roles["frequency"]) == {"grid": "assumed_default", "scf_convergence": "not_stated"}
    # sp with no declared program: the depositor-typed grid is a request; nothing else is known.
    assert _states(roles["energy"]) == {"grid": "requested", "scf_convergence": "not_stated"}
    grid = roles["energy"]["settings"][0]
    assert (grid["requested_value"], grid["requested_from"]) == ("ultrafine", "typed")


def test_the_section_is_absent_unless_requested(client, db_session, three_levels):
    entry, opt, _, _ = three_levels
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    _link_thermo(db_session, thermo, opt, ThermoCalculationRole.opt)
    [plain] = client.get(f"/api/v1/scientific/species-entries/{entry.id}/thermo").json()["records"]
    assert "calculation_settings" not in plain
    [asked] = _thermo_levels(client, entry)
    assert "calculation_settings" in asked


def test_a_level_with_no_linked_calculation_is_absent_not_filled(client, db_session, three_levels):
    entry, opt, _, _ = three_levels
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    _link_thermo(db_session, thermo, opt, ThermoCalculationRole.opt)

    [record] = _thermo_levels(client, entry)
    roles = _by_role(record["calculation_settings"])
    assert roles["geometry"]["calculation_ref"] == opt.public_ref
    # An optimisation's final energy is its own single point, so the opt answers the energy level too; frequency is not
    # claimed by an opt that stores no frequencies.
    assert roles["energy"]["calculation_ref"] == opt.public_ref
    assert roles["frequency"] == {
        "role": "frequency",
        "calculation_ref": None,
        "calculation_type": None,
        "absent_reason": "not_linked",
        "settings": [],
    }


def test_a_record_with_no_links_lists_all_three_levels_as_absent(client, db_session):
    entry = _entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry)
    [record] = _thermo_levels(client, entry)
    assert [(i["role"], i["absent_reason"], i["settings"]) for i in record["calculation_settings"]] == [
        ("geometry", "not_linked", []),
        ("frequency", "not_linked", []),
        ("energy", "not_linked", []),
    ]


def test_no_borrowing_from_a_sibling_that_the_record_does_not_link(client, db_session, three_levels):
    """A sibling thermo and an unlinked statmech on the same entry carry settings; the record that links neither shows none."""
    entry, opt, freq, sp = three_levels
    linked = make_thermo_scalar(db_session, species_entry=entry)
    _link_thermo(db_session, linked, opt, ThermoCalculationRole.opt)
    sibling = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-99.0)
    _link_thermo(db_session, sibling, freq, ThermoCalculationRole.freq)
    _link_thermo(db_session, sibling, sp, ThermoCalculationRole.sp)
    unlinked_statmech = make_statmech(db_session, species_entry=entry)
    attach_statmech_source_calculation(db_session, statmech=unlinked_statmech, calculation=freq, role=StatmechCalculationRole.freq)

    records = {r["thermo_ref"]: r for r in _thermo_levels(client, entry)}
    shown = {i["calculation_ref"] for i in records[linked.public_ref]["calculation_settings"]}
    assert shown == {opt.public_ref, None}
    assert freq.public_ref not in shown and sp.public_ref not in shown
    assert _by_role(records[linked.public_ref]["calculation_settings"])["frequency"]["absent_reason"] == "not_linked"
    # The sibling shows its own links and not the first record's optimisation as geometry.
    sib = _by_role(records[sibling.public_ref]["calculation_settings"])
    assert sib["geometry"]["calculation_ref"] is None
    assert (sib["frequency"]["calculation_ref"], sib["energy"]["calculation_ref"]) == (freq.public_ref, sp.public_ref)


def test_a_thermo_inherits_levels_only_through_its_own_statmech_link(client, db_session, three_levels):
    entry, opt, freq, sp = three_levels
    statmech = make_statmech(db_session, species_entry=entry)
    attach_statmech_source_calculation(db_session, statmech=statmech, calculation=opt, role=StatmechCalculationRole.opt)
    attach_statmech_source_calculation(db_session, statmech=statmech, calculation=freq, role=StatmechCalculationRole.freq)
    attach_statmech_source_calculation(db_session, statmech=statmech, calculation=sp, role=StatmechCalculationRole.sp)
    linked = make_thermo_scalar(db_session, species_entry=entry, statmech_id=statmech.id)
    unlinked = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-1.0)

    records = {r["thermo_ref"]: r for r in _thermo_levels(client, entry)}
    via_link = _by_role(records[linked.public_ref]["calculation_settings"])
    assert [via_link[r]["calculation_ref"] for r in ("geometry", "frequency", "energy")] == [
        opt.public_ref,
        freq.public_ref,
        sp.public_ref,
    ]
    assert {i["absent_reason"] for i in records[unlinked.public_ref]["calculation_settings"]} == {"not_linked"}


def test_assumed_default_without_a_log_is_labelled_so(client, db_session):
    entry = _entry(db_session)
    opt = _calc(db_session, entry, CalculationType.opt, log=False)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    _link_thermo(db_session, thermo, opt, ThermoCalculationRole.opt)
    [record] = _thermo_levels(client, entry)
    geometry = _by_role(record["calculation_settings"])["geometry"]
    assert _states(geometry) == {"grid": "assumed_default_no_output_log", "scf_convergence": "assumed_default_no_output_log"}
    assert [s["assumed_value"] for s in geometry["settings"]] == ["defgrid2", "tight"]


def test_two_values_in_one_rung_are_marked_conflicting(client, db_session):
    entry = _entry(db_session)
    opt = _calc(db_session, entry, CalculationType.opt, log=True)
    _param(db_session, opt, "grid.quality", "fine", typed=True)
    _param(db_session, opt, "grid.quality", "ultrafine", typed=True)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    _link_thermo(db_session, thermo, opt, ThermoCalculationRole.opt)
    [record] = _thermo_levels(client, entry)
    grid = _by_role(record["calculation_settings"])["geometry"]["settings"][0]
    assert (grid["state"], grid["conflicting"], grid["requested_value"]) == ("requested", True, None)


def test_two_single_points_at_different_levels_leave_the_energy_ambiguous(client, db_session):
    entry = _entry(db_session)
    sp_a = _calc(db_session, entry, CalculationType.sp, method="b3lyp")
    sp_b = _calc(db_session, entry, CalculationType.sp, method="wb97xd")
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    _link_thermo(db_session, thermo, sp_a, ThermoCalculationRole.sp)
    _link_thermo(db_session, thermo, sp_b, ThermoCalculationRole.sp)
    [record] = _thermo_levels(client, entry)
    energy = _by_role(record["calculation_settings"])["energy"]
    assert (energy["calculation_ref"], energy["absent_reason"]) == (None, "ambiguous")


def test_thermo_search_carries_the_section_inside_the_thermo_wrapper(client, db_session, three_levels):
    entry, opt, freq, sp = three_levels
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    _link_thermo(db_session, thermo, opt, ThermoCalculationRole.opt)
    _link_thermo(db_session, thermo, freq, ThermoCalculationRole.freq)
    _link_thermo(db_session, thermo, sp, ThermoCalculationRole.sp)
    inchi_key = entry.species.inchi_key
    asked = client.get(f"/api/v1/scientific/thermo/search?inchi_key={inchi_key}&include={_PARAMS}").json()
    plain = client.get(f"/api/v1/scientific/thermo/search?inchi_key={inchi_key}").json()
    [rec] = asked["records"]
    assert [i["calculation_ref"] for i in rec["thermo"]["calculation_settings"]] == [opt.public_ref, freq.public_ref, sp.public_ref]
    assert "calculation_settings" not in plain["records"][0]["thermo"]


# ---------------------------------------------------------------------------
# statmech
# ---------------------------------------------------------------------------


def _statmech_levels(client, entry):
    body = client.get(f"/api/v1/scientific/species-entries/{entry.id}/statmech?include={_PARAMS}").json()
    return body["records"]


def test_statmech_lists_its_source_calculations_by_level(client, db_session, three_levels):
    entry, opt, freq, sp = three_levels
    statmech = make_statmech(db_session, species_entry=entry)
    attach_statmech_source_calculation(db_session, statmech=statmech, calculation=opt, role=StatmechCalculationRole.opt)
    attach_statmech_source_calculation(db_session, statmech=statmech, calculation=freq, role=StatmechCalculationRole.freq)
    attach_statmech_source_calculation(db_session, statmech=statmech, calculation=sp, role=StatmechCalculationRole.sp)

    [record] = _statmech_levels(client, entry)
    roles = _by_role(record["calculation_settings"])
    assert [roles[r]["calculation_ref"] for r in ("geometry", "frequency", "energy")] == [
        opt.public_ref,
        freq.public_ref,
        sp.public_ref,
    ]
    assert _states(roles["geometry"]) == {"grid": "reported", "scf_convergence": "assumed_default"}
    assert _states(roles["frequency"]) == {"grid": "assumed_default", "scf_convergence": "not_stated"}
    assert _states(roles["energy"]) == {"grid": "requested", "scf_convergence": "not_stated"}
    plain = client.get(f"/api/v1/scientific/species-entries/{entry.id}/statmech").json()["records"][0]
    assert "calculation_settings" not in plain


def test_statmech_does_not_borrow_from_a_sibling_statmech_or_a_thermo(client, db_session, three_levels):
    entry, opt, freq, sp = three_levels
    mine = make_statmech(db_session, species_entry=entry)
    attach_statmech_source_calculation(db_session, statmech=mine, calculation=freq, role=StatmechCalculationRole.freq)
    sibling = make_statmech(db_session, species_entry=entry, point_group="D2h")
    attach_statmech_source_calculation(db_session, statmech=sibling, calculation=opt, role=StatmechCalculationRole.opt)
    attach_statmech_source_calculation(db_session, statmech=sibling, calculation=sp, role=StatmechCalculationRole.sp)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    _link_thermo(db_session, thermo, sp, ThermoCalculationRole.sp)

    records = {r["statmech"]["statmech_ref"]: r for r in _statmech_levels(client, entry)}
    roles = _by_role(records[mine.public_ref]["calculation_settings"])
    assert roles["frequency"]["calculation_ref"] == freq.public_ref
    assert (roles["geometry"]["calculation_ref"], roles["geometry"]["absent_reason"]) == (None, "not_linked")
    assert (roles["energy"]["calculation_ref"], roles["energy"]["absent_reason"]) == (None, "not_linked")
