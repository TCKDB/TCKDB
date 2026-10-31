"""Read the Hessian method an input deck states, before a deposit is judged.

ADR 0012's tau keys on how the frequency job built its second derivatives.
ARC (and any producer that ships raw decks) does not send that as a
``parameters`` entry: it attaches the input deck as an ``input`` artifact,
and the backend parses ``freq.hessian_method`` out of the deck only after the
upload has been validated. So a deck saying ``NumFreq`` was judged as if it
had said nothing. This module is the **single owner** of what a deck says
about the frequency job's Hessian, and both sides use it:

* upload validation reads an inline deck through :func:`detect_hessian_method`
  / :func:`deck_hessian_method` (``tckdb_schemas.hessian_method_assumption``
  ranks the result above any assumption);
* the backend parameter parsers import the route / input-block extractors and
  the keyword tables below, so the ``freq.hessian_method`` row the workflow
  later stores comes from the same text, the same tokenising and the same
  table.

Only explicit directives count. Gaussian ``Freq=Numer`` / ``Freq=Numerical``
and ``Freq=EnOnly``, ORCA ``NumFreq`` / ``AnFreq``. A deck with no such
directive says nothing, which is not a contradiction of anything. Gaussian
does not name its analytic default, so an unqualified ``Freq`` yields ``None``.

DB-free and Pydantic-free: text in, :class:`HessianMethod` out.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable
from typing import Protocol

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.enums import HessianMethod
from tckdb_schemas.ess_software_detection import SoftwareName, detect_software_from_text

#: Refusal code: the recorded ``freq.hessian_method`` and the attached input
#: deck state different Hessian methods.
W_HESSIAN_METHOD_CONTRADICTS_INPUT_DECK = "hessian_method_contradicts_input_deck"

__all__ = [
    "W_HESSIAN_METHOD_CONTRADICTS_INPUT_DECK",
    "check_recorded_method_agrees_with_deck",
    "GAUSSIAN_FREQ_HESSIAN_OPTIONS",
    "ORCA_FREQ_HESSIAN_KEYWORDS",
    "deck_hessian_method",
    "detect_hessian_method",
    "extract_gaussian_route_text",
    "hessian_method_class",
    "orca_hessian_method_for",
]

#: Gaussian ``Freq`` options that name the second-derivative method. Only
#: overrides are recorded: the default is never named in the output.
GAUSSIAN_FREQ_HESSIAN_OPTIONS: dict[str, HessianMethod] = {
    "numer": HessianMethod.finite_difference_gradient,
    "numerical": HessianMethod.finite_difference_gradient,
    "enonly": HessianMethod.finite_difference_energy,
}

#: ORCA ``!`` keywords that name the frequency job's Hessian method.
ORCA_FREQ_HESSIAN_KEYWORDS: dict[str, HessianMethod] = {
    "numfreq": HessianMethod.finite_difference_gradient,
    "anfreq": HessianMethod.analytic,
}

# ---------------------------------------------------------------------------
# Gaussian route extraction and tokenising (moved from the backend parser)
# ---------------------------------------------------------------------------

_ROUTE_DELIM = re.compile(r"^[\s-]{60,}$")

def extract_gaussian_route_text(text: str) -> str | None:
    """Return the Gaussian route section from a log echo or raw input file.

    Tries the log-echo dash-delimited block first. If that yields nothing
    (e.g. raw ``.gjf`` / ``.com`` input), falls back to scanning for the
    first non-empty line whose stripped form starts with ``#``, then
    collects continuation lines until the first blank line.

    The two modes use different join strategies:

    * Log echoes: Gaussian hard-wraps at column width; concatenating with
      no separator preserves tokens like ``def2tzvp`` that were split mid-
      identifier.
    * Raw input: lines are human-edited, so wrapping is logical not
      physical; joining with spaces preserves token boundaries.

    Returns ``None`` if no route could be identified.
    """

    log_route = _extract_route_line_from_log_echo(text)
    if log_route:
        return log_route
    return _extract_route_line_from_raw_input(text)

def _extract_route_line_from_log_echo(text: str) -> str:
    """Extract the route line from a Gaussian log echo.

    The route line sits between two rows of dashes (``------``) in the
    early part of the output.  It may span multiple physical lines, but
    Gaussian wraps at column width with no internal spaces, so the
    parts must be concatenated with no separator.
    """

    lines = text.splitlines()
    in_route = False
    route_parts: list[str] = []

    for line in lines:
        stripped = line.strip()
        if _ROUTE_DELIM.match(stripped):
            if in_route:
                # second delimiter — we're done
                break
            in_route = True
            continue
        if in_route:
            route_parts.append(stripped)

    raw = "".join(route_parts)
    if raw.startswith("#"):
        return raw
    # First dash block might be the warning/copyright block; try the
    # next one.
    return _extract_route_line_fallback(text)

def _extract_route_line_fallback(text: str) -> str:
    """Fallback: scan dash-delimited blocks for one starting with ``#``."""
    lines = text.splitlines()
    dash_positions = [
        i for i, line in enumerate(lines) if _ROUTE_DELIM.match(line.strip())
    ]
    for i in range(len(dash_positions) - 1):
        start = dash_positions[i] + 1
        end = dash_positions[i + 1]
        block = "".join(lines[j].strip() for j in range(start, end))
        if block.startswith("#"):
            return block
    return ""

def _extract_route_line_from_raw_input(text: str) -> str | None:
    """Extract the route from a raw Gaussian input (``.gjf`` / ``.com``).

    Rules:

    1. Find the first non-empty line whose stripped form starts with ``#``.
    2. Collect that line plus every subsequent non-empty line.
    3. Stop at the first blank line — that terminates the route section
       and starts the title block.
    4. Join with single spaces. Raw input is human-authored and wrapping
       is logical, so token boundaries land on the line breaks.

    Returns ``None`` if no ``#`` line was found.
    """

    lines = text.splitlines()
    in_route = False
    route_parts: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not in_route:
            if stripped.startswith("#"):
                in_route = True
                route_parts.append(stripped)
            continue
        # in_route
        if stripped == "":
            break
        route_parts.append(stripped)

    if not route_parts:
        return None
    return " ".join(route_parts)

def _tokenize_route(route: str) -> list[str]:
    """Split route line into tokens, respecting parenthesized groups."""
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    for char in route:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == " " and depth == 0:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens

_IOP_RE = re.compile(r"IOp\(([^)]+)\)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# ORCA input-block extraction (moved from the backend parser)
# ---------------------------------------------------------------------------

_INPUT_DELIM = re.compile(r"^={60,}$")

_INPUT_LINE = re.compile(r"^\|\s*\d+>\s*(.*)$")

def _extract_input_block(text: str) -> list[str]:
    """Return the ORCA input lines, from a log echo or a raw ``.in`` file.

    Two layouts are supported:

    * **Log echo**: ORCA echoes the input between ``INPUT FILE`` and
      ``****END OF INPUT****`` markers, with each line prefixed
      ``|  N> ``.
    * **Raw input**: a ``.in`` / ``.inp`` artifact uploaded directly,
      with no log markers and no line prefixes. Detected by the
      presence of ORCA-style ``!`` keyword or ``%`` block lines.

    The coordinate block (``* xyz ... *``) is filtered out so atom
    rows can never reach the keyword/block parsers.
    """
    lines = text.splitlines()

    log_echo = _extract_log_echo_input(lines)
    if log_echo:
        return log_echo

    if any(_looks_like_orca_input_line(line) for line in lines):
        return lines

    return []

def _extract_log_echo_input(lines: list[str]) -> list[str]:
    """Extract the ``|  N> content`` lines from a log file echo.

    Returns ``[]`` when no ``INPUT FILE`` block is present.
    """
    in_input = False
    input_lines: list[str] = []

    for line in lines:
        stripped = line.strip()

        if "INPUT FILE" in stripped:
            in_input = True
            continue

        if in_input and "****END OF INPUT****" in stripped:
            break

        if in_input:
            m = _INPUT_LINE.match(stripped)
            if m:
                input_lines.append(m.group(1))

    return input_lines

def _looks_like_orca_input_line(line: str) -> bool:
    """Best-effort signal that a raw line is ORCA input syntax."""
    stripped = line.lstrip()
    return stripped.startswith(("!", "%"))

def _strip_coordinate_block(lines: list[str]) -> list[str]:
    """Drop the ``* xyz ... *`` (or ``* int ... *``) coordinate block.

    The opening line starts with ``*`` and contains ``xyz`` or ``int``;
    the block ends at the next bare ``*`` line. Atom rows in between
    must never be passed to the keyword/block parsers.
    """
    out: list[str] = []
    in_coord = False
    for line in lines:
        stripped = line.strip()
        if not in_coord:
            if stripped.startswith("*") and (
                "xyz" in stripped.lower() or "int" in stripped.lower()
            ):
                in_coord = True
                continue
            out.append(line)
        else:
            if stripped == "*":
                in_coord = False
                continue
            # Atom row inside the coordinate block — drop.
    return out

def orca_keyword_lines(input_lines: list[str]) -> list[str]:
    """The ``! ...`` keyword text of each keyword line, comments removed.

    The coordinate block is dropped first so atom rows can never be read as
    keywords. This is the exact text the backend parser splits into keywords.
    """
    out: list[str] = []
    for line in _strip_coordinate_block(input_lines):
        stripped = line.strip()
        if not stripped.startswith("!"):
            continue
        keywords_str = stripped.lstrip("!").strip()
        if "#" in keywords_str:
            keywords_str = keywords_str[: keywords_str.index("#")].strip()
        if keywords_str:
            out.append(keywords_str)
    return out


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _gaussian_method(text: str) -> HessianMethod | None:
    """The method a Gaussian route states, mirroring the parser's branches.

    Later directives win, as they do when the stored rows are resolved.
    """
    route = extract_gaussian_route_text(text) or ""
    route = route.strip()
    if route.startswith("#"):
        m = re.match(r"#([PNTpnt])?\s*", route)
        route = route[m.end():] if m else route[1:]
    route = _IOP_RE.sub("", route).strip()

    found: HessianMethod | None = None
    for token in _tokenize_route(route):
        token = token.strip()
        if not token:
            continue
        if "/" in token and "=" not in token and "(" not in token:
            continue
        if "=(" in token:
            key, rest = token.split("=(", 1)
            if key.lower() != "freq":
                continue
            for sub in rest.rstrip(")").strip().split(","):
                sub = sub.strip()
                if not sub:
                    continue
                option = sub.split("=", 1)[0].strip() if "=" in sub else sub
                method = GAUSSIAN_FREQ_HESSIAN_OPTIONS.get(option.lower())
                if method is not None:
                    found = method
        elif token.lower().startswith("freq="):
            method = GAUSSIAN_FREQ_HESSIAN_OPTIONS.get(token.split("=", 1)[1].strip().lower())
            if method is not None:
                found = method
    return found


def orca_hessian_method_for(keyword: str, all_keywords: Iterable[str]) -> HessianMethod | None:
    """The method an ORCA frequency keyword states, given every keyword in the deck.

    ``NumFreq`` differentiates gradients. With ``NumGrad`` on the deck those
    gradients are themselves numerical, so the Hessian is built from energies
    (ORCA 5.0.4 manual, 8.3.2 and 9.24.5 "Numerical Gradients": ``! NumGrad``
    requests numerical instead of analytic gradients). ``NumFreq`` alone stays
    finite differences of analytic gradients. ``AnFreq`` is analytic.
    """
    method = ORCA_FREQ_HESSIAN_KEYWORDS.get(keyword.lower())
    if (
        method is HessianMethod.finite_difference_gradient
        and "numgrad" in {k.lower() for k in all_keywords}
    ):
        return HessianMethod.finite_difference_energy
    return method


def _orca_method(text: str) -> HessianMethod | None:
    keywords = [kw for line in orca_keyword_lines(_extract_input_block(text)) for kw in line.split()]
    found: HessianMethod | None = None
    for keyword in keywords:
        method = orca_hessian_method_for(keyword, keywords)
        if method is not None:
            found = method
    return found


def detect_hessian_method(software: str | None, text: str) -> HessianMethod | None:
    """The Hessian method ``text`` states for ``software``, or ``None``.

    :param software: ``"gaussian"`` or ``"orca"`` (any other program states
        nothing this module reads).
    """
    if software == "gaussian":
        return _gaussian_method(text)
    if software == "orca":
        return _orca_method(text)
    return None


#: The programs with a wired parameter parser (what the workflow can read a deck for).
_PARSED_PROGRAMS: dict[str, SoftwareName] = {"gaussian": "gaussian", "orca": "orca", "molpro": "molpro"}


class _ArtifactLike(Protocol):
    kind: object
    content_base64: str


def deck_hessian_method(
    declared_software: str | None, artifacts: Iterable[_ArtifactLike]
) -> HessianMethod | None:
    """The Hessian method the calculation's inline ``input`` decks state.

    Mirrors what the workflow will store, so validation and storage agree:
    each ``input`` artifact is parsed by the program its own banner names (the
    declared software is the fallback), a later parseable deck replaces an
    earlier one's result entirely (the stored parser rows are replaced
    wholesale), and a deck for a program with no parser is skipped.

    :param declared_software: The calculation's declared software name.
    """
    declared = (declared_software or "").strip().lower() or None
    result: HessianMethod | None = None
    for artifact in artifacts:
        kind = getattr(artifact.kind, "value", artifact.kind)
        if kind != "input":
            continue
        try:
            content = base64.b64decode(artifact.content_base64, validate=True)
        except (binascii.Error, ValueError):
            continue
        text = content.decode("utf-8", errors="replace")
        program: SoftwareName | None = detect_software_from_text(text)
        if program is None and declared is not None:
            program = _PARSED_PROGRAMS.get(declared)
        if program not in _PARSED_PROGRAMS:
            continue
        result = detect_hessian_method(program, text)
    return result


def hessian_method_class(value: str | None) -> str | None:
    """``"analytic"`` or ``"numerical"`` for a recorded value, ``None`` if unreadable.

    ``numerical`` covers both finite-difference methods and the loose spellings
    a depositor might use. Used to compare a recorded ``freq.hessian_method``
    with a deck without echoing the depositor's string anywhere.
    """
    if value is None:
        return None
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    if token == "analytic":
        return "analytic"
    if token in {"numerical", "numeric", "numer", "numfreq", "finite_difference"} or token.startswith(
        "finite_difference"
    ):
        return "numerical"
    return None


def _class_of(method: HessianMethod) -> str:
    return "analytic" if method is HessianMethod.analytic else "numerical"


def check_recorded_method_agrees_with_deck(
    parameters: Iterable[tuple[str | None, str | None]],
    deck_method: HessianMethod | None,
    *,
    calculation_key: str | None = None,
) -> None:
    """Refuse a recorded ``freq.hessian_method`` that the input deck contradicts.

    Two statements of the same fact that disagree cannot both be right, and
    judging a mode against either would be a guess. A deck with no frequency
    directive (``deck_method is None``) contradicts nothing, and a recorded
    value TCKDB cannot read is not compared.

    The message is built from the code and TCKDB's own words ("analytic",
    "numerical"); neither the depositor's recorded string nor any deck text is
    echoed. The calculation's local key goes in ``context``.

    :raises CodedValidationError: ``hessian_method_contradicts_input_deck``.
    """
    if deck_method is None:
        return
    recorded: str | None = None
    for key, value in parameters:
        if key == "freq.hessian_method" and value and value.strip():
            recorded = value
    if recorded is None:
        return
    recorded_class = hessian_method_class(recorded)
    if recorded_class is None:
        return
    deck_class = _class_of(deck_method)
    try:
        exact = HessianMethod(recorded.strip().lower())
    except ValueError:
        exact = None
    if recorded_class == deck_class and (exact is None or exact is deck_method):
        return
    # Both sides in TCKDB's vocabulary: the parsed method when the recorded value is
    # exactly a HessianMethod token, otherwise its analytic / numerical kind.
    recorded_token = exact.value if exact is not None else recorded_class
    deck_token = deck_method.value
    context: dict[str, object] = {
        "recorded": recorded_token,
        "input_deck": deck_token,
    }
    if calculation_key is not None:
        context["calculation_key"] = calculation_key
    raise CodedValidationError(
        W_HESSIAN_METHOD_CONTRADICTS_INPUT_DECK,
        f"the recorded freq.hessian_method says the Hessian method was {recorded_token} but "
        f"the input deck attached to the same calculation says {deck_token}. "
        "Both are statements about how the second derivatives were built and they "
        "cannot both be right; correct the recorded value or the deck.",
        context=context,
    )
