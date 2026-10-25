"""A filter matches through linked evidence unless the evidence rule withholds it (ADR 0023, step 4).

The thermo and kinetics level-of-theory and software filters match when ANY linked source calculation matches. Under the
``curated`` profile a REJECTED or DEPRECATED calculation is not a candidate for that match: a record whose only level-B
(or ORCA) link is such a calculation must not be found by asking for level B (or ORCA). A not-reviewed (or under-review)
calculation is evidence like any other and matches, with its badge. Exploratory withholds nothing. Each test names what
it kills.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    PhaseKind,
    RecordReviewStatus,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.kinetics import KineticsSourceCalculation
from tests.services.scientific_read._factories import (
    attach_thermo_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_software_release,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)

API = "/api/v1/scientific"

#: Review state of the second link, and whether the curated profile lets it take part in a filter match.
LINKS = [
    pytest.param(None, True, id="not_reviewed"),
    pytest.param(RecordReviewStatus.under_review, True, id="under_review"),
    pytest.param(RecordReviewStatus.rejected, False, id="rejected"),
    pytest.param(RecordReviewStatus.deprecated, False, id="deprecated"),
]


def _approve(db_session, record_type, record_id):
    set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.approved)


def _mark(db_session, calculation, status):
    if status is not None:
        set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calculation.id, status=status)


def _thermo_world(db_session, freq_status=None):
    """An approved thermo: sp at level A / gaussian (approved), freq at level B / orca (``freq_status``)."""
    species = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("CH"))
    entry = make_species_entry(db_session, species)
    lot_a, lot_b = make_lot(db_session, method="ca-method"), make_lot(db_session, method="cb-method")
    gaussian = make_software_release(db_session, name="gaussian", version="16")
    orca = make_software_release(db_session, name="orca", version="6")
    sp = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot_a.id, software_release_id=gaussian.id
    )
    freq = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id, lot_id=lot_b.id, software_release_id=orca.id
    )
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    thermo.phase = PhaseKind.gas
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=sp, role=ThermoCalculationRole.sp)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=freq, role=ThermoCalculationRole.freq)
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)
    _approve(db_session, SubmissionRecordType.species_entry, entry.id)  # a curated search needs a shown entry
    _approve(db_session, SubmissionRecordType.calculation, sp.id)
    _mark(db_session, freq, freq_status)
    return entry, thermo, lot_a, lot_b


def _refs(response):
    assert response.status_code == 200, response.text
    return [r["thermo_ref"] if "thermo_ref" in r else r["thermo"]["thermo_ref"] for r in response.json()["records"]]


def _expect(matches, thermo):
    return [thermo.public_ref] if matches else []


@pytest.mark.parametrize("freq_status,matches", LINKS)
def test_a_curated_thermo_read_level_filter(client, db_session, freq_status, matches):
    """Kills: matching through a rejected/deprecated link (leak), or refusing a not-reviewed one (the earlier rule)."""
    entry, thermo, lot_a, lot_b = _thermo_world(db_session, freq_status)
    url = f"{API}/species-entries/{entry.public_ref}/thermo"

    assert _refs(client.get(url, params={"profile": "curated", "level_of_theory_id": lot_a.id})) == [thermo.public_ref]
    assert _refs(client.get(url, params={"profile": "curated", "level_of_theory_id": lot_b.id})) == _expect(
        matches, thermo
    )
    # Exploratory withholds nothing as evidence, so it finds the record whatever the link's state.
    assert _refs(client.get(url, params={"profile": "exploratory", "level_of_theory_id": lot_b.id})) == [
        thermo.public_ref
    ]


@pytest.mark.parametrize("freq_status,matches", LINKS)
def test_a_curated_thermo_read_software_filter(client, db_session, freq_status, matches):
    """Kills: the software filter reading withheld links, or refusing visible not-reviewed ones."""
    entry, thermo, *_ = _thermo_world(db_session, freq_status)
    url = f"{API}/species-entries/{entry.public_ref}/thermo"

    assert _refs(client.get(url, params={"profile": "curated", "software": "gaussian"})) == [thermo.public_ref]
    assert _refs(client.get(url, params={"profile": "curated", "software": "orca"})) == _expect(matches, thermo)
    assert _refs(client.get(url, params={"profile": "exploratory", "software": "orca"})) == [thermo.public_ref]


@pytest.mark.parametrize("freq_status,matches", LINKS)
def test_a_curated_thermo_search_filters(client, db_session, freq_status, matches):
    """Kills: the same on /thermo/search, which composes the entry read."""
    entry, thermo, lot_a, lot_b = _thermo_world(db_session, freq_status)
    base = {"smiles": "CC"}

    for filt, other_value, approved_value in (
        ("level_of_theory_id", lot_b.id, lot_a.id),
        ("software", "orca", "gaussian"),
    ):
        curated_other = client.get(f"{API}/thermo/search", params={**base, "profile": "curated", filt: other_value})
        assert _refs(curated_other) == _expect(matches, thermo), filt
        curated_approved = client.get(f"{API}/thermo/search", params={**base, "profile": "curated", filt: approved_value})
        assert _refs(curated_approved) == [thermo.public_ref], filt
        exploratory = client.get(f"{API}/thermo/search", params={**base, "profile": "exploratory", filt: other_value})
        assert _refs(exploratory) == [thermo.public_ref], filt


@pytest.mark.parametrize("freq_status,matches", LINKS)
def test_a_curated_kinetics_read_filters(client, db_session, freq_status, matches):
    """The kinetics twin: withheld links never match; not-reviewed ones do. Pinned so the two cannot drift apart."""
    rs = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("CKR"))
    ps = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("CKP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    reactant_entry = make_species_entry(db_session, rs)
    entry = make_reaction_entry(
        db_session, reaction=chem, reactant_entries=[reactant_entry], product_entries=[make_species_entry(db_session, ps)]
    )
    lot_a, lot_b = make_lot(db_session, method="cka-method"), make_lot(db_session, method="ckb-method")
    gaussian = make_software_release(db_session, name="gaussian", version="16")
    orca = make_software_release(db_session, name="orca", version="6")
    ts = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=reactant_entry.id,
        lot_id=lot_a.id,
        software_release_id=gaussian.id,
    )
    freq = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=reactant_entry.id,
        lot_id=lot_b.id,
        software_release_id=orca.id,
    )
    kinetics = make_kinetics(db_session, reaction_entry=entry)
    db_session.add_all(
        [
            KineticsSourceCalculation(
                kinetics_id=kinetics.id, calculation_id=ts.id, role=KineticsCalculationRole.ts_energy
            ),
            KineticsSourceCalculation(kinetics_id=kinetics.id, calculation_id=freq.id, role=KineticsCalculationRole.freq),
        ]
    )
    db_session.flush()
    _approve(db_session, SubmissionRecordType.kinetics, kinetics.id)
    _approve(db_session, SubmissionRecordType.calculation, ts.id)
    _mark(db_session, freq, freq_status)

    def kinetics_refs(url, **params):
        response = client.get(url, params=params)
        assert response.status_code == 200, response.text
        return [
            r["kinetics_ref"] if "kinetics_ref" in r else r["kinetics"]["kinetics_ref"]
            for r in response.json()["records"]
        ]

    read = f"{API}/reaction-entries/{entry.public_ref}/kinetics"
    search = f"{API}/kinetics/search"
    for url, extra in ((read, {}), (search, {"reactants": "CC"})):
        for filt, other_value in (("level_of_theory_id", lot_b.id), ("software", "orca")):
            curated = kinetics_refs(url, profile="curated", **extra, **{filt: other_value})
            assert curated == ([kinetics.public_ref] if matches else []), (url, filt)
            assert kinetics_refs(url, profile="exploratory", **extra, **{filt: other_value}) == [kinetics.public_ref]
