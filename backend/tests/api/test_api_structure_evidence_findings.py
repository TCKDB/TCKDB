"""The curator route that appends structure-evidence findings, and what a recorded finding does.

Every finding here is written through ``POST /api/v1/curation/structure-evidence-findings``, never by inserting a row, and
the effect tests then run the three selections (structure, H298, kinetics) over the same database. The point of the
route is that the rule "a confirmed invalidation excludes the evidence, scoped by role" can fire on a real deployment;
a test that inserted the row directly would prove only the selection half.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    LiteratureKind,
    StructureFindingAuthority,
    ThermoCalculationRole,
    ThermoTargetKind,
)
from app.db.models.literature import Literature
from app.db.models.structure_determination import StructureEvidenceFinding
from app.services.scientific_read.profile import reset_current_read_profile
from app.services.thermo_selection import H298Request, Outcome, select_h298
from tests.id_leak import id_in_text
from tests.services.kinetics_selection.test_service import run as run_kinetics
from tests.services.kinetics_selection.test_source_findings_kinetics import Sources, only, record
from tests.services.scientific_read._factories import (
    attach_opt_result,
    attach_output_geometry,
    attach_thermo_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_geometry,
    make_literature,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
)
from tests.services.structure_selection._support import curated, declaration, make_world
from tests.services.structure_selection._support import request as structure_request
from tests.services.structure_selection.test_service import by_ref
from tests.services.structure_selection.test_service import run as run_structure
from tests.services.thermo_selection._support import make_thermo, protocol, species_entry_for

URL = "/api/v1/curation/structure-evidence-findings"
LATER = "a later ruling"


def post(client, **fields):
    body = {"rationale": "the curator's stated reason", "verdict": "invalidates", **fields}
    return client.post(URL, json=body)


def calc_finding(client, calc, **fields):
    return post(client, scope="calculation", subject_ref=calc.public_ref, kind="identity_incompatibility", **fields)


def overturn(client, calc, finding_ref):
    return post(
        client,
        scope="calculation",
        subject_ref=calc.public_ref,
        kind="adjudication",
        verdict="does_not_invalidate",
        rationale=LATER,
        supersedes_finding_ref=finding_ref,
    )


@pytest.fixture
def curator(login_as, _api_curator_user):
    return login_as(_api_curator_user)


@pytest.fixture
def calc(db_session):
    world = make_world(db_session)
    return world.sp("c", -76.4, declared=declaration())


# --------------------------------------------------------------------------------------------------------------------
# Standing, attribution, required fields
# --------------------------------------------------------------------------------------------------------------------


def test_a_non_curator_is_refused_on_write_and_read(client, db_session, calc):
    assert calc_finding(client, calc).status_code == 403
    assert client.get(URL, params={"subject_ref": calc.public_ref}).status_code == 403
    assert db_session.scalar(select(func.count()).select_from(StructureEvidenceFinding)) == 0


def test_a_curator_and_an_admin_may_record_and_the_write_is_attributed(client, db_session, calc, login_as, _api_curator_user, _api_admin_user):
    for user in (_api_curator_user, _api_admin_user):
        login_as(user)
        response = calc_finding(client, calc)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["authority"] == "authorized_adjudication" and body["in_force"] is True
        assert body["subject_ref"] == calc.public_ref and body["finding_ref"].startswith("sfnd_")
        row = db_session.scalar(select(StructureEvidenceFinding).where(StructureEvidenceFinding.public_ref == body["finding_ref"]))
        assert row.created_by == user and row.authority is StructureFindingAuthority.authorized_adjudication


def test_the_request_cannot_choose_its_own_authority_or_author(client, curator, calc):
    for extra in ({"authority": "producer_assertion"}, {"created_by": 1}, {"subject_calculation_id": 1}):
        response = calc_finding(client, calc, **extra)
        assert response.status_code == 422, extra


def test_a_rationale_is_required(client, curator, calc):
    missing = client.post(URL, json={"scope": "calculation", "subject_ref": calc.public_ref, "kind": "identity_incompatibility", "verdict": "invalidates"})
    assert missing.status_code == 422
    blank = calc_finding(client, calc, rationale="   ")
    assert blank.status_code == 422 and blank.json()["code"] == "structure_finding_rationale_required"


def test_a_role_is_named_exactly_for_a_role_invalidation(client, curator, calc):
    without = post(client, scope="calculation", subject_ref=calc.public_ref, kind="role_invalidation")
    assert without.status_code == 422 and without.json()["code"] == "structure_finding_role_mismatch"
    extra = calc_finding(client, calc, role="energy")
    assert extra.status_code == 422 and extra.json()["code"] == "structure_finding_role_mismatch"
    ok = post(client, scope="calculation", subject_ref=calc.public_ref, kind="role_invalidation", role="energy")
    assert ok.status_code == 201 and ok.json()["role"] == "energy"


# --------------------------------------------------------------------------------------------------------------------
# Subject and scope
# --------------------------------------------------------------------------------------------------------------------


def test_a_scope_that_does_not_match_the_subject_is_refused(client, curator, calc):
    geometry_ref = calc.input_geometries[0].geometry.public_ref
    for scope, ref in (("geometry", calc.public_ref), ("calculation", geometry_ref), ("determination", calc.public_ref)):
        response = post(client, scope=scope, subject_ref=ref, kind="identity_incompatibility")
        body = response.json()
        assert response.status_code == 422 and body["code"] == "structure_finding_scope_subject_mismatch", (scope, body)
        assert body["context"]["scope"] == scope


def test_each_scope_records_against_its_own_kind_of_subject(client, curator, calc):
    geometry = calc.input_geometries[0].geometry
    assert post(client, scope="geometry", subject_ref=geometry.public_ref, kind="state_incompatibility").status_code == 201
    assert calc_finding(client, calc).status_code == 201


def test_an_unknown_subject_is_a_404_that_echoes_the_ref_and_no_row_id(client, curator, calc):
    ghost = "calc_" + "a" * 26
    response = calc_finding_for(client, ghost)
    assert response.status_code == 404 and response.json()["code"] == "structure_finding_subject_not_found"
    assert ghost in response.text


def calc_finding_for(client, ref):
    return post(client, scope="calculation", subject_ref=ref, kind="identity_incompatibility")


def test_a_cited_supporting_calculation_and_paper_are_named_by_public_ref(client, db_session, curator, calc):
    other = make_calculation(db_session, type=CalculationType.sp, species_entry_id=calc.species_entry_id)
    paper = make_literature(db_session)
    body = calc_finding(client, calc, source_calculation_ref=other.public_ref, literature_ref=paper.public_ref).json()
    assert body["source_calculation_ref"] == other.public_ref and body["literature_ref"] == paper.public_ref


def test_a_paper_with_neither_a_doi_nor_an_isbn_is_refused(client, db_session, curator, calc):
    title_only = Literature(kind=LiteratureKind.article, title="Only a title", year=2020)
    db_session.add(title_only)
    db_session.flush()
    response = calc_finding(client, calc, literature_ref=title_only.public_ref)
    assert response.status_code == 422 and response.json()["code"] == "structure_finding_literature_unidentified"


def test_an_unknown_or_wrong_kind_of_cited_ref_is_refused_by_code(client, curator, calc):
    unknown = calc_finding(client, calc, source_calculation_ref="calc_" + "b" * 26)
    assert unknown.status_code == 404 and unknown.json()["code"] == "structure_finding_reference_not_found"
    assert unknown.json()["context"]["field"] == "source_calculation_ref"
    wrong = calc_finding(client, calc, literature_ref=calc.public_ref)
    assert wrong.status_code == 422 and wrong.json()["code"] == "structure_finding_reference_invalid"


# --------------------------------------------------------------------------------------------------------------------
# Corrections
# --------------------------------------------------------------------------------------------------------------------


def test_a_correction_must_be_an_adjudication_and_an_adjudication_must_name_a_finding(client, curator, calc):
    first = calc_finding(client, calc).json()["finding_ref"]
    plain = calc_finding(client, calc, supersedes_finding_ref=first)
    assert plain.status_code == 422 and plain.json()["code"] == "structure_finding_correction_needs_adjudication"
    bare = post(client, scope="calculation", subject_ref=calc.public_ref, kind="adjudication", verdict="does_not_invalidate", rationale=LATER)
    assert bare.status_code == 422 and bare.json()["code"] == "structure_finding_adjudication_needs_target"


def test_a_correction_must_name_an_existing_finding(client, curator, calc):
    response = overturn(client, calc, "sfnd_" + "c" * 26)
    assert response.status_code == 404 and response.json()["code"] == "structure_finding_reference_not_found"
    assert response.json()["context"]["field"] == "supersedes_finding_ref"


def test_a_correction_must_name_a_finding_about_the_same_subject(client, db_session, curator, calc):
    other = make_calculation(db_session, type=CalculationType.sp, species_entry_id=calc.species_entry_id)
    first = calc_finding(client, calc).json()["finding_ref"]
    response = overturn(client, other, first)
    assert response.status_code == 422 and response.json()["code"] == "structure_finding_correction_subject_mismatch"
    geometry = post(
        client, scope="geometry", subject_ref=calc.input_geometries[0].geometry.public_ref, kind="adjudication",
        verdict="does_not_invalidate", rationale=LATER, supersedes_finding_ref=first,
    )
    assert geometry.status_code == 422 and geometry.json()["code"] == "structure_finding_correction_subject_mismatch"


def test_an_adjudication_rules_does_not_invalidate_and_cannot_be_corrected_or_repeated(client, curator, calc):
    first = calc_finding(client, calc).json()["finding_ref"]
    affirming = post(
        client, scope="calculation", subject_ref=calc.public_ref, kind="adjudication", verdict="invalidates",
        rationale=LATER, supersedes_finding_ref=first,
    )
    assert affirming.status_code == 422 and affirming.json()["code"] == "structure_finding_adjudication_verdict_unsupported"
    done = overturn(client, calc, first)
    assert done.status_code == 201
    again = overturn(client, calc, first)
    assert again.status_code == 422 and again.json()["code"] == "structure_finding_already_settled"
    assert again.json()["context"]["settled_by_finding_ref"] == done.json()["finding_ref"]
    of_adjudication = overturn(client, calc, done.json()["finding_ref"])
    assert of_adjudication.status_code == 422 and of_adjudication.json()["code"] == "structure_finding_target_is_adjudication"


def test_the_read_says_what_is_in_force_and_what_settled_the_rest(client, curator, calc):
    first = calc_finding(client, calc).json()["finding_ref"]
    settled = overturn(client, calc, first).json()["finding_ref"]
    rows = client.get(URL, params={"subject_ref": calc.public_ref}).json()["findings"]
    by = {r["finding_ref"]: r for r in rows}
    assert by[first]["in_force"] is False and by[first]["settled_by_finding_ref"] == settled
    assert by[settled]["in_force"] is True and by[settled]["supersedes_finding_ref"] == first


# --------------------------------------------------------------------------------------------------------------------
# Append-only
# --------------------------------------------------------------------------------------------------------------------


def test_the_route_offers_no_way_to_edit_or_delete_a_finding(client, curator, calc):
    first = calc_finding(client, calc).json()["finding_ref"]
    for method in ("patch", "put", "delete"):
        for path in (URL, f"{URL}/{first}"):
            assert getattr(client, method)(path).status_code in (404, 405), (method, path)
    methods = {m for route in client.app.routes if getattr(route, "path", "").endswith("structure-evidence-findings") for m in route.methods}
    assert methods == {"GET", "POST"}, "a mutating method was added to the findings path"


def test_the_database_refuses_to_edit_or_delete_a_finding(client, db_session, curator, calc):
    ref = calc_finding(client, calc).json()["finding_ref"]
    for statement in (
        text("UPDATE structure_evidence_finding SET rationale = 'rewritten' WHERE public_ref = :r"),
        text("DELETE FROM structure_evidence_finding WHERE public_ref = :r"),
    ):
        with pytest.raises(DBAPIError, match="structure_evidence_finding_is_append_only"):
            with db_session.begin_nested():
                db_session.execute(statement, {"r": ref})
    assert db_session.scalar(select(StructureEvidenceFinding.rationale).where(StructureEvidenceFinding.public_ref == ref)) != "rewritten"


def test_no_refusal_carries_a_row_id(client, db_session, curator, calc):
    first = calc_finding(client, calc).json()["finding_ref"]
    ids = {calc.id, calc.input_geometries[0].geometry_id, db_session.scalar(select(StructureEvidenceFinding.id))}
    refusals = [
        calc_finding(client, calc, rationale="  "),
        calc_finding_for(client, "calc_" + "a" * 26),
        post(client, scope="geometry", subject_ref=calc.public_ref, kind="identity_incompatibility"),
        overturn(client, calc, "sfnd_" + "c" * 26),
        post(client, scope="calculation", subject_ref=calc.public_ref, kind="adjudication", verdict="invalidates", rationale=LATER, supersedes_finding_ref=first),
    ]
    overturn(client, calc, first)
    refusals.append(overturn(client, calc, first))
    for response in refusals:
        assert response.status_code >= 400
        for row_id in ids:
            assert not id_in_text(row_id, response.text), (row_id, response.text)


# --------------------------------------------------------------------------------------------------------------------
# Effect, through the route
# --------------------------------------------------------------------------------------------------------------------

EQUILIBRIUM = H298Request(target_kind=ThermoTargetKind.equilibrium_ensemble)


def test_structure_selection_excludes_on_an_adjudicated_invalidation_and_a_later_adjudication_restores(client, db_session, curator):
    world = make_world(db_session)
    c = world.sp("c", -76.4, declared=declaration())
    world.settle()
    assert by_ref(run_structure(db_session, world, structure_request()))[c.public_ref].physically_eligible
    finding = calc_finding(client, c).json()["finding_ref"]
    db_session.expire_all()
    excluded = by_ref(run_structure(db_session, world, structure_request()))[c.public_ref]
    assert not excluded.physically_eligible and excluded.blocking
    assert overturn(client, c, finding).status_code == 201
    db_session.expire_all()
    assert by_ref(run_structure(db_session, world, structure_request()))[c.public_ref].physically_eligible


def test_h298_selection_excludes_on_an_adjudicated_invalidation_and_a_later_adjudication_restores(client, db_session, curator):
    entry = species_entry_for(db_session, "Methane")
    thermo = make_thermo(db_session, entry, proto=protocol("g4"))
    calc = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    attach_opt_result(db_session, calculation=calc, final_energy_hartree=-76.4, converged=True)
    attach_output_geometry(db_session, calculation=calc, geometry=make_geometry(db_session))
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=calc, role=ThermoCalculationRole.opt)
    db_session.flush()

    def select():
        db_session.expire_all()
        return select_h298(db_session, species_entry_id=entry.id, request=EQUILIBRIUM, require_snapshot=False)

    assert select().selected_ref == thermo.public_ref
    finding = calc_finding(client, calc).json()["finding_ref"]
    assert select().outcome is Outcome.no_applicable_candidate
    assert overturn(client, calc, finding).status_code == 201
    assert select().selected_ref == thermo.public_ref


def test_a_role_invalidation_through_the_route_bites_only_the_role_it_names(client, db_session, curator):
    entry = species_entry_for(db_session, "Methane")
    energy_user = make_thermo(db_session, entry, proto=protocol("g4"))
    curvature_user = make_thermo(db_session, entry, proto=protocol("g3"))
    shared = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    attach_opt_result(db_session, calculation=shared, final_energy_hartree=-76.4, converged=True)
    attach_output_geometry(db_session, calculation=shared, geometry=make_geometry(db_session))
    attach_thermo_source_calculation(db_session, thermo=energy_user, calculation=shared, role=ThermoCalculationRole.sp)
    attach_thermo_source_calculation(db_session, thermo=curvature_user, calculation=shared, role=ThermoCalculationRole.freq)
    db_session.flush()
    post(client, scope="calculation", subject_ref=shared.public_ref, kind="role_invalidation", role="energy")
    db_session.expire_all()
    result = select_h298(db_session, species_entry_id=entry.id, request=EQUILIBRIUM, require_snapshot=False)
    blocked = {a.thermo_ref: a.blocking for a in result.assessments}
    assert blocked[energy_user.public_ref] and not blocked[curvature_user.public_ref]


@pytest.fixture
def kinetics_world(db_session):
    h = make_species_entry(db_session, make_species(db_session, smiles="[H]", multiplicity=2, inchi_key=next_inchi_key("KSA")))
    ch4 = make_species_entry(db_session, make_species(db_session, smiles="C", multiplicity=1, inchi_key=next_inchi_key("KSB")))
    h2 = make_species_entry(db_session, make_species(db_session, smiles="[H][H]", multiplicity=1, inchi_key=next_inchi_key("KSC")))
    ch3 = make_species_entry(db_session, make_species(db_session, smiles="[CH3]", multiplicity=2, inchi_key=next_inchi_key("KSD")))
    reaction = make_chem_reaction(db_session, reactants=[h.species, ch4.species], products=[h2.species, ch3.species])
    entry = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[h, ch4], product_entries=[h2, ch3])
    return SimpleNamespace(entry=entry, literature=make_literature(db_session), h=h, ch4=ch4)


def test_kinetics_selection_excludes_on_an_adjudicated_invalidation_and_a_later_adjudication_restores(client, db_session, curator, kinetics_world):
    sources = Sources(db_session, kinetics_world)
    c, _, role = sources.energy(KineticsCalculationRole.ts_energy)
    record(db_session, kinetics_world, "d", sources, links=[(c, role)])
    assert only(run_kinetics(db_session, kinetics_world.entry)).physically_eligible
    finding = calc_finding(client, c).json()["finding_ref"]
    db_session.expire_all()
    assert not only(run_kinetics(db_session, kinetics_world.entry)).physically_eligible
    assert overturn(client, c, finding).status_code == 201
    db_session.expire_all()
    assert only(run_kinetics(db_session, kinetics_world.entry)).physically_eligible


# --------------------------------------------------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------------------------------------------------


def test_under_curated_a_finding_on_a_hidden_subject_is_not_read_and_names_nothing(client, db_session, curator):
    world = make_world(db_session)
    visible = world.sp("a", -76.4, declared=declaration())
    hidden = world.sp("h", -76.9, status=None)
    world.settle()
    ref = calc_finding(client, hidden).json()["finding_ref"]
    db_session.expire_all()

    exploratory = run_structure(db_session, world, structure_request())
    assert not by_ref(exploratory)[hidden.public_ref].physically_eligible  # the positive control: the finding bites when seen

    token = curated()
    try:
        result = run_structure(db_session, world, structure_request())
    finally:
        reset_current_read_profile(token)
    document = json.dumps([a.to_dict() for a in result.assessments]) + json.dumps(result.excluded_by_review)
    assert hidden.public_ref not in document and ref not in document and visible.public_ref in document
    assert by_ref(result)[visible.public_ref].physically_eligible


def test_the_curator_read_lists_a_subject_the_public_profile_hides(client, db_session, curator):
    world = make_world(db_session)
    hidden = world.sp("h", -76.9, status=None)
    world.settle()
    ref = calc_finding(client, hidden).json()["finding_ref"]
    rows = client.get(URL, params={"subject_ref": hidden.public_ref}).json()["findings"]
    assert [r["finding_ref"] for r in rows] == [ref]
