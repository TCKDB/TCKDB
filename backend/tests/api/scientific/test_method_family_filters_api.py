"""The method-family and DFT-rung filters on the level-of-theory, species and transition-state reads, and
``scientific_origin`` on thermo, kinetics and statmech.

A family or a rung classifies a level's method (``app/chemistry/method_families.py``); it is a filter fact, not a measure
of accuracy, and it orders nothing. Each test names what it kills.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.chemistry.method_families import CLASSIFICATION, CLASSIFICATION_REF, MethodClassFilter, MethodFamily
from app.db.models.common import RecordReviewStatus, ScientificOriginKind, SubmissionRecordType
from app.db.models.level_of_theory import LevelOfTheory
from app.services.scientific_read.lot_identity_filters import method_family_matches
from tests.api.scientific.test_api_scientific_transition_states import (
    _attach_calc,
    _make_reaction_with_ts,
    _make_software_release,
)
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
    unique_smiles,
)

BASE = "/api/v1/scientific"
F = MethodFamily


def _used(session, lot):
    """A calculation at *lot*: the level-of-theory reads list only levels something used."""
    entry = make_species_entry(session, make_species(session, smiles=unique_smiles(), inchi_key=next_inchi_key("MF")))
    return make_calculation(session, species_entry_id=entry.id, lot_id=lot.id)


def _methods(body):
    return sorted(r["level_of_theory"]["method"] for r in body["records"])


@pytest.fixture
def levels(db_session):
    """One used level per class: rung 1 to 5, wavefunction, multireference, composite, semiempirical, unknown."""
    names = ["svwn", "pbe", "tpss", "b3lyp", "b2plyp", "ccsd(t)-f12", "casscf", "g4", "pm6", "xyg3"]
    out = {}
    for name in names:
        out[name] = make_lot(db_session, method=name, basis="def2tzvp")
        _used(db_session, out[name])
    return out


# ---------------------------------------------------------------------------------------------------------------
# level of theory
# ---------------------------------------------------------------------------------------------------------------


def test_a_level_of_theory_read_carries_its_family_rung_and_the_classification_version(client, levels):
    body = client.get(f"{BASE}/level-of-theories/browse", params={"limit": 50}).json()
    by_method = {r["level_of_theory"]["method"]: r["level_of_theory"] for r in body["records"]}
    expect = {
        "svwn": ("dft", 1),
        "pbe": ("dft", 2),
        "tpss": ("dft", 3),
        "b3lyp": ("dft", 4),
        "b2plyp": ("dft", 5),
        "ccsd(t)-f12": ("wavefunction", None),
        "casscf": ("multireference", None),
        "g4": ("composite", None),
        "pm6": ("semiempirical", None),
        "xyg3": ("unclassified", None),
    }
    assert {m: (c["method_family"], c["dft_rung"]) for m, c in by_method.items()} == expect
    assert {tuple(c["method_classification"].items()) for c in by_method.values()} == {
        (("name", CLASSIFICATION_REF.name), ("version", CLASSIFICATION_REF.version))
    }
    detail = client.get(f"{BASE}/level-of-theories/{levels['b3lyp'].public_ref}").json()["record"]["level_of_theory"]
    assert (detail["method_family"], detail["dft_rung"]) == ("dft", 4)


@pytest.mark.parametrize(
    ("params", "methods"),
    [
        ({"method_family": ["dft"]}, ["b2plyp", "b3lyp", "pbe", "svwn", "tpss"]),
        ({"method_family": ["wavefunction", "composite"]}, ["ccsd(t)-f12", "g4"]),
        ({"method_family": ["unclassified"]}, ["xyg3"]),
        ({"method_family": ["semiempirical", "multireference", "force_field"]}, ["casscf", "pm6"]),
        ({"dft_rung_min": 4}, ["b2plyp", "b3lyp"]),
        ({"dft_rung_max": 2}, ["pbe", "svwn"]),
        ({"dft_rung_min": 4, "dft_rung_max": 4}, ["b3lyp"]),
        ({"dft_rung_min": 1, "dft_rung_max": 1}, ["svwn"]),
        ({"dft_rung_min": 5, "dft_rung_max": 5}, ["b2plyp"]),
        ({"dft_rung_min": 5, "dft_rung_max": 4}, []),
        ({"method_family": ["wavefunction"], "dft_rung_min": 1}, []),  # a rung bound implies dft
        ({"method_family": ["wavefunction", "dft"], "dft_rung_min": 5}, ["b2plyp"]),
        ({"method_family": ["dft"], "method": "b3lyp"}, ["b3lyp"]),
        ({"method_family": ["dft"], "method": "g4"}, []),
    ],
)
def test_the_level_of_theory_browse_filters_by_family_and_rung(client, levels, params, methods):
    """Kills: a bound that is exclusive, a bound that does not imply dft, an unclassified level kept by a dft filter."""
    body = client.get(f"{BASE}/level-of-theories/browse", params={**params, "limit": 50}).json()
    assert _methods(body) == methods
    echoed = body["request"]["filter"]
    for key, value in params.items():
        assert echoed[key] == (sorted(value) if isinstance(value, list) else value)


def test_a_family_alone_is_a_meaningful_search_filter_and_may_be_posted_as_one_value(client, levels):
    """Kills: the search route refusing a family-only request with missing_filter, or a body that cannot name one family."""
    got = client.get(f"{BASE}/level-of-theories/search", params={"method_family": "composite"})
    assert got.status_code == 200 and _methods(got.json()) == ["g4"]
    posted = client.post(f"{BASE}/level-of-theories/search", json={"method_family": "composite"})
    assert posted.status_code == 200 and _methods(posted.json()) == ["g4"]
    listed = client.post(f"{BASE}/level-of-theories/search", json={"method_family": ["composite", "dft"], "dft_rung_min": 5})
    assert _methods(listed.json()) == ["b2plyp"]


@pytest.mark.parametrize(
    "params", [{"dft_rung_min": 0}, {"dft_rung_max": 6}, {"dft_rung_min": "high"}, {"method_family": "organic"}]
)
def test_a_rung_outside_one_to_five_or_an_unknown_family_is_refused(client, levels, params):
    assert client.get(f"{BASE}/level-of-theories/browse", params=params).status_code == 422


def test_a_dispersion_suffix_and_a_basis_never_change_the_class_in_sql(db_session):
    """Kills: the SQL twin and the Python classification disagreeing on a spelling (suffix, alias, case, unknown)."""
    spellings = [
        "b3lyp",
        "B3LYP-D3BJ",
        "b3lyp-d3(bj)",
        "wB97X-D",
        "wb97x-d3",
        "M06-2X",
        "m06-2x-d3zero",
        "ωB97X-D",
        "b2plyp-d3bj",
        "B97-D",
        "b97-d3",
        "ccsd(t)-f12a",
        "CBSQB3",
        "g4(mp2)",
        "dlpno-ccsd(t)",
        "mrci+q",
        "xyg3",
        "hf-3c",
        "totally-unknown",
        "pbe-d4",
    ]
    lots = {s: make_lot(db_session, method=s, basis="def2tzvp") for s in spellings}
    filters = [
        MethodClassFilter.of([F.dft], None, None),
        MethodClassFilter.of([F.wavefunction, F.composite], None, None),
        MethodClassFilter.of([F.unclassified], None, None),
        MethodClassFilter.of([F.dft, F.unclassified], None, None),
        MethodClassFilter.of(None, 4, 4),
        MethodClassFilter.of(None, 2, 3),
        MethodClassFilter.of(None, 5, None),
        MethodClassFilter.of([F.multireference], None, None),
        MethodClassFilter.of(None, 5, 4),
    ]
    for selected in filters:
        in_sql = set(db_session.scalars(select(LevelOfTheory.method).where(method_family_matches(selected))))
        in_python = {s for s in spellings if selected.matches(s)}
        assert in_sql & set(spellings) == in_python, selected
    assert set(lots) == set(spellings)


def test_every_table_key_is_found_by_its_own_family_in_sql(db_session):
    """Kills: a table entry the SQL filter cannot reach (a typo in a key the Python lookup never exercises)."""
    lots = {key: make_lot(db_session, method=key, basis="def2tzvp") for key in CLASSIFICATION}
    for family in (F.dft, F.wavefunction, F.multireference, F.composite, F.semiempirical, F.force_field):
        in_sql = set(db_session.scalars(select(LevelOfTheory.method).where(method_family_matches(MethodClassFilter.of([family], None, None)))))
        assert {k for k, c in CLASSIFICATION.items() if c.family is family} <= in_sql, family
    for rung in (1, 2, 3, 4, 5):
        in_sql = set(db_session.scalars(select(LevelOfTheory.method).where(method_family_matches(MethodClassFilter.of(None, rung, rung)))))
        assert in_sql & set(lots) == {k for k, c in CLASSIFICATION.items() if c.dft_rung == rung}, rung


def test_a_level_used_only_by_hidden_calculations_is_not_listed_under_curated_whatever_its_family(client, db_session):
    """Hidden equals unknown equals absent: the family filter cannot reveal a level the profile hides."""
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    _used(db_session, lot)  # never approved
    assert _methods(client.get(f"{BASE}/level-of-theories/browse", params={"method_family": "dft"}).json()) == ["b3lyp"]
    curated = client.get(f"{BASE}/level-of-theories/browse", params={"method_family": "dft", "profile": "curated"})
    assert _methods(curated.json()) == []


# ---------------------------------------------------------------------------------------------------------------
# species browse and transition-state browse: the shared calculation provenance filter
# ---------------------------------------------------------------------------------------------------------------


def _species_with_two_calculations(db_session):
    species = make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("SB"))
    entry = make_species_entry(db_session, species)
    _, gaussian = _make_software_release(db_session, name="gaussian", version="16")
    _, orca = _make_software_release(db_session, name="orca", version="6")
    a = make_calculation(db_session, species_entry_id=entry.id, lot_id=make_lot(db_session, method="b3lyp", basis="def2tzvp").id)
    b = make_calculation(db_session, species_entry_id=entry.id, lot_id=make_lot(db_session, method="ccsd(t)-f12", basis="cc-pvtz-f12").id)
    a.software_release_id, b.software_release_id = gaussian.id, orca.id
    db_session.flush()
    return species, entry, a, b


@pytest.mark.parametrize(
    ("params", "matches"),
    [
        ({"method_family": "dft"}, True),
        ({"method_family": ["composite"]}, False),
        ({"dft_rung_min": 4, "dft_rung_max": 4}, True),
        ({"dft_rung_min": 5}, False),
        ({"method_family": "dft", "software": "gaussian"}, True),
        ({"method_family": "dft", "software": "orca"}, False),  # the family of A with the software of B
        ({"method_family": "wavefunction", "software": "orca"}, True),
        ({"dft_rung_min": 4, "software": "orca"}, False),
        ({"method_family": "wavefunction", "method": "b3lyp"}, False),
    ],
)
def test_species_browse_matches_family_and_rung_on_the_same_calculation_as_the_other_provenance_fields(client, db_session, params, matches):
    """Kills: the family filter matched on a different calculation from method/basis/software."""
    species, *_ = _species_with_two_calculations(db_session)
    body = client.get(f"{BASE}/species/browse", params=params).json()
    assert (species.public_ref in {r["species_ref"] for r in body["records"]}) is matches
    for key, value in params.items():
        assert body["request"]["filter"][key] == (sorted(value) if key == "method_family" and isinstance(value, list) else [value] if key == "method_family" else value)


def test_species_browse_does_not_match_through_a_hidden_calculation_under_curated(client, db_session):
    species, entry, a, b = _species_with_two_calculations(db_session)
    set_review(db_session, record_type=SubmissionRecordType.species_entry, record_id=entry.id, status=RecordReviewStatus.approved)
    set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=b.id, status=RecordReviewStatus.approved)

    def refs(**params):
        return {r["species_ref"] for r in client.get(f"{BASE}/species/browse", params=params).json()["records"]}

    assert species.public_ref in refs(method_family="dft", profile="exploratory")
    assert species.public_ref not in refs(method_family="dft", profile="curated")  # calculation A is hidden
    assert species.public_ref not in refs(dft_rung_min=4, profile="curated")
    assert species.public_ref in refs(method_family="wavefunction", profile="curated")


def test_transition_state_browse_matches_family_and_rung_on_one_calculation(client, db_session):
    _, _, _, (tse,) = _make_reaction_with_ts(db_session)
    _, gaussian = _make_software_release(db_session, name="gaussian", version="16")
    _, orca = _make_software_release(db_session, name="orca", version="6")
    _attach_calc(db_session, tse=tse, lot=make_lot(db_session, method="b3lyp", basis="def2tzvp"), software_release=gaussian)
    _attach_calc(db_session, tse=tse, lot=make_lot(db_session, method="ccsd(t)", basis="cc-pvtz"), software_release=orca)

    def refs(**params):
        body = client.get(f"{BASE}/transition-states/browse", params=params).json()
        return {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}

    assert tse.public_ref in refs(method_family="dft")
    assert tse.public_ref in refs(method_family=["wavefunction", "composite"])
    assert tse.public_ref in refs(dft_rung_min=4, software="gaussian")
    assert tse.public_ref not in refs(dft_rung_min=4, software="orca")  # one calculation has to match both
    assert tse.public_ref not in refs(method_family="unclassified")
    assert tse.public_ref not in refs(dft_rung_min=5)


# ---------------------------------------------------------------------------------------------------------------
# thermo and kinetics searches: the query-string forms
# ---------------------------------------------------------------------------------------------------------------


def _thermo_world(db_session, *, origin=ScientificOriginKind.computed):
    entry = make_species_entry(db_session, make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("TQ")))
    return entry, make_thermo_scalar(db_session, species_entry=entry, scientific_origin=origin)


def test_the_thermo_search_and_the_entry_read_take_the_full_provenance_set_as_query_parameters(client, db_session):
    from app.db.models.common import ThermoCalculationRole
    from tests.services.scientific_read._factories import attach_thermo_source_calculation

    entry, thermo = _thermo_world(db_session)
    _, gaussian = _make_software_release(db_session, name="gaussian", version="16")
    calc = make_calculation(db_session, species_entry_id=entry.id, lot_id=make_lot(db_session, method="b3lyp", basis="def2tzvp").id)
    calc.software_release_id = gaussian.id
    db_session.flush()
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=calc, role=ThermoCalculationRole.sp)

    def entry_refs(**params):
        return [r["thermo_ref"] for r in client.get(f"{BASE}/species-entries/{entry.public_ref}/thermo", params=params).json()["records"]]

    def search_refs(**params):
        return [
            r["thermo"]["thermo_ref"]
            for r in client.get(f"{BASE}/thermo/search", params={"species_entry_ref": entry.public_ref, **params}).json()["records"]
        ]

    for refs in (entry_refs, search_refs):
        assert refs(method_family="dft", method="b3lyp", basis="def2tzvp", software="gaussian", software_version="16") == [thermo.public_ref]
        assert refs(method_family=["dft", "composite"], dft_rung_min=4, dft_rung_max=4) == [thermo.public_ref]
        assert refs(method_family="dft", software="orca") == []
        assert refs(dft_rung_min=5) == []
        assert refs(workflow_tool="arc") == []
        assert refs(scientific_origin="computed") == [thermo.public_ref]
        assert refs(scientific_origin="experimental") == []
    echo = client.get(f"{BASE}/thermo/search", params={"species_entry_ref": entry.public_ref, "method_family": ["dft"], "scientific_origin": "computed"}).json()
    assert echo["request"]["filter"]["method_family"] == ["dft"] and echo["request"]["filter"]["scientific_origin"] == "computed"
    for url in (f"{BASE}/thermo/search?species_entry_ref={entry.public_ref}", f"{BASE}/species-entries/{entry.public_ref}/thermo"):
        assert client.get(url, params={"dft_rung_min": 9}).status_code == 422
        assert client.get(url, params={"scientific_origin": "guessed"}).status_code == 422
        assert client.get(url, params={"method_family": "organic"}).status_code == 422


def test_the_kinetics_search_and_the_entry_read_take_scientific_origin_and_the_provenance_set(client, db_session):
    rs = make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("QR"))
    ps = make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("QP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    entry = make_reaction_entry(
        db_session, reaction=chem, reactant_entries=[make_species_entry(db_session, rs)], product_entries=[make_species_entry(db_session, ps)]
    )
    experimental = make_kinetics(db_session, reaction_entry=entry, scientific_origin=ScientificOriginKind.experimental)

    def entry_refs(**params):
        return [r["kinetics_ref"] for r in client.get(f"{BASE}/reaction-entries/{entry.public_ref}/kinetics", params=params).json()["records"]]

    assert entry_refs(scientific_origin="experimental") == [experimental.public_ref]
    assert entry_refs(scientific_origin="computed") == []
    assert entry_refs(method_family="dft") == []  # no linked calculation: matches no provenance filter
    assert entry_refs() == [experimental.public_ref]
    search = client.get(
        f"{BASE}/kinetics/search", params={"reaction_entry_ref": entry.public_ref, "scientific_origin": "experimental", "method_family": "dft"}
    )
    assert search.status_code == 200 and search.json()["records"] == []
    assert search.json()["request"]["filter"]["method_family"] == ["dft"]
    only_origin = client.get(f"{BASE}/kinetics/search", params={"reaction_entry_ref": entry.public_ref, "scientific_origin": "experimental"})
    assert [r["kinetics"]["kinetics_ref"] for r in only_origin.json()["records"]] == [experimental.public_ref]


# ---------------------------------------------------------------------------------------------------------------
# statmech: scientific_origin on search and on select
# ---------------------------------------------------------------------------------------------------------------


def test_the_statmech_search_filters_by_scientific_origin(client, db_session):
    entry = make_species_entry(db_session, make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("SM")))
    computed = make_statmech(db_session, species_entry=entry)
    experimental = make_statmech(db_session, species_entry=entry, scientific_origin=ScientificOriginKind.experimental)

    def refs(**params):
        body = client.get(f"{BASE}/statmech/search", params={"species_entry_ref": entry.public_ref, **params}).json()
        return {r["statmech"]["statmech_ref"] for r in body["records"]}

    assert refs() == {computed.public_ref, experimental.public_ref}
    assert refs(scientific_origin="experimental") == {experimental.public_ref}
    assert refs(scientific_origin="computed") == {computed.public_ref}
    assert refs(scientific_origin="estimated") == set()
    body = client.get(f"{BASE}/statmech/search", params={"species_entry_ref": entry.public_ref, "scientific_origin": "computed"}).json()
    assert body["request"]["filter"]["scientific_origin"] == "computed"
    assert client.get(f"{BASE}/statmech/search", params={"scientific_origin": "guessed"}).status_code == 422


def test_statmech_select_filters_by_scientific_origin(db_session):
    from app.services.statmech_selection.loader import apply_filters
    from app.services.statmech_selection.models import StatmechFilters

    entry = make_species_entry(db_session, make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("SS")))
    computed = make_statmech(db_session, species_entry=entry)
    experimental = make_statmech(db_session, species_entry=entry, scientific_origin=ScientificOriginKind.experimental)
    ids = [computed.id, experimental.id]
    assert apply_filters(db_session, ids, StatmechFilters(scientific_origin="experimental")) == [experimental.id]
    assert apply_filters(db_session, ids, StatmechFilters(scientific_origin="computed")) == [computed.id]
    assert apply_filters(db_session, ids, StatmechFilters(scientific_origin="estimated")) == []
    assert apply_filters(db_session, ids, StatmechFilters()) == ids
    assert StatmechFilters(scientific_origin="computed").to_record() == {"scientific_origin": "computed"}
