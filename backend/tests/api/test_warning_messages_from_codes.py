"""A warning's message is TCKDB's sentence for its code, built from the code and the context (#838).

The owner rule: no depositor free text presented as TCKDB's words, on any client. A deposit-time warning used to build
its ``message`` from an f-string that quoted a transition-state label, a local payload key, a conformer label or a file
name, so the upload response and the job result said the depositor's words in TCKDB's voice. Every producer now builds
its warning with ``UploadWarning.from_code``, whose message is :func:`render_message` of the code and the context: the
same call a public read makes on the stored row.

How these tests pin it
----------------------
* **Static**: no source file builds ``UploadWarning(...)`` directly. The one constructor is ``from_code``.
* **Sweep**: every upload kind is deposited with sentinel strings (``ZZQ...``) in every local key and label, and every
  warning that comes back must (a) be exactly ``render_message(code, context)`` and (b) contain no sentinel in its
  message. The positives (the warning was emitted, with its code) are asserted before the absences, and the number of
  distinct codes reached has a floor, so the sweep cannot pass over an empty set.
* **One sentence**: the upload response, the job result and the public read say the same thing for the same warning.
* **Labels**: a label that helps the depositor find the problem is under ``context["depositor_supplied"]``, shown to
  the writer in the response, and never read into the sentence.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from tckdb_schemas.deposit_warning_messages import RENDERED, render_message
from tckdb_schemas.fragments.refs import OrdinaryLevelOfTheoryRef, SoftwareReleaseRef
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.transition_state import TransitionStateEntry
from app.services import hessian_extraction
from tests.api.test_deposit_warning_reads import _computed_reaction, _items, _read_everything
from tests.workers.test_upload_worker_warnings import CASES

_ROOT = Path(__file__).resolve().parents[3]
_SOURCES = (_ROOT / "backend" / "app", _ROOT / "schemas" / "python" / "tckdb-schemas" / "tckdb_schemas")

SENTINEL_TS = "ZZQ-TS-LABEL-7"


# ---------------------------------------------------------------------------
# static: one constructor
# ---------------------------------------------------------------------------


def _calls(name_ok) -> list[tuple[Path, int]]:
    hits = []
    for root in _SOURCES:
        for path in root.rglob("*.py"):
            if "/build/" in str(path):
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and name_ok(node.func):
                    hits.append((path, node.lineno))
    return hits


def test_no_producer_builds_an_upload_warning_by_hand():
    direct = _calls(lambda f: isinstance(f, ast.Name) and f.id == "UploadWarning")
    assert direct == [], f"build these with UploadWarning.from_code: {direct}"
    via_code = _calls(
        lambda f: isinstance(f, ast.Attribute)
        and f.attr == "from_code"
        and isinstance(f.value, ast.Name)
        and f.value.id == "UploadWarning"
    )
    # The sweep above found every producer, so a count of zero would mean it looked at the wrong tree.
    assert len(via_code) >= 60, len(via_code)


def test_from_code_is_the_sentence_for_the_code_and_the_context():
    context = {"declared": 0, "logged": -1, "software": "Gaussian"}
    warning = UploadWarning.from_code(field="f", code="charge_mismatch", context=context)
    assert warning.message == render_message("charge_mismatch", context)
    assert warning.context == context
    assert "(+0)" in warning.message and "(-1)" in warning.message


# ---------------------------------------------------------------------------
# sweep
# ---------------------------------------------------------------------------


def _sentinelise(payload: dict, prefix: str) -> tuple[dict, set[str]]:
    """Every local key and label in ``payload`` renamed to a string that cannot occur by chance."""
    names: dict[str, str] = {}

    def fresh(old: str) -> str:
        return names.setdefault(old, f"ZZQ{prefix}{len(names)}-{old.replace('-', '')}")

    def walk(node):
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if (k == "key" or k.endswith("_key")) and isinstance(v, str):
                    node[k] = fresh(v)
                elif k.endswith("_keys") and isinstance(v, list):
                    node[k] = [fresh(x) if isinstance(x, str) else x for x in v]
                elif k == "label" and isinstance(v, str):
                    node[k] = fresh(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    walk(payload)
    if "transition_state" in payload and isinstance(payload["transition_state"], dict):
        payload["transition_state"]["label"] = SENTINEL_TS
        names["__ts__"] = SENTINEL_TS
    return payload, set(names.values())


def _extra_deposits() -> list[tuple[str, str, dict, set[str]]]:
    """``(name, url, payload, codes it must earn)`` for warnings the nine worker payloads do not reach."""
    from tests.api import test_api_frequency_linearity as linearity
    from tests.api import test_api_frequency_list_completeness as completeness
    from tests.api import test_api_uploads as uploads
    from tests.api.test_api_monatomic_electronic import _levels

    def conformer(freq_calc: dict, kind: str) -> dict:
        payload = uploads._hydrogen_conformer_payload()
        payload["species_entry"]["species_entry_kind"] = kind
        payload["additional_calculations"] = [freq_calc]
        return payload

    def atom(term: str, levels: list | None) -> dict:
        body = {
            "species_entry": {"smiles": "[O]", "charge": 0, "multiplicity": 3, "term_symbol": term},
            "scientific_origin": "computed",
            "statmech_treatment": "rrho",
            "external_symmetry": 1,
        }
        if levels is not None:
            body["electronic_levels"] = levels
        return body

    ts_modes = [-1300.0, -20.0, -13.0, 800.0, 1400.0]
    return [
        (
            "linear_count_for_bent",
            "/api/v1/uploads/conformers",
            linearity._conformer_payload(
                smiles="O", xyz_text=linearity._WATER_XYZ, frequencies=linearity._WATER_PLUS_ONE, label="water"
            ),
            {"freq_list_linear_mode_count_for_bent_geometry"},
        ),
        (
            "bent_count_for_linear",
            "/api/v1/uploads/conformers",
            linearity._conformer_payload(
                smiles="O=C=O", xyz_text=linearity._CO2_XYZ, frequencies=linearity._CO2_DEDUPLICATED, label="co2"
            ),
            {"freq_list_bent_mode_count_for_linear_geometry"},
        ),
        (
            "incomplete_list",
            "/api/v1/uploads/conformers",
            completeness._conformer_payload(
                smiles="O", xyz_text=linearity._WATER_XYZ, frequencies=[1595.0], label="water-short"
            ),
            {"freq_list_incomplete_for_geometry"},
        ),
        (
            "vdw_stiff_mode",
            "/api/v1/uploads/conformers",
            conformer(uploads._freq_calc(n_imag=1, imag_freq_cm1=-900.0), "vdw_complex"),
            {"n_imag_contradicts_minimum", "n_imag_suggests_transition_state"},
        ),
        (
            "vdw_two_modes",
            "/api/v1/uploads/conformers",
            conformer(uploads._freq_calc(n_imag=2, imag_freq_cm1=-30.0), "vdw_complex"),
            {"n_imag_higher_order_saddle"},
        ),
        (
            "minimum_soft_mode",
            "/api/v1/uploads/conformers",
            conformer(uploads._freq_calc(n_imag=1, imag_freq_cm1=-20.0), "minimum"),
            {"minimum_imaginary_modes_below_tau"},
        ),
        (
            "ts_extra_modes_below_tau",
            "/api/v1/uploads/transition-states",
            uploads._transition_state_payload(
                n_imag=3,
                imag_freq_cm1=-1300.0,
                frequencies=ts_modes,
                reaction_coordinate_mode_index=1,
                dispositions=uploads._MOTIVATING_DISPOSITIONS,
            ),
            {"transition_state_extra_imaginary_modes_below_tau"},
        ),
        (
            "ts_extra_mode_above_tau",
            "/api/v1/uploads/transition-states",
            uploads._transition_state_payload(
                n_imag=2,
                imag_freq_cm1=-1300.0,
                frequencies=[-1300.0, -120.0, 800.0, 1400.0],
                reaction_coordinate_mode_index=1,
                dispositions={2: "torsion"},
            ),
            {"transition_state_extra_imaginary_mode_above_tau"},
        ),
        (
            "atom_without_levels",
            "/api/v1/uploads/statmech",
            atom("3P", None),
            {"missing_atomic_electronic_levels"},
        ),
        (
            "atom_wrong_ground_term",
            "/api/v1/uploads/statmech",
            atom("3P0", _levels((0.0, 1))),
            {"term_symbol_contradicts_ground_term"},
        ),
        (
            "atom_wrong_level_order",
            "/api/v1/uploads/statmech",
            atom("3P", _levels((0.0, 1), (158.265, 3), (226.977, 5))),
            {"atomic_electronic_degeneracy_contradicts_term"},
        ),
    ]


def _deposit_everything(client) -> list[tuple[str, dict, set[str]]]:
    """``(kind, warning, sentinels)`` for every warning every upload kind earns."""
    warnings = []
    drivers = [(kind.value, url, build(), set(required)) for kind, (build, url, _j, required) in CASES.items()]
    drivers += _extra_deposits()
    for kind, url, built, required in drivers:
        payload, sentinels = _sentinelise(built, kind)
        savepoint = client._db_session.begin_nested()
        try:
            response = client.post(url, json=payload)
        finally:
            savepoint.rollback()
        assert response.status_code == 201, (kind, response.text[:400])
        emitted = response.json()["warnings"]
        # Positives first: the warnings this kind is known to earn really came back.
        assert required <= {w["code"] for w in emitted}, (kind, [w["code"] for w in emitted])
        warnings.extend((kind, w, sentinels) for w in emitted)
    return warnings


#: Distinct warning codes the sweep reaches, measured at 32 (70 warnings): the nine upload kinds and eleven further
#: deposits, each with sentinel keys and labels. The ref-level and file-level producers are driven directly below.
#: The floor sits just under the measured count.
SWEEP_CODE_FLOOR = 31


def test_no_warning_carries_a_depositor_string_and_each_is_the_sentence_for_its_code(client):
    swept = _deposit_everything(client)
    codes = {w["code"] for _kind, w, _s in swept}
    assert len(swept) >= 65, len(swept)
    assert len(codes) >= SWEEP_CODE_FLOOR, (len(codes), sorted(codes))

    for kind, warning, sentinels in swept:
        assert warning["message"] == render_message(warning["code"], warning["context"]), (kind, warning["code"])
        assert warning["code"] in RENDERED, warning["code"]
        # The sentence never has a sentinel; the field may (it is the depositor's own path).
        assert not any(s in warning["message"] for s in sentinels), (kind, warning["code"], warning["message"])
        # In the context a depositor string is allowed in one place only: the marked key.
        unmarked = {k: v for k, v in (warning["context"] or {}).items() if k != "depositor_supplied"}
        assert not any(s in json.dumps(unmarked) for s in sentinels), (kind, warning["code"], unmarked)
    # A value the depositor typed that cannot be a sentinel (a term symbol) must not reach a rendered key either: the
    # element and ground term are TCKDB's own (the NIST table), whatever term was declared.
    (ground,) = [w for _k, w, _s in swept if w["code"] == "term_symbol_contradicts_ground_term"]
    assert ground["context"] == {"element": "O", "ground_term": "3P", "ground_j": 2.0}
    # Something really carried a sentinel in its field, or the sweep would pass over a payload it never renamed.
    assert any(s in w["field"] for _k, w, ss in swept for s in ss), "no field reached carries a local key"


def test_the_ref_level_and_file_level_producers_carry_no_depositor_string():
    sentinel = "ZZQ-ref-sentinel"
    method = "CBS-QB3-paraskevas"  # a method is a closed shape here: the string itself is what the depositor typed
    produced = [
        (SoftwareReleaseRef(name=sentinel, version=f"{sentinel} 09, Revision D.01").version_warning(f"c['{sentinel}']."), sentinel),
        (SoftwareReleaseRef(name=sentinel, version="ORCA 6.0.0").version_warning(), sentinel),
        (SoftwareReleaseRef(name=sentinel, version=f"{sentinel} 09 Revision D", revision="B.01").version_warning(), sentinel),
        (OrdinaryLevelOfTheoryRef(method=method).method_warning(), method),
        (
            hessian_extraction._not_stored(
                "hessian_not_stored_failed", f"{sentinel}.log", "storing it failed.", f"calculations['{sentinel}'].artifacts"
            ),
            sentinel,
        ),
    ]
    # Positives first: every producer emitted, with the code it is known to carry.
    assert all(w is not None for w, _ in produced), [i for i, (w, _) in enumerate(produced) if w is None]
    assert {w.code for w, _ in produced} == {
        "software_release_version_is_composite",
        "software_release_name_looks_wrong",
        "level_of_theory_method_names_correction_table",
        "hessian_not_stored_failed",
    }
    for warning, typed in produced:
        assert warning.message == render_message(warning.code, warning.context)
        assert typed not in warning.message and sentinel not in warning.message
        unmarked = {k: v for k, v in warning.context.items() if k != "depositor_supplied"}
        assert typed not in json.dumps(unmarked)
        # What helps the depositor is named, under the marked key.
        assert typed in json.dumps(warning.context["depositor_supplied"])


# ---------------------------------------------------------------------------
# one sentence: response, read
# ---------------------------------------------------------------------------


def test_the_response_and_the_public_read_give_the_same_sentence(client, db_session):
    payload, sentinels = _sentinelise(_computed_reaction(), "read")
    response = client.post("/api/v1/uploads/computed-reaction", json=payload)
    assert response.status_code == 201, response.text[:300]
    body = response.json()
    said = {(w["code"], json.dumps(w["context"], sort_keys=True)): w["message"] for w in body["warnings"]}
    assert "transition_state_missing_irc_evidence" in {c for c, _ in said}

    matched = 0
    for read in _read_everything(client, db_session, body):
        for item in _items(read):
            key = (item["code"], json.dumps(item["context"], sort_keys=True))
            if key in said:
                matched += 1
                assert item["message"] == said[key], item["code"]
                assert not any(s in item["message"] for s in sentinels)
    assert matched >= 5, "the reads and the response share too few warnings for this to prove anything"


def test_a_helpful_label_is_in_depositor_supplied_and_not_in_the_sentence(client, db_session):
    payload, sentinels = _sentinelise(_computed_reaction(), "lab")
    response = client.post("/api/v1/uploads/computed-reaction", json=payload)
    assert response.status_code == 201, response.text[:300]
    body = response.json()

    (irc,) = [w for w in body["warnings"] if w["code"] == "transition_state_missing_irc_evidence"]
    # Shown to the writer, in the response, under the marked key ...
    assert irc["context"]["depositor_supplied"] == {"transition_state_label": SENTINEL_TS}
    # ... and never read into the sentence, which is the fixed one for the code.
    assert SENTINEL_TS not in irc["message"]
    assert irc["message"] == RENDERED["transition_state_missing_irc_evidence"]

    tse = db_session.get(TransitionStateEntry, body["transition_state_entry_id"])
    read = client.get(f"/api/v1/scientific/transition-state-entries/{tse.public_ref}").json()
    (public,) = [i for i in _items(read) if i["code"] == "transition_state_missing_irc_evidence"]
    assert public["message"] == irc["message"]
    assert SENTINEL_TS not in public["message"] and SENTINEL_TS not in public["field"]


@pytest.mark.parametrize("code", sorted(RENDERED))
def test_every_fixed_sentence_is_free_of_a_placeholder_left_behind(code):
    assert "{" not in RENDERED[code] and "}" not in RENDERED[code], code
