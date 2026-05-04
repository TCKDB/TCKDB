"""Parse Gaussian log files to extract execution parameters.

Pure-function parser: takes text, returns structured dicts compatible with
the CalculationParameter model.  No DB dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Canonical key mapping — initial seed (core + secondary tier)
# ---------------------------------------------------------------------------

#: Maps (section, raw_key) → (canonical_key, canonical_value_fn | None)
#: canonical_value_fn is an optional callable that normalizes the raw_value.
_CANONICAL_MAP: dict[tuple[str, str], tuple[str, str | None]] = {
    # opt section
    ("opt", "calcfc"): ("opt.initial_hessian", "calculate_at_first_point"),
    ("opt", "calcall"): ("opt.initial_hessian", "calculate_at_every_point"),
    ("opt", "readfc"): ("opt.initial_hessian", "read_from_checkpoint"),
    ("opt", "tight"): ("opt.convergence", "tight"),
    ("opt", "verytight"): ("opt.convergence", "very_tight"),
    ("opt", "loose"): ("opt.convergence", "loose"),
    ("opt", "maxcycle"): ("opt.max_cycles", None),
    ("opt", "maxstep"): ("opt.max_step", None),
    ("opt", "ts"): ("opt.saddle_order", "1"),
    ("opt", "noeigentest"): ("opt.eigen_test", "disabled"),
    # scf section
    ("scf", "tight"): ("scf.convergence", "tight"),
    ("scf", "verytight"): ("scf.convergence", "very_tight"),
    ("scf", "direct"): ("scf.direct", "true"),
    ("scf", "incore"): ("scf.direct", "incore"),
    ("scf", "xqc"): ("scf.fallback", "xqc"),
    ("scf", "maxcycle"): ("scf.max_cycles", None),
    # integral section
    ("integral", "grid"): ("grid.quality", None),
    ("integral", "acc2e"): ("integral.accuracy", None),
    # general section
    ("general", "guess"): ("guess.strategy", None),
    # symmetry section
    ("symmetry", "nosymm"): ("symmetry.disabled", "true"),
    # resource section
    ("resource", "%mem"): ("memory.raw", None),
    ("resource", "%nprocshared"): ("parallel.nproc_shared", None),
    ("resource", "%nproc"): ("parallel.nproc", None),
}


def _lookup_canonical(
    section: str, raw_key: str
) -> tuple[str | None, str | None]:
    """Return (canonical_key, canonical_value) for a given section+raw_key.

    Returns (None, None) if no mapping exists.
    """
    key = (section.lower(), raw_key.lower())
    if key in _CANONICAL_MAP:
        ck, cv = _CANONICAL_MAP[key]
        return ck, cv
    return None, None


# ---------------------------------------------------------------------------
# Route-line extraction
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


def _extract_route_line(text: str) -> str:
    """Backwards-compatible wrapper around :func:`extract_gaussian_route_text`.

    Returns ``""`` for the no-route case instead of ``None`` so existing
    call sites that pass straight into :func:`_parse_route_tokens` keep
    working without per-call None guards.
    """

    route = extract_gaussian_route_text(text)
    return route or ""


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


# ---------------------------------------------------------------------------
# Link 0 extraction (%mem, %nproc, %chk)
# ---------------------------------------------------------------------------

# Link0 directives that are file paths, not execution parameters.
_LINK0_EXCLUDED = frozenset({
    "chk", "oldchk", "rwf", "int", "d2e", "scr",
    "subst", "nosave", "save",
})

_LINK0_RE = re.compile(r"^%(\w+)\s*=\s*(.+)$", re.IGNORECASE)


def _extract_link0(text: str) -> list[dict]:
    """Extract Link 0 commands (% directives) from log text.

    File-path directives (%chk, %oldchk, %rwf, etc.) are excluded — they
    are not execution parameters.
    """
    params = []
    for line in text.splitlines():
        m = _LINK0_RE.match(line.strip())
        if m and m.group(1).lower() not in _LINK0_EXCLUDED:
            directive = f"%{m.group(1)}"
            value = m.group(2).strip()
            ck, cv = _lookup_canonical("resource", directive)
            params.append(
                {
                    "raw_key": directive,
                    "canonical_key": ck,
                    "raw_value": value,
                    "canonical_value": cv,
                    "section": "resource",
                    "value_type": _guess_value_type(value),
                }
            )
    return params


# ---------------------------------------------------------------------------
# Route-line tokenizer and parser
# ---------------------------------------------------------------------------

_IOP_RE = re.compile(r"IOp\(([^)]+)\)", re.IGNORECASE)


def _parse_route_tokens(route: str) -> list[dict]:
    """Parse the Gaussian route line into parameter dicts.

    Handles:
      - #P / #N / #T  (verbosity)
      - method/basis   (uwb97xd/def2tzvp)
      - key=value      (guess=read)
      - key=(sub,opts) (opt=(calcfc,maxcycle=100,tight))
      - IOp(o/k=v)     (IOp(2/9=2000))
    """
    params: list[dict] = []

    # Strip the # prefix
    route = route.strip()
    if route.startswith("#"):
        # Extract verbosity: #P, #N, #T, or just #
        m = re.match(r"#([PNTpnt])?\s*", route)
        if m and m.group(1):
            params.append(
                {
                    "raw_key": "verbosity",
                    "canonical_key": "output.verbosity",
                    "raw_value": m.group(1).upper(),
                    "canonical_value": {
                        "P": "full",
                        "N": "normal",
                        "T": "terse",
                    }.get(m.group(1).upper()),
                    "section": "general",
                    "value_type": "enum",
                }
            )
        route = route[m.end() :] if m else route[1:]

    # Extract IOp() directives first (they contain parens that confuse
    # the general tokenizer). Each IOp(overlay/option=value) becomes one
    # row; the overlay/option pair stays in raw_key so the same canonical
    # key 'internal_option.iop' is queryable across all IOp settings.
    for iop_match in _IOP_RE.finditer(route):
        iop_body = iop_match.group(1)
        for part in iop_body.split(","):
            part = part.strip()
            if "=" in part:
                iop_key, iop_val = part.split("=", 1)
                iop_key = iop_key.strip()
                iop_val = iop_val.strip()
                params.append(
                    {
                        "raw_key": f"IOp({iop_key})",
                        "canonical_key": "internal_option.iop",
                        "raw_value": iop_val,
                        "canonical_value": None,
                        "section": "internal_option",
                        "value_type": _guess_value_type(iop_val),
                    }
                )
    # Remove IOp(...) from route for further parsing
    route = _IOP_RE.sub("", route).strip()

    # Now tokenize the remaining route.
    # Tokens are separated by spaces, but parenthesized groups must be kept together.
    tokens = _tokenize_route(route)

    for token in tokens:
        token = token.strip()
        if not token:
            continue

        # method/basis pattern: word/word (no = sign, contains /)
        if "/" in token and "=" not in token and "(" not in token:
            # This is level-of-theory, not a parameter — skip (belongs in LoT)
            continue

        # key=(sub-options) pattern
        if "=(" in token:
            key, rest = token.split("=(", 1)
            sub_options = rest.rstrip(")").strip()
            section = key.lower()
            for sub in sub_options.split(","):
                sub = sub.strip()
                if not sub:
                    continue
                if "=" in sub:
                    sub_key, sub_val = sub.split("=", 1)
                    sub_key = sub_key.strip()
                    sub_val = sub_val.strip()
                    ck, cv = _lookup_canonical(section, sub_key)
                    params.append(
                        {
                            "raw_key": sub_key,
                            "canonical_key": ck,
                            "raw_value": sub_val,
                            "canonical_value": cv if cv else None,
                            "section": section,
                            "value_type": _guess_value_type(sub_val),
                        }
                    )
                else:
                    # Boolean flag
                    ck, cv = _lookup_canonical(section, sub)
                    params.append(
                        {
                            "raw_key": sub,
                            "canonical_key": ck,
                            "raw_value": "true",
                            "canonical_value": cv if cv else None,
                            "section": section,
                            "value_type": "bool",
                        }
                    )

        # simple key=value
        elif "=" in token:
            key, value = token.split("=", 1)
            key = key.strip()
            value = value.strip()
            ck, cv = _lookup_canonical("general", key)
            params.append(
                {
                    "raw_key": key,
                    "canonical_key": ck,
                    "raw_value": value,
                    "canonical_value": cv if cv else None,
                    "section": "general",
                    "value_type": _guess_value_type(value),
                }
            )

        # Standalone keyword (not method/basis, not key=value)
        else:
            lowered = token.lower()
            # Symmetry-control flags land in their own section so they
            # are independent of the generic "general" bucket.
            if lowered == "nosymm":
                ck, cv = _lookup_canonical("symmetry", lowered)
                params.append(
                    {
                        "raw_key": lowered,
                        "canonical_key": ck,
                        "raw_value": "true",
                        "canonical_value": cv,
                        "section": "symmetry",
                        "value_type": "bool",
                    }
                )
            elif lowered in ("force", "test"):
                ck, cv = _lookup_canonical("general", lowered)
                params.append(
                    {
                        "raw_key": lowered,
                        "canonical_key": ck,
                        "raw_value": "true",
                        "canonical_value": cv,
                        "section": "general",
                        "value_type": "bool",
                    }
                )

    return params


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


# ---------------------------------------------------------------------------
# Software version extraction
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(
    r"Gaussian\s+(\d+):\s+(\S+)\s+(\d{1,2}-\w{3}-\d{4})"
)


def parse_software_version(text: str) -> dict | None:
    """Extract Gaussian software version info from log text."""
    m = _VERSION_RE.search(text)
    if m:
        return {
            "name": "gaussian",
            "version": m.group(1),
            "build": m.group(2),
            "release_date_raw": m.group(3),
        }
    return None


# ---------------------------------------------------------------------------
# Charge / multiplicity
# ---------------------------------------------------------------------------

_CHARGE_MULT_RE = re.compile(r"Charge\s*=\s*(-?\d+)\s+Multiplicity\s*=\s*(\d+)")


def parse_charge_multiplicity(text: str) -> tuple[int, int] | None:
    """Extract charge and multiplicity from log text."""
    m = _CHARGE_MULT_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


# ---------------------------------------------------------------------------
# Method / basis extraction from route line
# ---------------------------------------------------------------------------

_METHOD_BASIS_RE = re.compile(r"(?:^|\s)(u?\w+)/(\S+)", re.IGNORECASE)


def parse_method_basis(route: str) -> dict | None:
    """Extract method and basis set from the route line."""
    m = _METHOD_BASIS_RE.search(route)
    if m:
        return {"method": m.group(1), "basis": m.group(2)}
    return None


# ---------------------------------------------------------------------------
# Value type guessing
# ---------------------------------------------------------------------------


def _guess_value_type(value: str) -> str:
    """Heuristic guess for the value type of a parameter."""
    if value.lower() in ("true", "false"):
        return "bool"
    try:
        int(value)
        return "int"
    except ValueError:
        pass
    try:
        float(value)
        return "float"
    except ValueError:
        pass
    return "string"


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------

PARSER_VERSION = "gaussian_v1"


def parse_gaussian_log(filepath: str | Path) -> dict:
    """Parse a Gaussian log file and extract execution parameters.

    Returns a dict with:
      - parameters: list of CalculationParameter-compatible dicts
      - parameters_json: dict snapshot of all parsed parameters
      - route_line: the raw route line string
      - software: software version info (or None)
      - charge_multiplicity: (charge, mult) tuple (or None)
      - method_basis: {"method": ..., "basis": ...} (or None)
      - parser_version: version tag for this parser
    """
    text = Path(filepath).read_text()

    route = _extract_route_line(text)
    link0_params = _extract_link0(text)
    route_params = _parse_route_tokens(route)

    all_params = link0_params + route_params

    # Build the JSON snapshot
    parameters_json: dict = {"route_line": route, "sections": {}}
    for p in all_params:
        section = p.get("section", "unknown")
        if section not in parameters_json["sections"]:
            parameters_json["sections"][section] = {}
        parameters_json["sections"][section][p["raw_key"]] = p["raw_value"]

    return {
        "parameters": all_params,
        "parameters_json": parameters_json,
        "route_line": route,
        "software": parse_software_version(text),
        "charge_multiplicity": parse_charge_multiplicity(text),
        "method_basis": parse_method_basis(route),
        "parser_version": PARSER_VERSION,
    }
