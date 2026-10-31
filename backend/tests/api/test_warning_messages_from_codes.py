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

import json
from pathlib import Path

import pytest
from tckdb_schemas.deposit_warning_messages import RENDERED, render_message
from tckdb_schemas.fragments.refs import OrdinaryLevelOfTheoryRef, SoftwareReleaseRef
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.transition_state import TransitionStateEntry
from app.services import hessian_extraction
from tests import warning_construction_guard as construction_guard
from tests.api.test_deposit_warning_reads import _computed_reaction, _items, _read_everything
from tests.workers.test_upload_worker_warnings import CASES

_ROOT = Path(__file__).resolve().parents[3]
_SOURCES = (_ROOT / "backend" / "app", _ROOT / "schemas" / "python" / "tckdb-schemas" / "tckdb_schemas")

SENTINEL_TS = "ZZQ-TS-LABEL-7"


# ---------------------------------------------------------------------------
# static: one constructor
# ---------------------------------------------------------------------------


def _sources() -> list[Path]:
    return [p for root in _SOURCES for p in sorted(root.rglob("*.py")) if "/build/" not in str(p)]


def test_no_producer_builds_or_edits_an_upload_warning_by_hand():
    found = [v for path in _sources() for v in construction_guard.violations(path.read_text(), str(path))]
    assert found == [], "\n".join(found)
    from_code = sum(path.read_text().count("UploadWarning.from_code(") for path in _sources())
    # The scan above found every producer, so a count of zero would mean it looked at the wrong tree.
    assert from_code >= 60, from_code


@pytest.mark.parametrize(
    ("snippet", "needle"),
    [
        ("w = UploadWarning(field='f', code='c', message='m')", "directly"),
        ("from tckdb_schemas.upload_warning import UploadWarning as _UW\nw = _UW(field='f', code='c', message='m')", "directly"),
        ("import tckdb_schemas.upload_warning as uw\nw = uw.UploadWarning(field='f', code='c', message='m')", "directly"),
        ("_UW = UploadWarning\nw = _UW(field='f', code='c', message='m')", "directly"),
        ("w = UploadWarning.model_validate({'field': 'f', 'code': 'c', 'message': 'm'})", "model_validate"),
        ("w = UploadWarning.model_construct(field='f', code='c', message='m')", "model_construct"),
        ("from x import UploadWarning as UW\nw = UW.model_validate_json('{}')", "model_validate_json"),
        ("def f(w: UploadWarning):\n    return w.model_copy(update={'message': 'x'})", "model_copy"),
        ("def f(w):\n    return w.model_copy(update={**extra, 'field': 'f'})", "model_copy"),
        ("def f(w: UploadWarning, m):\n    return w.model_copy(update=m)", "model_copy"),
        ("def f(w: UploadWarning):\n    w.message = 'x'", ".message"),
        ("def f(w: UploadWarning):\n    setattr(w, 'message', 'x')", "after the fact"),
        ("def f(w: UploadWarning):\n    object.__setattr__(w, 'message', 'x')", "after the fact"),
    ],
)
def test_the_construction_guard_flags_every_way_around_from_code(snippet, needle):
    found = construction_guard.violations(snippet)
    assert found and any(needle in v for v in found), (snippet, found)


@pytest.mark.parametrize(
    "snippet",
    [
        "w = UploadWarning.from_code(field='f', code='c')",
        "def f(w: UploadWarning):\n    return w.model_copy(update={'field': 'a.' + w.field})",
        "def g(items: list[UploadWarning]):\n    return [i.field for i in items]",
        "def f(a, i):\n    return a.model_copy(update={'participant_index': i})",
    ],
)
def test_the_construction_guard_leaves_the_legitimate_uses_alone(snippet):
    assert construction_guard.violations(snippet) == []


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
                "hessian_not_stored_failed", f"{sentinel}.log", "store_failed", f"calculations['{sentinel}'].artifacts"
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


# ---------------------------------------------------------------------------
# a sentence says what happened on its own branch
# ---------------------------------------------------------------------------

_TAU = {"tau_cm1": 30.0}


@pytest.mark.parametrize(
    ("basis", "phrase"),
    [
        ("analytic_default", "at the protocol that produced them"),
        ("finite_difference_gradient", "at the protocol that produced them"),
        ("assumed_analytic_default", "at the protocol assumed for them"),
        ("assumed_finite_difference_energy", "at the protocol assumed for them"),
        ("protocol_not_recorded", "at a protocol that is not recorded"),
    ],
)
def test_the_tau_sentences_name_the_protocol_the_basis_allows(basis, phrase):
    modes = [{"mode_index": 2, "magnitude_cm1": 20.0, "disposition": "torsion"}]
    context = {**_TAU, "tau_basis": basis}
    minimum = render_message(
        "minimum_imaginary_modes_below_tau", {**context, "n_imag": 1, "imaginary_modes_cm1": [20.0]}
    )
    below = render_message(
        "transition_state_extra_imaginary_modes_below_tau",
        {**context, "extra_imaginary_mode_count": 1, "extra_imaginary_modes": modes},
    )
    assert phrase in minimum and phrase in below
    other = {"at the protocol that produced them", "at the protocol assumed for them", "at a protocol that is not recorded"}
    assert not any(o in minimum for o in other - {phrase})
    # tau's reason rides with every one of them, as TCKDB's words for the basis.
    assert "Why tau = 30 cm-1: " in minimum and "Remediation, in order:" in minimum


def test_the_exact_tau_sentence_when_the_protocol_is_assumed():
    text = render_message(
        "transition_state_extra_imaginary_mode_above_tau",
        {
            **_TAU,
            "tau_basis": "assumed_analytic_default",
            "extra_imaginary_mode_count": 1,
            "extra_imaginary_modes": [{"mode_index": 2, "magnitude_cm1": 42.0, "disposition": None}],
        },
    )
    assert text.startswith(
        "The transition state has an imaginary mode at or above tau = 30 cm-1 besides its reaction coordinate "
        "(mode 2 at 42.0 cm-1 (undeclared)), so at the protocol assumed for it the negative curvature is real "
        "rather than numerical noise."
    )
    assert "Why tau = 30 cm-1: 'freq.hessian_method' is not recorded, so this is an assumption" in text
    assert "a consumer treating it as a plain first-order saddle would be wrong" in text


def test_the_deduction_sentences_name_what_the_evidence_was():
    td = render_message(
        "electronic_state_contradicts_method",
        {"deduced": "excited", "evidence": "excited_state_job_type", "confidence": "definitive"},
    )
    method = render_message(
        "electronic_state_contradicts_method",
        {"deduced": "excited", "evidence": "excited_state_method", "confidence": "definitive"},
    )
    assert td == (
        "The declared electronic state disagrees with the value that the excited-state (TD-DFT) job type of the "
        "calculation indicates (excited)."
    )
    assert method == (
        "The declared electronic state disagrees with the value that the excited-state method of the calculation "
        "indicates (excited)."
    )
    assert "job type" not in method and "method of the calculation" not in td


def test_the_log_implied_electronic_energy_is_said_to_be_log_implied():
    text = render_message(
        "composite_energy_log_mismatch",
        {
            "log_method": "cbs-qb3",
            "disagreements": [
                {"quantity": "e0_hartree", "deposited_hartree": -1.0, "log_hartree": -1.5},
                {"quantity": "electronic_energy_hartree", "deposited_hartree": -2.0, "log_hartree": -2.5},
            ],
        },
    )
    assert "e0_hartree -1.000000 Ha against the log's -1.500000 Ha" in text
    assert "electronic_energy_hartree -2.000000 Ha against the log-implied -2.500000 Ha (the log's 0 K energy minus its ZPE)" in text


def test_a_hess_file_that_held_no_matrix_does_not_say_a_hessian_was_found():
    sent = render_message("hessian_not_stored_unparseable", {"reason": "sent_as_hessian"})
    block = render_message("hessian_not_stored_unparseable", {"reason": "log_block_unreadable"})
    assert sent == (
        "An artifact sent as a Hessian contained no complete force-constant matrix that could be read, so no Hessian "
        "was found to store. The artifact itself is stored; only the Hessian is not."
    )
    assert block == (
        "A Hessian was found in an uploaded artifact but not stored: the log has a force-constant block but no "
        "complete, trustworthy matrix could be read from it (it may be truncated). The artifact itself is stored; "
        "only the Hessian is not."
    )


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (
            {"action": "split_banner_with_revision"},
            "A software version was a parsed banner carrying the program name and a revision label. It was "
            "normalised to a version and a revision (the leading package name was stripped and the trailing "
            "revision label was split out).",
        ),
        (
            {"action": "split_banner_name_only"},
            "A software version carried the software's own name with it. It was normalised to the version alone "
            "(the leading package name was stripped).",
        ),
        (
            {"action": "revision_already_supplied"},
            "A software version looks like a parsed software banner embedding its own revision label, but a "
            "revision was also supplied. Both fields were left exactly as sent rather than choosing between them.",
        ),
    ],
)
def test_each_branch_of_the_composite_software_version_says_what_it_did(context, expected):
    assert render_message("software_release_version_is_composite", context) == expected


def test_the_three_software_version_branches_come_from_the_ref():
    split = SoftwareReleaseRef(name="gaussian", version="gaussian 09, Revision D.01").version_warning()
    name_only = SoftwareReleaseRef(name="orca", version="orca 6.0.0").version_warning()
    kept = SoftwareReleaseRef(name="gaussian", version="gaussian 09, Revision D.01", revision="B.01").version_warning()
    assert [w.context["action"] for w in (split, name_only, kept)] == [
        "split_banner_with_revision",
        "split_banner_name_only",
        "revision_already_supplied",
    ]


# ---------------------------------------------------------------------------
# what was said before, still said (remedies and consequences that need no depositor text)
# ---------------------------------------------------------------------------

_KEPT = {
    "named_composite_deposited_as_sp": ({"calculation_type": "sp"}, ["type 'composite'", "program_run", "refused once producers"]),
    "named_composite_deposited_as_opt": ({"calculation_type": "opt"}, ["type 'composite'", "program_run", "refused once producers"]),
    "transition_state_extra_imaginary_modes_not_assessable": (
        {**_TAU, "tau_basis": "protocol_not_recorded", "extra_imaginary_mode_count": 2},
        ["no frequency list was deposited", "complete signed, unrounded frequency list"],
    ),
    "composite_input_geometry_undeclared": (None, ["Declare the single point's input geometry"]),
    "composite_log_method_mismatch": (
        {"log_method": "cbs-qb3"},
        ["Attach the log of the run that produced this calculation, or correct the level of theory"],
    ),
    "scf_stability_source_conformer_unverified": (None, ["give both calculations a conformer_key"]),
    "scf_stability_source_level_mismatch": (None, ["wavefunction, not the carrying calculation's"]),
    "literature_title_mismatch": (None, ["verify the identifier is correct"]),
    "literature_year_mismatch": ({"supplied_year": 1999, "fetched_year": 2024}, ["verify the identifier is correct"]),
    "n_imag_contradicts_minimum": ({"n_imag": 1}, ["tighter integration grid"]),
    "n_imag_higher_order_saddle": ({"n_imag": 2}, ["tighter integration grid"]),
    "n_imag_suggests_transition_state": (
        {"imaginary_frequency_cm1": 900.0, "ts_minimum_cm1": 50.0},
        ["deposit it as a transition state"],
    ),
    "freq_parser_extracted_but_modes_missing": (None, ["re-run the parser or attach the mode list"]),
    "missing_kinetics_transition_state_interpretation": (None, ["master-equation solve", "role `transition_state`"]),
    "statmech_frequency_scale_factor_software_mismatch": (None, ["a deliberate choice, not an error", "confirm this is intentional"]),
    "network_state_energy_sum_not_compared": (
        {"reasons": ["stated_precision_unknown"]},
        ["2625.499639", "energy_precision_kj_mol"],
    ),
    "transition_state_extra_imaginary_mode_above_tau": (
        {
            **_TAU,
            "tau_basis": "analytic_default",
            "extra_imaginary_mode_count": 1,
            "extra_imaginary_modes": [{"mode_index": 2, "magnitude_cm1": 42.0, "disposition": "torsion"}],
        },
        ["torsional maximum", "first-order saddle would be wrong"],
    ),
    "transition_state_imaginary_frequency_too_small": (
        {**_TAU, "tau_basis": "analytic_default", "reaction_coordinate_cm1": 20.0, "ts_minimum_cm1": 50.0},
        ["flat and variational barriers"],
    ),
    "reported_network_solve": (None, ["cannot be re-derived", "cannot be re-solved outside the reported"]),
    "network_wide_energy_transfer_scope": (None, ["(well, collider) pair"]),
    "reaction_atom_map_absent": (
        {"deposit_path": "atom_map_field"},
        ["reaction-path degeneracy", "kinetic isotope effect", "will not infer"],
    ),
    "freq_list_bent_mode_count_for_linear_geometry": (
        {"n_modes": 3, "n_atoms": 3, "linear_count": 4, "transverse_ratio": 0.0, "collinear_max_transverse_ratio": 1e-3},
        ["threshold of 0.001 for calling a geometry linear", "de-duplicates"],
    ),
    "freq_list_linear_mode_count_for_bent_geometry": (
        {"n_modes": 4, "n_atoms": 3, "bent_count": 3, "transverse_ratio": 0.2, "bent_min_transverse_ratio": 3e-2},
        ["threshold of 0.03 for calling a geometry bent", "rigid-body translation/rotation"],
    ),
    "freq_list_incomplete_for_geometry": (
        {"n_modes": 1, "n_atoms": 3, "minimum_modes": 3},
        ["silent about the ones it omits", "partial Hessian"],
    ),
    "transition_state_missing_irc_evidence": (None, ["do not stand in for it"]),
    "composite_frequency_level_differs_from_recipe": (None, ["Link the frequency calculation at the recipe's own level"]),
    "missing_energy_correction_scheme_software": ({"scheme_kind": "atom_hf"}, ["does not trigger this warning"]),
    "ambiguous_energy_correction_scheme_without_literature": (None, ["same correction deposited twice"]),
    "software_release_name_looks_wrong": (None, ["stale or default name"]),
    "level_of_theory_method_names_correction_table": ({"method_stem": "cbs-qb3"}, ["Send method=cbs-qb3", "energy correction scheme"]),
    "network_energy_zpe_source_absent": (
        {"correction_convention": "electronic_plus_zpe", "subject": "state_energy"},
        ["zpe_calculation_key"],
    ),
    "composite_delta_prefer_scheme_terms": (None, ["composite-scheme terms"]),
    "missing_frequency_scale_factor_provenance": (None, ["value=1.0"]),
}


@pytest.mark.parametrize("code", sorted(_KEPT))
def test_the_remedies_and_consequences_the_old_message_carried_are_still_said(code):
    context, phrases = _KEPT[code]
    text = render_message(code, context)
    for phrase in phrases:
        assert phrase in text, (code, phrase, text)


def test_every_tau_basis_has_a_reason():
    from tckdb_schemas.deposit_warning_messages import _TAU_WHY
    from tckdb_schemas.stationary_point import TauBasis

    assert set(_TAU_WHY) == {b.value for b in TauBasis}


# ---------------------------------------------------------------------------
# hand copies pinned to what they copy
# ---------------------------------------------------------------------------


def test_the_not_compared_reason_tokens_are_the_services_constants():
    from tckdb_schemas.deposit_warning_messages import NOT_COMPARED_REASONS

    from app.services import network_energy_sources, transition_state_validation

    found = {
        value
        for module in (network_energy_sources, transition_state_validation)
        for name, value in vars(module).items()
        if name.startswith("NOT_COMPARED_") and isinstance(value, str)
    }
    assert found and found == set(NOT_COMPARED_REASONS)


def test_the_stereo_and_composite_reason_tokens_are_the_producers_constants():
    from tckdb_schemas import composite_total
    from tckdb_schemas.deposit_warning_messages import (
        COMPOSITE_TOTAL_UNVERIFIABLE_REASONS,
        STEREO_NOT_REPRESENTED_REASONS,
    )

    from app.chemistry import stereo

    stereo_tokens = {v for k, v in vars(stereo).items() if k.startswith("R_") and isinstance(v, str)}
    assert stereo_tokens >= set(STEREO_NOT_REPRESENTED_REASONS)
    composite = {v for k, v in vars(composite_total).items() if k.startswith("REASON_") and isinstance(v, str)}
    assert composite == set(COMPOSITE_TOTAL_UNVERIFIABLE_REASONS)


# ---------------------------------------------------------------------------
# the public path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "public"),
    [
        ("species['h'].thermo", "species[*].thermo"),
        ("transition_states[ts_elim].validation_evidence", "transition_states[*].validation_evidence"),
        ("transition_states['ts_elim']", "transition_states[*]"),
        ("species['o'k'].thermo", "species[*].thermo"),
        ('species["o\'k"].thermo', "species[*].thermo"),
        ("species['a\\'b'].calculations['c d'].freq", "species[*].calculations[*].freq"),
        ("species['x]y'].statmech", "species[*].statmech"),
        ("species[ch3].conformers['c1'].calculation", "species[*].conformers[*].calculation"),
        ("kinetics[0].reactants[12].x", "kinetics[0].reactants[12].x"),
        ("species[*].thermo", "species[*].thermo"),
    ],
)
def test_the_public_field_replaces_every_key_and_keeps_only_integer_positions(field, public):
    from tckdb_schemas.deposit_warning_messages import normalise_field

    out = normalise_field(field)
    assert out == public, (field, out)


def test_a_key_with_an_embedded_quote_reaches_no_public_field():
    from tckdb_schemas.deposit_warning_messages import normalise_field

    for key in ("o'k", 'a"b', "a'] b", "x]y", "ZZQ-q'z"):
        for field in (f"species[{key!r}].thermo", f"species['{key}'].thermo", f"transition_states[{key}].x"):
            if "]" in key and "'" + key + "'" in field and key == "a'] b":
                continue  # a hand-quoted key that itself contains the closing quote and bracket is ambiguous
            public = normalise_field(field)
            assert "ZZQ" not in public and "o'k" not in public, (field, public)


# ---------------------------------------------------------------------------
# the shape of depositor_supplied
# ---------------------------------------------------------------------------


def test_from_code_drops_entries_that_hold_nothing_and_refuses_a_wrong_shape():
    warning = UploadWarning.from_code(
        field="f",
        code="composite_total_unverifiable",
        context={"reason": "no_total_deposited", "depositor_supplied": {"term_key": None, "input": "x", "empty": []}},
    )
    assert warning.context["depositor_supplied"] == {"input": "x"}
    none_left = UploadWarning.from_code(
        field="f",
        code="composite_total_unverifiable",
        context={"reason": "no_total_deposited", "depositor_supplied": {"term_key": None}},
    )
    assert none_left.context == {"reason": "no_total_deposited"}
    for bad in ({"n": 3}, {"n": [1, 2]}, {"n": {"a": "b"}}, {"n": [["a"]]}):
        with pytest.raises(TypeError):
            UploadWarning.from_code(field="f", code="charge_mismatch", context={"depositor_supplied": bad})


def test_the_context_rule_catches_a_label_moved_out_of_the_marked_key():
    from tckdb_schemas.deposit_warning_messages import context_problems

    assert context_problems("scf_stability_source_level_mismatch", {"depositor_supplied": {"calculation_key": "k"}}) == []
    moved = context_problems("scf_stability_source_level_mismatch", {"calculation_key": "ZZQ-key"})
    assert moved and "outside depositor_supplied" in moved[0]
    assert context_problems("term_symbol_contradicts_ground_term", {"element": "3P0"})
    assert context_problems("reaction_reversible_twin", {"twin_ref": "ZZQ-label"})
    assert context_problems("no_such_code", None)


def test_software_is_a_token_in_a_sentence():
    text = render_message("charge_mismatch", {"declared": 0, "logged": -1, "software": "Gaussian 16 ZZQ label"})
    assert "ZZQ" not in text
