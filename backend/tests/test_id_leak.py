"""``id_in_text`` finds an id that stands alone, and never one inside a random public ref."""

from __future__ import annotations

import pytest

from tests.id_leak import id_in_text


@pytest.mark.parametrize("digit", range(10))
def test_a_digit_inside_a_public_ref_is_not_a_leak(digit):
    """The flake: ``str(1) in 'species_entry_ab1cd...'`` is true by chance; the token check is not."""
    ref = f"species_entry_ab{digit}cdefghijklmnopqrstuvw{digit}"
    assert str(digit) in ref  # the old substring check fires
    assert not id_in_text(digit, f"species_entry not found (species_entry_ref={ref!r})")


@pytest.mark.parametrize(
    "message",
    ["row 7 failed", "species_entry_id=7", "id: 7.", "(7)", "7", "calc 7, calc 8", "[7]"],
)
def test_an_id_standing_alone_is_a_leak(message):
    """Kills: a helper that never fires (a vacuous pass)."""
    assert id_in_text(7, message)


def test_a_longer_number_is_not_the_id():
    assert not id_in_text(7, "row 17 and 71 and 7a")
