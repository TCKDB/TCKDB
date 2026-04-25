"""Parse statmech data from Arkane conformer() blocks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ArkaneHinderedRotor:
    symmetry_number: int
    treatment: str  # 'hindered_rotor' or 'free_rotor'


@dataclass
class ArkaneConformer:
    label: str
    e0_kj_mol: float
    spin_multiplicity: int
    optical_isomers: int
    is_linear: bool | None          # None = monoatomic
    external_symmetry: int | None   # from rotator symmetry field
    rigid_rotor_kind: str | None    # 'atom','linear','asymmetric_top','symmetric_top','spherical_top'
    statmech_treatment: str | None  # 'rrho','rrho_1d','rrho_nd','rrho_1d_nd'
    harmonic_frequencies_cm1: list[float] = field(default_factory=list)
    hindered_rotors: list[ArkaneHinderedRotor] = field(default_factory=list)


def _extract_conformer_block(text: str) -> str:
    """Extract the last conformer(...) block from the file text."""
    # Find all occurrences of 'conformer(' and take the last one
    start_idx = -1
    search_from = 0
    while True:
        idx = text.find("conformer(", search_from)
        if idx == -1:
            break
        start_idx = idx
        search_from = idx + 1

    if start_idx == -1:
        raise ValueError("No conformer(...) block found in text.")

    # Find the matching closing paren by counting depth
    paren_depth = 0
    i = start_idx + len("conformer(") - 1  # position of the opening paren
    end_idx = -1
    while i < len(text):
        if text[i] == "(":
            paren_depth += 1
        elif text[i] == ")":
            paren_depth -= 1
            if paren_depth == 0:
                end_idx = i
                break
        i += 1

    if end_idx == -1:
        raise ValueError("Unmatched parenthesis in conformer() block.")

    return text[start_idx : end_idx + 1]


def _extract_e0_kj_mol(block: str) -> float:
    """Extract E0 value and convert to kJ/mol."""
    # Match both single-line and multi-line E0 assignments
    # E0 = (value, 'units') or E0 = (\n    value,\n    'units',\n)
    pattern = re.compile(
        r"E0\s*=\s*\(\s*([-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*,\s*['\"]([^'\"]+)['\"]\s*,?\s*\)",
        re.DOTALL,
    )
    m = pattern.search(block)
    if not m:
        raise ValueError(f"Could not parse E0 from conformer block.")

    value = float(m.group(1))
    units = m.group(2).strip()

    if units == "kJ/mol":
        return value
    elif units == "J/mol":
        return value / 1000.0
    elif units == "kcal/mol":
        return value * 4.184
    elif units == "cal/mol":
        return value * 4.184 / 1000.0
    else:
        raise ValueError(f"Unrecognised E0 units: '{units}'")


def _extract_scalar_int(block: str, field_name: str) -> int | None:
    """Extract a simple integer field like spin_multiplicity=2."""
    m = re.search(rf"{re.escape(field_name)}\s*=\s*(\d+)", block)
    return int(m.group(1)) if m else None


def _extract_label(block: str) -> str:
    """Extract the label field."""
    m = re.search(r"label\s*=\s*['\"]([^'\"]+)['\"]", block)
    return m.group(1) if m else ""


def _extract_modes_string(block: str) -> str:
    """Extract the full modes=[...] list string using balanced-bracket extraction.

    Simple regex with ``.*?`` stops at the first ``]``, which breaks on nested
    lists like ``inertia=([...])`` inside NonlinearRotor.  Walk character by
    character instead.
    """
    m = re.search(r"modes\s*=\s*(\[)", block)
    if not m:
        return ""
    start = m.start(1)
    depth = 0
    for i in range(start, len(block)):
        if block[i] == "[":
            depth += 1
        elif block[i] == "]":
            depth -= 1
            if depth == 0:
                return block[start : i + 1]
    return ""


def _extract_symmetry_from_rotor(rotor_str: str) -> int | None:
    """Extract symmetry=N from a rotor string."""
    m = re.search(r"symmetry\s*=\s*(\d+)", rotor_str)
    return int(m.group(1)) if m else None


def _extract_inertia_values(rotor_str: str) -> list[float]:
    """Extract inertia values list from a NonlinearRotor string."""
    m = re.search(r"inertia\s*=\s*\(\s*\[([^\]]+)\]", rotor_str)
    if not m:
        return []
    vals_str = m.group(1)
    return [float(v.strip()) for v in vals_str.split(",") if v.strip()]


def _classify_nonlinear_rotor(inertia: list[float]) -> str:
    """Classify a nonlinear rotor as spherical, symmetric, or asymmetric top."""
    if len(inertia) < 3:
        return "asymmetric_top"

    ia, ib, ic = sorted(inertia)
    max_i = max(ia, ib, ic)

    if (max_i - ia) / max_i < 0.05:
        return "spherical_top"

    # Check oblate (Ia ~ Ib) or prolate (Ib ~ Ic)
    if abs(ia - ib) / max(ia, ib) < 0.05 or abs(ib - ic) / max(ib, ic) < 0.05:
        return "symmetric_top"

    return "asymmetric_top"


def _extract_harmonic_frequencies(modes_str: str) -> list[float]:
    """Extract frequencies from HarmonicOscillator(...) in the modes string."""
    m = re.search(r"HarmonicOscillator\s*\(.*?frequencies\s*=\s*\(\s*\[([^\]]+)\]", modes_str, re.DOTALL)
    if not m:
        return []
    vals_str = m.group(1)
    return [float(v.strip()) for v in vals_str.split(",") if v.strip()]


def _extract_rotor_blocks(modes_str: str, rotor_type: str) -> list[str]:
    """Extract all blocks of a given rotor type from the modes string."""
    blocks = []
    search_from = 0
    while True:
        idx = modes_str.find(rotor_type + "(", search_from)
        if idx == -1:
            break
        # Find the closing paren
        paren_depth = 0
        i = idx + len(rotor_type)  # at the '('
        end_idx = -1
        while i < len(modes_str):
            if modes_str[i] == "(":
                paren_depth += 1
            elif modes_str[i] == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    end_idx = i
                    break
            i += 1
        if end_idx == -1:
            break
        blocks.append(modes_str[idx : end_idx + 1])
        search_from = end_idx + 1
    return blocks


def parse_arkane_conformer(text: str) -> ArkaneConformer:
    """Parse an ArkaneConformer from Arkane output file text."""
    block = _extract_conformer_block(text)

    label = _extract_label(block)
    e0_kj_mol = _extract_e0_kj_mol(block)
    spin_multiplicity = _extract_scalar_int(block, "spin_multiplicity") or 1
    optical_isomers = _extract_scalar_int(block, "optical_isomers") or 1

    modes_str = _extract_modes_string(block)

    # Detect rotor type
    has_linear = "LinearRotor(" in modes_str
    has_nonlinear = "NonlinearRotor(" in modes_str

    is_linear: bool | None = None
    external_symmetry: int | None = None
    rigid_rotor_kind: str | None = None

    if has_linear:
        is_linear = True
        linear_blocks = _extract_rotor_blocks(modes_str, "LinearRotor")
        if linear_blocks:
            external_symmetry = _extract_symmetry_from_rotor(linear_blocks[0])
        rigid_rotor_kind = "linear"

    elif has_nonlinear:
        is_linear = False
        nonlinear_blocks = _extract_rotor_blocks(modes_str, "NonlinearRotor")
        if nonlinear_blocks:
            external_symmetry = _extract_symmetry_from_rotor(nonlinear_blocks[0])
            inertia = _extract_inertia_values(nonlinear_blocks[0])
            rigid_rotor_kind = _classify_nonlinear_rotor(inertia)
        else:
            rigid_rotor_kind = "asymmetric_top"

    else:
        # Monoatomic
        is_linear = None
        external_symmetry = None
        rigid_rotor_kind = "atom"

    # Harmonic frequencies
    harmonic_frequencies_cm1 = _extract_harmonic_frequencies(modes_str)

    # Hindered and free rotors
    hindered_rotors: list[ArkaneHinderedRotor] = []

    for hr_block in _extract_rotor_blocks(modes_str, "HinderedRotor"):
        sym = _extract_symmetry_from_rotor(hr_block) or 1
        hindered_rotors.append(ArkaneHinderedRotor(symmetry_number=sym, treatment="hindered_rotor"))

    for fr_block in _extract_rotor_blocks(modes_str, "FreeRotor"):
        sym = _extract_symmetry_from_rotor(fr_block) or 1
        hindered_rotors.append(ArkaneHinderedRotor(symmetry_number=sym, treatment="free_rotor"))

    # Statmech treatment
    n_torsions = len(hindered_rotors)
    if n_torsions == 0:
        statmech_treatment = "rrho"
    else:
        statmech_treatment = "rrho_1d"

    return ArkaneConformer(
        label=label,
        e0_kj_mol=e0_kj_mol,
        spin_multiplicity=spin_multiplicity,
        optical_isomers=optical_isomers,
        is_linear=is_linear,
        external_symmetry=external_symmetry,
        rigid_rotor_kind=rigid_rotor_kind,
        statmech_treatment=statmech_treatment,
        harmonic_frequencies_cm1=harmonic_frequencies_cm1,
        hindered_rotors=hindered_rotors,
    )


def parse_arkane_conformer_from_file(path: str | Path) -> ArkaneConformer:
    """Parse an ArkaneConformer from an Arkane output.py file."""
    text = Path(path).read_text(errors="replace")
    return parse_arkane_conformer(text)
