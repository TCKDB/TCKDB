"""The two warning blocks on a read, and whose words they are in.

``record_warnings`` are the warnings about a record; ``deposit_warnings`` are the ones about the deposit that no one
record could take. Both are rendered in TCKDB's wording; the depositor's own text (a transition-state label, a local
key) reaches only the deposit's owner and curators.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionKind
from app.db.models.common import SubmissionRecordType as RT
from app.db.models.deposit_warning import DepositWarning
from app.db.models.reaction import ReactionEntry
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionStateEntry
from app.services.auth import create_api_key
from app.services.deposit_warning_messages import RENDERED, normalise_field, render_message
from app.services.deposit_warnings import content_hash, note_deposit_targets, store_deposit_warnings
from app.services.record_review import RecordRef, ensure_record_review, set_record_review_status
from app.services.submission import create_submission, link_record
from tests.api.test_api_bundle_upload_warnings import _thermo_record
from tests.api.test_api_ts_contract_621 import _bundle
from tests.services.scientific_read._factories import make_calculation, make_species, make_species_entry, next_inchi_key
from tests.workers.test_upload_worker_warnings import _computed_reaction

BASE = "/api/v1/scientific"


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {"title": "x", "container-title": ["x"], "issued": 1998, "URL": f"https://doi.org/{doi}"},
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _user(session, name: str, role: AppUserRole) -> tuple[AppUser, str]:
    user = AppUser(username=name, role=role)
    session.add(user)
    session.flush()
    _, key = create_api_key(session, user)
    return user, key


def _owner_key(session) -> str:
    owner = session.scalars(select(AppUser).where(AppUser.username == "testuser")).one()
    return create_api_key(session, owner)[1]


def _items(read: dict) -> list[dict]:
    return [i for b in read.get("record_warnings", []) for i in b["items"]] + [
        i for b in read.get("deposit_warnings", []) for i in b["items"]
    ]


def _entry(read: dict, ref: str) -> dict:
    (entry,) = [w for w in read["record_warnings"] if w["record_ref"] == ref]
    return entry


def _owned_calculation(session) -> Calculation:
    species = make_species(session, inchi_key=next_inchi_key("DWREAD"))
    return make_calculation(session, species_entry_id=make_species_entry(session, species).id)


def _read_everything(client, session, body: dict, *, headers: dict | None = None) -> list[dict]:
    """Every detail and per-parent read of the records a computed-reaction deposit made."""
    headers = headers or {}
    reads = []
    for ref in body["calculation_key_refs"].values():
        reads.append(client.get(f"{BASE}/calculations/{ref}", headers=headers))
    tse = session.get(TransitionStateEntry, body["transition_state_entry_id"])
    reads.append(client.get(f"{BASE}/transition-state-entries/{tse.public_ref}", headers=headers))
    rxe = session.get(ReactionEntry, body["reaction_entry_id"])
    reads.append(client.get(f"{BASE}/reaction-entries/{rxe.public_ref}/kinetics", headers=headers))
    for thermo_id in body["thermo_ids"]:
        entry = session.get(Thermo, thermo_id).species_entry
        reads.append(client.get(f"{BASE}/species-entries/{entry.public_ref}/thermo", headers=headers))
    for r in reads:
        assert r.status_code == 200, r.text[:300]
    return [r.json() for r in reads]


def _rename_keys(payload: dict) -> tuple[dict, set[str]]:
    """The payload with every local key given a distinctive name, and the set of new names."""
    names: dict[str, str] = {}

    def fresh(old: str) -> str:
        return names.setdefault(old, f"zq{len(names)}{old.replace('-', '')}zq")

    def walk(node):
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if (k == "key" or k.endswith("_key")) and isinstance(v, str):
                    node[k] = fresh(v)
                elif k.endswith("_keys") and isinstance(v, list):
                    node[k] = [fresh(x) if isinstance(x, str) else x for x in v]
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    # Two passes: a key is declared before another field references it.
    walk(payload)
    walk(payload)
    return payload, set(names.values())


def _deposit_with_labels() -> tuple[dict, set[str]]:
    payload = _computed_reaction()
    payload["transition_state"]["label"] = "TS0"
    payload, keys = _rename_keys(payload)
    return payload, keys | {"TS0"}


# ---------------------------------------------------------------------------
# deposit_warnings: what no one record could take
# ---------------------------------------------------------------------------


def test_every_record_of_a_multi_result_deposit_shows_the_scheme_warnings_under_deposit_warnings(client, db_session):
    body = client.post("/api/v1/uploads/computed-reaction", json=_computed_reaction()).json()
    scheme_codes = {"missing_literature_provenance", "missing_energy_correction_scheme_software"}
    reads = _read_everything(client, db_session, body)
    assert len(reads) >= 5
    for read in reads:
        blocks = read["deposit_warnings"]
        assert len(blocks) == 1, read.get("record_warnings")
        assert scheme_codes <= {i["code"] for i in blocks[0]["items"]}
        # Nothing is duplicated onto a record, and the records' own warnings are untouched.
        assert not scheme_codes & {i["code"] for b in read["record_warnings"] for i in b["items"]}
        # An anonymous reader is not told which submission.
        assert "submission_ref" not in blocks[0]
    thermo_reads = [r for r in reads if any(i["code"] == "composite_delta_prefer_scheme_terms" for i in _items(r))]
    assert thermo_reads, "the composite-delta warning is still about its species' thermo"


def test_deposit_warnings_name_the_submission_to_a_caller_who_authenticates(client, db_session):
    body = client.post("/api/v1/uploads/computed-reaction", json=_computed_reaction()).json()
    from app.db.models.submission import Submission

    submission = db_session.get(Submission, body["submission_id"])
    tse = db_session.get(TransitionStateEntry, body["transition_state_entry_id"])
    read = client.get(f"{BASE}/transition-state-entries/{tse.public_ref}", headers={"X-API-Key": _owner_key(db_session)})
    assert read.status_code == 200
    assert [b["submission_ref"] for b in read.json()["deposit_warnings"]] == [submission.public_ref]


def test_a_single_result_deposit_keeps_its_warnings_on_the_record_and_shows_none_under_deposit_warnings(client, db_session):
    response = client.post("/api/v1/uploads/thermo", json=_thermo_record())
    assert response.status_code == 201, response.text[:300]
    thermo = db_session.get(Thermo, response.json()["id"])
    read = client.get(f"{BASE}/species-entries/{thermo.species_entry.public_ref}/thermo").json()
    codes = {i["code"] for i in _entry(read, thermo.public_ref)["items"]}
    assert {"missing_literature_provenance", "missing_energy_correction_scheme_software"} <= codes
    assert read["deposit_warnings"] == []


def _unplaceable_deposit(session, calc: Calculation) -> None:
    user = session.scalars(select(AppUser).where(AppUser.username == "testuser")).one()
    submission = create_submission(session, created_by=user.id, submission_kind=SubmissionKind.thermo)
    link_record(session, submission=submission, record_type=RT.calculation, record_id=calc.id)
    note_deposit_targets(session, submission.id, [RecordRef(RT.calculation, calc.id)])
    store_deposit_warnings(
        session,
        submission.id,
        [UploadWarning(field="nowhere.at.all", code="converged_opt_no_usable_energy", message="about some record or other")],
    )


def test_an_unplaceable_warning_shows_under_deposit_warnings_and_its_records_read_not_recorded(client, db_session):
    calc = _owned_calculation(db_session)
    _unplaceable_deposit(db_session, calc)
    read = client.get(f"{BASE}/calculations/{calc.public_ref}").json()
    assert _entry(read, calc.public_ref) == {"record_ref": calc.public_ref, "status": "not_recorded", "items": []}
    (block,) = read["deposit_warnings"]
    assert [i["code"] for i in block["items"]] == ["converged_opt_no_usable_energy"]
    assert block["items"][0]["message"] == render_message("converged_opt_no_usable_energy", None)


def test_the_curated_profile_blanks_a_hidden_ref_in_a_deposit_level_context(client, db_session):
    curator, _ = _user(db_session, "dw-curated-curator", AppUserRole.curator)
    visible, hidden = _owned_calculation(db_session), _owned_calculation(db_session)
    for calc, status in ((visible, RecordReviewStatus.approved), (hidden, RecordReviewStatus.rejected)):
        ensure_record_review(db_session, record_type=RT.calculation, record_id=calc.id)
        set_record_review_status(db_session, record_type=RT.calculation, record_id=calc.id, status=status, actor=curator, note="x")
    user = db_session.scalars(select(AppUser).where(AppUser.username == "testuser")).one()
    submission = create_submission(db_session, created_by=user.id, submission_kind=SubmissionKind.thermo)
    link_record(db_session, submission=submission, record_type=RT.calculation, record_id=visible.id)
    context = {"source": hidden.public_ref}
    db_session.add(
        DepositWarning(
            submission_id=submission.id,
            code="sp_energy_payload_log_mismatch",
            field="f",
            message="m",
            context=context,
            content_hash=content_hash("sp_energy_payload_log_mismatch", "f", "m", context),
            check_version=1,
        )
    )
    db_session.flush()
    read = client.get(f"{BASE}/calculations/{visible.public_ref}", params={"profile": "curated"})
    assert read.status_code == 200, read.text[:300]
    (block,) = read.json()["deposit_warnings"]
    (item,) = block["items"]
    assert item["code"] == "sp_energy_payload_log_mismatch" and item["context"] is None
    assert hidden.public_ref not in read.text


# ---------------------------------------------------------------------------
# whose words
# ---------------------------------------------------------------------------


def test_an_anonymous_read_carries_neither_a_label_nor_a_local_key(client, db_session):
    payload, tokens = _deposit_with_labels()
    response = client.post("/api/v1/uploads/computed-reaction", json=payload)
    assert response.status_code == 201, response.text[:300]
    body = response.json()
    # The depositor's own response still says it, as before.
    assert any("TS0" in w["message"] for w in body["warnings"])
    tse = db_session.get(TransitionStateEntry, body["transition_state_entry_id"])
    read = client.get(f"{BASE}/transition-state-entries/{tse.public_ref}").json()
    irc = [i for i in _items(read) if i["code"] == "transition_state_missing_irc_evidence"]
    assert irc
    for item in _items(read):
        assert not any(t in item["message"] or t in item["field"] for t in tokens), item
        assert "depositor_message" not in item and "depositor_field" not in item


def _labelled_ts_bundle() -> tuple[dict, set[str]]:
    bundle, tokens = _rename_keys(_bundle([]))
    bundle["transition_state"]["label"] = "TS0"
    return bundle, tokens | {"TS0"}


@pytest.mark.parametrize("build", [_deposit_with_labels, _labelled_ts_bundle], ids=["aec_vdw_bundle", "ts_bundle"])
def test_every_anonymous_read_of_every_fixture_is_free_of_depositor_labels_and_keys(client, db_session, build):
    payload, tokens = build()
    response = client.post("/api/v1/uploads/computed-reaction", json=payload)
    assert response.status_code == 201, response.text[:300]
    seen = 0
    for read in _read_everything(client, db_session, response.json()):
        for item in _items(read):
            seen += 1
            for text in (item["message"], item["field"], json.dumps(item["context"])):
                assert not any(t in text for t in tokens), (item["code"], text)
            assert "depositor_message" not in item and "depositor_field" not in item
    assert seen > 5, "the fixture earns too few warnings for this to prove anything"


def _ts_read(client, session, body, headers=None):
    tse = session.get(TransitionStateEntry, body["transition_state_entry_id"])
    read = client.get(f"{BASE}/transition-state-entries/{tse.public_ref}", headers=headers or {})
    assert read.status_code == 200
    return read.json()


def test_the_deposit_owner_and_curators_get_the_depositor_text_and_nobody_else_does(client, db_session):
    payload, tokens = _deposit_with_labels()
    body = client.post("/api/v1/uploads/computed-reaction", json=payload).json()
    owner_key = _owner_key(db_session)
    _, curator_key = _user(db_session, "dw-text-curator", AppUserRole.curator)
    _, other_key = _user(db_session, "dw-text-other", AppUserRole.user)

    for key in (owner_key, curator_key):
        read = _ts_read(client, db_session, body, {"X-API-Key": key})
        (irc,) = [i for i in _items(read) if i["code"] == "transition_state_missing_irc_evidence"]
        assert "TS0" in irc["depositor_message"]
        assert irc["depositor_field"] == "transition_state.validation_evidence"
        # The public words are still TCKDB's.
        assert "TS0" not in irc["message"]
        keyed = [i for i in _items(read) if "zq" in i.get("depositor_field", "")]
        assert all("zq" not in i["field"] for i in keyed)

    other = _ts_read(client, db_session, body, {"X-API-Key": other_key})
    anonymous = _ts_read(client, db_session, body)
    for read in (other, anonymous):
        assert _items(read)
        for item in _items(read):
            assert "depositor_message" not in item and "depositor_field" not in item
            assert not any(t in item["message"] or t in item["field"] for t in tokens)


def test_a_depositor_supplied_context_key_survives_on_a_public_read(client, db_session):
    calc = _owned_calculation(db_session)
    user = db_session.scalars(select(AppUser).where(AppUser.username == "testuser")).one()
    submission = create_submission(db_session, created_by=user.id, submission_kind=SubmissionKind.thermo)
    link_record(db_session, submission=submission, record_type=RT.calculation, record_id=calc.id)
    context = {"delta_hartree": 0.5, "depositor_supplied": {"label": "my note"}}
    db_session.add(
        DepositWarning(
            submission_id=submission.id,
            record_type=RT.calculation,
            record_id=calc.id,
            code="sp_energy_payload_log_mismatch",
            field="f",
            message="m",
            context=context,
            content_hash=content_hash("sp_energy_payload_log_mismatch", "f", "m", context),
            check_version=1,
        )
    )
    db_session.flush()
    read = client.get(f"{BASE}/calculations/{calc.public_ref}").json()
    (item,) = _entry(read, calc.public_ref)["items"]
    assert item["context"] == context


def test_the_public_field_replaces_depositor_keys_and_keeps_positions():
    assert normalise_field("species['h'].thermo.workflow_tool_release") == "species[*].thermo.workflow_tool_release"
    assert normalise_field("species['ch3'].calculations['x-freq']") == "species[*].calculations[*]"
    assert normalise_field('species["a b"].statmech') == "species[*].statmech"
    assert normalise_field("kinetics[0].energy_level_of_theory") == "kinetics[0].energy_level_of_theory"
    assert normalise_field("reactants[0].species_entry.term_symbol") == "reactants[0].species_entry.term_symbol"


def test_a_message_uses_the_numbers_the_check_stated_and_nothing_the_depositor_typed():
    text = render_message("charge_mismatch", {"declared": 0, "logged": -1, "software": "Gaussian"})
    assert "(+0)" in text and "(-1)" in text and "Gaussian" in text
    assert render_message("charge_mismatch", {"declared": "TS0"}) == RENDERED["charge_mismatch"]
    assert render_message("no_such_code_yet", None).startswith("The deposit tripped a warning")


# ---------------------------------------------------------------------------
# the rendering table covers every producer code
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[3]

#: Codes a producer reaches by a dynamic expression the static sweep cannot resolve to one literal, listed once.
_DYNAMIC = {
    # hessian_extraction._not_stored(code, ...)
    "hessian_not_stored_geometry_mismatch",
    "hessian_not_stored_no_unique_input_geometry",
    "hessian_not_stored_unparseable",
    "hessian_not_stored_failed",
    "hessian_not_stored_not_frequency_calculation",
    # upload_reconciliation._DEDUCTION_WARNING_CODES and the van der Waals branch of stationary_point
    "n_imag_contradicts_minimum",
    "n_imag_higher_order_saddle",
    "n_imag_suggests_transition_state",
    "electronic_state_contradicts_method",
    "term_symbol_mismatch",
    # composite_result_resolution._DEPOSITED_AS_WARNING_CODE
    "named_composite_deposited_as_opt",
    "named_composite_deposited_as_sp",
    # refs.py _version_warning_code / _method_warning_code
    "software_release_version_is_composite",
    "software_release_name_looks_wrong",
    "level_of_theory_method_names_correction_table",
}

#: Block-tier findings share the producer: they refuse the payload and never reach a read.
_BLOCK_ONLY = {
    "freq_list_exceeds_geometry_degrees_of_freedom",
    "transition_state_no_imaginary_mode",
    "transition_state_reaction_coordinate_not_designated",
    "transition_state_reaction_coordinate_ambiguous",
}


def _swept_codes() -> set[str]:
    files = [
        p
        for root in (_ROOT / "backend" / "app", _ROOT / "schemas" / "python" / "tckdb-schemas" / "tckdb_schemas")
        for p in root.rglob("*.py")
        if "/importers/" not in str(p)
    ]
    trees = [ast.parse(p.read_text()) for p in files]
    consts: dict[str, set[str]] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    consts.setdefault(node.targets[0].id, set()).add(node.value.value)
    codes: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name not in ("UploadWarning", "StationaryPointFinding"):
                continue
            for kw in node.keywords:
                if kw.arg != "code":
                    continue
                value = kw.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    codes.add(value.value)
                else:
                    base = value.id if isinstance(value, ast.Name) else value.attr if isinstance(value, ast.Attribute) else None
                    if base and base.startswith("W_") and len(consts.get(base, ())) == 1:
                        codes |= consts[base]
    return codes


def test_the_rendering_table_covers_every_code_a_producer_can_emit():
    swept = (_swept_codes() | _DYNAMIC) - _BLOCK_ONLY
    assert len(swept) >= 70, f"the sweep found only {len(swept)} codes: it is not looking at the producers"
    assert swept - set(RENDERED) == set(), "a producer code has no public sentence"
    assert set(RENDERED) - swept == set(), "a sentence is for a code no producer emits"
    for code, sentence in RENDERED.items():
        assert sentence and "{" not in sentence and "'" not in sentence.replace("'s", ""), code
