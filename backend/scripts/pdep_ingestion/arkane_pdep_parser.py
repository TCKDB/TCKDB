"""Parsers for a full Arkane pressure-dependence run.

Reuses ``scripts.arc_ingestion.arkane_conformer_parser.parse_arkane_conformer``
(the existing per-block statmech regex parser) applied to each ``conformer(...)``
block, and adds NEW parsers the arc_ingestion package did not have:

- ``parse_all_conformers`` -- extract every ``conformer(...)`` block by label
  (the reused arc_ingestion parser only returned the *last* block).
- ``parse_pdep_reactions`` -- the NEW ``pdepreaction(...) / Chebyshev(...)``
  parser (arc_ingestion only handled modified-Arrhenius ``kinetics(...)``).
- ``parse_input_file`` -- Arkane ``input.py`` DSL (species / transitionState /
  reaction / network / pressureDependence / energyTransfer).
- ``parse_data_file`` -- per-species ``Data/<x>.py`` (bonds, symmetry, spin,
  optical isomers, ``Log(...)`` paths).
- ``parse_supporting_information`` -- the clean per-species scalar CSV.

Style follows the arc_ingestion convention: regex / balanced-bracket text
extraction over the file text, never ``exec``-ing the Arkane file.
"""

from __future__ import annotations

import ast
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

# Reuse the existing arc_ingestion per-block statmech parser.
from scripts.arc_ingestion.arkane_conformer_parser import (
    ArkaneConformer,
    parse_arkane_conformer,
)

# ---------------------------------------------------------------------------
# Shared balanced-paren block extraction
# ---------------------------------------------------------------------------


def _iter_call_blocks(text: str, call_name: str):
    """Yield the full ``call_name(...)`` block text for each top-level call.

    Uses balanced-paren walking (the same technique as the arc_ingestion
    conformer parser) so nested parens / brackets do not truncate a block.
    Only matches ``call_name`` when it begins a line (optionally indented),
    which keeps it from matching substrings inside other tokens.
    """
    pattern = re.compile(rf"^[ \t]*{re.escape(call_name)}\(", re.MULTILINE)
    for m in pattern.finditer(text):
        open_idx = text.index("(", m.start())
        depth = 0
        for i in range(open_idx, len(text)):
            c = text[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    yield text[m.start() : i + 1]
                    break


def extract_first_call(text: str, call_name: str) -> str | None:
    """Return the first ``call_name(...)`` block anywhere in ``text``.

    Unlike ``_iter_call_blocks`` this is not line-anchored, so it matches
    calls nested inside other blocks (e.g. ``SingleExponentialDown(...)`` or
    ``LevelOfTheory(...)`` inside a ``species(...)`` call). Balanced-paren
    walking tolerates inner parens such as ``basis="aug-cc-pV(T+d)Z"``.
    """
    m = re.search(rf"{re.escape(call_name)}\(", text)
    if not m:
        return None
    open_idx = text.index("(", m.start())
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[m.start() : i + 1]
    return None


def strip_commented_lines(text: str) -> str:
    """Drop whole-line comments so commented-out Arkane blocks are ignored.

    ``output.py`` contains many commented-out ``pdepreaction(...)`` blocks
    (only 21 are active). A line whose first non-space char is ``#`` is
    removed entirely; inline trailing comments are left untouched.
    """
    kept = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# output.py conformer blocks (species + TS statmech)
# ---------------------------------------------------------------------------


def parse_all_conformers(text: str) -> dict[str, ArkaneConformer]:
    """Parse every ``conformer(...)`` block in Arkane output text, keyed by label.

    Each block is handed to the reused arc_ingestion ``parse_arkane_conformer``.
    """
    result: dict[str, ArkaneConformer] = {}
    for block in _iter_call_blocks(text, "conformer"):
        conf = parse_arkane_conformer(block)
        if conf.label:
            result[conf.label] = conf
    return result


# ---------------------------------------------------------------------------
# output.py pdepreaction / Chebyshev blocks  (NEW)
# ---------------------------------------------------------------------------


@dataclass
class ChebyshevFit:
    """A parsed Chebyshev k(T,P) fit from a ``pdepreaction`` block."""

    reactants: list[str]
    products: list[str]
    coefficients: list[list[float]]  # n_temperature rows x n_pressure cols
    kunits: str                       # e.g. 's^-1', 'cm^3/(mol*s)'
    tmin_value: float
    tmax_value: float
    temperature_units: str            # 'K', as labelled in the file
    pmin_value: float
    pmax_value: float
    pressure_units: str               # 'bar' or 'atm', as labelled in the file

    @property
    def n_temperature(self) -> int:
        return len(self.coefficients)

    @property
    def n_pressure(self) -> int:
        return len(self.coefficients[0]) if self.coefficients else 0


def _extract_str_list(block: str, name: str) -> list[str]:
    """Extract ``name = ['a', 'b']`` as a list of strings."""
    m = re.search(rf"{name}\s*=\s*(\[[^\]]*\])", block)
    if not m:
        return []
    return [str(x) for x in ast.literal_eval(m.group(1))]


def _extract_value_units(block: str, name: str) -> tuple[float, str] | None:
    """Extract ``name = (value, 'units')`` (single- or multi-line)."""
    pattern = rf"{name}\s*=\s*\(\s*([-\d.eE+]+)\s*,\s*['\"]([^'\"]+)['\"]\s*,?\s*\)"
    m = re.search(pattern, block, re.DOTALL)
    if not m:
        return None
    return float(m.group(1)), m.group(2)


def _extract_chebyshev_coeffs(block: str) -> list[list[float]]:
    """Extract the ``coeffs = [[...], [...]]`` 2D grid from a Chebyshev block."""
    m = re.search(r"coeffs\s*=\s*(\[)", block)
    if not m:
        return []
    start = m.start(1)
    depth = 0
    end = start
    for i in range(start, len(block)):
        if block[i] == "[":
            depth += 1
        elif block[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    grid = ast.literal_eval(block[start:end])
    return [[float(v) for v in row] for row in grid]


@dataclass
class SkippedPdepReaction:
    """A ``pdepreaction`` block the Chebyshev parser did not turn into a fit."""

    reactants: list[str]
    products: list[str]
    reason: str


def parse_pdep_reactions_with_skips(
    text: str,
) -> tuple[list[ChebyshevFit], list[SkippedPdepReaction]]:
    """Parse active ``pdepreaction(...)`` blocks, returning fits and skips.

    Commented-out blocks (leading ``#``) are dropped first. Chebyshev blocks
    become :class:`ChebyshevFit`; any non-Chebyshev block is recorded as a
    :class:`SkippedPdepReaction` (fail-loud, never silently dropped).
    """
    clean = strip_commented_lines(text)
    fits: list[ChebyshevFit] = []
    skips: list[SkippedPdepReaction] = []
    for block in _iter_call_blocks(clean, "pdepreaction"):
        reactants = _extract_str_list(block, "reactants")
        products = _extract_str_list(block, "products")
        if "Chebyshev(" not in block:
            kind_m = re.search(r"kinetics\s*=\s*([A-Za-z_]+)\(", block)
            kind = kind_m.group(1) if kind_m else "unknown"
            skips.append(
                SkippedPdepReaction(reactants, products, f"non-Chebyshev ({kind})")
            )
            continue
        coeffs = _extract_chebyshev_coeffs(block)

        kunits_m = re.search(r"kunits\s*=\s*['\"]([^'\"]+)['\"]", block)
        kunits = kunits_m.group(1) if kunits_m else ""

        tmin = _extract_value_units(block, "Tmin")
        tmax = _extract_value_units(block, "Tmax")
        pmin = _extract_value_units(block, "Pmin")
        pmax = _extract_value_units(block, "Pmax")
        if not (tmin and tmax and pmin and pmax):
            raise ValueError(
                f"pdepreaction {reactants}->{products} missing T/P bounds."
            )
        if pmin[1] != pmax[1]:
            raise ValueError("Chebyshev Pmin/Pmax units differ.")
        if tmin[1] != tmax[1]:
            raise ValueError("Chebyshev Tmin/Tmax units differ.")

        fits.append(
            ChebyshevFit(
                reactants=reactants,
                products=products,
                coefficients=coeffs,
                kunits=kunits,
                tmin_value=tmin[0],
                tmax_value=tmax[0],
                temperature_units=tmin[1],
                pmin_value=pmin[0],
                pmax_value=pmax[0],
                pressure_units=pmin[1],
            )
        )
    return fits, skips


def parse_pdep_reactions(text: str) -> list[ChebyshevFit]:
    """Parse every *active* ``pdepreaction(...)`` Chebyshev block."""
    fits, _skips = parse_pdep_reactions_with_skips(text)
    return fits


# ---------------------------------------------------------------------------
# input.py DSL  (NEW)
# ---------------------------------------------------------------------------


@dataclass
class InputSpecies:
    label: str
    data_file: str | None            # e.g. 'Data/N2H4.py' (None for bath gas)
    smiles: str | None
    reactive: bool = True
    e0_kj_mol: float | None = None   # for non-ab-initio species (bath gas)
    spin_multiplicity: int | None = None
    optical_isomers: int | None = None


@dataclass
class InputTransitionState:
    label: str
    data_file: str | None
    e0_kj_mol: float | None = None   # set for the literature-Arrhenius stubs


@dataclass
class InputReaction:
    label: str
    reactants: list[str]
    products: list[str]
    transition_state: str | None
    has_ab_initio_ts: bool           # True when TS carries a Data/ file


@dataclass
class EnergyTransfer:
    model: str
    alpha0_cm_inv: float | None
    t_ref_k: float | None
    t_exponent: float | None


@dataclass
class PressureDependence:
    tmin_k: float
    tmax_k: float
    pmin_bar: float
    pmax_bar: float
    grain_size_value: float
    grain_size_units: str
    grain_count: int | None
    method: str | None
    interpolation_model: str | None
    n_cheb_t: int | None
    n_cheb_p: int | None


@dataclass
class ArkaneInput:
    species: dict[str, InputSpecies]
    transition_states: dict[str, InputTransitionState]
    reactions: list[InputReaction]
    network_label: str | None
    isomers: list[str]
    reactant_channels: list[list[str]]
    bath_gas: dict[str, float]
    pressure_dependence: PressureDependence | None
    energy_transfer: EnergyTransfer | None
    freq_scale_factor: float | None
    opt_method: str | None
    opt_basis: str | None
    opt_software: str | None
    energy_method: str | None
    energy_basis: str | None
    energy_software: str | None


def _first(pattern: str, text: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


def parse_input_file(text: str) -> ArkaneInput:
    """Parse an Arkane ``input.py``."""
    species: dict[str, InputSpecies] = {}
    transition_states: dict[str, InputTransitionState] = {}
    reactions: list[InputReaction] = []

    # --- species(...) blocks ---
    for block in _iter_call_blocks(text, "species"):
        # Positional form: species('LABEL', 'Data/x.py', ...)
        pos = re.match(
            r"species\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+\.py)['\"]",
            block,
        )
        label = None
        data_file = None
        if pos:
            label = pos.group(1)
            data_file = pos.group(2)
        else:
            label = _first(r"label\s*=\s*['\"]([^'\"]+)['\"]", block)
        if label is None:
            continue
        smiles = _first(r"SMILES\(\s*['\"]([^'\"]*)['\"]\s*\)", block)
        reactive_raw = _first(r"reactive\s*=\s*(True|False)", block)
        reactive = reactive_raw != "False"
        e0 = _extract_value_units(block, "E0") if "E0" in block else None
        spin = _first(r"spinMultiplicity\s*=\s*(\d+)", block)
        optical = _first(r"opticalIsomers\s*=\s*(\d+)", block)
        species[label] = InputSpecies(
            label=label,
            data_file=data_file,
            smiles=smiles,
            reactive=reactive,
            e0_kj_mol=(e0[0] if e0 and e0[1] == "kJ/mol" else None),
            spin_multiplicity=int(spin) if spin else None,
            optical_isomers=int(optical) if optical else None,
        )

    # --- transitionState(...) blocks ---
    for block in _iter_call_blocks(text, "transitionState"):
        pos = re.match(
            r"transitionState\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+\.py)['\"]",
            block,
        )
        if pos:
            transition_states[pos.group(1)] = InputTransitionState(
                label=pos.group(1), data_file=pos.group(2)
            )
            continue
        label = _first(r"label\s*=\s*['\"]([^'\"]+)['\"]", block)
        if label is None:
            continue
        e0 = _extract_value_units(block, "E0")
        transition_states[label] = InputTransitionState(
            label=label,
            data_file=None,
            e0_kj_mol=(e0[0] if e0 else None),
        )

    # --- reaction(...) blocks ---
    for block in _iter_call_blocks(text, "reaction"):
        label = _first(r"label\s*=\s*['\"]([^'\"]+)['\"]", block) or ""
        reactants = _extract_str_list(block, "reactants")
        products = _extract_str_list(block, "products")
        ts = _first(r"transitionState\s*=\s*['\"]([^'\"]+)['\"]", block)
        has_ai = bool(
            ts and ts in transition_states and transition_states[ts].data_file
        )
        reactions.append(
            InputReaction(
                label=label,
                reactants=reactants,
                products=products,
                transition_state=ts,
                has_ab_initio_ts=has_ai,
            )
        )

    # --- network(...) block ---
    isomers: list[str] = []
    reactant_channels: list[list[str]] = []
    bath_gas: dict[str, float] = {}
    network_label: str | None = None
    net_blocks = list(_iter_call_blocks(text, "network"))
    if net_blocks:
        nb = net_blocks[0]
        network_label = _first(r"label\s*=\s*['\"]([^'\"]+)['\"]", nb)
        iso_m = re.search(r"isomers\s*=\s*(\[.*?\])", nb, re.DOTALL)
        if iso_m:
            isomers = [str(x) for x in ast.literal_eval(iso_m.group(1))]
        rc_m = re.search(r"reactants\s*=\s*(\[.*?\])\s*,\s*bathGas", nb, re.DOTALL)
        if rc_m:
            for tup in ast.literal_eval(rc_m.group(1)):
                reactant_channels.append([str(x) for x in tup])
        bg_m = re.search(r"bathGas\s*=\s*(\{.*?\})", nb, re.DOTALL)
        if bg_m:
            bath_gas = {
                str(k): float(v) for k, v in ast.literal_eval(bg_m.group(1)).items()
            }

    # --- pressureDependence(...) block ---
    pdep: PressureDependence | None = None
    pd_blocks = list(_iter_call_blocks(text, "pressureDependence"))
    if pd_blocks:
        pb = pd_blocks[0]
        tmin = _extract_value_units(pb, "Tmin")
        tmax = _extract_value_units(pb, "Tmax")
        pmin = _extract_value_units(pb, "Pmin")
        pmax = _extract_value_units(pb, "Pmax")
        grain = _extract_value_units(pb, "maximumGrainSize")
        gcount = _first(r"minimumGrainCount\s*=\s*(\d+)", pb)
        method = _first(r"method\s*=\s*['\"]([^'\"]+)['\"]", pb)
        interp_m = re.search(
            r"interpolationModel\s*=\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*(\d+)\s*,\s*(\d+)",
            pb,
        )
        pdep = PressureDependence(
            tmin_k=tmin[0] if tmin else 0.0,
            tmax_k=tmax[0] if tmax else 0.0,
            pmin_bar=_to_bar(pmin) if pmin else 0.0,
            pmax_bar=_to_bar(pmax) if pmax else 0.0,
            grain_size_value=grain[0] if grain else 0.0,
            grain_size_units=grain[1] if grain else "",
            grain_count=int(gcount) if gcount else None,
            method=method,
            interpolation_model=interp_m.group(1) if interp_m else None,
            n_cheb_t=int(interp_m.group(2)) if interp_m else None,
            n_cheb_p=int(interp_m.group(3)) if interp_m else None,
        )

    # --- energyTransferModel (first SingleExponentialDown found) ---
    et: EnergyTransfer | None = None
    body = extract_first_call(text, "SingleExponentialDown")
    if body:
        alpha = _extract_value_units(body, "alpha0")
        t0 = _extract_value_units(body, "T0")
        n = _first(r"\bn\s*=\s*([-\d.eE+]+)", body)
        et = EnergyTransfer(
            model="single_exponential_down",
            alpha0_cm_inv=alpha[0] if alpha else None,
            t_ref_k=t0[0] if t0 else None,
            t_exponent=float(n) if n else None,
        )

    # --- level of theory (CompositeLevelOfTheory) ---
    fsf = _first(r"frequencyScaleFactor\s*=\s*([-\d.eE+]+)", text)

    def _lot_fields(prefix: str):
        km = re.search(rf"{prefix}\s*=\s*LevelOfTheory\(", text)
        if not km:
            return None, None, None
        b = extract_first_call(text[km.start():], "LevelOfTheory") or ""
        return (
            _first(r"method\s*=\s*['\"]([^'\"]+)['\"]", b),
            _first(r"basis\s*=\s*['\"]([^'\"]+)['\"]", b),
            _first(r"software\s*=\s*['\"]([^'\"]+)['\"]", b),
        )

    om, ob, osw = _lot_fields("freq")
    em, eb, esw = _lot_fields("energy")

    return ArkaneInput(
        species=species,
        transition_states=transition_states,
        reactions=reactions,
        network_label=network_label,
        isomers=isomers,
        reactant_channels=reactant_channels,
        bath_gas=bath_gas,
        pressure_dependence=pdep,
        energy_transfer=et,
        freq_scale_factor=float(fsf) if fsf else None,
        opt_method=om,
        opt_basis=ob,
        opt_software=osw,
        energy_method=em,
        energy_basis=eb,
        energy_software=esw,
    )


def _to_bar(value_units: tuple[float, str]) -> float:
    value, units = value_units
    if units == "bar":
        return value
    if units == "atm":
        return value * 1.01325
    raise ValueError(f"Unexpected pressure units {units!r}.")


# ---------------------------------------------------------------------------
# Data/<species>.py  (NEW)
# ---------------------------------------------------------------------------


@dataclass
class DataFile:
    bonds: dict[str, int]
    external_symmetry: int | None
    spin_multiplicity: int | None
    optical_isomers: int | None
    energy_log: str | None       # embedded (home-dir) path
    geometry_log: str | None
    frequencies_log: str | None
    scan_logs: list[str] = field(default_factory=list)


def parse_data_file(text: str) -> DataFile:
    """Parse a per-species ``Data/<x>.py`` file (regex, never exec)."""
    bonds: dict[str, int] = {}
    bm = re.search(r"bonds\s*=\s*(\{[^}]*\})", text)
    if bm:
        bonds = {str(k): int(v) for k, v in ast.literal_eval(bm.group(1)).items()}

    def _log(name: str) -> str | None:
        m = re.search(rf"{name}\s*=\s*Log\(\s*['\"]([^'\"]+)['\"]", text)
        return m.group(1) if m else None

    scan_logs = re.findall(r"scanLog\s*=\s*Log\(\s*['\"]([^'\"]+)['\"]", text)

    return DataFile(
        bonds=bonds,
        external_symmetry=(
            int(_first(r"externalSymmetry\s*=\s*(\d+)", text) or 0) or None
        ),
        spin_multiplicity=(
            int(_first(r"spinMultiplicity\s*=\s*(\d+)", text) or 0) or None
        ),
        optical_isomers=(
            int(_first(r"opticalIsomers\s*=\s*(\d+)", text) or 0) or None
        ),
        energy_log=_log("energy"),
        geometry_log=_log("geometry"),
        frequencies_log=_log("frequencies"),
        scan_logs=list(scan_logs),
    )


def resolve_log_path(embedded_path: str, run_dir: Path) -> Path:
    """Resolve an embedded ``Log('/home/.../Data/<x>/<f>')`` path onto disk.

    Gotcha #3: the embedded paths point at the author's home directory. We
    re-root them at ``<run_dir>/Data`` by keeping the tail after the last
    ``/Data/`` segment.
    """
    marker = "/Data/"
    idx = embedded_path.rfind(marker)
    tail = embedded_path[idx + len(marker):] if idx != -1 else Path(embedded_path).name
    data_root = (run_dir / "Data").resolve()
    resolved = (data_root / tail).resolve()
    # Containment guard: a crafted ``..`` tail must not escape <run_dir>/Data.
    if data_root not in resolved.parents and resolved != data_root:
        raise ValueError(
            f"Resolved Log() path {resolved} escapes the run Data directory "
            f"{data_root}; refusing to read."
        )
    return resolved


# ---------------------------------------------------------------------------
# supporting_information.csv  (NEW)
# ---------------------------------------------------------------------------


@dataclass
class SupportingInfo:
    label: str
    symmetry_number: int | None
    optical_isomers: int | None
    point_group: str | None
    rotational_constants_cm_inv: list[float]
    frequencies_cm_inv: list[float]   # imaginary entries stored negative
    n_imag: int
    electronic_energy_j_mol: float | None
    e0_zpe_j_mol: float | None
    e0_corrected_j_mol: float | None
    xyz_text: str | None
    t1_diagnostic: float | None
    d1_diagnostic: float | None


def _parse_freq_token(tok: str) -> float | None:
    tok = tok.strip()
    if not tok or tok == "-":
        return None
    if tok.endswith("i"):
        return -float(tok[:-1])
    return float(tok)


def _parse_csv_xyz(raw: str) -> str | None:
    """Convert the CSV coordinate string into an XYZ text block."""
    raw = raw.strip()
    if not raw or raw == "-":
        return None
    atoms = [a.strip() for a in raw.split(",") if a.strip()]
    lines = []
    for a in atoms:
        parts = a.split()
        if len(parts) != 4:
            return None
        sym, x, y, z = parts
        lines.append(f"{sym} {x} {y} {z}")
    return f"{len(lines)}\n\n" + "\n".join(lines)


def parse_supporting_information(path: Path) -> dict[str, SupportingInfo]:
    """Parse ``supporting_information.csv`` into a label -> SupportingInfo map."""
    out: dict[str, SupportingInfo] = {}
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            label = (row.get("Label") or "").strip()
            if not label:
                continue

            def _num(key: str) -> float | None:
                v = (row.get(key) or "").strip()
                if not v or v == "-":
                    return None
                return float(v)

            def _int(key: str) -> int | None:
                v = _num(key)
                return int(v) if v is not None else None

            freqs_raw = (
                row.get("Calculated Frequencies (unscaled and prior to "
                        "projection, cm^-1)") or ""
            )
            freqs = [
                f for f in (_parse_freq_token(t) for t in freqs_raw.split(","))
                if f is not None
            ]
            n_imag = sum(1 for f in freqs if f < 0)

            rot_raw = (row.get("Rotational constant (cm-1)") or "").strip()
            rot = [
                float(t) for t in rot_raw.split(",")
                if t.strip() and t.strip() != "-"
            ] if rot_raw and rot_raw != "-" else []

            out[label] = SupportingInfo(
                label=label,
                symmetry_number=_int("Symmetry Number"),
                optical_isomers=_int("Number of optical isomers"),
                point_group=((row.get("Symmetry Group") or "").strip() or None),
                rotational_constants_cm_inv=rot,
                frequencies_cm_inv=freqs,
                n_imag=n_imag,
                electronic_energy_j_mol=_num("Electronic energy (J/mol)"),
                e0_zpe_j_mol=_num("E0 (electronic energy + ZPE, J/mol)"),
                e0_corrected_j_mol=_num(
                    "E0 with atom and bond corrections (J/mol)"
                ),
                xyz_text=_parse_csv_xyz(
                    row.get("Atom XYZ coordinates (angstrom)") or ""
                ),
                t1_diagnostic=_num("T1 diagnostic"),
                d1_diagnostic=_num("D1 diagnostic"),
            )
    return out
