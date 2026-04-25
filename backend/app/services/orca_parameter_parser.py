"""Parse ORCA log files to extract execution parameters.

Pure-function parser: takes text, returns structured dicts compatible with
the CalculationParameter model.  No DB dependency.

ORCA input structure (echoed in log file):
  - ``! keyword1 keyword2 ...`` — keyword line(s)
  - ``%section ... end`` — block settings
  - ``%maxcore N`` — single-line block
  - ``* xyz charge mult`` — coordinate header
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Canonical key mapping — ORCA-specific entries
# ---------------------------------------------------------------------------

#: Maps (section, raw_key) → (canonical_key, canonical_value)
#: Uses the same canonical vocabulary as the Gaussian parser where
#: semantics overlap (e.g. scf_convergence, nproc).
_CANONICAL_MAP: dict[tuple[str, str], tuple[str, str | None]] = {
    # SCF convergence keywords (from ! line, section="scf")
    ("scf", "tightscf"): ("scf_convergence", "tight"),
    ("scf", "verytightscf"): ("scf_convergence", "very_tight"),
    ("scf", "loosescf"): ("scf_convergence", "loose"),
    ("scf", "normalscf"): ("scf_convergence", "normal"),
    ("scf", "scfconv"): ("scf_convergence", None),
    ("scf", "maxiter"): ("scf_max_cycles", None),
    # Optimization convergence (from ! line, section="opt")
    ("opt", "tightopt"): ("opt_convergence", "tight"),
    ("opt", "verytightopt"): ("opt_convergence", "very_tight"),
    ("opt", "looseopt"): ("opt_convergence", "loose"),
    ("opt", "normalopt"): ("opt_convergence", "normal"),
    # PNO truncation (from ! line, section="pno")
    ("pno", "tightpno"): ("pno_truncation", "tight"),
    ("pno", "normalpno"): ("pno_truncation", "normal"),
    ("pno", "loosepno"): ("pno_truncation", "loose"),
    # Grid keywords (from ! line, section="grid")
    ("grid", "defgrid1"): ("grid_quality", "defgrid1"),
    ("grid", "defgrid2"): ("grid_quality", "defgrid2"),
    ("grid", "defgrid3"): ("grid_quality", "defgrid3"),
    ("grid", "grid4"): ("grid_quality", "grid4"),
    ("grid", "grid5"): ("grid_quality", "grid5"),
    ("grid", "grid6"): ("grid_quality", "grid6"),
    ("grid", "grid7"): ("grid_quality", "grid7"),
    # Resource (from %maxcore, %pal)
    ("resource", "maxcore"): ("maxcore_mb", None),
    ("resource", "nprocs"): ("nproc", None),
}


def _lookup_canonical(
    section: str, raw_key: str
) -> tuple[str | None, str | None]:
    """Return (canonical_key, canonical_value) for a given section+raw_key."""
    key = (section.lower(), raw_key.lower())
    if key in _CANONICAL_MAP:
        return _CANONICAL_MAP[key]
    return None, None


# ---------------------------------------------------------------------------
# Classification sets — what belongs in LoT vs parameters
# ---------------------------------------------------------------------------

#: Job types — these define Calculation.type, not parameters.
_JOB_TYPES = frozenset({
    "sp", "opt", "optts", "copt", "zopt", "freq", "numfreq", "neb", "neb-ts",
    "neb-ci", "irc", "md", "goat",
})

#: Known method keywords — belong in level_of_theory, not parameters.
_METHOD_PREFIXES = (
    "hf", "uhf", "rhf", "rohf",
    "dft", "b3lyp", "pbe", "pbe0", "bp86", "tpss", "m06",
    "mp2", "ri-mp2", "dlpno-mp2",
    "ccsd", "ccsd(t)", "dlpno-ccsd", "dlpno-ccsd(t)", "dlpno-ccsd(t1)",
    "casscf", "nevpt2", "dlpno-nevpt2",
    "wb97x", "wb97x-d3", "wb97x-d3bj", "cam-b3lyp",
    "r2scan", "r2scan-3c",
)

#: Dispersion correction keywords — belong in level_of_theory.dispersion.
_DISPERSION_KEYWORDS = frozenset({
    "d3", "d3bj", "d3zero", "d4", "vdwfn",
    "nod3", "nod3bj", "nod4", "novdwfn",
})

#: Known basis set patterns — belong in level_of_theory.
_BASIS_PATTERNS = re.compile(
    r"^("
    r"def2-[a-z]+p?|"              # def2-SVP, def2-TZVP, def2-TZVPP, etc.
    r"cc-pv[dtq56]z(-f12)?|"       # cc-pVDZ, cc-pVTZ-F12, etc.
    r"aug-cc-pv[dtq56]z(/c)?|"     # aug-cc-pVTZ, aug-cc-pVTZ/C
    r"cc-pv[dtq56]z-f12-cabs|"     # CABS basis sets
    r"6-31[g+\*]+|"                # Pople basis sets
    r"sto-3g|"
    r"ma-def2-[a-z]+p?"            # minimally augmented
    r")$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Section classifiers for ! line keywords
# ---------------------------------------------------------------------------

_SCF_KEYWORDS = frozenset({
    "tightscf", "verytightscf", "loosescf", "normalscf",
    "scfconv", "nori", "rijcosx", "rijonx", "rijk",
    "noiter", "conv", "unconventionalscf",
})

_PNO_KEYWORDS = frozenset({
    "tightpno", "normalpno", "loosepno",
})

_OPT_KEYWORDS = frozenset({
    "tightopt", "verytightopt", "looseopt", "normalopt",
})

_GRID_KEYWORDS = frozenset({
    "defgrid1", "defgrid2", "defgrid3",
    "grid4", "grid5", "grid6", "grid7",
    "nofinalgrid", "nofinalgridx",
})


def _classify_keyword(kw: str) -> str | None:
    """Classify an ORCA ! keyword into a section, or None to skip.

    Returns None for LoT keywords and job types (they don't become
    parameter rows).
    """
    kw_lower = kw.lower()

    # Job types → skip
    if kw_lower in _JOB_TYPES:
        return None

    # Method → skip (LoT territory)
    if kw_lower in _METHOD_PREFIXES:
        return None

    # Basis → skip
    if _BASIS_PATTERNS.match(kw_lower):
        return None

    # Dispersion → skip (LoT territory)
    if kw_lower in _DISPERSION_KEYWORDS:
        return None

    # SCF keywords
    if kw_lower in _SCF_KEYWORDS:
        return "scf"

    # Optimization keywords
    if kw_lower in _OPT_KEYWORDS:
        return "opt"

    # PNO keywords
    if kw_lower in _PNO_KEYWORDS:
        return "pno"

    # Grid keywords
    if kw_lower in _GRID_KEYWORDS:
        return "grid"

    # Everything else: store as general parameter
    return "general"


# ---------------------------------------------------------------------------
# Input block extraction
# ---------------------------------------------------------------------------

_INPUT_DELIM = re.compile(r"^={60,}$")
_INPUT_LINE = re.compile(r"^\|\s*\d+>\s*(.*)$")


def _extract_input_block(text: str) -> list[str]:
    """Extract the echoed ORCA input from the log file.

    ORCA echoes the input between ``INPUT FILE`` and ``****END OF INPUT****``
    markers.  Each line has the format: ``|  N> content``.
    """
    lines = text.splitlines()
    in_input = False
    input_lines: list[str] = []

    for i, line in enumerate(lines):
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


# ---------------------------------------------------------------------------
# Keyword line parsing
# ---------------------------------------------------------------------------


def _parse_keyword_lines(input_lines: list[str]) -> list[dict]:
    """Parse all ``! keyword ...`` lines from the ORCA input.

    Returns parameter dicts for keywords that are not LoT or job types.
    """
    params: list[dict] = []

    for line in input_lines:
        stripped = line.strip()
        if not stripped.startswith("!"):
            continue

        # Strip the ! and optional leading space, then remove inline comments
        keywords_str = stripped.lstrip("!").strip()
        if "#" in keywords_str:
            keywords_str = keywords_str[:keywords_str.index("#")].strip()
        if not keywords_str:
            continue

        for kw in keywords_str.split():
            section = _classify_keyword(kw)
            if section is None:
                # LoT or job type — skip
                continue

            ck, cv = _lookup_canonical(section, kw)
            params.append({
                "raw_key": kw,
                "canonical_key": ck,
                "raw_value": "true",
                "canonical_value": cv,
                "section": section,
                "value_type": "bool",
            })

    return params


# ---------------------------------------------------------------------------
# Block section parsing
# ---------------------------------------------------------------------------

_BLOCK_START = re.compile(r"^%(\w+)\s*(.*?)$", re.IGNORECASE)


def _parse_block_sections(input_lines: list[str]) -> list[dict]:
    """Parse ``%section ... end`` blocks from the ORCA input.

    Handles:
    - Single-line blocks: ``%maxcore 4096``
    - Multi-line blocks: ``%pal\\n  nprocs 8\\nend``
    - Comments: ``# ...`` stripped
    """
    params: list[dict] = []
    i = 0

    while i < len(input_lines):
        line = input_lines[i].strip()

        # Strip inline comments
        if "#" in line:
            line = line[:line.index("#")].strip()

        m = _BLOCK_START.match(line)
        if not m:
            i += 1
            continue

        block_name = m.group(1).lower()
        rest = m.group(2).strip()

        # Single-line block: %maxcore 4096  or  %CPCM EPSILON 6.02 REFRAC 1.3723 END
        if rest and rest.lower() != "end":
            # Strip trailing END if present (e.g. %CPCM ... END)
            if rest.lower().endswith(" end"):
                rest = rest[:-4].strip()
            # Use multi-pair tokenizer for single-line blocks
            params.extend(_parse_block_content(block_name, [rest], single_line=True))
            i += 1
            continue

        # Multi-line block: collect until matching "end".
        # ORCA blocks like %GEOM can contain nested sub-sections
        # (Constraints...END, SCAN...END) that have their own END
        # markers.  Track nesting depth to find the true block end.
        block_lines: list[str] = []
        i += 1
        depth = 0
        # Sub-section keywords that open a nested END-terminated block
        _NESTED_OPENERS = frozenset({"constraints", "scan"})
        while i < len(input_lines):
            bline = input_lines[i].strip()
            # Strip inline comments
            if "#" in bline:
                bline = bline[:bline.index("#")].strip()

            bline_lower = bline.lower()
            if bline_lower in _NESTED_OPENERS:
                depth += 1
                i += 1
                continue
            if bline_lower == "end":
                if depth > 0:
                    depth -= 1
                    i += 1
                    continue
                # This END closes the block itself
                i += 1
                break
            # Skip constraint content lines like {C 0 C}
            if bline.startswith("{") and bline.endswith("}"):
                i += 1
                continue
            if bline:
                block_lines.append(bline)
            i += 1

        params.extend(_parse_block_content(block_name, block_lines))

    return params


def _parse_block_content(
    block_name: str, lines: list[str], *, single_line: bool = False
) -> list[dict]:
    """Parse key-value pairs from within an ORCA block section.

    :param block_name: Lowercase block name (e.g. ``"scf"``, ``"cpcm"``).
    :param lines: Content lines (stripped of the block header/END).
    :param single_line: If True, use multi-pair tokenizer for lines like
        ``EPSILON 6.02 REFRAC 1.3723``.  If False (default), each line
        is one ``KEY VALUE`` pair, allowing identifier values like
        ``Interpolation IDPP``.
    """
    params: list[dict] = []

    section_map = {
        "maxcore": "resource",
        "pal": "resource",
        "scf": "scf",
        "mdci": "correlation",
        "method": "general",
        "basis": "general",
        "geom": "opt",
        "rel": "relativity",
    }
    section = section_map.get(block_name, block_name)

    for line in lines:
        # Skip SCAN/Constraints directives in %GEOM — they define scan
        # coordinates, not execution parameters.
        if block_name == "geom" and line.strip().upper().startswith(("SCAN", "CONSTRAINTS")):
            continue

        # Special case: %maxcore has the value directly (no key)
        if block_name == "maxcore":
            raw_value = line.strip()
            ck, cv = _lookup_canonical(section, "maxcore")
            params.append({
                "raw_key": "maxcore",
                "canonical_key": ck,
                "raw_value": raw_value,
                "canonical_value": cv,
                "section": section,
                "value_type": _guess_value_type(raw_value),
            })
            continue

        if single_line:
            # Multi-pair tokenizer for single-line blocks:
            # EPSILON 6.02 REFRAC 1.3723
            tokens = line.split()
            i = 0
            while i < len(tokens):
                raw_key = tokens[i]
                if i + 1 < len(tokens) and _looks_like_value(tokens[i + 1]):
                    raw_value = tokens[i + 1]
                    i += 2
                else:
                    raw_value = "true"
                    i += 1

                ck, cv = _lookup_canonical(section, raw_key)
                params.append({
                    "raw_key": raw_key,
                    "canonical_key": ck,
                    "raw_value": raw_value,
                    "canonical_value": cv,
                    "section": section,
                    "value_type": _guess_value_type(raw_value),
                })
        else:
            # One pair per line: KEY VALUE (value can be any identifier)
            parts = line.split(None, 1)
            if not parts:
                continue
            raw_key = parts[0]
            raw_value = parts[1].strip() if len(parts) > 1 else "true"

            ck, cv = _lookup_canonical(section, raw_key)
            params.append({
                "raw_key": raw_key,
                "canonical_key": ck,
                "raw_value": raw_value,
                "canonical_value": cv,
                "section": section,
                "value_type": _guess_value_type(raw_value),
            })

    return params


def _looks_like_value(token: str) -> bool:
    """Check if a token looks like a value rather than a keyword.

    Used by multi-pair line tokenizer (e.g. ``EPSILON 6.02 REFRAC 1.3723``).
    """
    if not token:
        return False
    first = token[0]
    # Numeric values
    if first in "0123456789.-+":
        return True
    # Quoted strings
    if first in "'\"":
        return True
    # Boolean-like
    if token.lower() in ("true", "false", "yes", "no", "on", "off"):
        return True
    return False


# ---------------------------------------------------------------------------
# Scan coordinate extraction from %GEOM blocks
# ---------------------------------------------------------------------------

#: Maps ORCA coordinate type letters to ScanCoordinateKind values.
_ORCA_COORD_TYPE = {"B": "bond", "A": "angle", "D": "dihedral"}

_SCAN_RE = re.compile(
    r"SCAN\s+([BAD])\s+"  # type + atoms
    r"([\d\s]+)"  # atom indices
    r"=\s*"
    r"([\d.,\s]+)"  # start, end, nsteps
    r"END",
    re.IGNORECASE,
)


def _parse_scan_definitions(input_lines: list[str]) -> list[dict]:
    """Extract scan coordinate definitions from ``%GEOM`` blocks.

    ORCA format: ``SCAN B 11 16 = 3.245, 0.745, 126 END``

    Returns dicts compatible with the CalculationScanCoordinate schema:
    coordinate_kind, atom indices (1-indexed), step_count, step_size,
    start_value, end_value.

    .. important:: Atom index convention boundary

       ORCA uses **0-based** atom indices in its input and output.
       TCKDB stores **1-based** indices (matching Gaussian convention
       and natural chemical numbering).  The ``+1`` conversion happens
       here at the parser boundary — all downstream code sees 1-based
       indices only.  Gaussian's ModRedundant lines are already 1-based,
       so no conversion is needed there.
    """
    # Reconstruct the full input to handle multi-line SCAN directives
    full_text = "\n".join(input_lines)
    scan_defs: list[dict] = []

    for m in _SCAN_RE.finditer(full_text):
        coord_type = m.group(1).upper()
        atom_str = m.group(2).strip()
        values_str = m.group(3).strip()

        kind = _ORCA_COORD_TYPE.get(coord_type)
        if kind is None:
            continue

        # ORCA 0-based → TCKDB 1-based (see docstring above)
        atoms = [int(a) + 1 for a in atom_str.split()]
        expected_arity = {"bond": 2, "angle": 3, "dihedral": 4}[kind]
        if len(atoms) != expected_arity:
            continue

        # Parse start, end, nsteps
        values = [v.strip() for v in values_str.split(",") if v.strip()]
        if len(values) != 3:
            continue

        start_val = float(values[0])
        end_val = float(values[1])
        n_steps = int(values[2])

        step_size = (end_val - start_val) / n_steps if n_steps > 0 else None

        scan_def: dict = {
            "coordinate_kind": kind,
            "atom1_index": atoms[0],
            "atom2_index": atoms[1],
            "atom3_index": atoms[2] if len(atoms) > 2 else None,
            "atom4_index": atoms[3] if len(atoms) > 3 else None,
            "step_count": n_steps,
            "step_size": round(step_size, 6) if step_size is not None else None,
            "start_value": start_val,
            "end_value": end_val,
        }
        scan_defs.append(scan_def)

    return scan_defs


# ---------------------------------------------------------------------------
# Scan result extraction (fast path from summary block)
# ---------------------------------------------------------------------------


def parse_scan_results(text: str) -> list[dict] | None:
    """Extract scan point data from the RELAXED SURFACE SCAN RESULTS block.

    Parses the ``'Actual Energy'`` table — coordinate/energy pairs in order.
    Returns a list of point dicts ready for ``calc_scan_point`` +
    ``calc_scan_point_coordinate_value`` population.

    Returns ``None`` if no scan results block is found.
    """
    marker = "The Calculated Surface using the 'Actual Energy'"
    idx = text.find(marker)
    if idx == -1:
        return None

    lines = text[idx:].splitlines()
    points: list[dict] = []
    point_index = 1  # calc_scan_point uses 1-based indexing

    for line in lines[1:]:  # skip the header line
        stripped = line.strip()
        if not stripped:
            # Blank line after the data block — done
            if points:
                break
            continue

        # Stop at the next section header
        if stripped.startswith("The Calculated Surface"):
            break

        parts = stripped.split()
        if len(parts) != 2:
            break

        try:
            coord_value = float(parts[0])
            energy = float(parts[1])
        except ValueError:
            break

        points.append({
            "point_index": point_index,
            "coordinate_value": coord_value,
            "electronic_energy_hartree": energy,
        })
        point_index += 1

    return points if points else None


# ---------------------------------------------------------------------------
# NEB PATH SUMMARY extraction
# ---------------------------------------------------------------------------


def parse_neb_path_summary(text: str) -> list[dict] | None:
    """Extract per-image results from the ORCA NEB PATH SUMMARY block.

    Handles two ORCA output formats:

    **NEB-CI** (has path distance column)::

        Image Dist.(Ang.)    E(Eh)   dE(kcal/mol)  max(|Fp|)  RMS(Fp)
          0     0.000   -1967.19636      0.00       0.00002   0.00001
         58     6.718   -1967.12166     46.87       0.00034   0.00011 <= CI

    **NEB-TS** (no path distance, adds a TS row)::

        Image     E(Eh)   dE(kcal/mol)  max(|Fp|)  RMS(Fp)
         57   -1967.12058    47.56       0.00165   0.00074 <= CI
         TS   -1967.12211    46.59       0.00087   0.00020 <= TS

    Uses the **last** PATH SUMMARY block in the output (the converged one).
    Returns ``None`` if no PATH SUMMARY block is found.
    """
    # Find the LAST PATH SUMMARY block (the converged result)
    marker = "PATH SUMMARY"
    idx = text.rfind(marker)
    if idx == -1:
        return None

    lines = text[idx:].splitlines()
    images: list[dict] = []

    # Detect format from header line
    has_dist = False
    data_started = False
    for line in lines[1:]:
        stripped = line.strip()

        # Skip separator lines
        if not stripped or stripped.startswith("---"):
            continue

        # Detect format from header and start data
        if stripped.startswith("Image"):
            has_dist = "Dist" in stripped
            data_started = True
            continue

        # Skip info lines before data
        if stripped.startswith("All forces"):
            continue

        if not data_started:
            continue

        # Stop at non-data sections
        if stripped.startswith("Straight line"):
            continue
        first_word = stripped.split()[0] if stripped.split() else ""
        if not first_word.isdigit():
            # Skip non-image rows (e.g. "TS" row in NEB-TS output).
            # The optimized TS is a separate refined object, not a path
            # image — it belongs in the calculation's output, not here.
            if first_word == "TS":
                continue
            break

        # Check for CI marker
        is_ci = "<= CI" in line

        # Remove markers before splitting
        clean = line.split("<=")[0].strip() if "<=" in line else stripped
        parts = clean.split()

        # Parse numbered image rows
        try:
            image_idx = int(parts[0])
            if has_dist:
                # NEB-CI format: idx, dist, E, dE, max_fp, rms_fp
                if len(parts) < 6:
                    break
                path_dist = float(parts[1])
                energy = float(parts[2])
                rel_energy = float(parts[3])
                max_fp = float(parts[4])
                rms_fp = float(parts[5])
            else:
                # NEB-TS format: idx, E, dE, max_fp, rms_fp
                if len(parts) < 5:
                    break
                path_dist = None
                energy = float(parts[1])
                rel_energy = float(parts[2])
                max_fp = float(parts[3])
                rms_fp = float(parts[4])
        except (ValueError, IndexError):
            break

        rel_energy_kj = round(rel_energy * 4.184, 4)
        images.append({
            "image_index": image_idx,
            "electronic_energy_hartree": energy,
            "relative_energy_kj_mol": rel_energy_kj,
            "path_distance_angstrom": path_dist,
            "max_force": max_fp,
            "rms_force": rms_fp,
            "is_climbing_image": is_ci,
        })

    return images if images else None


# ---------------------------------------------------------------------------
# IRC PATH SUMMARY extraction
# ---------------------------------------------------------------------------


def parse_irc_path_summary(text: str) -> dict | None:
    """Extract IRC path data from the ORCA IRC PATH SUMMARY block.

    Parses lines like::

        Step        E(Eh)      dE(kcal/mol)  max(|G|)   RMS(G)
           1     -1967.185827   -39.977023    0.001971  0.000413
          74     -1967.122120    0.000000    0.000008  0.000004 <= TS
          75     -1967.123694   -0.987880    0.009347  0.001949

    ORCA IRC with ``Direction both`` puts both directions in one log.
    The TS point (marked ``<= TS``) separates forward from backward.

    Returns a dict with:
    - ``points``: list of point dicts for ``calc_irc_point``
    - ``ts_point_index``: the step index of the TS point
    - ``has_forward``, ``has_reverse``: whether each direction is present
    - ``direction``: ``"both"``, ``"forward"``, or ``"reverse"``

    Returns ``None`` if no IRC PATH SUMMARY block is found.
    """
    marker = "IRC PATH SUMMARY"
    idx = text.rfind(marker)
    if idx == -1:
        return None

    lines = text[idx:].splitlines()
    points: list[dict] = []
    ts_point_index: int | None = None

    data_started = False
    for line in lines[1:]:
        stripped = line.strip()

        if not stripped or stripped.startswith("---"):
            continue

        if stripped.startswith("Step") or stripped.startswith("All"):
            if stripped.startswith("Step"):
                data_started = True
            continue

        if not data_started:
            continue

        # Stop at non-data lines
        first_word = stripped.split()[0] if stripped.split() else ""
        if not first_word.isdigit():
            break

        is_ts = "<= TS" in line

        clean = line.split("<=")[0].strip() if "<=" in line else stripped
        parts = clean.split()
        if len(parts) < 5:
            break

        try:
            step_idx = int(parts[0])
            energy = float(parts[1])
            rel_energy_kcal = float(parts[2])
            max_grad = float(parts[3])
            rms_grad = float(parts[4])
        except (ValueError, IndexError):
            break

        rel_energy_kj = round(rel_energy_kcal * 4.184, 4)

        if is_ts:
            ts_point_index = step_idx

        points.append({
            "point_index": step_idx,
            "is_ts": is_ts,
            "direction": None,  # assigned below after TS is found
            "electronic_energy_hartree": energy,
            "relative_energy_kj_mol": rel_energy_kj,
            "max_gradient": max_grad,
            "rms_gradient": rms_grad,
        })

    if not points:
        return None

    # Assign directions based on TS position.
    # Points before the TS go one direction, points after go the other.
    # ORCA convention: steps before TS are "backward" (toward reactant),
    # steps after TS are "forward" (toward product).
    has_forward = False
    has_reverse = False
    if ts_point_index is not None:
        for pt in points:
            if pt["is_ts"]:
                pt["direction"] = None  # TS itself has no direction
            elif pt["point_index"] < ts_point_index:
                pt["direction"] = "reverse"
                has_reverse = True
            else:
                pt["direction"] = "forward"
                has_forward = True
    else:
        # No TS marker — cannot determine directions
        pass

    direction_mode = "both" if (has_forward and has_reverse) else (
        "forward" if has_forward else "reverse" if has_reverse else "both"
    )

    return {
        "points": points,
        "ts_point_index": ts_point_index,
        "has_forward": has_forward,
        "has_reverse": has_reverse,
        "direction": direction_mode,
        "point_count": len(points),
    }


# ---------------------------------------------------------------------------
# Charge / multiplicity
# ---------------------------------------------------------------------------


def parse_charge_multiplicity(text: str) -> dict | None:
    """Extract charge and multiplicity from ORCA coordinate header."""
    input_lines = _extract_input_block(text)
    for line in input_lines:
        stripped = line.strip()
        # ``* xyz charge mult`` or ``* int charge mult``
        if stripped.startswith("*") and ("xyz" in stripped.lower() or "int" in stripped.lower()):
            parts = stripped.split()
            if len(parts) >= 4:
                try:
                    return {
                        "charge": int(parts[2]),
                        "multiplicity": int(parts[3]),
                    }
                except ValueError:
                    pass
    return None


# ---------------------------------------------------------------------------
# Software version extraction
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"Program\s+Version\s+(\d+\.\d+\.\d+)")


def parse_software_version(text: str) -> dict | None:
    """Extract ORCA software version from log text.

    Matches: ``Program Version 5.0.4 -  RELEASE  -``
    """
    m = _VERSION_RE.search(text)
    if m:
        return {
            "name": "orca",
            "version": m.group(1),
            "build": None,
            "release_date_raw": None,
        }
    return None


# ---------------------------------------------------------------------------
# Method/basis extraction
# ---------------------------------------------------------------------------


def parse_method_basis(text: str) -> dict | None:
    """Extract method and basis from ORCA ! keyword line.

    Returns dict with method, basis, aux_basis, cabs_basis keys.
    Classification:
    - ``-cabs`` suffix → CABS basis (F12 complementary auxiliary)
    - ``/c`` suffix → auxiliary correlation fitting basis
    - first orbital basis → primary basis
    - remaining → aux_basis
    """
    input_lines = _extract_input_block(text)

    method = None
    basis = None
    aux_basis = None
    cabs_basis = None

    for line in input_lines:
        stripped = line.strip()
        if not stripped.startswith("!"):
            continue

        kw_line = stripped.lstrip("!").strip()
        if "#" in kw_line:
            kw_line = kw_line[:kw_line.index("#")].strip()

        for kw in kw_line.split():
            kw_lower = kw.lower()

            # Method detection
            if kw_lower in _METHOD_PREFIXES:
                method = kw

            # Basis detection
            elif _BASIS_PATTERNS.match(kw_lower):
                if "-cabs" in kw_lower:
                    cabs_basis = kw
                elif "/c" in kw_lower:
                    aux_basis = kw
                elif basis is None:
                    basis = kw
                elif aux_basis is None:
                    aux_basis = kw

    if method or basis:
        return {
            "method": method,
            "basis": basis,
            "aux_basis": aux_basis,
            "cabs_basis": cabs_basis,
        }
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _guess_value_type(val: str) -> str:
    """Guess the type hint for a raw value string."""
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
# Main entry point
# ---------------------------------------------------------------------------


def parse_orca_log(
    text: str | None = None,
    path: str | Path | None = None,
) -> dict:
    """Parse an ORCA log file and return structured parameter data.

    :param text: Raw log text (provide one of text or path).
    :param path: Path to log file.
    :returns: Dict with keys: parameters, parameters_json, software,
              charge_multiplicity, method_basis, parser_version.
    """
    if text is None and path is not None:
        text = Path(path).read_text()
    if text is None:
        raise ValueError("Provide text or path")

    input_lines = _extract_input_block(text)

    # Parse all parameter sources
    keyword_params = _parse_keyword_lines(input_lines)
    block_params = _parse_block_sections(input_lines)
    all_params = keyword_params + block_params

    # Parse scan coordinate definitions from %GEOM blocks (if any)
    scan_coordinates = _parse_scan_definitions(input_lines)

    # Parse scan results from output summary (fast path)
    scan_points = parse_scan_results(text)

    # Parse NEB path summary (if present)
    neb_images = parse_neb_path_summary(text)

    # Parse IRC path summary (if present)
    irc_result = parse_irc_path_summary(text)

    return {
        "parameters": all_params,
        "scan_coordinates": scan_coordinates,
        "scan_points": scan_points,
        "neb_images": neb_images,
        "irc_result": irc_result,
        "parameters_json": {
            "input_lines": input_lines,
            "parameters": all_params,
            "scan_coordinates": scan_coordinates,
        },
        "software": parse_software_version(text),
        "charge_multiplicity": parse_charge_multiplicity(text),
        "method_basis": parse_method_basis(text),
        "parser_version": "orca_v1",
    }
