"""A Gaussian log echoes the route wrapped at the column width, one blank column in front of every line.

Joining the lines with their leading column kept fused two tokens when the wrap fell on a space
(``guess=read`` + `` ub3lyp/def2tzvp`` became ``guess=readub3lyp/def2tzvp``), so a deck and its own log
disagreed about the grid and the basis. Dropping exactly the one blank column and nothing else keeps the space.
"""

from __future__ import annotations

from tckdb_schemas.hessian_method_detection import extract_gaussian_route_text

from app.services.calculation_parameter_extraction import _parse_gaussian_text

_DASHES = " " + "-" * 70

#: A wrapped echo as ARC fixtures (neb_1) carry it: the wrap falls inside ``ultrafine``.
_WRAPPED_MID_TOKEN = "\n".join(
    [
        " Entering Gaussian System, Link 0=g16",
        _DASHES,
        " #P guess=read ub3lyp/def2tzvp freq IOp(7/33=1) integral=(grid=ultrafin",
        " e, Acc2E=12) IOp(2/9=2000) scf=(direct,tight)",
        _DASHES,
        "",
    ]
)

#: The same route wrapped on the space after ``guess=read``: the next line keeps that space in its second column.
_WRAPPED_ON_A_SPACE = "\n".join(
    [
        " Entering Gaussian System, Link 0=g16",
        _DASHES,
        " #P opt guess=read",
        "  ub3lyp/def2tzvp integral=(grid=ultrafine) scf=(direct,tight)",
        _DASHES,
        "",
    ]
)


def _canonical(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in _parse_gaussian_text(text)["parameters"]:
        if p["canonical_key"] and p["canonical_value"] is not None:
            out.setdefault(p["canonical_key"], set()).add(p["canonical_value"])
    return out


def test_a_wrap_inside_a_token_is_rejoined():
    route = extract_gaussian_route_text(_WRAPPED_MID_TOKEN)
    assert "grid=ultrafine, Acc2E=12)" in route
    canonical = _canonical(_WRAPPED_MID_TOKEN)
    assert canonical["grid.quality"] == {"ultrafine"}
    assert _parse_gaussian_text(_WRAPPED_MID_TOKEN)["method_basis"] == {"method": "ub3lyp", "basis": "def2tzvp"}


def test_a_wrap_on_a_space_keeps_the_two_tokens_apart():
    route = extract_gaussian_route_text(_WRAPPED_ON_A_SPACE)
    assert "guess=read ub3lyp/def2tzvp" in route
    parsed = _parse_gaussian_text(_WRAPPED_ON_A_SPACE)
    assert parsed["method_basis"] == {"method": "ub3lyp", "basis": "def2tzvp"}
    assert _canonical(_WRAPPED_ON_A_SPACE)["guess.strategy"] == {"read"}


def test_a_log_echo_and_the_deck_it_echoes_read_the_same_settings():
    deck = "#P opt guess=read ub3lyp/def2tzvp integral=(grid=ultrafine) scf=(direct,tight)\n\ntitle\n\n0 1\nH 0 0 0\n\n"
    assert _canonical(deck) == _canonical(_WRAPPED_ON_A_SPACE)
