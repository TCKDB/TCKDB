"""Parse Molpro log files to extract execution parameters and SP energy.

Pure-function parser: takes text, returns structured dicts compatible with
the :class:`CalculationParameter` model.  **No DB dependency** — mirrors
:mod:`app.services.orca_parameter_parser` and
:mod:`app.services.gaussian_parameter_parser`.

Molpro echoes its input deck near the top of the ``.out`` log (between the
``Variables initialized`` and ``Commands initialized`` banners), so the deck
is recovered from that echo.  Two method families are supported, matching the
real fixtures in ``tests/fixtures/molpro/``:

* **CCSD(T)-F12 / cc-pVTZ-F12** (closed-shell ``ccsd(t)-f12`` and open-shell
  ``uccsd(t)-f12``).  SP energy follows ARC's F12a/F12b-by-basis convention.
* **Plain MRCI / cc-pVTZ** (a ``{rhf}`` → ``{casscf}`` → ``{mrci}`` chain).
  SP energy is the Davidson relaxed-reference cluster-corrected energy.

``MRCI-F12`` (``basis=aug-cc-pvtz-f12`` + ``{mrci-f12;}``) is a deliberately
**unsupported** variant here: it is detected and its SP energy is reported as
``None`` rather than mis-reported from a plain-MRCI or F12a/F12b line.

Parse only what appears in these real jobs — the canonical vocabulary grows
from observed outputs, never from the Molpro manual.
"""

from __future__ import annotations

import re
from pathlib import Path

PARSER_VERSION = "molpro_v1"


# ---------------------------------------------------------------------------
# Minimal element → atomic-number table (for MRCI positional-wf charge
# derivation).  Kept local so the parser stays free of chemistry-package
# and DB imports, matching the ORCA parser's self-contained style.
# ---------------------------------------------------------------------------

_SYMBOL_TO_Z: dict[str, int] = {
    "H": 1, "He": 2, "Li": 3, "Be": 4, "B": 5, "C": 6, "N": 7, "O": 8,
    "F": 9, "Ne": 10, "Na": 11, "Mg": 12, "Al": 13, "Si": 14, "P": 15,
    "S": 16, "Cl": 17, "Ar": 18, "K": 19, "Ca": 20, "Sc": 21, "Ti": 22,
    "V": 23, "Cr": 24, "Mn": 25, "Fe": 26, "Co": 27, "Ni": 28, "Cu": 29,
    "Zn": 30, "Ga": 31, "Ge": 32, "As": 33, "Se": 34, "Br": 35, "Kr": 36,
    "I": 53,
}


# ---------------------------------------------------------------------------
# Input-deck echo extraction
# ---------------------------------------------------------------------------

_ECHO_START = re.compile(r"Variables initialized", re.IGNORECASE)
_ECHO_END = re.compile(r"Commands initialized", re.IGNORECASE)


def _extract_deck_lines(text: str) -> list[str]:
    """Return the echoed Molpro input-deck lines.

    Molpro echoes the deck between ``Variables initialized`` and
    ``Commands initialized``.  When those markers are absent (e.g. a raw
    ``.in`` uploaded directly) the whole text is treated as the deck.
    """
    lines = text.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if start is None and _ECHO_START.search(line):
            start = i + 1
            continue
        if start is not None and _ECHO_END.search(line):
            end = i
            break
    if start is not None and end is not None and end > start:
        return [ln.strip() for ln in lines[start:end]]
    # Fallback: no echo markers — treat the full text as the deck.
    return [ln.strip() for ln in lines]


# ---------------------------------------------------------------------------
# Value-type guessing
# ---------------------------------------------------------------------------


def _guess_value_type(val: str) -> str:
    """Heuristic type hint for a raw value string."""
    if val.lower() in ("true", "false", "on", "off", "yes", "no"):
        return "bool"
    try:
        int(val)
        return "int"
    except ValueError:
        pass
    try:
        float(val)
        return "float"
    except ValueError:
        pass
    return "string"


# ---------------------------------------------------------------------------
# Method family detection
# ---------------------------------------------------------------------------

_CCSD_LINE = re.compile(r"\bu?ccsd\(t\)-f12\b", re.IGNORECASE)
_MRCI_F12_LINE = re.compile(r"\bmrci-f12\b", re.IGNORECASE)
_MRCI_LINE = re.compile(r"\{?\s*mrci\b", re.IGNORECASE)


def _non_comment_deck_lines(deck_lines: list[str]) -> list[str]:
    """Drop Molpro comment/title lines from a deck.

    Molpro comment and title lines start with ``*`` (the deck title is
    ``***,<name>``).  These must never feed method/basis classification —
    a title like ``***,CH4 vs mrci benchmark`` would otherwise poison the
    family detection and silently drop a genuine CCSD(T)-F12 energy.
    """
    return [ln for ln in deck_lines if not ln.lstrip().startswith("*")]


def _detect_method_family(deck_lines: list[str]) -> str:
    """Classify the deck's top correlation method.

    Returns one of ``"mrci_f12"``, ``"mrci"``, ``"ccsd_f12"`` or
    ``"unknown"``.  ``mrci-f12`` is checked first so it is never confused
    with plain ``mrci``.  Comment/title lines are excluded so a title
    string can never poison the classification.
    """
    joined = "\n".join(_non_comment_deck_lines(deck_lines))
    if _MRCI_F12_LINE.search(joined):
        return "mrci_f12"
    if _MRCI_LINE.search(joined):
        return "mrci"
    if _CCSD_LINE.search(joined):
        return "ccsd_f12"
    return "unknown"


# ---------------------------------------------------------------------------
# Method / basis extraction
# ---------------------------------------------------------------------------

_BASIS_RE = re.compile(r"^basis\s*=\s*(\S+?);?\s*$", re.IGNORECASE)
_REFERENCE_METHODS = ("rhf", "hf", "casscf", "multi")


def parse_method_basis(text: str) -> dict | None:
    """Extract method, basis and reference chain from the deck echo.

    Returns a dict shaped like the ORCA parser's
    (``method``/``basis``/``aux_basis``/``cabs_basis``) plus a
    ``reference_methods`` list for multireference (MRCI) chains.
    """
    deck_lines = _extract_deck_lines(text)

    basis: str | None = None
    for line in deck_lines:
        m = _BASIS_RE.match(line)
        if m:
            basis = m.group(1)
            break

    family = _detect_method_family(deck_lines)
    method: str | None = None
    references: list[str] = []

    for line in deck_lines:
        low = line.lower().lstrip("{").strip()
        token = low.rstrip(";").strip()
        # Correlation method (primary)
        cm = _CCSD_LINE.search(line)
        if cm:
            method = cm.group(0).lower()
            continue
        if family in ("mrci", "mrci_f12") and token in ("mrci", "mrci-f12"):
            method = token
            continue
        # Reference steps in a multireference chain
        if family in ("mrci", "mrci_f12") and token in _REFERENCE_METHODS:
            ref = token
            if ref not in references:
                references.append(ref)

    if method is None and family in ("mrci", "mrci_f12"):
        method = "mrci-f12" if family == "mrci_f12" else "mrci"

    if method is None and basis is None:
        return None

    return {
        "method": method,
        "basis": basis,
        "aux_basis": None,
        "cabs_basis": None,
        "reference_methods": references or None,
    }


# ---------------------------------------------------------------------------
# Charge / multiplicity  (handles both wf syntaxes)
# ---------------------------------------------------------------------------

# Keyword form (CCSD(T)-F12 decks):  wf,spin=0,charge=0
_WF_KEYWORD_RE = re.compile(r"\bwf\b[^\n]*", re.IGNORECASE)
_WF_SPIN_KW = re.compile(r"spin\s*=\s*(-?\d+)", re.IGNORECASE)
_WF_CHARGE_KW = re.compile(r"charge\s*=\s*(-?\d+)", re.IGNORECASE)
# Positional form (MRCI decks):  wf,18,1,0   (nelec, sym, spin)
_WF_POSITIONAL_RE = re.compile(
    r"\bwf\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(-?\d+)", re.IGNORECASE
)

_GEOM_START = re.compile(r"geometry\s*=\s*\{", re.IGNORECASE)


def _sum_atomic_numbers(deck_lines: list[str]) -> int | None:
    """Sum atomic numbers of the atoms in the echoed geometry block."""
    total = 0
    found = False
    in_geom = False
    for line in deck_lines:
        if not in_geom:
            if _GEOM_START.search(line):
                in_geom = True
            continue
        atom_part = line.split("}")[0] if "}" in line else line
        parts = atom_part.split()
        if parts:
            sym = parts[0].strip().capitalize()
            z = _SYMBOL_TO_Z.get(sym)
            if z is not None and len(parts) >= 4:
                total += z
                found = True
        if "}" in line:
            break
    return total if found else None


def parse_charge_multiplicity(text: str) -> dict | None:
    """Extract charge and multiplicity from the ``wf`` directive.

    Supports both Molpro ``wf`` syntaxes:

    * **Keyword** (CCSD(T)-F12 decks): ``wf,spin=<2S>,charge=<q>`` — charge
      is read directly.
    * **Positional** (MRCI decks): ``wf,<nelec>,<sym>,<2S>`` — charge is
      derived as ``sum(atomic_numbers) - nelec`` from the echoed geometry.

    In both cases Molpro ``spin`` is ``2S`` (unpaired electrons), so
    ``multiplicity = spin + 1``.  Returns ``None`` if no ``wf`` line found.
    """
    deck_lines = _extract_deck_lines(text)

    for line in deck_lines:
        if not _WF_KEYWORD_RE.search(line):
            continue

        spin_m = _WF_SPIN_KW.search(line)
        charge_m = _WF_CHARGE_KW.search(line)
        if spin_m is not None:
            # Keyword form
            spin = int(spin_m.group(1))
            charge = int(charge_m.group(1)) if charge_m else 0
            return {"charge": charge, "multiplicity": spin + 1}

        pos_m = _WF_POSITIONAL_RE.search(line)
        if pos_m is not None:
            # Positional form: nelec, sym, spin
            nelec = int(pos_m.group(1))
            spin = int(pos_m.group(3))
            z_sum = _sum_atomic_numbers(deck_lines)
            charge = (z_sum - nelec) if z_sum is not None else None
            return {"charge": charge, "multiplicity": spin + 1}

    return None


# ---------------------------------------------------------------------------
# Software version
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"Version\s+(\d+\.\d+(?:\.\d+)?)\s+linked", re.IGNORECASE)


def parse_software_version(text: str) -> dict | None:
    """Extract the Molpro version, e.g. ``Version 2026.1 linked ...``."""
    m = _VERSION_RE.search(text)
    if m:
        return {
            "name": "molpro",
            "version": m.group(1),
            "build": None,
            "release_date_raw": None,
        }
    return None


# ---------------------------------------------------------------------------
# Execution-parameter extraction (deck)
# ---------------------------------------------------------------------------

# memory,Total=5250,m;   or   memory,752,m;
_MEMORY_RE = re.compile(
    r"^memory\s*,\s*(?:total\s*=\s*)?(\d+)\s*,\s*([a-zA-Z]+)", re.IGNORECASE
)
# maxit,999;   (SCF max iterations)
_MAXIT_RE = re.compile(r"^maxit\s*,\s*(\d+)", re.IGNORECASE)

#: Molpro memory unit letter → recorded unit tag.  ``m`` is mega-*words*
#: (8 bytes/word), NOT megabytes — so it maps to ``memory.raw`` (a
#: unit-tagged string), never the MB-specific ``memory.maxcore_mb``.
_MEMORY_UNIT = {"m": "MW", "k": "kW", "g": "GW"}

# F12 explicit-correlation ansatz (output body), e.g.
#   Using MP2-F12 with ansatz 3C(FIX)
#   F12a corrections for ansatz F12/3C(FIX) added to CCSD energy
_ANSATZ_RE = re.compile(
    r"ansatz\s+(?:F12/)?(\d\*?[A-Z]\([A-Z,]+\))", re.IGNORECASE
)


def _parse_deck_parameters(deck_lines: list[str], family: str) -> list[dict]:
    """Extract execution parameters from the echoed deck."""
    params: list[dict] = []
    for line in deck_lines:
        mem = _MEMORY_RE.match(line)
        if mem:
            unit = _MEMORY_UNIT.get(mem.group(2).lower(), mem.group(2))
            params.append({
                "raw_key": "memory",
                "canonical_key": "memory.raw",
                "raw_value": mem.group(1),
                "canonical_value": mem.group(1),
                "section": "resource",
                "value_type": "int",
                "unit": unit,
            })
            continue
        maxit = _MAXIT_RE.match(line)
        if maxit:
            params.append({
                "raw_key": "maxit",
                "canonical_key": "scf.max_cycles",
                "raw_value": maxit.group(1),
                "canonical_value": maxit.group(1),
                "section": "scf",
                "value_type": "int",
            })
            continue
    return params


def _parse_f12_ansatz(text: str) -> list[dict]:
    """Extract the F12 explicit-correlation ansatz from the output body.

    Emitted as a single ``f12.ansatz`` parameter (e.g. ``3C(FIX)``).  This
    is a genuinely new, observed canonical key seeded alongside the parser.
    """
    m = _ANSATZ_RE.search(text)
    if not m:
        return []
    ansatz = m.group(1).upper()
    return [{
        "raw_key": "ansatz",
        "canonical_key": "f12.ansatz",
        "raw_value": ansatz,
        "canonical_value": ansatz,
        "section": "f12",
        "value_type": "string",
    }]


# ---------------------------------------------------------------------------
# Single-point electronic energy  (ARC-convention, Hartree)
# ---------------------------------------------------------------------------


def _detect_f12_ansatz_choice(text: str) -> str:
    """Return ``"a"`` or ``"b"`` for the F12a/F12b energy selection.

    Mirrors ARC: ``vtz``/``vdz`` basis → F12a; ``vqz``/``v5z``/… → F12b.
    Defaults to F12a when the basis is absent/ambiguous (the cc-pVTZ-F12
    case).

    The scan is scoped to the deck's ``basis=`` directive only — never the
    whole log — so a stray ``vqz`` token elsewhere in the output body (a
    ``gprint,basis`` library echo, an auxiliary-basis line, etc.) cannot
    silently flip a genuine cc-pVTZ-F12 job to F12b and return the wrong
    energy.  This matches ARC's line-scoped ``basis=`` handling.
    """
    basis = ""
    for line in _extract_deck_lines(text):
        m = _BASIS_RE.match(line)
        if m:
            basis = m.group(1).lower()
            break
    if any(hb in basis for hb in ("vqz", "v5z", "v6z", "v7z", "v8z")):
        return "b"
    return "a"


def _parse_ccsd_f12_energy(text: str, want: str) -> float | None:
    """Extract the CCSD(T)-F12a/F12b total energy (Hartree).

    Handles both real fixture formats:

    * **Closed-shell**: ``!CCSD(T)-F12a total energy   <E>`` /
      ``!CCSD(T)-F12b total energy   <E>`` — the label itself carries the
      ansatz tag.
    * **Open-shell**: ``!RHF-UCCSD(T)-F12 energy   <E>`` appears twice with
      no ``a``/``b`` in the label; the F12a block precedes the F12b block,
      so the surrounding ``...-F12a``/``...-F12b`` markers disambiguate.

    A single forward scan tracks the "current" ansatz from any ``F12a`` /
    ``F12b`` marker and assigns each ``!...CCSD(T)... energy`` line to it.
    Returns the last value seen for the requested ansatz.
    """
    current: str | None = None
    candidate: dict[str, float] = {}
    for raw in text.splitlines():
        low = raw.lower()
        # F12b is checked first: the block-transition line
        # "F12b corrections ... added to CCSD(T)-F12a energy" contains both
        # tags but means we are entering the F12b block.
        if "f12b" in low:
            current = "b"
        elif "f12a" in low:
            current = "a"
        stripped = raw.strip()
        if (
            stripped.startswith("!")
            and "ccsd(t)" in low
            and "energy" in low
            and current is not None
        ):
            try:
                candidate[current] = float(stripped.split()[-1])
            except (ValueError, IndexError):
                continue
    return candidate.get(want)


_DAVIDSON_RE = re.compile(r"\(Davidson,\s*relaxed reference\)", re.IGNORECASE)


def _parse_mrci_energy(text: str) -> float | None:
    """Extract the MRCI Davidson relaxed-reference energy (Hartree).

    Mirrors ARC exactly: the line
    ``Cluster corrected energies   <E> (Davidson, relaxed reference)`` —
    take ``float(line.split()[3])``.  Searches from the end.
    """
    for raw in reversed(text.splitlines()):
        if _DAVIDSON_RE.search(raw):
            parts = raw.split()
            if len(parts) >= 4:
                try:
                    return float(parts[3])
                except ValueError:
                    continue
    return None


def parse_sp_energy(text: str) -> float | None:
    """Parse the single-point electronic energy in Hartree.

    Precedence (matching ARC): MRCI (Davidson relaxed reference) wins over
    CCSD(T)-F12a/F12b.  ``MRCI-F12`` is an unsupported variant — it returns
    ``None`` rather than mis-reporting a wrong number.  Molpro's native unit
    is Hartree, so no conversion is applied (unlike ARC's kJ/mol output).
    """
    family = _detect_method_family(_extract_deck_lines(text))

    if family == "mrci_f12":
        # Unsupported: different energy format, sometimes incomplete runs.
        return None
    if family == "mrci":
        return _parse_mrci_energy(text)
    if family == "ccsd_f12":
        return _parse_ccsd_f12_energy(text, _detect_f12_ansatz_choice(text))
    # Unknown family — try MRCI then CCSD as a best effort, else None.
    return _parse_mrci_energy(text) or _parse_ccsd_f12_energy(
        text, _detect_f12_ansatz_choice(text)
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_molpro_log(
    text: str | None = None,
    path: str | Path | None = None,
) -> dict:
    """Parse a Molpro log and return structured parameter data.

    :param text: Raw log text (provide one of ``text`` or ``path``).
    :param path: Path to a log file.
    :returns: Dict with the same shape as the ORCA/Gaussian parsers:
        ``parameters``, ``parameters_json``, ``software``,
        ``charge_multiplicity``, ``method_basis``, ``parser_version``,
        plus Molpro's ``sp_electronic_energy_hartree`` and
        ``method_family``.
    """
    if text is None and path is not None:
        text = Path(path).read_text()
    if text is None:
        raise ValueError("Provide text or path")

    deck_lines = _extract_deck_lines(text)
    family = _detect_method_family(deck_lines)

    params = _parse_deck_parameters(deck_lines, family)
    if family == "ccsd_f12":
        params += _parse_f12_ansatz(text)

    parameters_json: dict = {
        "deck_lines": deck_lines,
        "method_family": family,
        "parameters": params,
    }

    return {
        "parameters": params,
        "parameters_json": parameters_json,
        "software": parse_software_version(text),
        "charge_multiplicity": parse_charge_multiplicity(text),
        "method_basis": parse_method_basis(text),
        "method_family": family,
        "sp_electronic_energy_hartree": parse_sp_energy(text),
        "parser_version": PARSER_VERSION,
    }


def parse_hessian(text: str) -> tuple[int, list[float]] | None:
    """Cartesian Hessian (hartree/bohr²) from a Molpro output log.

    Mirrors Arkane's ``MolproLog.load_force_constant_matrix`` for the
    ``Force Constants (Second Derivatives of the Energy) in [a.u.]`` block
    (emitted when the deck contains ``print,hessian``). Molpro already prints
    in atomic units, so the packed lower triangle is stored verbatim. Returns
    ``(natoms, packed_lower_triangle)`` or ``None`` when the block is absent.

    NOTE (deferred): unlike ORCA's ``.hess``, a Molpro log offers no clean
    in-frame geometry to cross-check the matrix's orientation against the
    bound geometry (Molpro reorients to its own frame by default and the
    force-constant block carries atom-axis labels, not coordinates). The
    orientation cross-check the hook applies to ORCA is therefore not
    available here; binding relies on the exactly-one-input-geometry and
    atom-count guards. Revisit if a reliable in-frame coordinate block is
    identified in the Molpro output.
    """
    from app.services.hessian_parsing import (
        MOLPRO_HESSIAN_MARKER,
        parse_triangular_force_constants,
    )

    return parse_triangular_force_constants(text, marker=MOLPRO_HESSIAN_MARKER)
