"""The channel kind is one function of the two state kinds (pure package, no backend)."""

import pytest
from tckdb_schemas.enums import NetworkChannelKind
from tckdb_schemas.network_channel_kind import W_NETWORK_CHANNEL_KIND_MISMATCH, derive_channel_kind

FRAGMENTS = ("bimolecular", "termolecular")


@pytest.mark.parametrize(
    ("source", "sink", "expected"),
    [("well", "well", "isomerization")]
    + [(frag, "well", "association") for frag in FRAGMENTS]
    + [("well", frag, "dissociation") for frag in FRAGMENTS]
    + [(a, b, "exchange") for a in FRAGMENTS for b in FRAGMENTS],
)
def test_every_pair_has_exactly_one_kind(source: str, sink: str, expected: str) -> None:
    assert derive_channel_kind(source, sink) is NetworkChannelKind(expected)


def test_the_enum_is_the_four_derived_kinds() -> None:
    assert {k.value for k in NetworkChannelKind} == {"isomerization", "association", "dissociation", "exchange"}


def test_termolecular_is_bimolecular() -> None:
    for other in ("well", "bimolecular", "termolecular"):
        assert derive_channel_kind("termolecular", other) is derive_channel_kind("bimolecular", other)
        assert derive_channel_kind(other, "termolecular") is derive_channel_kind(other, "bimolecular")


def test_an_unknown_state_kind_raises_instead_of_defaulting() -> None:
    with pytest.raises(ValueError, match="unknown network state kind"):
        derive_channel_kind("well", "unimolecular")


def test_the_refusal_code() -> None:
    assert W_NETWORK_CHANNEL_KIND_MISMATCH == "network_channel_kind_mismatch"
