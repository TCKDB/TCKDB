"""Read the Hessian method an input deck states, before a deposit is judged.

ADR 0012's tau keys on how the frequency job built its second derivatives.
ARC (and any producer that ships raw decks) does not send that as a
``parameters`` entry: it attaches the input deck as an ``input`` artifact,
and the backend parses ``freq.hessian_method`` out of the deck only after the
upload has been validated. So a deck saying ``NumFreq`` was judged as if it
had said nothing. This module is the **single owner** of what a deck says
about the frequency job's Hessian, and both sides use it:

* upload validation reads an inline deck through :func:`detect_hessian_method`
  / :func:`deck_hessian_method`, and the input an inline output log echoes
  through :func:`echo_hessian_method` (``tckdb_schemas.hessian_method_assumption``
  ranks the echo above the deck and both above any assumption);
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
from tckdb_schemas.hessian_method_reported import ORCA_MULTI_JOB, agreed_method, split_orca_runs

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
    "echo_hessian_method",
    "extract_gaussian_route_text",
    "gaussian_freq_hessian_option",
    "hessian_method_class",
    "orca_freq_hessian",
    "orca_lacks_analytic_gradient",
    "orca_numfreq_refines_gradient_to_energy",
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

#: A row of dashes round the echoed route. Gaussian sizes the rows to the text they enclose: 70 wide for a long route,
#: but only as wide as the route itself for a short one (``# sp b3lyp/def2svp`` gets 43). Rows shorter than 60
#: are therefore delimiters too; without them a short route was never closed and ran on into the log.
_ROUTE_DELIM = re.compile(r"^(?:[\s-]{60,}|-{4,})$")

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

def _echo_text(line: str) -> str:
    """One echoed route line without its leading blank column and without its line ending."""
    line = line.rstrip("\r\n")
    return line[1:] if line.startswith(" ") else line.lstrip()


def _extract_route_line_from_log_echo(text: str) -> str:
    """Extract the route line from a Gaussian log echo.

    The route line sits between two rows of dashes (``------``) in the
    early part of the output.  It may span multiple physical lines, but
    Gaussian wraps at column width, so the parts are concatenated with no
    separator. Every echoed line starts with one blank column; that column is
    dropped and nothing else is stripped from the line, so a space that falls at
    the wrap point (``guess=read`` then `` ub3lyp/def2tzvp``) is kept and the two
    tokens are not fused into ``guess=readub3lyp/def2tzvp``.
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
            route_parts.append(_echo_text(line))

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
        block = "".join(_echo_text(lines[j]) for j in range(start, end))
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
        # A comment starts at ``#`` and ends at the next ``#`` (or the end of the
        # line); what follows the closing ``#`` is keywords again.
        segments = stripped.lstrip("!").split("#")
        keywords_str = " ".join(part.strip() for part in segments[0::2]).strip()
        if keywords_str:
            out.append(keywords_str)
    return out


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


_FREQ_KEYWORD_TOKEN = re.compile(r"^([A-Za-z]+)(=\(.*\)|=[^()]*|\(.*\))?$")


def gaussian_freq_hessian_option(text: str) -> tuple[str, HessianMethod] | None:
    """The ``Freq`` option a Gaussian route uses to state the Hessian method.

    Returns ``(option exactly as written, method)`` or ``None``. Reads every spelling Gaussian
    accepts for the keyword and its options (``Freq=Numer``, ``Freq=(Numer,..)``,
    ``Freq(Numer)``, ``Freq (Numer)``, ``Freq = Numer``, ``Frequency=Numer``), and
    only the keywords ``Freq`` / ``Frequency`` themselves: ``FreqChk`` or an
    option of another keyword (``SCF=(Numer)``, ``Opt=CalcNumerFC``) is not read.
    If ``EnOnly`` is present it wins (energies are the coarser input); otherwise
    ``Numer`` / ``Numerical`` mean finite differences of gradients.
    """
    route = extract_gaussian_route_text(text) or ""
    route = route.strip()
    if route.startswith("#"):
        m = re.match(r"#([PNTpnt])?\s*", route)
        route = route[m.end():] if m else route[1:]
    route = _IOP_RE.sub("", route).strip()
    # ``keyword (options)`` and ``keyword = value`` are one token each.
    route = re.sub(r"\s*=\s*", "=", route)
    route = re.sub(r"(?<=[\w)])\s+\(", "(", route)

    options: list[tuple[str, str]] = []  # (as written, lower-cased)
    for token in _tokenize_route(route):
        match = _FREQ_KEYWORD_TOKEN.match(token.strip())
        if match is None or match.group(1).lower() not in ("freq", "frequency"):
            continue
        rest = match.group(2) or ""
        if rest.startswith("="):
            rest = rest[1:]
        rest = rest.strip()
        if rest.startswith("(") and rest.endswith(")"):
            rest = rest[1:-1]
        for part in re.split(r"[,\s]+", rest):
            written = part.split("=", 1)[0].strip()
            if written:
                options.append((written, written.lower()))
    for written, lowered in options:
        if lowered == "enonly":
            return written, GAUSSIAN_FREQ_HESSIAN_OPTIONS["enonly"]
    for written, lowered in options:
        if lowered in GAUSSIAN_FREQ_HESSIAN_OPTIONS:
            return written, GAUSSIAN_FREQ_HESSIAN_OPTIONS[lowered]
    return None


def orca_lacks_analytic_gradient(method: str | None) -> bool:
    """True for an ORCA method keyword with no analytic gradient: CCSD, CCSD(T), DLPNO and F12 methods (not ``AUTOCI-``)."""
    token = (method or "").strip().lower().replace("_", "-")
    if not token or token.startswith("autoci"):
        return False
    if token.startswith(("ccsd", "dlpno-ccsd", "f12-ccsd", "f12/ri-ccsd")):
        return True
    return "f12" in token and "mp2" in token


def orca_numfreq_refines_gradient_to_energy(
    software: str | None,
    lot_method: str | None,
    recorded: HessianMethod | None,
    stated: HessianMethod | None,
    *,
    lacks_gradient: bool | None = None,
) -> bool:
    """True when a recorded finite difference of gradients is refined to energies (ORCA ``NumFreq``, method without a gradient)."""
    return (
        recorded is HessianMethod.finite_difference_gradient
        and stated is HessianMethod.finite_difference_energy
        and (software or "").strip().lower() == "orca"
        and (orca_lacks_analytic_gradient(lot_method) if lacks_gradient is None else lacks_gradient)
    )


def orca_freq_hessian(keywords: Iterable[str]) -> tuple[str, HessianMethod] | None:
    """The Hessian method the ORCA keywords state, as ``(directive as written, method)``.

    ``NumFreq`` wins over ``AnFreq``. ``NumFreq`` reads as finite differences of gradients, or of energies with
    ``NumGrad`` or for a method with no analytic gradient (:func:`orca_lacks_analytic_gradient`).
    The directive text is the deck's own: ``NumFreq``, ``NumFreq NumGrad`` or ``AnFreq``    directive text is exactly what the deck says: ``NumFreq``, ``NumFreq NumGrad``
    (each as written) or ``AnFreq``.
    """
    written = list(keywords)

    def first(name: str) -> str | None:
        return next((k for k in written if k.lower() == name), None)

    numfreq = first("numfreq")
    if numfreq is not None:
        numgrad = first("numgrad")
        if numgrad is not None:
            return f"{numfreq} {numgrad}", HessianMethod.finite_difference_energy
        lowered = [k.lower() for k in written]
        references = {k for k in lowered if k in ("uhf", "uks", "rohf")}
        open_shell_mp2 = (
            any(k.startswith("dlpno-mp2") for k in lowered) and bool(references)
        ) or ("rohf" in references and any(k.startswith(("mp2", "ri-mp2")) for k in lowered))
        if open_shell_mp2 or any(orca_lacks_analytic_gradient(k) for k in written):
            return numfreq, HessianMethod.finite_difference_energy
        return numfreq, HessianMethod.finite_difference_gradient
    anfreq = first("anfreq")
    if anfreq is not None:
        return anfreq, HessianMethod.analytic
    return None


def _gaussian_method(text: str) -> HessianMethod | None:
    found = gaussian_freq_hessian_option(text)
    return found[1] if found is not None else None


def _orca_method(text: str) -> HessianMethod | None:
    keywords = [kw for line in orca_keyword_lines(_extract_input_block(text)) for kw in line.split()]
    found = orca_freq_hessian(keywords)
    return found[1] if found is not None else None


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


def _gaussian_echoed_route_count(text: str) -> int:
    """How many route blocks (dash-delimited, starting with ``#``) a Gaussian log echoes."""
    lines = text.splitlines()
    dashes = [i for i, line in enumerate(lines) if _ROUTE_DELIM.match(line.strip())]
    return sum(
        "".join(_echo_text(lines[j]) for j in range(first + 1, second)).startswith("#")
        for first, second in zip(dashes, dashes[1:])
    )


def echo_hessian_method(declared_software: str | None, artifacts: Iterable[_ArtifactLike]) -> HessianMethod | None:
    """The Hessian method the input echoed in the inline ``output_log`` artifacts requests.

    An output log echoes the input it ran, and the backend stores that echo as the calculation's requested rows,
    ranked above an uploaded deck, so validation reads it too. A log whose echo cannot be bound to one job reads
    nothing: an ORCA echo with ``$new_job`` or ``%compound`` merges every job's ``!`` lines, and a Gaussian log that
    echoes more than one route (``--Link1--`` jobs) would be read from its first route only. When several logs state
    a method they must agree; otherwise nothing is read.

    :param declared_software: The calculation's declared software name.
    """
    declared = (declared_software or "").strip().lower() or None
    found: list[HessianMethod | None] = []
    for artifact in artifacts:
        kind = getattr(artifact.kind, "value", artifact.kind)
        if kind != "output_log":
            continue
        try:
            text = base64.b64decode(artifact.content_base64, validate=True).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError):
            continue
        program: SoftwareName | None = detect_software_from_text(text)
        if program is None and declared is not None:
            program = _PARSED_PROGRAMS.get(declared)
        if program == "orca" and not ORCA_MULTI_JOB.search(text):
            # A file of several runs (concatenated, without $new_job) echoes one input per run: every run must state
            # the same method, as for the report. One run that is silent, or that differs, reads nothing.
            runs = {detect_hessian_method("orca", run) for run in split_orca_runs(text)}
            found.append(next(iter(runs)) if len(runs) == 1 else None)
        elif program == "gaussian" and _gaussian_echoed_route_count(text) == 1:
            found.append(detect_hessian_method("gaussian", text))
    return agreed_method(found)


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
    stated_in: str = "input",
    software: str | None = None,
    lot_method: str | None = None,
    lacks_gradient: bool | None = None,
) -> None:
    """Refuse a recorded ``freq.hessian_method`` that the input deck (or the input a log echoes) contradicts.

    ``stated_in`` says where the contradicting method was stated: ``"input"`` (an attached input deck) or
    ``"output_log"`` (the input an attached output log echoes). It is carried in ``context`` and selects the wording,
    so a depositor who sent only a log is not told about a deck.

    Two statements of the same fact that disagree cannot both be right, and
    judging a mode against either would be a guess. A deck with no frequency
    directive (``deck_method is None``) contradicts nothing, and a recorded
    value TCKDB cannot read is not compared.

    The message is built from the code and TCKDB's own words ("analytic",
    "numerical"); neither the depositor's recorded string nor any deck text is
    echoed. The calculation's local key goes in ``context``.

    A recorded gradient method against a stated energy method is accepted for ORCA methods with no analytic gradient
    (:func:`orca_numfreq_refines_gradient_to_energy`).

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
    if orca_numfreq_refines_gradient_to_energy(
        software, lot_method, exact, deck_method, lacks_gradient=lacks_gradient
    ):
        # Refined to the energy basis, with a ``hessian_method_refined`` warning; not a contradiction.
        return
    # Both sides in TCKDB's vocabulary: the parsed method when the recorded value is
    # exactly a HessianMethod token, otherwise its analytic / numerical kind.
    recorded_token = exact.value if exact is not None else recorded_class
    deck_token = deck_method.value
    context: dict[str, object] = {
        "recorded": recorded_token,
        "input_deck": deck_token,
        "stated_in": stated_in,
    }
    if calculation_key is not None:
        context["calculation_key"] = calculation_key
    if stated_in == "output_log":
        message = (
            f"the recorded freq.hessian_method says the Hessian method was {recorded_token} but "
            f"the input echoed by the output log attached to the same calculation says {deck_token}. "
            "Both are statements about how the second derivatives were built and they "
            "cannot both be right; correct the recorded value, or omit it and let the log speak."
        )
    else:
        message = (
            f"the recorded freq.hessian_method says the Hessian method was {recorded_token} but "
            f"the input deck attached to the same calculation says {deck_token}. "
            "Both are statements about how the second derivatives were built and they "
            "cannot both be right; correct the recorded value or the deck."
        )
    raise CodedValidationError(
        W_HESSIAN_METHOD_CONTRADICTS_INPUT_DECK,
        message,
        context=context,
    )
