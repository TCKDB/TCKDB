"""What the statmech select route lets a caller learn about records it may not see, and about records that are not theirs to cite.

Two rules, each held for every public ref a request can name (the target, a basin, a determination, an ensemble
declaration, a reference model, an explicit candidate):

* **Hidden is unknown.** Under the curated profile a ref whose record the profile hides answers exactly as a ref that
  names nothing: the same status, code and body, up to the ref itself, which the caller supplied and is echoed back.
* **Visible but not yours is refused, not answered.** A ref the caller may see but that belongs to another entry cannot be
  part of a question about this one; it is a coded 422 owner mismatch, never a quiet 200 about nothing. The check comes
  after the 404 equivalence, so the 422 never reveals a record the caller could not otherwise see.
"""

from __future__ import annotations

import json

import pytest

from app.db.models.common import RecordReviewStatus, StatmechModelScopeKind, SubmissionRecordType
from app.db.models.statmech import Statmech
from tests.db.test_statmech_model_declaration_db import _declaration
from tests.services.scientific_read._factories import make_statmech
from tests.services.statmech_selection.test_request import THERMAL, make
from tests.services.statmech_selection.test_service import build, build_ts_scene, restatus, set_review

URL = "/api/v1/scientific/statmech/select"
CURATED = "?profile=curated"
GHOST = "a" * 26
MISMATCH = "statmech_selection_scope_owner_mismatch"


def approve(db_session, scene):
    set_review(db_session, record_type=SubmissionRecordType.conformer_group, record_id=scene.group.id, status=RecordReviewStatus.approved)


def body_for(scene, **changes) -> dict:
    defaults = {
        "target__species_entry_ref": scene.world.entry.public_ref,
        "target__scope__conformer_group_ref": scene.group.public_ref,
    }
    return make(THERMAL, **{**defaults, **changes})


def ensemble_body(scene, ref: str, **changes) -> dict:
    return body_for(
        scene, task="canonical_model", observable="internal_partition",
        target__scope={"kind": "explicit_equilibrium_ensemble", "ensemble_declaration_ref": ref}, **changes,
    )


def fidelity_body(scene, ref: str) -> dict:
    return body_for(scene, intent="protocol_preferred", comparison_objective="model_fidelity", reference_model_ref=ref)


def normalised(response, ref: str) -> tuple[int, object]:
    """The response with the one ref the caller supplied masked, so two refusals can be compared as the caller sees them."""
    return response.status_code, json.loads(response.text.replace(ref, "<ref>"))


def declared_ref(db_session, entry_scene, *, status: RecordReviewStatus) -> str:
    """A declaration on a fresh statmech record of ``entry_scene``'s entry, whose record has the given review status."""
    host = make_statmech(db_session, species_entry=entry_scene.world.entry)
    declaration = _declaration(db_session, host, StatmechModelScopeKind.atom)
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=host.id, status=status)
    return declaration.public_ref


# --- hidden is unknown ---------------------------------------------------------------------------------------------------------


def test_a_hidden_target_answers_exactly_as_an_unknown_one(client, db_session):
    hidden = build(db_session)
    approve(db_session, hidden)
    restatus(db_session, SubmissionRecordType.species_entry, hidden.world.entry.id, RecordReviewStatus.not_reviewed)
    ref = hidden.world.entry.public_ref
    got = client.post(URL + CURATED, json=body_for(hidden))
    unknown = client.post(URL + CURATED, json=body_for(hidden, target__species_entry_ref="spe_" + GHOST))
    assert got.status_code == 404
    assert normalised(got, ref) == normalised(unknown, "spe_" + GHOST)


def test_a_hidden_transition_state_target_answers_exactly_as_an_unknown_one(client, db_session):
    ts = build_ts_scene(db_session)
    restatus(db_session, SubmissionRecordType.transition_state_entry, ts.entry.id, RecordReviewStatus.not_reviewed)
    ref = ts.entry.public_ref
    got = client.post(URL + CURATED, json=ts.body)
    unknown = client.post(URL + CURATED, json=make(ts.body, target__transition_state_entry_ref="tse_" + GHOST))
    assert got.status_code == 404
    assert normalised(got, ref) == normalised(unknown, "tse_" + GHOST)


def test_a_hidden_basin_answers_exactly_as_an_unknown_one(client, db_session):
    scene = build(db_session)  # its group has no approval: hidden under curated
    got = client.post(URL + CURATED, json=body_for(scene))
    unknown = client.post(URL + CURATED, json=body_for(scene, target__scope__conformer_group_ref="cg_" + GHOST))
    assert got.status_code == 404
    assert normalised(got, scene.group.public_ref) == normalised(unknown, "cg_" + GHOST)


def test_a_basin_of_a_hidden_entry_answers_exactly_as_an_unknown_one_not_as_a_mismatch(client, db_session):
    mine, theirs = build(db_session), build(db_session)
    approve(db_session, mine)
    approve(db_session, theirs)
    restatus(db_session, SubmissionRecordType.species_entry, theirs.world.entry.id, RecordReviewStatus.not_reviewed)
    got = client.post(URL + CURATED, json=body_for(mine, target__scope__conformer_group_ref=theirs.group.public_ref))
    unknown = client.post(URL + CURATED, json=body_for(mine, target__scope__conformer_group_ref="cg_" + GHOST))
    assert got.status_code == 404
    assert normalised(got, theirs.group.public_ref) == normalised(unknown, "cg_" + GHOST)


def test_a_determination_of_a_hidden_entry_answers_exactly_as_an_unknown_one(client, db_session):
    mine, theirs = build_ts_scene(db_session), build_ts_scene(db_session)
    restatus(db_session, SubmissionRecordType.transition_state_entry, theirs.entry.id, RecordReviewStatus.not_reviewed)
    got = client.post(URL + CURATED, json=make(mine.body, target__scope__determination_ref=theirs.determination.public_ref))
    unknown = client.post(URL + CURATED, json=make(mine.body, target__scope__determination_ref="sdet_" + GHOST))
    assert got.status_code == 404
    assert normalised(got, theirs.determination.public_ref) == normalised(unknown, "sdet_" + GHOST)


@pytest.mark.parametrize("builder", [ensemble_body, fidelity_body], ids=["ensemble", "reference_model"])
@pytest.mark.parametrize("where", ["another_entry", "own_entry"])
def test_a_hidden_declaration_answers_exactly_as_an_unknown_one(client, db_session, builder, where):
    mine = build(db_session)
    approve(db_session, mine)
    other = build(db_session) if where == "another_entry" else mine
    ref = declared_ref(db_session, other, status=RecordReviewStatus.not_reviewed)
    got = client.post(URL + CURATED, json=builder(mine, ref))
    unknown = client.post(URL + CURATED, json=builder(mine, "smd_" + GHOST))
    assert got.status_code == 404 and code_of(got) == "handle_not_found"
    assert normalised(got, ref) == normalised(unknown, "smd_" + GHOST)


def test_a_hidden_candidate_answers_exactly_as_an_unknown_one(client, db_session):
    scene = build(db_session)
    approve(db_session, scene)
    hidden = Statmech(species_entry_id=scene.world.entry.id, scientific_origin="computed")
    db_session.add(hidden)
    db_session.flush()
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=hidden.id, status=RecordReviewStatus.not_reviewed)

    def population(ref):
        return {"kind": "model_revisions", "model_refs": [ref]}

    got = client.post(URL + CURATED, json=body_for(scene, population=population(hidden.public_ref)))
    unknown = client.post(URL + CURATED, json=body_for(scene, population=population("sm_" + GHOST)))
    assert got.status_code == 404 and code_of(got) == "unknown_statmech_candidate_ref"
    assert normalised(got, hidden.public_ref) == normalised(unknown, "sm_" + GHOST)


def code_of(response) -> str | None:
    body = response.json()
    return body.get("code") if isinstance(body, dict) else None


# --- visible but not yours ---------------------------------------------------------------------------------------------------


def test_another_entrys_visible_basin_is_a_coded_owner_mismatch_not_a_quiet_200(client, db_session):
    mine, theirs = build(db_session), build(db_session)
    approve(db_session, mine)
    approve(db_session, theirs)
    for profile in ("", CURATED):
        response = client.post(URL + profile, json=body_for(mine, target__scope__conformer_group_ref=theirs.group.public_ref))
        assert response.status_code == 422 and code_of(response) == MISMATCH, response.text
        assert response.json()["context"]["field"] == "conformer_group_ref"
        assert theirs.group.public_ref not in response.text and theirs.world.entry.public_ref not in response.text
    assert client.post(URL + CURATED, json=body_for(mine)).status_code == 200  # its own basin is fine


def test_another_transition_states_visible_determination_is_a_coded_owner_mismatch(client, db_session):
    mine, theirs = build_ts_scene(db_session), build_ts_scene(db_session)
    response = client.post(URL, json=make(mine.body, target__scope__determination_ref=theirs.determination.public_ref))
    assert response.status_code == 422 and code_of(response) == MISMATCH
    assert response.json()["context"]["field"] == "determination_ref" and theirs.determination.public_ref not in response.text
    assert client.post(URL, json=mine.body).status_code == 200


@pytest.mark.parametrize(
    ("builder", "field"), [(ensemble_body, "ensemble_declaration_ref"), (fidelity_body, "reference_model_ref")], ids=["ensemble", "reference_model"]
)
def test_another_entrys_visible_declaration_is_a_coded_owner_mismatch(client, db_session, builder, field):
    mine, theirs = build(db_session), build(db_session)
    approve(db_session, mine)
    foreign = theirs.host.model_declaration.public_ref
    response = client.post(URL, json=builder(mine, foreign))
    assert response.status_code == 422 and code_of(response) == MISMATCH, response.text
    assert response.json()["context"]["field"] == field and foreign not in response.text
    own = mine.host.model_declaration.public_ref
    assert client.post(URL, json=builder(mine, own)).status_code == 200  # its own declaration is a fine reference


def test_a_species_entrys_record_cannot_be_cited_by_a_transition_state_question(client, db_session):
    species, ts = build(db_session), build_ts_scene(db_session)
    response = client.post(URL, json=make(ts.body, reference_model_ref=species.host.model_declaration.public_ref,
                                          intent="protocol_preferred", comparison_objective="model_fidelity"))
    assert response.status_code == 422 and code_of(response) == MISMATCH


def test_the_hidden_equivalence_is_checked_before_the_mismatch(client, db_session):
    """A foreign ref that is also hidden is a 404, so the 422 can never reveal a record the caller could not see."""
    mine, theirs = build(db_session), build(db_session)
    approve(db_session, mine)  # theirs: group unapproved -> hidden under curated; visible without it
    exploratory = client.post(URL, json=body_for(mine, target__scope__conformer_group_ref=theirs.group.public_ref))
    curated = client.post(URL + CURATED, json=body_for(mine, target__scope__conformer_group_ref=theirs.group.public_ref))
    assert exploratory.status_code == 422 and code_of(curated) == "handle_not_found" and curated.status_code == 404


def test_a_hidden_target_is_a_404_before_any_scope_ref_is_judged(client, db_session):
    """Every other ref is judged against a target the caller may see; for one they may not, nothing else is reached."""
    hidden, other = build(db_session), build(db_session)
    approve(db_session, hidden)
    approve(db_session, other)
    restatus(db_session, SubmissionRecordType.species_entry, hidden.world.entry.id, RecordReviewStatus.not_reviewed)
    foreign_basin = client.post(URL + CURATED, json=body_for(hidden, target__scope__conformer_group_ref=other.group.public_ref))
    unknown = client.post(URL + CURATED, json=body_for(hidden, target__species_entry_ref="spe_" + GHOST))
    assert foreign_basin.status_code == 404
    assert normalised(foreign_basin, hidden.world.entry.public_ref) == normalised(unknown, "spe_" + GHOST)


def test_a_species_ref_is_not_a_transition_states_basin_and_the_error_never_names_the_other_owner(client, db_session):
    species, ts = build(db_session), build_ts_scene(db_session)
    approve(db_session, species)
    body = make(ts.body, target__scope__determination_ref=ts.determination.public_ref)
    assert client.post(URL, json=body).status_code == 200
    declared = species.host.model_declaration.public_ref
    response = client.post(URL, json=make(ts.body, intent="protocol_preferred", comparison_objective="model_fidelity", reference_model_ref=declared))
    assert response.status_code == 422 and species.world.entry.public_ref not in response.text and declared not in response.text
