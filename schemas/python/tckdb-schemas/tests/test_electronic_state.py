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
from tckdb_schemas.fragments.identity import STEREO_DESCRIPTOR_PATTERN, SpeciesEntryIdentityPayload

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
    assert check(value) == value


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
def test_no_stereo_descriptor_token_is_ever_accepted(field: str) -> None:
    """A state label or term symbol can never be read as one of TCKDB's own facet words."""
    is_valid = FIELDS[field][0]
    tokens = ["R", "S", "r", "s", "E", "Z", "e", "z", "M", "P", "m", "p", "Ra", "Sa", "?", "-"]
    tokens += ["R,S", "E,Z", "Ra,Sa", "E,?"]
    for token in tokens:
        assert not is_valid(token), token
    # and every token the stereo grammar itself accepts, over the full single-token vocabulary
    for letter in "RSrsEZez?-":
        assert STEREO_DESCRIPTOR_PATTERN.fullmatch(letter) and not is_valid(letter)


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
    assert not es.is_valid_electronic_state_label("2Sigma_g+")  # 9 characters, the column holds 8
    assert es.is_valid_electronic_state_label("2Sigma+")


def _entry(**fields):
    return SpeciesEntryIdentityPayload(smiles="[H]", charge=0, multiplicity=2, **fields)


def test_the_payload_surrounding_whitespace_is_trimmed_before_it_is_checked() -> None:
    entry = _entry(electronic_state_label="  X  ", term_symbol=" 2S ")
    assert (entry.electronic_state_label, entry.term_symbol) == ("X", "2S")


def test_the_payload_refuses_with_the_code_and_ignores_the_raw_term() -> None:
    entry = _entry(term_symbol_raw="X 1A1g (planar, D6h)")
    assert entry.term_symbol_raw == "X 1A1g (planar, D6h)"
    for field, bad in (("electronic_state_label", "R"), ("term_symbol", "ground")):
        with pytest.raises(ValidationError) as caught:
            _entry(**{field: bad})
        (error,) = caught.value.errors()
        assert error["ctx"]["error"].code == FIELDS[field][2]
