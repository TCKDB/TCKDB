"""The shared species-name normaliser and the query rules built on it."""

from __future__ import annotations

import pytest

from app.services.species_name_normalisation import (
    is_valid_query_key,
    normalise_name,
    prefix_upper_bound,
)


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("Water", "water"),
        ("  Methyl   Radical ", "methyl radical"),
        ("methyl\tradical", "methyl radical"),
        ("methyl\u00a0radical", "methyl radical"),
        ("methyl\u2003\u2003radical", "methyl radical"),
        ("Stra\u00dfe", "strasse"),
        ("STRASSE", "strasse"),
        ("\uff21\uff22\uff23", "abc"),  # full-width letters, NFKC
        ("\ufb01", "fi"),  # ligature
        ("N\u2019-methyl", "n'-methyl"),
        ("N\u2018-methyl", "n'-methyl"),
        ("N\u2032-methyl", "n'-methyl"),
        ("e\u0301thane", "\u00e9thane"),  # combining accent composes
        ("\u00c9thane", "\u00e9thane"),
    ],
)
def test_normalise(raw, key):
    assert normalise_name(raw) == key


def test_normalise_is_idempotent():
    for raw in ("  Stra\u00dfe\u2019s  ", "\uff21\u00a0B", "Methyl  Radical"):
        once = normalise_name(raw)
        assert normalise_name(once) == once


@pytest.mark.parametrize(
    "raw",
    ["water", "n,n-dimethylformamide", "2-propanol (iso)", "1,2-[x]+.:", "o'brien", "\u00e9thane", "\u6c34\u6676", "h2o"],
)
def test_valid_queries(raw):
    assert is_valid_query_key(raw, normalise_name(raw))


@pytest.mark.parametrize(
    "raw",
    [
        "a",  # too short
        " a ",
        "x" * 65,
        "100%",
        "a_b",
        "a\\b",
        "a;--",
        'a"b',
        "a$b",
        "a\x00b",
        "a\x07b",
        "a\x1bb",
        "a\x7fb",
        "a\x1cb",  # str.split() would turn this into a space
        "a\nb",
        "a\u200bb",  # zero-width space
        "a<b>",
        "a*b",
        "ab=cd",
    ],
)
def test_invalid_queries(raw):
    assert not is_valid_query_key(raw, normalise_name(raw))


def test_length_is_measured_after_normalisation():
    assert is_valid_query_key("a" + " " * 50 + "b", normalise_name("a" + " " * 50 + "b"))
    assert is_valid_query_key("x" * 64, "x" * 64)
    assert not is_valid_query_key("x" * 65, "x" * 65)


def test_prefix_upper_bound():
    assert prefix_upper_bound("wat") == "wau"
    assert prefix_upper_bound("a\U0010ffff") == "b"
    assert prefix_upper_bound("\U0010ffff") is None
    assert prefix_upper_bound(chr(0xFF)) == chr(0x100)


def test_prefix_upper_bound_skips_the_surrogate_block():
    assert prefix_upper_bound(chr(0xD7FF)) == chr(0xE000)


def test_every_string_with_the_prefix_is_in_the_range():
    key = "wat"
    upper = prefix_upper_bound(key)
    for tail in ("", "er", "\U0010ffff", "\u0000"):
        assert key <= key + tail < upper
    assert not ("wau" < upper)


def test_acute_accent_folds_to_an_apostrophe_before_nfkc():
    assert normalise_name("n" + chr(0xB4) + "-methyl") == "n'-methyl"
    assert normalise_name("n" + chr(0xB4) + "-methyl") == normalise_name("n'-methyl")


@pytest.mark.parametrize(
    "ch",
    [0xFE00, 0xFE0F, 0xE0100, 0xE01EF, 0x34F, 0x115F, 0x1160, 0x3164, 0xFFA0, 0x180B, 0x180F, 0x17B4, 0x17B5, 0x200B, 0xAD, 0xFEFF],
)
def test_default_ignorable_code_points_are_refused(ch):
    raw = "ab" + chr(ch) + "cd"
    assert not is_valid_query_key(raw, normalise_name(raw))


def test_a_key_of_only_combining_marks_is_refused():
    raw = chr(0x301) + chr(0x302)
    assert not is_valid_query_key(raw, normalise_name(raw))
    assert is_valid_query_key("ae" + chr(0x301), normalise_name("ae" + chr(0x301)))


def test_only_decimal_digits_count_as_digits():
    for raw in (chr(0x3007) + chr(0x3007), "ab" + chr(0x3007)):  # ideographic zero
        assert not is_valid_query_key(raw, normalise_name(raw))
    assert is_valid_query_key("ab" + chr(0x663), normalise_name("ab" + chr(0x663)))  # Arabic-Indic digit three


@pytest.mark.parametrize("raw", ["--", chr(0x301) + " " + chr(0x301), chr(0x301) + "-", "()", "+."])
def test_a_key_needs_a_letter_or_digit(raw):
    assert not is_valid_query_key(raw, normalise_name(raw))
    assert is_valid_query_key(raw + "a", normalise_name(raw + "a"))


def test_an_apostrophe_made_by_nfkc_is_folded():
    # U+0149 decomposes under NFKC to U+02BC + "n"
    assert normalise_name(chr(0x149) + "x") == "'nx"
    assert normalise_name(chr(0x149) + "x") == normalise_name("'nx")
