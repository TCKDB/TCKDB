"""``GET /scientific/composite-schemes/{ref}`` and ``LevelOfTheorySummary.composite_scheme`` (ADR 0021, P2).

Pins, per the contract in the plan:

* the record's content: identity, the recipe's internal levels, terms and their
  inputs in order, and the levels bound to the scheme;
* 404 for an unknown ref, 422 for the wrong resource's prefix;
* no database id anywhere in the default response;
* ``LevelOfTheorySummary.composite_scheme`` is ``None`` for an unbound level
  and the scheme's ref, kind and name for a bound one, on every read that builds
  a level summary that this file exercises.
"""

from __future__ import annotations

import hashlib

import pytest
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.db.models.common import (
    CalculationType,
    CompositeExtrapolationFormula,
    CompositeInputSlot,
    CompositeSchemeKind,
    CompositeTermOperation,
    EnergyComponentKind,
    StatmechCalculationRole,
    TransportCalculationRole,
)
from app.db.models.composite_scheme import CompositeScheme, CompositeSchemeTerm
from app.db.models.level_of_theory import LevelOfTheory
from app.services.calculation_resolution import resolve_level_of_theory_ref
from app.services.composite_scheme_resolution import add_scheme_term_input, named_method_definition_hash
from tests.services.scientific_read._factories import (
    attach_statmech_source_calculation,
    attach_transport_source_calculation,
    make_calculation,
    make_frequency_scale_factor,
    make_lot,
    make_species,
    make_species_entry,
    make_statmech,
    make_transport,
    next_inchi_key,
)

_URL = "/api/v1/scientific/composite-schemes"


def _walk_id_keys(value, path: str = "") -> list[str]:
    """Every key that names a database id: ``id``, ``*_id`` or ``*_ids``."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            here = f"{path}.{key}" if path else key
            if key == "id" or key.endswith("_id") or key.endswith("_ids"):
                found.append(here)
            found.extend(_walk_id_keys(child, here))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found.extend(_walk_id_keys(child, f"{path}[{i}]"))
    return found


def _bound_cbs_qb3(db_session):
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CBS-QB3"))
    scheme = db_session.query(CompositeScheme).one()
    return lot, scheme


def _declared_scheme(db_session):
    """An ``extrapolation`` scheme with two ordered terms and cardinal inputs."""
    scheme = CompositeScheme(
        kind=CompositeSchemeKind.extrapolation,
        name="CCSD(T)/CBS(TZ,QZ)",
        definition_hash=hashlib.sha256(b"api-test-scheme").hexdigest(),
    )
    db_session.add(scheme)
    db_session.flush()
    reference = CompositeSchemeTerm(
        scheme_id=scheme.id,
        position=0,
        operation=CompositeTermOperation.base,
        energy_component=EnergyComponentKind.reference,
    )
    correlation = CompositeSchemeTerm(
        scheme_id=scheme.id,
        position=1,
        operation=CompositeTermOperation.extrapolation,
        energy_component=EnergyComponentKind.correlation,
        formula=CompositeExtrapolationFormula.inverse_power,
        exponent=3.0,
    )
    db_session.add_all([correlation, reference])  # inserted out of order on purpose
    db_session.flush()
    qz = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVQZ"))
    tz = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CCSD(T)", basis="cc-pVTZ"))
    add_scheme_term_input(db_session, reference, slot=CompositeInputSlot.value, level_of_theory_id=qz.id)
    add_scheme_term_input(
        db_session, correlation, slot=CompositeInputSlot.cardinal, level_of_theory_id=tz.id, cardinal_number=3
    )
    add_scheme_term_input(
        db_session, correlation, slot=CompositeInputSlot.cardinal, level_of_theory_id=qz.id, cardinal_number=4
    )
    return scheme, tz, qz


def _calc_on(db_session, lot):
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("CSCH"))
    entry = make_species_entry(db_session, species)
    return make_calculation(db_session, species_entry_id=entry.id, lot_id=lot.id)


# ---------------------------------------------------------------------------
# The scheme read
# ---------------------------------------------------------------------------


def test_named_method_scheme_content(client, db_session):
    lot, scheme = _bound_cbs_qb3(db_session)

    resp = client.get(f"{_URL}/{scheme.public_ref}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["request"]["include"] == []
    assert body["review_summary"]["total"] == 0
    core = body["record"]["composite_scheme"]
    assert core["composite_scheme_ref"] == scheme.public_ref
    assert core["kind"] == "named_method"
    assert core["name"] == "CBS-QB3"
    assert core["definition_hash"] == named_method_definition_hash("cbs-qb3")
    assert core["recipe_zpe_scale_factor"] == 0.99
    assert core["source_literature_ref"] is None
    assert (core["geometry_level_of_theory"]["method"], core["geometry_level_of_theory"]["basis"]) == (
        "B3LYP",
        "CBSB7",
    )
    assert core["frequency_level_of_theory"]["basis"] == "CBSB7"
    # The recipe's internal levels are ordinary: unbound.
    assert core["geometry_level_of_theory"]["composite_scheme"] is None
    # No term list is catalogued, and none is invented.
    assert body["record"]["terms"] == []
    bound = body["record"]["bound_levels_of_theory"]
    assert [b["level_of_theory"]["level_of_theory_ref"] for b in bound] == [lot.public_ref]
    assert bound[0]["binding_source"] == "named_method_catalogue"
    assert bound[0]["level_of_theory"]["composite_scheme"] == {
        "composite_scheme_ref": scheme.public_ref,
        "kind": "named_method",
        "name": "CBS-QB3",
        # P7a: the recipe's own geometry level, by ref, so a record's notation can tell whether its geometry is it.
        "geometry_level_of_theory_ref": core["geometry_level_of_theory"]["level_of_theory_ref"],
    }


def test_scheme_with_terms_lists_them_in_order_with_their_inputs(client, db_session):
    scheme, tz, qz = _declared_scheme(db_session)

    body = client.get(f"{_URL}/{scheme.public_ref}").json()

    assert body["record"]["composite_scheme"]["kind"] == "extrapolation"
    terms = body["record"]["terms"]
    assert [t["position"] for t in terms] == [0, 1]
    assert [t["operation"] for t in terms] == ["base", "extrapolation"]
    assert [t["energy_component"] for t in terms] == ["reference", "correlation"]
    assert terms[0]["formula"] is None and terms[0]["exponent"] is None
    assert (terms[1]["formula"], terms[1]["exponent"]) == ("inverse_power", 3.0)
    assert [(i["slot"], i["cardinal_number"], i["level_of_theory"]["level_of_theory_ref"]) for i in terms[0]["inputs"]] == [
        ("value", None, qz.public_ref)
    ]
    assert [(i["slot"], i["cardinal_number"], i["level_of_theory"]["level_of_theory_ref"]) for i in terms[1]["inputs"]] == [
        ("cardinal", 3, tz.public_ref),
        ("cardinal", 4, qz.public_ref),
    ]
    # Inputs are ordinary levels, so none carries a scheme.
    assert all(i["level_of_theory"]["composite_scheme"] is None for t in terms for i in t["inputs"])
    assert body["record"]["bound_levels_of_theory"] == []


def test_unknown_ref_is_404(client, db_session):
    resp = client.get(f"{_URL}/csch_{'a' * 26}")
    assert resp.status_code == 404, resp.text
    assert "handle_not_found" in resp.text


def test_unknown_integer_handle_is_404_and_does_not_echo_the_id(client, db_session):
    resp = client.get(f"{_URL}/987654321")
    assert resp.status_code == 404, resp.text
    assert "987654321" not in resp.text


def test_wrong_prefix_is_422(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    resp = client.get(f"{_URL}/{lot.public_ref}")
    assert resp.status_code == 422, resp.text
    assert "handle_type_mismatch" in resp.text


def test_default_response_carries_no_database_id(client, db_session):
    _bound_cbs_qb3(db_session)
    scheme, _tz, _qz = _declared_scheme(db_session)
    for ref in (db_session.query(CompositeScheme).all()):
        body = client.get(f"{_URL}/{ref.public_ref}").json()
        assert _walk_id_keys(body) == [], ref.public_ref
    assert scheme.id  # the ids exist; they are what must not leak


def test_internal_ids_token_does_not_restore_ids_unless_the_deployment_allows_it(client, db_session):
    scheme, _tz, _qz = _declared_scheme(db_session)
    body = client.get(f"{_URL}/{scheme.public_ref}", params={"include": "internal_ids"}).json()
    assert _walk_id_keys(body) == []
    assert body["request"]["include"] == []


def test_internal_ids_are_restored_only_on_request_when_allowed(client, db_session, allow_internal_ids):
    scheme, _tz, _qz = _declared_scheme(db_session)
    body = client.get(f"{_URL}/{scheme.public_ref}", params={"include": "internal_ids"}).json()
    # The level summaries are the only place an id is modelled; the point is
    # that stripping, not absence from the model, is what hides them.
    assert any(key.endswith("level_of_theory_id") for key in _walk_id_keys(body))


def test_unknown_include_token_is_422(client, db_session):
    scheme, _tz, _qz = _declared_scheme(db_session)
    resp = client.get(f"{_URL}/{scheme.public_ref}", params={"include": "terms"})
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# LevelOfTheorySummary.composite_scheme
# ---------------------------------------------------------------------------


def test_summary_is_null_for_an_unbound_level(client, db_session):
    lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="B3LYP", basis="def2-tzvp"))
    calc = _calc_on(db_session, lot)

    record = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}").json()["record"]

    assert "composite_scheme" in record["level_of_theory"]
    assert record["level_of_theory"]["composite_scheme"] is None


def test_summary_names_the_scheme_for_a_bound_level(client, db_session):
    lot, scheme = _bound_cbs_qb3(db_session)
    calc = _calc_on(db_session, lot)

    record = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}").json()["record"]

    geometry_level = db_session.get(LevelOfTheory, scheme.geometry_level_of_theory_id)
    assert record["level_of_theory"]["composite_scheme"] == {
        "composite_scheme_ref": scheme.public_ref,
        "kind": "named_method",
        "name": "CBS-QB3",
        # P7a: the recipe's own geometry level, by ref (what a record's notation compares against).
        "geometry_level_of_theory_ref": geometry_level.public_ref,
    }


def test_level_of_theory_detail_carries_the_scheme_only_when_bound(client, db_session):
    bound, scheme = _bound_cbs_qb3(db_session)
    plain = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="B3LYP", basis="def2-tzvp"))
    for lot in (bound, plain):
        _calc_on(db_session, lot)

    bound_body = client.get(f"/api/v1/scientific/level-of-theories/{bound.public_ref}").json()
    plain_body = client.get(f"/api/v1/scientific/level-of-theories/{plain.public_ref}").json()

    assert bound_body["record"]["level_of_theory"]["composite_scheme"]["composite_scheme_ref"] == scheme.public_ref
    assert plain_body["record"]["level_of_theory"]["composite_scheme"] is None
    # The hash is the level's own, untouched by the binding.
    assert bound_body["record"]["level_of_theory"]["lot_hash"] == bound.lot_hash


def test_calculation_search_summary_names_the_scheme(client, db_session):
    lot, scheme = _bound_cbs_qb3(db_session)
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("CSCHS"))
    entry = make_species_entry(db_session, species)
    make_calculation(db_session, species_entry_id=entry.id, lot_id=lot.id)

    resp = client.get("/api/v1/scientific/calculations/search", params={"lot_ref": lot.public_ref})

    assert resp.status_code == 200, resp.text
    records = resp.json()["records"]
    assert records
    assert {r["level_of_theory"]["composite_scheme"]["composite_scheme_ref"] for r in records} == {scheme.public_ref}


@pytest.mark.parametrize("path", ["unbound", "bound"])
def test_summary_field_is_always_present(client, db_session, path):
    if path == "bound":
        lot, _ = _bound_cbs_qb3(db_session)
    else:
        lot = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="B3LYP", basis="def2-tzvp"))
    calc = _calc_on(db_session, lot)
    summary = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}").json()["record"]["level_of_theory"]
    assert "composite_scheme" in summary


# ---------------------------------------------------------------------------
# Every read that builds a level summary reports the binding
# ---------------------------------------------------------------------------


def _expected(scheme):
    # ``geometry_level_of_theory_ref`` (P7a): the recipe's own geometry level, by ref. The catalogue states B3LYP/CBSB7.
    return {
        "composite_scheme_ref": scheme.public_ref,
        "kind": "named_method",
        "name": "CBS-QB3",
        "geometry_level_of_theory_ref": _geometry_ref(scheme),
    }


def _geometry_ref(scheme):
    from sqlalchemy import inspect

    session = inspect(scheme).session
    return session.get(LevelOfTheory, scheme.geometry_level_of_theory_id).public_ref


def test_frequency_scale_factor_read_names_the_scheme_of_a_bound_level(client, db_session):
    lot, scheme = _bound_cbs_qb3(db_session)
    plain = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="B3LYP", basis="def2-tzvp"))
    bound_fsf = make_frequency_scale_factor(db_session, lot=lot, value=0.99)
    plain_fsf = make_frequency_scale_factor(db_session, lot=plain, value=0.97)

    bound = client.get(f"/api/v1/scientific/frequency-scale-factors/{bound_fsf.public_ref}").json()
    unbound = client.get(f"/api/v1/scientific/frequency-scale-factors/{plain_fsf.public_ref}").json()

    assert bound["record"]["level_of_theory"]["composite_scheme"] == _expected(scheme)
    assert unbound["record"]["level_of_theory"]["composite_scheme"] is None


def test_transport_source_calculation_level_names_the_scheme(client, db_session):
    lot, scheme = _bound_cbs_qb3(db_session)
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("CSCHT"))
    entry = make_species_entry(db_session, species)
    tr = make_transport(db_session, species_entry=entry)
    calc = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id)
    attach_transport_source_calculation(
        db_session, transport=tr, calculation=calc, role=TransportCalculationRole.full_transport
    )

    body = client.get(f"/api/v1/scientific/transport/{tr.public_ref}", params={"include": "source_calculations"}).json()

    sources = body["record"]["source_calculations"]
    assert [s["level_of_theory"]["composite_scheme"] for s in sources] == [_expected(scheme)]


def test_statmech_levels_source_calculations_and_scale_factor_name_the_scheme(client, db_session):
    lot, scheme = _bound_cbs_qb3(db_session)
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("CSCHM"))
    entry = make_species_entry(db_session, species)
    fsf = make_frequency_scale_factor(db_session, lot=lot, value=0.99)
    sm = make_statmech(db_session, species_entry=entry, frequency_scale_factor_id=fsf.id)
    calc = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id, lot_id=lot.id)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=calc, role=StatmechCalculationRole.freq)

    body = client.get(f"/api/v1/scientific/statmech/{sm.public_ref}", params={"include": "source_calculations"}).json()
    record = body["record"]

    # derived levels (the single-level builder), the bulk builder, and the scale factor's level
    assert record["levels"]["frequency"]["composite_scheme"] == _expected(scheme)
    assert record["source_calculations"][0]["level_of_theory"]["composite_scheme"] == _expected(scheme)
    assert record["frequency_scale_factor"]["level_of_theory"]["composite_scheme"] == _expected(scheme)
