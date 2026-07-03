"""CHEMKIN lexer/parser -> raw AST (stage 1).

Pure text processing: no RDKit, no TCKDB imports. Parses a gas-phase CHEMKIN
mechanism (``ELEMENTS``/``SPECIES``/``THERMO``/``REACTIONS``) plus, optionally,
a separate ``therm.dat`` thermo file. A companion ``tran.dat`` parser lives in
``transport.py``.

The parser is deliberately forgiving about column alignment for reaction lines
(free format) while parsing NASA-7 thermo cards by the fixed-column layout with
a whitespace fallback, so it handles both canonical and loosely-formatted files.
"""

from __future__ import annotations

import re

from .ast import (
    ChebyshevBlock,
    Mechanism,
    PlogPoint,
    Reaction,
    SpeciesDecl,
    ThermoEntry,
)
from .forms import (
    DEFAULT_A_CONC_BASIS,
    DEFAULT_EA_TOKEN,
    KNOWN_AUX_KEYWORDS,
    UNSUPPORTED_AUX_KEYWORDS,
)


class ChemkinParseError(ValueError):
    """Raised for structural problems the parser cannot recover from."""


_BLOCK_STARTS = {
    "ELEMENTS": "ELEMENTS",
    "ELEM": "ELEMENTS",
    "SPECIES": "SPECIES",
    "SPEC": "SPECIES",
    "THERMO": "THERMO",
    "THERM": "THERMO",  # RMG emits "THERM ALL"
    "THER": "THERMO",
    "REACTIONS": "REACTIONS",
    "REAC": "REACTIONS",
    "TRANSPORT": "TRANSPORT",
    "TRAN": "TRANSPORT",
}

_A_CONC_TOKENS = {"MOLES", "MOLECULES"}


def _strip_comment(line: str) -> tuple[str, str | None]:
    """Return ``(code, comment)`` splitting on the first unquoted ``!``."""
    idx = line.find("!")
    if idx == -1:
        return line.rstrip("\n"), None
    return line[:idx], line[idx + 1 :].strip() or None


def _fortran_floats(
    line: str, width: int = 15, max_count: int | None = None
) -> list[float]:
    """Read fixed-width Fortran floats, falling back to whitespace splitting.

    NASA-7 coefficient lines are written in ``E15.8`` fields that can run
    together with no separating space (e.g. ``-1.0E+01-2.0E-02``); fixed-width
    chunking is required to split those. Loosely-formatted files that *do* use
    spaces are handled by the whitespace fallback.

    :param max_count: Stop after this many values. Used to ignore the trailing
        card-index digit in column 80 of NASA coefficient lines.
    """
    body = line.rstrip("\n")

    def _norm(tok: str) -> str:
        return tok.strip().replace("D", "E").replace("d", "e")

    def _cap(values: list[float]) -> list[float]:
        return values[:max_count] if max_count is not None else values

    # Try fixed-width chunks first.
    chunks = [body[i : i + width] for i in range(0, len(body), width)]
    fixed: list[float] = []
    ok = True
    for chunk in chunks:
        tok = _norm(chunk)
        if not tok:
            continue
        try:
            fixed.append(float(tok))
        except ValueError:
            ok = False
            break
        if max_count is not None and len(fixed) >= max_count:
            break
    if ok and fixed:
        return _cap(fixed)

    # Fallback: whitespace split.
    out: list[float] = []
    for tok in body.split():
        out.append(float(_norm(tok)))
        if max_count is not None and len(out) >= max_count:
            break
    return _cap(out)


# ---------------------------------------------------------------------------
# Top-level block splitting
# ---------------------------------------------------------------------------


def _split_blocks(lines: list[str]) -> list[tuple[str, int, list[tuple[int, str]]]]:
    """Split raw lines into ``(block_name, header_line_no, body_lines)`` groups.

    ``body_lines`` are ``(line_no, code_text)`` pairs with comments stripped,
    excluding the block-start keyword line and the terminating ``END``.
    The block-start keyword line's *remainder* (e.g. header units after
    ``REACTIONS``) is retained as the first body entry.
    """
    blocks: list[tuple[str, int, list[tuple[int, str]]]] = []
    current: str | None = None
    header_no = 0
    body: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal current, body, header_no
        if current is not None:
            blocks.append((current, header_no, body))
        current, body = None, []

    for i, raw in enumerate(lines, start=1):
        code, _comment = _strip_comment(raw)
        stripped = code.strip()
        if not stripped:
            if current is not None:
                body.append((i, ""))
            continue

        first = stripped.split()[0].upper()
        if first in _BLOCK_STARTS:
            flush()
            current = _BLOCK_STARTS[first]
            header_no = i
            remainder = stripped[len(stripped.split()[0]) :].strip()
            body = [(i, remainder)] if remainder else []
            continue

        if first == "END":
            flush()
            continue

        if current is not None:
            body.append((i, code.rstrip()))

    flush()
    return blocks


# ---------------------------------------------------------------------------
# ELEMENTS / SPECIES
# ---------------------------------------------------------------------------


def _parse_elements(body: list[tuple[int, str]]) -> list[str]:
    out: list[str] = []
    for _no, text in body:
        for tok in text.split():
            if tok.upper() == "END":
                continue
            out.append(tok)
    return out


def _parse_species(lines: list[str]) -> list[SpeciesDecl]:
    """Species need their trailing comments, so parse from raw lines."""
    out: list[SpeciesDecl] = []
    for _no, raw in lines:
        code, comment = _strip_comment(raw)
        tokens = code.split()
        if not tokens:
            continue
        # A species line may carry one name + a structure comment, or several
        # bare names. Attach the comment only when the line has a single name.
        if len(tokens) == 1:
            out.append(SpeciesDecl(name=tokens[0], comment=comment, line_no=_no))
        else:
            for tok in tokens:
                out.append(SpeciesDecl(name=tok, line_no=_no))
    return out


# ---------------------------------------------------------------------------
# THERMO (NASA-7)
# ---------------------------------------------------------------------------

_COMP_RE = re.compile(r"([A-Za-z][A-Za-z]?)\s*(-?\d+)")


def _parse_composition(card1: str) -> dict[str, int]:
    """Parse the elemental composition from a NASA card's first line.

    Standard layout: four 5-char (element[2] + count[3]) fields in columns
    25-44, plus an optional fifth field in columns 74-78. Zero counts are
    dropped. Element symbols are normalised to CHEMKIN's upper-case form.
    """
    comp: dict[str, int] = {}
    fields = [card1[24:29], card1[29:34], card1[34:39], card1[39:44]]
    if len(card1) >= 78:
        fields.append(card1[73:78])
    for field in fields:
        chunk = field.strip()
        if not chunk:
            continue
        m = _COMP_RE.fullmatch(chunk.replace(" ", ""))
        if not m:
            # Loose fallback: try scanning the raw chunk.
            m = _COMP_RE.search(chunk)
            if not m:
                continue
        sym = m.group(1).upper()
        count = int(m.group(2))
        if count != 0:
            comp[sym] = comp.get(sym, 0) + count
    return comp


def _parse_thermo_block(
    body: list[tuple[int, str]],
) -> dict[str, ThermoEntry]:
    """Parse a THERMO section body into name -> ThermoEntry.

    The optional global temperature-defaults line (three floats right after
    ``THERMO``/``THERMO ALL``) is consumed and used only when a card omits its
    common temperature.
    """
    # Drop leading "ALL" token / temperature-default line.
    rows = [(no, text) for no, text in body if text.strip()]
    default_common: float | None = None
    start = 0
    if rows and rows[0][1].strip().upper() in {"ALL", ""}:
        start = 1
    # A line of exactly three floats is the global default temp line.
    if len(rows) > start:
        toks = rows[start][1].split()
        if len(toks) == 3:
            try:
                _tlo, default_common, _thi = (float(t) for t in toks)
                start += 1
            except ValueError:
                pass

    cards = rows[start:]
    entries: dict[str, ThermoEntry] = {}
    i = 0
    while i < len(cards):
        no, line1 = cards[i]
        # A valid NASA card line 1 ends with a '1' index in column 80 region.
        if i + 3 >= len(cards):
            break
        _n2, line2 = cards[i + 1]
        _n3, line3 = cards[i + 2]
        _n4, line4 = cards[i + 3]

        name = line1[:18].split()[0] if line1[:18].strip() else line1.split()[0]
        comp = _parse_composition(line1)
        phase = line1[44:45].strip() or "G"
        # Temperatures in columns 46-73 (three E10 fields), fallback to split.
        temp_region = line1[45:73]
        temps = _fortran_floats(temp_region, width=10)
        if len(temps) < 2:
            # Loose fallback: pull trailing floats from the whole line.
            nums = re.findall(r"[-+]?\d*\.?\d+(?:[EeDd][-+]?\d+)?", line1[45:])
            temps = [float(x.replace("D", "E").replace("d", "e")) for x in nums[:3]]
        t_low = temps[0]
        t_high = temps[1]
        t_common = temps[2] if len(temps) >= 3 and temps[2] else (default_common or 1000.0)

        # Cap each coefficient line to its field count so the column-80 card
        # index ("2"/"3"/"4") is never read as a coefficient.
        c2 = _fortran_floats(line2, max_count=5)
        c3 = _fortran_floats(line3, max_count=5)
        c4 = _fortran_floats(line4, max_count=4)
        coeffs = c2 + c3 + c4
        if len(coeffs) < 14:
            raise ChemkinParseError(
                f"NASA thermo card for '{name}' (line {no}) has "
                f"{len(coeffs)} coefficients; expected 14."
            )
        coeffs_high = coeffs[0:7]
        coeffs_low = coeffs[7:14]

        entries[name] = ThermoEntry(
            name=name,
            composition=comp,
            phase=phase,
            t_low=t_low,
            t_high=t_high,
            t_common=t_common,
            coeffs_high=coeffs_high,
            coeffs_low=coeffs_low,
            line_no=no,
        )
        i += 4
    return entries


# ---------------------------------------------------------------------------
# REACTIONS
# ---------------------------------------------------------------------------

_ARROW_RE = re.compile(r"<=>|=>|<=|=")
_FALLOFF_RE = re.compile(r"\(\+\s*([A-Za-z0-9_\-\(\)\*]+)\s*\)")
_SLASH_TOKEN_RE = re.compile(r"[^/]+")


def _split_side(side: str) -> list[tuple[int, str]]:
    """Split one side of a reaction into ``(coefficient, species)`` pairs.

    Handles ``2 H2`` and ``2H2`` stoichiometric prefixes. A bare ``M`` term is
    left in place here; the caller strips it.
    """
    out: list[tuple[int, str]] = []
    for term in re.split(r"\+", side):
        tok = term.strip()
        if not tok:
            continue
        m = re.match(r"^(\d+)\s*(.*)$", tok)
        if m and m.group(2):
            coeff = int(m.group(1))
            name = m.group(2).strip()
        else:
            coeff = 1
            name = tok
        out.append((coeff, name))
    return out


def _parse_equation(
    equation: str,
) -> tuple[list[tuple[int, str]], list[tuple[int, str]], bool, bool, bool, str | None]:
    """Parse a reaction equation string.

    :returns: ``(reactants, products, reversible, is_third_body, is_falloff,
        falloff_collider)`` with the third-body / falloff markers stripped from
        the species lists.
    """
    is_falloff = False
    falloff_collider: str | None = None
    m = _FALLOFF_RE.search(equation)
    if m:
        is_falloff = True
        falloff_collider = m.group(1).strip()
        equation = _FALLOFF_RE.sub("", equation)

    arrow = _ARROW_RE.search(equation)
    if not arrow:
        raise ChemkinParseError(f"No reaction arrow in equation: {equation!r}")
    token = arrow.group(0)
    reversible = token in ("<=>", "=")
    lhs = equation[: arrow.start()]
    rhs = equation[arrow.end() :]

    reactants = _split_side(lhs)
    products = _split_side(rhs)

    is_third_body = False

    def _strip_m(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
        nonlocal is_third_body
        kept = []
        for coeff, name in pairs:
            if name.upper() == "M":
                is_third_body = True
                continue
            kept.append((coeff, name))
        return kept

    reactants = _strip_m(reactants)
    products = _strip_m(products)

    return reactants, products, reversible, is_third_body, is_falloff, falloff_collider


def _slash_floats(text: str) -> list[float]:
    """Extract the numbers between the first pair of slashes."""
    inner = text[text.find("/") + 1 : text.rfind("/")]
    return [float(t.replace("D", "E").replace("d", "e")) for t in inner.split()]


def _parse_efficiency_line(text: str) -> dict[str, float]:
    """Parse a collider efficiency list like ``H2O/6.0/ AR/0.7/``."""
    tokens = [t.strip() for t in _SLASH_TOKEN_RE.findall(text)]
    tokens = [t for t in tokens if t]
    effs: dict[str, float] = {}
    for i in range(0, len(tokens) - 1, 2):
        name = tokens[i]
        value = float(tokens[i + 1])
        effs[name] = value
    return effs


def _looks_like_reaction(line: str) -> bool:
    return bool(_ARROW_RE.search(line))


def _aux_keyword(line: str) -> str | None:
    """Return the leading aux keyword (upper-case) if the line is an aux line."""
    stripped = line.strip()
    if not stripped:
        return None
    first = re.split(r"[\s/]", stripped, maxsplit=1)[0].upper()
    if first in KNOWN_AUX_KEYWORDS or first in UNSUPPORTED_AUX_KEYWORDS:
        return first
    return None


def _parse_reactions_block(
    body: list[tuple[int, str]],
) -> tuple[list[Reaction], str, str]:
    """Parse a REACTIONS section into reactions + header units."""
    ea_units = DEFAULT_EA_TOKEN
    a_basis = DEFAULT_A_CONC_BASIS

    rows = [(no, text) for no, text in body if text.strip()]
    start = 0
    if rows:
        header_tokens = rows[0][1].split()
        # Header line: only unit tokens, no reaction arrow.
        if header_tokens and not _looks_like_reaction(rows[0][1]):
            for tok in header_tokens:
                up = tok.upper()
                if up in _A_CONC_TOKENS:
                    a_basis = up
                else:
                    ea_units = up
            start = 1

    reactions: list[Reaction] = []
    current: Reaction | None = None
    # Pending Chebyshev accumulation across CHEB continuation lines.
    cheb_state: dict | None = None

    for no, text in rows[start:]:
        if _looks_like_reaction(text):
            # New reaction main line: "<equation>  A  n  Ea"
            _finalize_cheb(current, cheb_state)
            cheb_state = None
            tokens = text.split()
            if len(tokens) < 3:
                raise ChemkinParseError(
                    f"Reaction line {no} has too few tokens: {text!r}"
                )
            a, n, ea = (float(t) for t in tokens[-3:])
            equation = " ".join(tokens[:-3])
            (
                reactants,
                products,
                reversible,
                is_tb,
                is_fo,
                collider,
            ) = _parse_equation(equation)
            current = Reaction(
                reactants=reactants,
                products=products,
                reversible=reversible,
                a=a,
                n=n,
                ea=ea,
                is_third_body=is_tb,
                is_falloff=is_fo,
                falloff_collider=collider,
                line_no=no,
            )
            reactions.append(current)
            continue

        if current is None:
            raise ChemkinParseError(
                f"Auxiliary line {no} before any reaction: {text!r}"
            )

        kw = _aux_keyword(text)
        if kw in UNSUPPORTED_AUX_KEYWORDS:
            current.unsupported_aux.append(text.strip())
            continue
        if kw in ("DUP", "DUPLICATE"):
            current.duplicate = True
            continue
        if kw == "LOW":
            vals = _slash_floats(text)
            current.low = (vals[0], vals[1], vals[2])
            continue
        if kw == "HIGH":
            # Chemically-activated high-pressure limit; store like LOW for now.
            vals = _slash_floats(text)
            current.low = (vals[0], vals[1], vals[2])
            continue
        if kw == "TROE":
            current.troe = _slash_floats(text)
            continue
        if kw == "SRI":
            current.sri = _slash_floats(text)
            continue
        if kw == "REV":
            vals = _slash_floats(text)
            current.rev = (vals[0], vals[1], vals[2])
            continue
        if kw == "PLOG":
            vals = _slash_floats(text)
            current.plog.append(
                PlogPoint(pressure_atm=vals[0], a=vals[1], n=vals[2], ea=vals[3])
            )
            continue
        if kw in ("CHEB", "TCHEB", "PCHEB"):
            cheb_state = _accumulate_cheb(cheb_state, kw, text)
            continue

        # No recognised keyword and contains '/' -> collider efficiency list.
        if "/" in text:
            current.efficiencies.update(_parse_efficiency_line(text))
            continue

        raise ChemkinParseError(f"Unrecognised reaction aux line {no}: {text!r}")

    _finalize_cheb(current, cheb_state)
    return reactions, ea_units, a_basis


def _accumulate_cheb(state: dict | None, kw: str, text: str) -> dict:
    if state is None:
        state = {"tmin": None, "tmax": None, "pmin": None, "pmax": None,
                 "n_t": None, "n_p": None, "coeffs": []}
    vals = _slash_floats(text)
    if kw == "TCHEB":
        state["tmin"], state["tmax"] = vals[0], vals[1]
    elif kw == "PCHEB":
        state["pmin"], state["pmax"] = vals[0], vals[1]
    else:  # CHEB
        if state["n_t"] is None and len(vals) >= 2 and float(vals[0]).is_integer():
            state["n_t"] = int(vals[0])
            state["n_p"] = int(vals[1])
            state["coeffs"].extend(vals[2:])
        else:
            state["coeffs"].extend(vals)
    return state


def _finalize_cheb(reaction: Reaction | None, state: dict | None) -> None:
    if reaction is None or state is None or state["n_t"] is None:
        return
    n_t, n_p = state["n_t"], state["n_p"]
    flat = state["coeffs"]
    matrix = [flat[r * n_p : (r + 1) * n_p] for r in range(n_t)]
    reaction.chebyshev = ChebyshevBlock(
        n_temperature=n_t,
        n_pressure=n_p,
        tmin=state["tmin"] if state["tmin"] is not None else 300.0,
        tmax=state["tmax"] if state["tmax"] is not None else 2500.0,
        pmin_atm=state["pmin"] if state["pmin"] is not None else 0.001,
        pmax_atm=state["pmax"] if state["pmax"] is not None else 100.0,
        coefficients=matrix,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def parse_mechanism(text: str, thermo_text: str | None = None) -> Mechanism:
    """Parse a CHEMKIN mechanism string into a :class:`Mechanism` AST.

    :param text: Contents of ``chem.inp`` (ELEMENTS/SPECIES/THERMO?/REACTIONS).
    :param thermo_text: Optional separate ``therm.dat`` contents (NASA-7). Used
        when the mechanism has no inline THERMO block, or merged with it.
    """
    lines = text.splitlines()
    blocks = _split_blocks(lines)

    mech = Mechanism()
    raw_line_index: dict[int, str] = {i: raw for i, raw in enumerate(lines, start=1)}

    for name, header_no, body in blocks:
        if name == "ELEMENTS":
            mech.elements = _parse_elements(body)
        elif name == "SPECIES":
            species_raw = [(no, raw_line_index[no]) for no, _t in body if no in raw_line_index]
            mech.species = _parse_species(species_raw)
        elif name == "THERMO":
            mech.thermo.update(_parse_thermo_block(body))
        elif name == "REACTIONS":
            reactions, ea_units, a_basis = _parse_reactions_block(body)
            mech.reactions = reactions
            mech.ea_units = ea_units
            mech.a_conc_basis = a_basis

    if thermo_text is not None:
        mech.thermo.update(parse_thermo_file(thermo_text))

    return mech


def parse_thermo_file(text: str) -> dict[str, ThermoEntry]:
    """Parse a standalone ``therm.dat`` NASA-7 file into name -> ThermoEntry."""
    lines = text.splitlines()
    # A therm.dat may or may not have a THERMO header; normalise by wrapping.
    blocks = _split_blocks(lines)
    for name, _no, body in blocks:
        if name == "THERMO":
            return _parse_thermo_block(body)
    # No THERMO header: treat all non-empty lines as the block body.
    body = [
        (i, _strip_comment(raw)[0].rstrip())
        for i, raw in enumerate(lines, start=1)
        if _strip_comment(raw)[0].strip()
        and _strip_comment(raw)[0].strip().upper() not in {"END"}
    ]
    return _parse_thermo_block(body)
