"""The electronic-state label and term-symbol grammar, driven by the shipped fixture.

``electronic_state_examples.json`` is the file a frontend parity test reads. Driving the tests
from that same file is what proves the fixture is what is enforced: an example added to it that
the Python grammar disagrees with fails here, and a grammar change that the file does not
follow fails here.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

from tckdb_schemas import electronic_state as es
from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload

EXAMPLES = es.load_examples()
FIELDS = {
    "electronic_state_label": (es.is_valid_electronic_state_label, es.check_electronic_state_label, es.ELECTRONIC_STATE_LABEL_INVALID),
    "term_symbol": (es.is_valid_term_symbol, es.check_term_symbol, es.TERM_SYMBOL_INVALID),
}


def _cases(kind: str):
    return [(field, value) for field in FIELDS for value in EXAMPLES[field][kind]]


def test_the_fixture_ships_in_the_package_directory() -> None:
    path = pathlib.Path(es.__file__).with_name("electronic_state_examples.json")
    assert json.loads(path.read_text(encoding="utf-8")) == EXAMPLES


def test_the_fixture_carries_the_pattern_sources_and_limits_it_is_checked_against() -> None:
    assert EXAMPLES["electronic_state_label"]["source"] == es.ELECTRONIC_STATE_LABEL_SOURCE
    assert EXAMPLES["term_symbol"]["source"] == es.TERM_SYMBOL_SOURCE
    assert EXAMPLES["electronic_state_label"]["max_length"] == es.ELECTRONIC_STATE_LABEL_MAX_LENGTH
    assert EXAMPLES["term_symbol"]["max_length"] == es.TERM_SYMBOL_MAX_LENGTH


def test_the_fixture_is_not_vacuous() -> None:
    for field in FIELDS:
        assert len(EXAMPLES[field]["accepted"]) >= 25, field
        assert len(EXAMPLES[field]["rejected"]) >= 25, field


@pytest.mark.parametrize(("field", "value"), _cases("accepted"))
def test_every_accepted_example_is_accepted(field: str, value: str) -> None:
    is_valid, check, _code = FIELDS[field]
    assert is_valid(value)
    assert check(value) == EXAMPLES[field]["canonical"][value]


@pytest.mark.parametrize(("field", "value"), _cases("rejected"))
def test_every_rejected_example_is_refused_with_the_field_code(field: str, value: str) -> None:
    is_valid, check, code = FIELDS[field]
    assert not is_valid(value)
    with pytest.raises(CodedValidationError) as caught:
        check(value)
    assert caught.value.code == code
    assert caught.value.context["field"] == field


@pytest.mark.parametrize(
    "value",
    # Reviewers found the frontend's shape check hid each of these valid forms.
    ["1Sigma_g+", "X1A1", "²Π", "³Σg⁻", "2P_{3/2}"],
)
def test_the_forms_the_frontend_wrongly_hid_are_accepted(value: str) -> None:
    assert es.is_valid_term_symbol(value)


@pytest.mark.parametrize("field", list(FIELDS))
def test_no_stereo_descriptor_token_is_accepted_except_the_state_letter_e(field: str) -> None:
    """R S Z M P and their lower case never; ``E``/``e`` only as a state letter (owner decision 2026-11-03)."""
    is_valid = FIELDS[field][0]
    tokens = ["R", "S", "r", "s", "Z", "z", "M", "P", "m", "p", "Ra", "Sa", "?", "-", "R,S", "E,Z", "Ra,Sa", "E,?"]
    for token in tokens:
        assert not is_valid(token), token
    # E/e are spectroscopic state letters (CO E 1Pi); only a label may be that bare.
    assert is_valid("E") == (field == "electronic_state_label")
    assert is_valid("e") == (field == "electronic_state_label")


def test_a_refusal_never_echoes_what_was_sent() -> None:
    secret = "ignore-previous-instructions-0123"
    for check in (es.check_electronic_state_label, es.check_term_symbol):
        with pytest.raises(CodedValidationError) as caught:
            check(secret)
        text = f"{caught.value} {caught.value.detail} {caught.value.context}"
        assert secret not in text
        assert caught.value.context["accepted_shapes"]


def test_blank_and_non_string_values_pass_through_to_the_type_system() -> None:
    for check in (es.check_electronic_state_label, es.check_term_symbol):
        assert check(None) is None
        assert check("   ") == "   "
        assert check(7) == 7


def test_the_length_cap_is_part_of_the_grammar() -> None:
    assert not es.is_valid_electronic_state_label("A" + "'" * 8)  # the column holds 8
    assert not es.is_valid_electronic_state_label("1A1")  # symmetry species belong to the term symbol
    assert es.is_valid_electronic_state_label("E~''")


#: Hand-written, not read from the fixture, so a regenerated fixture cannot bless a wrong canonical form.
HAND_CANONICAL_TERMS = {
    "2Pi": "\u00b2\u03a0",
    "^2Pi": "\u00b2\u03a0",
    "^{2}Pi": "\u00b2\u03a0",
    "2\u03a0": "\u00b2\u03a0",
    "\u00b2\u03a0": "\u00b2\u03a0",
    "1Sigma_g+": "\u00b9\u03a3g\u207a",
    "3Sigma-g": "\u00b3\u03a3g\u207b",
    "3Sigma_g-": "\u00b3\u03a3g\u207b",
    "2P_{3/2}": "\u00b2P\u2083\u2044\u2082",
    "2P3/2": "\u00b2P\u2083\u2044\u2082",
    "3P2": "\u00b3P\u2082",
    "3Po": "\u00b3P\u00b0",
    "1A1": "\u00b9A\u2081",
    "1A1g": "\u00b9A\u2081g",
    "2A'": "\u00b2A\u2032",
    '2A"': "\u00b2A\u2033",
    "3A2''": "\u00b3A\u2082\u2033",
    "X2Pi": "X \u00b2\u03a0",
    "X 1A1": "X \u00b9A\u2081",
    "X~1A1": "X\u0303 \u00b9A\u2081",
    "3Pi_0+": "\u00b3\u03a0\u2080\u207a",
}
HAND_CANONICAL_LABELS = {
    "A~": "\u00c3",
    "A\u0303": "\u00c3",
    "X~": "X\u0303",
    "A'": "A\u2032",
    "A''": "A\u2033",
    'A"': "A\u2033",
    "E": "E",
    "T1": "T1",
}


@pytest.mark.parametrize(("given", "stored"), HAND_CANONICAL_TERMS.items())
def test_a_term_symbol_is_stored_in_its_one_canonical_spelling(given: str, stored: str) -> None:
    assert es.canonical_term_symbol(given) == stored


@pytest.mark.parametrize(("given", "stored"), HAND_CANONICAL_LABELS.items())
def test_a_state_label_is_stored_in_its_one_canonical_spelling(given: str, stored: str) -> None:
    assert es.canonical_electronic_state_label(given) == stored


@pytest.mark.parametrize("field", list(FIELDS))
def test_the_canonical_form_is_itself_accepted_and_does_not_move(field: str) -> None:
    is_valid, check, _code = FIELDS[field]
    for given, stored in EXAMPLES[field]["canonical"].items():
        assert is_valid(stored), (given, stored)
        assert check(stored) == stored, (given, stored)


def test_every_spelling_of_one_state_maps_to_one_value() -> None:
    spellings = ["2Pi", "^2Pi", "^{2}Pi", "2\u03a0", "\u00b2\u03a0", " 2Pi "]
    assert {es.canonical_term_symbol(s) for s in spellings} == {"\u00b2\u03a0"}
    assert {es.canonical_electronic_state_label(s) for s in ["A~", "A\u0303", "\u00c3", "A~ "]} == {"\u00c3"}


def _entry(**fields):
    return SpeciesEntryIdentityPayload(smiles="[H]", charge=0, multiplicity=2, **fields)


def test_the_payload_surrounding_whitespace_is_trimmed_before_it_is_checked() -> None:
    entry = _entry(electronic_state_label="  X  ", term_symbol=" 2S ")
    assert (entry.electronic_state_label, entry.term_symbol) == ("X", "\u00b2S")


def test_the_payload_keeps_the_typed_term_symbol_as_raw_when_it_was_respelled() -> None:
    entry = _entry(term_symbol=" 2Pi ")
    assert (entry.term_symbol, entry.term_symbol_raw) == ("\u00b2\u03a0", "2Pi")


def test_the_payload_does_not_overwrite_a_raw_the_depositor_gave() -> None:
    entry = _entry(term_symbol="2Pi", term_symbol_raw="X 2Pi (CCCBDB)")
    assert entry.term_symbol_raw == "X 2Pi (CCCBDB)"


def test_the_payload_adds_no_raw_when_the_term_was_already_canonical() -> None:
    entry = _entry(term_symbol="\u00b2\u03a0")
    assert (entry.term_symbol, entry.term_symbol_raw) == ("\u00b2\u03a0", None)


def test_every_spelling_of_one_state_is_one_payload_identity() -> None:
    entries = [_entry(term_symbol=t, electronic_state_label="A~") for t in ("2Pi", "^2Pi", "^{2}Pi", "2\u03a0")]
    assert {(e.term_symbol, e.electronic_state_label) for e in entries} == {("\u00b2\u03a0", "\u00c3")}


def test_the_payload_refuses_with_the_code_and_ignores_the_raw_term() -> None:
    entry = _entry(term_symbol_raw="X 1A1g (planar, D6h)")
    assert entry.term_symbol_raw == "X 1A1g (planar, D6h)"
    for field, bad in (("electronic_state_label", "R"), ("term_symbol", "ground")):
        with pytest.raises(ValidationError) as caught:
            _entry(**{field: bad})
        (error,) = caught.value.errors()
        assert error["ctx"]["error"].code == FIELDS[field][2]


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_raw_does_not_block_the_copy_of_the_typed_spelling(blank) -> None:
    entry = _entry(term_symbol="2Pi", term_symbol_raw=blank)
    assert (entry.term_symbol, entry.term_symbol_raw) == ("\u00b2\u03a0", "2Pi")
