"""A filter never matches through a calculation the profile hides (D6: hidden == unknown == absent).

The thermo and kinetics level-of-theory and software filters match when ANY linked source calculation matches. Under the
``curated`` profile a calculation below the review floor is not a candidate for that match: a record whose only level-B
(or ORCA) link is an unreviewed calculation must not be found by asking for level B (or ORCA). Each test names what it
kills.
"""

from __future__ import annotations

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


def _approve(db_session, record_type, record_id):
    set_review(db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.approved)


def _thermo_world(db_session):
    """An approved thermo: sp at level A / gaussian (approved), freq at level B / orca (NOT reviewed, so hidden)."""
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
    _approve(db_session, SubmissionRecordType.calculation, sp.id)  # the freq calculation stays not_reviewed
    return entry, thermo, lot_a, lot_b


def _refs(response):
    assert response.status_code == 200, response.text
    return [r["thermo_ref"] if "thermo_ref" in r else r["thermo"]["thermo_ref"] for r in response.json()["records"]]


def test_a_curated_thermo_read_does_not_match_a_level_through_a_hidden_calculation(client, db_session):
    """Kills: the level filter reading every linked calculation, hidden ones included."""
    entry, thermo, lot_a, lot_b = _thermo_world(db_session)
    url = f"{API}/species-entries/{entry.public_ref}/thermo"

    assert _refs(client.get(url, params={"profile": "curated", "level_of_theory_id": lot_a.id})) == [thermo.public_ref]
    assert _refs(client.get(url, params={"profile": "curated", "level_of_theory_id": lot_b.id})) == []
    # Hidden equals absent: the exploratory profile, which shows the calculation, does find it.
    assert _refs(client.get(url, params={"profile": "exploratory", "level_of_theory_id": lot_b.id})) == [
        thermo.public_ref
    ]


def test_a_curated_thermo_read_does_not_match_a_software_through_a_hidden_calculation(client, db_session):
    """Kills: the software filter reading every linked calculation, hidden ones included."""
    entry, thermo, *_ = _thermo_world(db_session)
    url = f"{API}/species-entries/{entry.public_ref}/thermo"

    assert _refs(client.get(url, params={"profile": "curated", "software": "gaussian"})) == [thermo.public_ref]
    assert _refs(client.get(url, params={"profile": "curated", "software": "orca"})) == []
    assert _refs(client.get(url, params={"profile": "exploratory", "software": "orca"})) == [thermo.public_ref]


def test_a_curated_thermo_search_does_not_match_through_a_hidden_calculation(client, db_session):
    """Kills: the same leak on /thermo/search, which composes the entry read."""
    entry, thermo, lot_a, lot_b = _thermo_world(db_session)
    base = {"smiles": "CC"}

    for filt, hidden_value, visible_value in (
        ("level_of_theory_id", lot_b.id, lot_a.id),
        ("software", "orca", "gaussian"),
    ):
        curated_hidden = client.get(f"{API}/thermo/search", params={**base, "profile": "curated", filt: hidden_value})
        assert _refs(curated_hidden) == [], filt
        curated_visible = client.get(f"{API}/thermo/search", params={**base, "profile": "curated", filt: visible_value})
        assert _refs(curated_visible) == [thermo.public_ref], filt
        exploratory = client.get(f"{API}/thermo/search", params={**base, "profile": "exploratory", filt: hidden_value})
        assert _refs(exploratory) == [thermo.public_ref], filt


def test_a_curated_kinetics_read_does_not_match_through_a_hidden_calculation(client, db_session):
    """The kinetics twin already filters hidden sources; this pins it so the two cannot drift apart again."""
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
        db_session, type=CalculationType.sp, species_entry_id=reactant_entry.id, lot_id=lot_a.id,
        software_release_id=gaussian.id,
    )
    freq = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=reactant_entry.id, lot_id=lot_b.id,
        software_release_id=orca.id,
    )
    kinetics = make_kinetics(db_session, reaction_entry=entry)
    db_session.add_all(
        [
            KineticsSourceCalculation(
                kinetics_id=kinetics.id, calculation_id=ts.id, role=KineticsCalculationRole.ts_energy
            ),
            KineticsSourceCalculation(
                kinetics_id=kinetics.id, calculation_id=freq.id, role=KineticsCalculationRole.freq
            ),
        ]
    )
    db_session.flush()
    _approve(db_session, SubmissionRecordType.kinetics, kinetics.id)
    _approve(db_session, SubmissionRecordType.calculation, ts.id)

    def kinetics_refs(url, **params):
        response = client.get(url, params=params)
        assert response.status_code == 200, response.text
        return [r["kinetics_ref"] if "kinetics_ref" in r else r["kinetics"]["kinetics_ref"] for r in response.json()["records"]]

    read = f"{API}/reaction-entries/{entry.public_ref}/kinetics"
    search = f"{API}/kinetics/search"
    for url, extra in ((read, {}), (search, {"reactants": "CC"})):
        for filt, hidden_value in (("level_of_theory_id", lot_b.id), ("software", "orca")):
            assert kinetics_refs(url, profile="curated", **extra, **{filt: hidden_value}) == [], (url, filt)
            assert kinetics_refs(url, profile="exploratory", **extra, **{filt: hidden_value}) == [kinetics.public_ref]
