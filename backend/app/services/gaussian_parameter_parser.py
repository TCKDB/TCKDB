"""Parse Gaussian log files to extract execution parameters.

Pure-function parser: takes text, returns structured dicts compatible with
the CalculationParameter model.  No DB dependency.
"""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

from tckdb_schemas.hessian_method_detection import (  # noqa: F401  (re-exported: the route readers live in tckdb-schemas)
    _IOP_RE,
    _ROUTE_DELIM,
    GAUSSIAN_FREQ_HESSIAN_OPTIONS,
    _echo_text,
    _extract_route_line_fallback,
    _extract_route_line_from_log_echo,
    _extract_route_line_from_raw_input,
    _tokenize_route,
    extract_gaussian_route_text,
    gaussian_freq_hessian_option,
)

from app.parser_registry import PARSERS
from app.services.parameter_closed_values import CLOSED_VALUES, canonical_or_none, normalise_solvent

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
    # freq section
    #
    # The imaginary-mode tau keys on how the *frequency job* built its second
    # derivatives. Gaussian only says so when the default is overridden,
    # so only the overrides are recorded: an unqualified ``Freq`` leaves
    # the key absent, which resolves to the conservative tau rather than
    # being read as "analytic". Assuming the default would be a guess
    # dressed as provenance.
    # The option table is shared with upload validation
    # (``tckdb_schemas.hessian_method_detection``): one owner.
    **{
        ("freq", option): ("freq.hessian_method", method.value)
        for option, method in GAUSSIAN_FREQ_HESSIAN_OPTIONS.items()
    },
    # integral section. ``Int=UltraFine``, ``Int=(Grid=UltraFine)`` and ``Integral=UltraFineGrid`` state the
    # same grid, so the bare names and the ``Grid=`` value land on one canonical value (see
    # :func:`_grid_value`, which also reads ``Grid=<digits>``).
    ("integral", "grid"): ("grid.quality", None),
    ("integral", "acc2e"): ("integral.accuracy", None),
    **{
        ("integral", name): ("grid.quality", value)
        for name, value in {
            "coarse": "coarse", "coarsegrid": "coarse",
            "sg1": "sg1", "sg1grid": "sg1",
            "fine": "fine", "finegrid": "fine",
            "ultrafine": "ultrafine", "ultrafinegrid": "ultrafine",
            "superfine": "superfine", "superfinegrid": "superfine",
            "pass0grid": "pass0grid",
        }.items()
    },
    # Gaussian's ``Integral`` relativistic options (G09 manual, Integral, "Relativistic calculations"):
    # DKH, DKH2 and DouglasKrollHess are synonyms; NoDKH and NonRelativistic request the default Hamiltonian.
    **{
        ("integral", name): ("relativistic.hamiltonian", value)
        for name, value in {
            "dkh": "dkh2", "dkh2": "dkh2", "douglaskrollhess": "dkh2",
            "dkh0": "dkh0", "dkhso": "dkhso", "resc": "resc",
            "nodkh": "none", "nonrelativistic": "none",
        }.items()
    },
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





def _extract_route_line(text: str) -> str:
    """Backwards-compatible wrapper around :func:`extract_gaussian_route_text`.

    Returns ``""`` for the no-route case instead of ``None`` so existing
    call sites that pass straight into :func:`_parse_route_tokens` keep
    working without per-call None guards.
    """

    route = extract_gaussian_route_text(text)
    return route or ""








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
                _mirror_free_form_value(
                    {
                        "raw_key": directive,
                        "canonical_key": ck,
                        "raw_value": value,
                        "canonical_value": cv,
                        "section": "resource",
                        "value_type": _guess_value_type(value),
                    }
                )
            )
    return params


# ---------------------------------------------------------------------------
# Route-line tokenizer and parser
# ---------------------------------------------------------------------------



def _emit_scf_convergence_failure_ignored(iop_value: str) -> list[dict]:
    """Emit specialized canonical rows for ``IOp(5/13=1)``.

    Gaussian's overlay/option ``5/13=1`` instructs the SCF to continue when
    convergence fails — so the reported energy/geometry comes from a non-
    converged wavefunction. This is a calculation trust flag, not SCF
    wavefunction stability evidence. Two canonical rows are emitted (in
    addition to the generic ``internal_option.iop`` row written by the
    caller):

    - ``scf.convergence_failure_ignored = true`` — boolean, drives simple
      safety queries.
    - ``scf.convergence_failure_action = continue`` — enum, leaves room
      for future Gaussian options that select alternative actions
      without re-canonicalizing.

    Returns an empty list for any value other than ``"1"``. The default
    Gaussian value (``0`` — fail and stop) is not stored as a canonical
    row to avoid flooding the table with no-op observations.
    """
    if iop_value.strip() != "1":
        return []
    return [
        {
            "raw_key": "IOp(5/13)",
            "canonical_key": "scf.convergence_failure_ignored",
            "raw_value": iop_value,
            "canonical_value": "true",
            "section": "internal_option",
            "value_type": "bool",
        },
        {
            "raw_key": "IOp(5/13)",
            "canonical_key": "scf.convergence_failure_action",
            "raw_value": iop_value,
            "canonical_value": "continue",
            "section": "internal_option",
            "value_type": "enum",
        },
    ]


# Maps an IOp overlay/option (lhs of ``=``, e.g. ``"5/13"``) to a
# specialized emitter that returns 0+ extra parameter rows for that
# specific value. Equality on the lhs string naturally rejects similar-
# but-distinct overlays like ``"15/13"`` or ``"5/130"``.
_IOP_TRUST_FLAG_EMITTERS: dict[str, callable] = {
    "5/13": _emit_scf_convergence_failure_ignored,
}


# ---------------------------------------------------------------------------
# Keywords that carry options: Int/Integral, SCF, Opt, EmpiricalDispersion, SCRF, Stable
# ---------------------------------------------------------------------------

#: ``keyword`` spellings whose options are read into a section. ``Int`` is Gaussian's short form of ``Integral``.
_SECTIONED_KEYWORDS = {"int": "integral", "integral": "integral", "scf": "scf", "opt": "opt"}

_KEYWORD_SPLIT = re.compile(r"^([A-Za-z][A-Za-z0-9]*)(?:=(.*)|(\(.*\)))?$", re.DOTALL)

#: A named grid (``UltraFine``, ``FineGrid``) is a *pruned* grid; ``Grid=mmmnnn`` is an *unpruned* mmm x nnn grid
#: (G09 Integral manual, "Integration grid selection option"). They are different grids, so a numeric grid is never
#: mapped to a named one: it keeps its digits (``99590``) as its canonical value. The tight-grid test of the
#: imaginary-mode tolerance (``stationary_point._TIGHT_GRID_VALUES``) lists the digits ``99590`` beside the names and
#: is unchanged.
_GRID_NAMES = {
    name: value
    for (section, name), (key, value) in _CANONICAL_MAP.items()
    if section == "integral" and key == "grid.quality" and value is not None
}

_DISPERSION_VALUES = {"gd2": "d2", "gd3": "d3_zero", "gd3bj": "d3_bj", "none": "none"}
_SCRF_MODELS = {"smd": "smd", "cpcm": "cpcm", "pcm": "iefpcm", "iefpcm": "iefpcm"}
#: Every SCRF option that is a flag rather than a solvent name, so a solvent name that itself contains a comma
#: (``n,n-dimethylformamide``) can be rejoined.
_SCRF_FLAGS = frozenset({
    "smd", "cpcm", "pcm", "iefpcm", "ipcm", "scipcm", "dipole", "onsager", "read", "check", "iterative", "externaliteration",
    "nodis", "dis", "norep", "rep", "nocav", "cav", "nocavity", "cavity", "dipolem", "solute", "volume", "tabulated",
})
_CORE_OPTIONS = {"full": "all_electron", "fc": "frozen_core", "frozencore": "frozen_core", "window": "window"}
#: Correlated methods whose route option can carry the frozen-core choice. Not ``Pop=(Full)``, which means something else.
_CORE_METHOD_PREFIXES = (
    "mp2", "mp3", "mp4", "mp5", "ccsd", "ccd", "cid", "cisd", "qcisd", "bd", "eomccsd", "ci",
    "ump2", "ump3", "ump4", "rmp2", "ccsdt",
)
_FUNCTION_TYPES = {
    "5d": ("basis.d_function_type", "spherical"),
    "6d": ("basis.d_function_type", "cartesian"),
    "7f": ("basis.f_function_type", "spherical"),
    "10f": ("basis.f_function_type", "cartesian"),
}


def _method_part(token: str) -> str | None:
    """The method of a ``method/basis`` token, or ``None`` when the token is not one.

    The first ``/`` outside parentheses splits them, and an ``=`` outside parentheses before it means the token
    is a keyword with a value, not a method (``CIS(NStates=5,Full)/6-31G`` is a method: its ``=`` is inside).
    """
    depth = 0
    for index, char in enumerate(token):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and char == "=":
            return None
        elif depth == 0 and char == "/":
            return token[:index]
    return None


def _grid_value(raw: str) -> str | None:
    """The canonical grid for ``Grid=<raw>``: a documented grid name, or the digits of a numeric grid; else ``None``.

    A name Gaussian does not document stays a raw row with no canonical key.
    """
    cleaned = raw.strip().lower().lstrip("+")
    if cleaned in _GRID_NAMES:
        return _GRID_NAMES[cleaned]
    return cleaned if cleaned.lstrip("-").isdigit() else None


def _split_keyword(token: str) -> tuple[str, str, str] | None:
    """``(keyword as written, lower-cased keyword, option text)`` for ``Key``, ``Key=value``, ``Key=(..)``, ``Key(..)``."""
    match = _KEYWORD_SPLIT.match(token)
    if match is None:
        return None
    written = match.group(1)
    body = match.group(2) if match.group(2) is not None else match.group(3)
    body = (body or "").strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1].strip()
    return written, written.lower(), body


def _split_options(body: str) -> list[tuple[str, str | None]]:
    """Top-level options of ``body`` as ``(name, value or None)``; commas and spaces separate, parentheses group."""
    pieces: list[str] = []
    current: list[str] = []
    depth = 0
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if depth == 0 and char in ", \t":
            if current:
                pieces.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        pieces.append("".join(current))

    options: list[tuple[str, str | None]] = []
    for piece in pieces:
        if "=" in piece:
            name, value = piece.split("=", 1)
            options.append((name.strip(), value.strip()))
        elif options and options[-1][0].lower() == "solvent" and piece.lower() not in _SCRF_FLAGS:
            options[-1] = (options[-1][0], f"{options[-1][1]},{piece}")
        else:
            options.append((piece.strip(), None))
    return options


def _row(raw_key: str, raw_value: str, ck: str | None, cv: str | None, section: str, value_type: str | None = None) -> dict:
    """One parameter row, its canonical key kept only when the value is in the key's closed set."""
    ck, cv = canonical_or_none(ck, cv)
    return {
        "raw_key": raw_key,
        "canonical_key": ck,
        "raw_value": raw_value,
        "canonical_value": cv,
        "section": section,
        "value_type": value_type or _guess_value_type(raw_value),
    }


def _emit_keyword_options(written: str, key: str, body: str) -> list[dict]:
    """Rows for a keyword whose options are parameters (``SCF=Tight``, ``Int=(Grid=UltraFine)``, ``Opt(TS)``, ...)."""
    options = _split_options(body)
    rows: list[dict] = []

    if key in _SECTIONED_KEYWORDS:
        section = _SECTIONED_KEYWORDS[key]
        for name, value in options:
            ck, cv = _lookup_canonical(section, name)
            if ck == "grid.quality" and name.lower() == "grid" and value is not None:
                cv = _grid_value(value)
                if cv is None:
                    ck = None
            rows.append(_row(name, value if value is not None else "true", ck, cv, section, None if value else "bool"))
        return rows

    if key == "empiricaldispersion":
        if options:
            name, value = options[0]
            text = value if value is not None else name
            rows.append(_row(written, text, "dispersion.correction", _DISPERSION_VALUES.get(text.lower()), "general", "enum"))
        return rows

    if key == "scrf":
        for name, value in options:
            lowered = name.lower()
            if lowered == "solvent" and value is not None:
                rows.append(_row(name, value, "solvation.solvent", normalise_solvent(value), "scrf", "string"))
            elif lowered in _SCRF_MODELS and value is None:
                rows.append(_row(name, "true", "solvation.model", _SCRF_MODELS[lowered], "scrf", "bool"))
            else:
                rows.append(_row(name, value if value is not None else "true", None, None, "scrf"))
        return rows

    if key == "stable":
        wants_optimisation = any(name.lower() == "opt" for name, _ in options)
        rows.append(
            _row(written, body or "true", "scf.stability_analysis", "optimize" if wants_optimisation else "check_only", "stability", "enum")
        )
        return rows

    return rows


def _emit_core_options(method_part: str) -> list[dict]:
    """Frozen-core rows for a correlated method written with route options (``MP2(Full)``, ``CCSD(T,FC)``)."""
    split = _split_keyword(method_part)
    if split is None or not split[1].startswith(_CORE_METHOD_PREFIXES):
        return []
    rows: list[dict] = []
    for name, value in _split_options(split[2]):
        core = _CORE_OPTIONS.get(name.lower())
        if core is not None:
            rows.append(_row(name, value if value is not None else "true", "core.frozen", core, "method_options", "enum"))
    return rows


def _mirror_free_form_value(row: dict) -> dict:
    """Give a canonical row with no mapped value its raw value, as the ORCA parser does.

    ``MaxCycle=512`` has a canonical key and no enumeration to map to; leaving ``canonical_value`` empty made the
    row unreadable to anything that reads canonical values only. ``IOp`` rows are excluded: their raw key carries
    the coordinate, so one canonical value would stand for many different options.
    """
    if (
        row.get("canonical_key") is not None
        and row.get("canonical_value") is None
        and row["canonical_key"] != "internal_option.iop"
        and row["canonical_key"] not in CLOSED_VALUES
    ):
        value = row["raw_value"]
        # Case carries no meaning in a setting's value (``Guess=Read``, ``Grid=Pass0Grid``), and the comparison
        # with a typed value is exact, so the canonical form is lower case; a memory request or a processor count
        # is left as written.
        row["canonical_value"] = value if row["canonical_key"].startswith(("memory.", "parallel.")) else value.lower()
    return row


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
    original_route = route

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
    #
    # Specific overlay/option pairs that materially affect result trust
    # ALSO emit specialized canonical rows alongside the generic one, so
    # safety queries like "find all calcs where SCF convergence failure
    # was ignored" can hit a stable canonical_key without scanning raw
    # values across thousands of IOp permutations. See
    # ``_IOP_TRUST_FLAG_EMITTERS`` for the registered set.
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
                emitter = _IOP_TRUST_FLAG_EMITTERS.get(iop_key)
                if emitter is not None:
                    params.extend(emitter(iop_val))
    # Remove IOp(...) from route for further parsing
    route = _IOP_RE.sub("", route).strip()

    # ``keyword (options)`` and ``keyword = value`` are one token each.
    route = re.sub(r"\s*=\s*", "=", route)
    route = re.sub(r"(?<=[\w)])\s+\(", "(", route)

    # Now tokenize the remaining route.
    # Tokens are separated by spaces, but parenthesized groups must be kept together.
    tokens = _tokenize_route(route)

    for token in tokens:
        token = token.strip()
        if not token:
            continue

        # method/basis pattern: ``method/basis``. The method and basis are level-of-theory, not parameters,
        # but the method can carry the frozen-core choice (``CCSD(T,Full)/cc-pVTZ``), which is.
        method_part = _method_part(token)
        if method_part is not None:
            params.extend(_emit_core_options(method_part))
            continue

        split = _split_keyword(token)
        if split is not None:
            written, keyword, body = split
            if keyword in _SECTIONED_KEYWORDS or keyword in ("empiricaldispersion", "scrf", "stable"):
                params.extend(_emit_keyword_options(written, keyword, body))
                continue
            if keyword.startswith(_CORE_METHOD_PREFIXES):
                params.extend(_emit_core_options(token))
                continue
        if token.lower() in _FUNCTION_TYPES:
            key, value = _FUNCTION_TYPES[token.lower()]
            params.append(_row(token, "true", key, value, "basis", "enum"))
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

        # ``Freq=Numer``: the one-option form of ``Freq=(Numer)``.
        #
        # Handled separately because the generic key=value branch below
        # files everything under the ``general`` section, which would put
        # the frequency job's Hessian method somewhere the tau
        # resolution cannot see it. Only route it here when the option is
        # one the vocabulary knows; anything else keeps the old shape.
        elif token.lower().startswith("freq=") and _lookup_canonical(
            "freq", token.split("=", 1)[1].strip()
        ) != (None, None):
            sub = token.split("=", 1)[1].strip()
            ck, cv = _lookup_canonical("freq", sub)
            params.append(
                {
                    "raw_key": sub,
                    "canonical_key": ck,
                    "raw_value": "true",
                    "canonical_value": cv,
                    "section": "freq",
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

    # The Hessian method is read once, by the reader shared with upload
    # validation (every spelling of ``Freq``, ``EnOnly`` over ``Numer``), so the
    # stored row is the one validation judged. Rows stay exactly as the deck
    # wrote them (``raw_key`` keeps its case, the freq section keeps every
    # option); only the winning option's row keeps the canonical key and value,
    # so no row's raw text contradicts its canonical value.
    shared = gaussian_freq_hessian_option(original_route)
    winner_seen = False
    for p in params:
        if p.get("canonical_key") != "freq.hessian_method":
            continue
        if shared is not None and not winner_seen and str(p["raw_key"]).lower() == shared[0].lower():
            p["canonical_value"] = shared[1].value
            winner_seen = True
        else:
            p["canonical_key"] = None
            p["canonical_value"] = None
    if shared is not None and not winner_seen:
        # A spelling the generic branches above do not emit a row for
        # (``Freq(Numer)``, ``Freq (Numer)``, ``Freq = Numer``, ``Frequency=..``).
        option, method = shared
        params.append(
            {
                "raw_key": option,
                "canonical_key": "freq.hessian_method",
                "raw_value": "true",
                "canonical_value": method.value,
                "section": "freq",
                "value_type": "bool",
            }
        )

    return [_mirror_free_form_value(p) for p in params]




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


def parse_all_charge_multiplicity(text: str) -> list[tuple[int, int]]:
    """Return *every* ``Charge = q Multiplicity = m`` declaration in the log.

    :func:`parse_charge_multiplicity` deliberately returns only the first
    match, which is the right answer for parameter extraction. Gaussian
    however prints the pair once per fragment in a counterpoise job and
    once per layer in an ONIOM job, so on those logs the first match
    describes a *fragment* rather than the whole system.

    Callers that cross-check the declared charge/multiplicity against the
    log use this function instead and require the declarations to agree
    before trusting them — see
    :mod:`app.services.charge_multiplicity_reconciliation`.
    """
    return [
        (int(m.group(1)), int(m.group(2)))
        for m in _CHARGE_MULT_RE.finditer(text)
    ]


# ---------------------------------------------------------------------------
# Method / basis extraction from route line
# ---------------------------------------------------------------------------

#: ``method/basis``: the method starts with a letter and may carry hyphens (``CAM-B3LYP``, ``wB97X-D``) and one
#: parenthesised group (``CCSD(T)``, ``CCSD(T,Full)``, ``MP2(Full)``). An ``IOp(2/9=2000)`` is not one.
_METHOD_BASIS_RE = re.compile(r"(?:^|\s)([A-Za-z][\w\-+*]*(?:\([^)\s]*\))?)/(\S+)")


def parse_method_basis(route: str) -> dict | None:
    """Extract method and basis set from the route line."""
    m = _METHOD_BASIS_RE.search(route)
    if m:
        return {"method": m.group(1), "basis": m.group(2)}
    return None


_LINK1 = re.compile(r"^\s*--link1--\s*$", re.IGNORECASE | re.MULTILINE)


def all_route_texts(text: str) -> list[str]:
    """The route of every job in the file: each echoed route block of a log, else each ``--Link1--`` part of a deck."""
    lines = text.splitlines()
    dashes = [i for i, line in enumerate(lines) if _ROUTE_DELIM.match(line.strip())]
    echoed = []
    for first, second in pairwise(dashes):
        block = "".join(_echo_text(lines[j]) for j in range(first + 1, second))
        if block.startswith("#"):
            echoed.append(block)
    if echoed:
        return echoed
    routes = []
    for part in _LINK1.split(text):
        route = extract_gaussian_route_text(part)
        if route:
            routes.append(route)
    return routes


def parse_method_basis_jobs(text: str) -> list[dict]:
    """``{"methods": [method], "basis": basis}`` for every job whose route states a ``method/basis``."""
    jobs = []
    for route in all_route_texts(text):
        found = parse_method_basis(route)
        if found:
            jobs.append({"methods": [found["method"]], "basis": found["basis"]})
    return jobs


def parse_qualifier_jobs(text: str) -> list[dict]:
    """One entry per job route of the text: its method, basis and the canonical settings its route states.

    ``{"methods": [method], "basis": basis, "route": route, "pairs": {(canonical_key, canonical_value), ...}}``.
    Lets a check that compares a declared setting read one job's settings and not another's.
    """
    jobs = []
    for route in all_route_texts(text):
        found = parse_method_basis(route)
        pairs = {
            (p["canonical_key"], p["canonical_value"])
            for p in _parse_route_tokens(route)
            if p.get("canonical_key") is not None and p.get("canonical_value") is not None
        }
        jobs.append(
            {
                "methods": [found["method"]] if found else [],
                "basis": found["basis"] if found else None,
                "route": route,
                "pairs": pairs,
            }
        )
    return jobs


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

PARSER = PARSERS.get("gaussian_parameters")


def parse_gaussian_log(filepath: str | Path) -> dict:
    """Parse a Gaussian log file and extract execution parameters.

    Returns a dict with:
      - parameters: list of CalculationParameter-compatible dicts
      - parameters_json: dict snapshot of all parsed parameters
      - route_line: the raw route line string
      - software: software version info (or None)
      - charge_multiplicity: (charge, mult) tuple (or None)
      - method_basis: {"method": ..., "basis": ...} (or None)
      - parser: the parser's stamp (name and version)
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
        "parser": PARSER.stamp().model_dump(),
    }


# ---------------------------------------------------------------------------
# Single-point electronic energy
# ---------------------------------------------------------------------------

# Composite methods (CBS-*, G1-G4 and their MP2/B3 variants, Wn) interleave
# intermediate ``SCF Done``/``EUMP2`` lines whose energy is NOT the method
# result, so the electronic energy cannot be cross-checked from the log alone.
# Declaring them unverifiable (returning None) is safer than reporting a wrong
# sub-step value.
#
# The Gaussian route line declares the method and is the authoritative,
# truncation- and case-robust signal, so we gate on it primarily. As a backstop
# for logs whose route cannot be extracted, we also match the composite *result*
# lines — their ``(0 K)``/``Energy=`` suffix cannot occur in a user title/comment,
# so this never suppresses a plain DFT job that merely mentions a composite name.
_COMPOSITE_ROUTE_PATTERN = re.compile(
    r"\b(cbs-[a-z0-9]+|g[1-4](mp2|b3)?|w1(u|bd|ro)?)\b", re.IGNORECASE
)
_COMPOSITE_RESULT_MARKERS = (
    "CBS-QB3 (0 K)",
    "CBS-4 (0 K)",
    "CBS-APNO (0 K)",
    "G1(0 K)",
    "G2(0 K)",
    "G2MP2(0 K)",
    "G3(0 K)",
    "G3MP2(0 K)",
    "G3B3(0 K)",
    "G4(0 K)",
    "G4MP2(0 K)",
    "W1BD (0 K)",
    "W1U (0 K)",
    "W1RO (0 K)",
)


def _is_composite_gaussian(text: str, route: str) -> bool:
    """True if the log is a composite (CBS/Gn/Wn) job — unverifiable here."""
    if route and _COMPOSITE_ROUTE_PATTERN.search(route):
        return True
    return any(marker in text for marker in _COMPOSITE_RESULT_MARKERS)


def is_composite_gaussian_log(text: str) -> bool:
    """Public form of :func:`_is_composite_gaussian`, deriving the route itself.

    Used to *record why* a Gaussian log yields no single-point energy. A
    composite job's energy is read by ``gaussian_composite_parser``, not here.
    """
    return _is_composite_gaussian(text, extract_gaussian_route_text(text) or "")


def _sp_float_at_index(line: str, idx: int) -> float | None:
    parts = line.split()
    if len(parts) > idx:
        try:
            return float(parts[idx].replace("D", "E"))
        except ValueError:
            return None
    return None


def _sp_last_float(line: str) -> float | None:
    parts = line.split()
    if parts:
        try:
            return float(parts[-1].replace("D", "E"))
        except ValueError:
            return None
    return None


def _sp_scf_done(line: str) -> float | None:
    match = re.search(r"E\(.+\)\s+=\s+([-]?\d+\.\d+)", line)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _sp_archive_hf(line: str, lines: list[str], idx: int) -> float | None:
    # The ``HF=<energy>`` field of the Gaussian archive block may wrap onto
    # the next line; join and slice between ``HF=`` and the next backslash.
    next_line = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
    joined = line.strip() + next_line
    start = joined.find("HF=") + 3
    end = joined.find("\\", start)
    if start > 2 and end > start:
        try:
            return float(joined[start:end])
        except ValueError:
            return None
    return None


def parse_sp_energy(text: str) -> float | None:
    """Electronic energy (Hartree) from a Gaussian single-point output.

    Mirrors ARC's Gaussian adapter for the single-value method families
    (DFT/HF ``SCF Done``, ``CCSD(T)=``, ``MP2 =``, ``E(CORR)=``, the
    ``E2(...)/E(...)`` line, and the archive ``HF=`` fallback). The last
    matching value wins, matching Gaussian's re-print order.

    Composite methods (CBS-*, Gn, Wn) are **not** supported here: their
    electronic energy cannot be cross-checked from interleaved intermediate
    ``SCF Done`` lines, so the function returns ``None`` (unverifiable)
    rather than a wrong sub-step value — see :func:`_is_composite_gaussian`.
    Returns ``None`` when no electronic energy is found.
    """
    if _is_composite_gaussian(text, extract_gaussian_route_text(text) or ""):
        return None

    lines = text.splitlines()
    e_elect: float | None = None
    for i, line in enumerate(lines):
        value: float | None = None
        if "SCF Done:" in line:
            value = _sp_scf_done(line)
        elif " E2(" in line and " E(" in line:
            value = _sp_last_float(line)
        elif "MP2 =" in line:
            value = _sp_last_float(line)
        elif "E(CORR)=" in line:
            value = _sp_float_at_index(line, 3)
        elif "CCSD(T)=" in line:
            value = _sp_float_at_index(line, 1)
        elif "HF=" in line and e_elect is None:
            value = _sp_archive_hf(line, lines, i)
        if value is not None:
            e_elect = value
    return e_elect


def parse_hessian(text: str) -> tuple[int, list[float]] | None:
    """Cartesian Hessian (hartree/bohr²) from a Gaussian output log.

    Mirrors Arkane's ``GaussianLog.load_force_constant_matrix`` for the
    ``Force constants in Cartesian coordinates:`` block (the last one wins),
    but keeps Gaussian's native atomic units — no conversion to J/m². Returns
    ``(natoms, packed_lower_triangle)`` or ``None`` when the block is absent.

    Orientation guard (Arkane): when the route requests an optimization and
    the molecule has more than 13 atoms, Gaussian prints the force-constant
    matrix in *standard* (not input) orientation unless ``IOp(2/9=2000)`` is
    set. That matrix is meaningless against the input-orientation geometry we
    would bind, so — consistent with the best-effort contract — we skip
    (return ``None``) rather than raise as Arkane does.
    """
    from app.services.hessian_parsing import (
        GAUSSIAN_HESSIAN_MARKER,
        parse_triangular_force_constants,
    )

    result = parse_triangular_force_constants(text, marker=GAUSSIAN_HESSIAN_MARKER)
    if result is None:
        return None

    natoms, _ = result
    route = (extract_gaussian_route_text(text) or "").lower()
    optimization_requested = "opt" in route
    iop_2_9_2000 = "2/9=2000" in text
    if optimization_requested and natoms > 13 and not iop_2_9_2000:
        return None

    return result
