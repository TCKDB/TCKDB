"""CHEMKIN mechanism serializer (docs/specs/bulk_export_design.md §5).

The exact inverse of a CHEMKIN importer: takes an :class:`ExportRecordSet`
produced by :mod:`app.services.scientific_read.export` (with
``collapse=first`` — CHEMKIN cannot represent multiple candidates per
record) and emits the three canonical mechanism files:

* ``chem.inp``  — ELEMENTS / SPECIES / REACTIONS with Arrhenius, falloff
  (LOW/TROE/SRI), third-body efficiencies, PLOG, and Chebyshev blocks.
* ``therm.dat`` — NASA-7 14-coefficient thermo cards.
* ``tran.dat``  — transport (geometry index, epsilon/kB, sigma, dipole,
  polarizability, rotational relaxation).

Every species carries a ``!``-comment with its SMILES + TCKDB public ref
so an exported mechanism stays traceable back to the database. Anything
that cannot be represented (a species with only points/scalar thermo and
no NASA block, an unparseable structure) is reported as a gap rather than
emitted as a broken block.

Naming: species need short mechanism names. The default policy derives a
formula-based name with a numeric disambiguator; ``public_ref`` uses the
(sanitized) TCKDB ref. Names are guaranteed unique within one export.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.db.models.common import ArrheniusAUnits, KineticsModelKind
from app.services.scientific_read.export import (
    ExportGap,
    ExportRecordSet,
    ReactionExportRecord,
    SelectedKinetics,
    SpeciesExportRecord,
)

_AVOGADRO = 6.02214076e23
#: 1 kJ/mol in cal/mol.
_KJ_PER_MOL_TO_CAL_PER_MOL = 239.005736
#: 1 kJ/mol in J/mol.
_KJ_PER_MOL_TO_J_PER_MOL = 1000.0
#: 1 kJ/mol in K (Ea/R), R = 8.314462618 J/mol/K.
_KJ_PER_MOL_TO_KELVIN = 1000.0 / 8.314462618

_EA_UNIT_HEADERS = {
    "cal/mol": "CAL/MOLE",
    "kcal/mol": "KCAL/MOLE",
    "j/mol": "JOULES/MOLE",
    "kj/mol": "KJOULES/MOLE",
    "k": "KELVINS",
}

_FALLOFF_KINDS = {
    KineticsModelKind.lindemann,
    KineticsModelKind.troe,
    KineticsModelKind.sri,
}


@dataclass
class ChemkinOptions:
    """POST-body options for CHEMKIN export."""

    #: Energy unit for the REACTIONS header: cal/mol | kcal/mol | j/mol |
    #: kj/mol | k.
    energy_units: str = "cal/mol"
    #: Emit tran.dat.
    include_transport: bool = True
    #: Species naming policy: ``formula`` | ``public_ref``.
    naming_policy: str = "formula"


@dataclass
class ChemkinExport:
    files: dict[str, str]
    gaps: list[ExportGap]


# ---------------------------------------------------------------------------
# Elemental composition + naming
# ---------------------------------------------------------------------------


def _composition(smiles: str) -> Counter | None:
    """Element → atom count (H included) from a SMILES, or ``None``.

    Uses RDKit (already a hard dependency of the model layer). Returns
    ``None`` when the SMILES will not parse, so the caller records a gap.
    """
    try:
        from rdkit import Chem
    except Exception:  # pragma: no cover - rdkit always present in this env
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    mol = Chem.AddHs(mol)
    counts: Counter = Counter()
    for atom in mol.GetAtoms():
        counts[atom.GetSymbol().upper()] += 1
    return counts


def _sanitize_name(raw: str) -> str:
    """Make a CHEMKIN-safe token: no spaces or parentheses."""
    out = []
    for ch in raw:
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        else:
            out.append("_")
    return "".join(out) or "SPECIES"


def _assign_names(
    species_records: list[SpeciesExportRecord],
    *,
    naming_policy: str,
    compositions: dict[int, Counter | None],
) -> dict[int, str]:
    """Assign a unique mechanism name per species entry id."""
    names: dict[int, str] = {}
    used: set[str] = set()
    for sr in species_records:
        se_id = sr.species_entry.id
        if naming_policy == "public_ref":
            base = _sanitize_name(sr.species.public_ref)
        else:
            comp = compositions.get(se_id)
            base = _formula(comp) if comp else _sanitize_name(sr.species.smiles)
        name = base
        suffix = 1
        while name in used:
            suffix += 1
            name = f"{base}-{suffix}"
        used.add(name)
        names[se_id] = name
    return names


_ELEMENT_ORDER = ["C", "H", "N", "O", "S", "P", "F", "CL", "BR", "I"]


def _formula(comp: Counter) -> str:
    parts: list[str] = []
    for el in _ELEMENT_ORDER:
        if comp.get(el):
            parts.append(f"{el.capitalize()}{comp[el]}")
    for el in sorted(comp):
        if el not in _ELEMENT_ORDER and comp[el]:
            parts.append(f"{el.capitalize()}{comp[el]}")
    return "".join(parts) or "X"


# ---------------------------------------------------------------------------
# Energy / A-factor unit conversion
# ---------------------------------------------------------------------------


def _convert_ea(ea_kj_mol: float | None, energy_units: str) -> float | None:
    if ea_kj_mol is None:
        return None
    unit = energy_units.lower()
    if unit in ("cal/mol", "cal/mole"):
        return ea_kj_mol * _KJ_PER_MOL_TO_CAL_PER_MOL
    if unit in ("kcal/mol", "kcal/mole"):
        return ea_kj_mol * _KJ_PER_MOL_TO_CAL_PER_MOL / 1000.0
    if unit in ("j/mol", "joules/mole"):
        return ea_kj_mol * _KJ_PER_MOL_TO_J_PER_MOL
    if unit in ("kj/mol", "kjoules/mole"):
        return ea_kj_mol
    if unit in ("k", "kelvins"):
        return ea_kj_mol * _KJ_PER_MOL_TO_KELVIN
    return ea_kj_mol * _KJ_PER_MOL_TO_CAL_PER_MOL


def _a_to_mol_cm_s(a: float | None, units: ArrheniusAUnits | None) -> float | None:
    """Convert an A-factor to the CHEMKIN ``MOLES`` / cm basis.

    The reaction order (and thus the volume power) is inferred from the
    unit token itself, so the conversion is exact for the common forms.
    Unknown/None units pass through unchanged (best effort).
    """
    if a is None:
        return None
    if units is None:
        return a
    if units is ArrheniusAUnits.per_s:
        return a
    if units is ArrheniusAUnits.cm3_mol_s:
        return a
    if units is ArrheniusAUnits.cm3_molecule_s:
        return a * _AVOGADRO
    if units is ArrheniusAUnits.m3_mol_s:
        return a * 1.0e6
    if units is ArrheniusAUnits.cm6_mol2_s:
        return a
    if units is ArrheniusAUnits.cm6_molecule2_s:
        return a * _AVOGADRO * _AVOGADRO
    if units is ArrheniusAUnits.m6_mol2_s:
        return a * 1.0e12
    return a


# ---------------------------------------------------------------------------
# therm.dat
# ---------------------------------------------------------------------------


def _nasa_card(name: str, comp: Counter, selected) -> list[str]:
    """Build the 4-line NASA-7 thermo card for one species.

    Coefficient ordering follows the CHEMKIN convention: coefficients 1-7
    are the high-temperature interval, 8-14 the low-temperature interval.
    TCKDB's authoritative convention (``tckdb_schemas.thermo.ThermoNASABase``
    and the read serialization) is ``a1..a7`` = LOW-temperature, ``b1..b7``
    = HIGH-temperature. Hence CHEMKIN high block ← ``b*``; low block ← ``a*``.
    """
    nasa = selected.nasa
    t_low = nasa.t_low if nasa.t_low is not None else 300.0
    t_high = nasa.t_high if nasa.t_high is not None else 5000.0
    t_mid = nasa.t_mid if nasa.t_mid is not None else 1000.0

    # Element field: up to four "AA###" groups in cols 25-44.
    elem_field = ""
    for el in list(comp.items())[:4]:
        elem_field += f"{el[0][:2]:<2}{el[1]:>3d}"
    elem_field = elem_field.ljust(20)

    line1 = (
        f"{name:<18}"
        f"{'':6}"  # date cols 19-24
        f"{elem_field}"  # cols 25-44
        f"G"  # phase col 45
        f"{t_low:>10.3f}{t_high:>10.3f}{t_mid:>8.2f}"
        f"{'':6}"  # cols 74-79
        f"1"
    )

    high = [nasa.b1, nasa.b2, nasa.b3, nasa.b4, nasa.b5, nasa.b6, nasa.b7]
    low = [nasa.a1, nasa.a2, nasa.a3, nasa.a4, nasa.a5, nasa.a6, nasa.a7]
    coeffs = [c if c is not None else 0.0 for c in (high + low)]

    def fmt(values: list[float]) -> str:
        return "".join(f"{v:>15.8E}" for v in values)

    # Coefficients occupy cols 1-75; the card-index digit must land in col 80
    # (``line[79]``) with the intervening cols 76-79 blank. Cantera's
    # ``parse_nasa7_section`` groups the four card lines *only* when
    # ``len(line) >= 80 and line[79] == marker`` for markers 1/2/3/4 — a marker
    # at any earlier column leaves the card ungrouped and the species with "no
    # thermo data". Pad each coefficient line to exactly 80 columns.
    line2 = f"{fmt(coeffs[0:5]):<75}    2"
    line3 = f"{fmt(coeffs[5:10]):<75}    3"
    line4 = f"{fmt(coeffs[10:14]):<75}    4"
    return [line1, line2, line3, line4]


def _build_therm_dat(
    record_set: ExportRecordSet,
    names: dict[int, str],
    compositions: dict[int, Counter | None],
    gaps: list[ExportGap],
) -> str:
    lines = ["THERMO ALL", "   300.000  1000.000  5000.000"]
    for sr in record_set.species_records:
        se_id = sr.species_entry.id
        ref = sr.species_entry.public_ref
        comp = compositions.get(se_id)
        if comp is None:
            gaps.append(
                ExportGap(
                    kind="composition",
                    ref=ref,
                    detail=f"cannot parse SMILES for elemental composition: {sr.species.smiles!r}",
                )
            )
            continue
        # Require the NASA-7 child, not just the "nasa" model_kind: with
        # stored-column classification a (data-inconsistent) nasa7 row could
        # lack its ThermoNASA child, and _nasa_card would crash on it. Such a
        # record degrades to the gap message below, as it did pre-fix.
        nasa_thermo = next(
            (
                t
                for t in sr.thermos
                if t.model_kind == "nasa" and t.nasa is not None
            ),
            None,
        )
        if nasa_thermo is None:
            detail = (
                "selected thermo has no NASA-7 block"
                if sr.thermos
                else "no thermo record"
            )
            gaps.append(
                ExportGap(kind="thermo_nasa", ref=ref, detail=detail)
            )
            continue
        lines.append(
            f"! {names[se_id]}  SMILES={sr.species.smiles}  ref={sr.species.public_ref}"
        )
        lines.extend(_nasa_card(names[se_id], comp, nasa_thermo))
    lines.append("END")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# chem.inp
# ---------------------------------------------------------------------------


def _elements(compositions: dict[int, Counter | None]) -> list[str]:
    els: set[str] = set()
    for comp in compositions.values():
        if comp:
            els.update(comp.keys())
    ordered = [e for e in _ELEMENT_ORDER if e in els]
    ordered += sorted(e for e in els if e not in _ELEMENT_ORDER)
    return ordered


def _reaction_equation(
    rr: ReactionExportRecord,
    names_by_ref: dict[str, str],
    *,
    third_body: bool,
) -> str:
    def side(refs: list[str]) -> str:
        toks = [names_by_ref.get(r, r) for r in refs]
        joined = " + ".join(toks) if toks else "M"
        return joined

    arrow = "<=>" if rr.reaction.reversible else "=>"
    lhs = side(rr.reactant_refs)
    rhs = side(rr.product_refs)
    if third_body:
        lhs += " (+M)"
        rhs += " (+M)"
    return f"{lhs} {arrow} {rhs}"


def _kinetics_lines(
    rr: ReactionExportRecord,
    sk: SelectedKinetics,
    names_by_ref: dict[str, str],
    collider_names: dict[int, str],
    options: ChemkinOptions,
) -> list[str]:
    k = sk.kinetics
    is_falloff = k.model_kind in _FALLOFF_KINDS
    is_chebyshev = k.model_kind is KineticsModelKind.chebyshev
    has_third_body = bool(k.third_body_efficiencies) or is_falloff
    # A Chebyshev reaction is pressure-dependent and CHEMKIN requires it to be
    # written in the ``(+M)`` third-body form, exactly like falloff.
    eq = _reaction_equation(rr, names_by_ref, third_body=is_falloff or is_chebyshev)

    if is_chebyshev:
        # k(T,P) lives entirely in the CHEB block; the main-line Arrhenius is a
        # required CHEMKIN placeholder (RMG convention: 1.0 0 0), not a stored
        # rate. Emit the placeholder so the file re-parses without inventing a
        # rate constant.
        lines = [f"{eq}   1.0000E+00 0.000 0.0000"]
    else:
        a = _a_to_mol_cm_s(k.a, k.a_units)
        n = k.n if k.n is not None else 0.0
        ea = _convert_ea(k.ea_kj_mol, options.energy_units)
        a_str = f"{a:.4E}" if a is not None else "0.0"
        ea_str = f"{ea:.4f}" if ea is not None else "0.0"
        lines = [f"{eq}   {a_str} {n:.3f} {ea_str}"]

    if is_falloff and k.falloff is not None:
        fo = k.falloff
        low_a = _a_to_mol_cm_s(fo.low_a, fo.low_a_units or k.a_units)
        low_n = fo.low_n if fo.low_n is not None else 0.0
        low_ea = _convert_ea(fo.low_ea_kj_mol, options.energy_units) or 0.0
        low_a_str = f"{low_a:.4E}" if low_a is not None else "0.0"
        lines.append(f"    LOW / {low_a_str} {low_n:.3f} {low_ea:.4f} /")
        if k.model_kind is KineticsModelKind.troe and fo.troe_alpha is not None:
            troe = [fo.troe_alpha, fo.troe_t3, fo.troe_t1]
            if fo.troe_t2 is not None:
                troe.append(fo.troe_t2)
            lines.append(
                "    TROE / "
                + " ".join(f"{v:.4G}" for v in troe if v is not None)
                + " /"
            )
        elif k.model_kind is KineticsModelKind.sri and fo.sri_a is not None:
            sri = [fo.sri_a, fo.sri_b, fo.sri_c]
            if fo.sri_d is not None:
                sri.append(fo.sri_d)
            if fo.sri_e is not None:
                sri.append(fo.sri_e)
            lines.append(
                "    SRI / "
                + " ".join(f"{v:.4G}" for v in sri if v is not None)
                + " /"
            )

    if has_third_body and k.third_body_efficiencies:
        effs = " ".join(
            f"{collider_names.get(tb.collider_species_id, str(tb.collider_species_id))}/{tb.efficiency:.3G}/"
            for tb in k.third_body_efficiencies
        )
        if effs:
            lines.append(f"    {effs}")

    if k.model_kind is KineticsModelKind.plog and k.plog_entries:
        for pe in k.plog_entries:
            pa = _a_to_mol_cm_s(pe.a, pe.a_units or k.a_units)
            pn = pe.n if pe.n is not None else 0.0
            pea = _convert_ea(pe.ea_kj_mol, options.energy_units) or 0.0
            pa_str = f"{pa:.4E}" if pa is not None else "0.0"
            # pressure in atm for PLOG (CHEMKIN convention); stored in bar.
            p_atm = pe.pressure_bar / 1.01325
            lines.append(
                f"    PLOG / {p_atm:.4E} {pa_str} {pn:.3f} {pea:.4f} /"
            )

    if k.model_kind is KineticsModelKind.chebyshev and k.chebyshev is not None:
        cb = k.chebyshev
        if cb.tmin_k is not None and cb.tmax_k is not None:
            lines.append(f"    TCHEB / {cb.tmin_k:.2f} {cb.tmax_k:.2f} /")
        if cb.pmin_bar is not None and cb.pmax_bar is not None:
            lines.append(
                f"    PCHEB / {cb.pmin_bar / 1.01325:.4E} {cb.pmax_bar / 1.01325:.4E} /"
            )
        flat = _flatten_cheb(cb.coefficients)
        lines.append(
            f"    CHEB / {cb.n_temperature} {cb.n_pressure} "
            + " ".join(f"{v:.6E}" for v in flat)
            + " /"
        )
    return lines


def _flatten_cheb(coefficients) -> list[float]:
    if isinstance(coefficients, dict):
        coefficients = coefficients.get("coeffs", [])
    flat: list[float] = []
    if isinstance(coefficients, list):
        for row in coefficients:
            if isinstance(row, list):
                flat.extend(float(v) for v in row)
            else:
                flat.append(float(row))
    return flat


def _build_chem_inp(
    record_set: ExportRecordSet,
    names: dict[int, str],
    compositions: dict[int, Counter | None],
    collider_names: dict[int, str],
    options: ChemkinOptions,
    gaps: list[ExportGap],
) -> str:
    names_by_ref = {
        sr.species_entry.public_ref: names[sr.species_entry.id]
        for sr in record_set.species_records
    }
    lines: list[str] = []

    lines.append("ELEMENTS")
    lines.append(" ".join(_elements(compositions)) or " ")
    lines.append("END")
    lines.append("")

    lines.append("SPECIES")
    for sr in record_set.species_records:
        se_id = sr.species_entry.id
        lines.append(
            f"{names[se_id]}   ! SMILES={sr.species.smiles} "
            f"ref={sr.species.public_ref}"
        )
    lines.append("END")
    lines.append("")

    header = f"REACTIONS {_EA_UNIT_HEADERS.get(options.energy_units.lower(), 'CAL/MOLE')} MOLES"
    lines.append(header)

    # CHEMKIN requires that any two reactions sharing an equation each carry a
    # DUPLICATE keyword (otherwise a downstream interpreter rejects the file).
    # RMG ``DUPLICATE`` rates persist as separate reaction entries with the
    # same participants, so count emitted equations and mark the repeats.
    emitted = [rr for rr in record_set.reaction_records if rr.kinetics]

    def _dup_key(rr: ReactionExportRecord) -> tuple:
        # Cantera's ``Kinetics::checkDuplicates`` requires the DUPLICATE keyword
        # only when two reactions are genuinely *indistinguishable*: same
        # reactant multiset, same product multiset, in the SAME written
        # direction, and of the same kinetic type. It deliberately does NOT
        # require it for a reversible reaction stated forwards vs. its reverse
        # when the two are otherwise distinguishable (e.g. a plain Arrhenius
        # ``A<=>B`` alongside a Chebyshev ``B(+M)<=>A(+M)`` — different types,
        # different third-body form), and it *rejects* an over-declared marker
        # only via the reverse check. So we key on the ordered (sorted
        # reactants, sorted products, reversible) triple — same-direction
        # collisions only — which is exactly the set Cantera demands here.
        # Collapsing forward/reverse into one unordered key over-declares
        # (marking reversed reversibles that Cantera treats as distinct) and
        # inflates the DUPLICATE count. ``validate_chemkin_mechanism`` loads the
        # result through Cantera and is the ground-truth backstop for any case
        # this heuristic gets wrong.
        return (
            "dup",
            tuple(sorted(rr.reactant_refs)),
            tuple(sorted(rr.product_refs)),
            rr.reaction.reversible,
        )

    dup_counts = Counter(_dup_key(rr) for rr in emitted)
    for rr in emitted:
        sk = rr.kinetics[0]
        lines.extend(
            _kinetics_lines(rr, sk, names_by_ref, collider_names, options)
        )
        if dup_counts[_dup_key(rr)] > 1:
            lines.append("    DUPLICATE")
    lines.append("END")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# tran.dat
# ---------------------------------------------------------------------------


def _geometry_index(sr: SpeciesExportRecord, comp: Counter | None) -> int:
    if sr.is_linear is True:
        return 1
    if sr.is_linear is False:
        return 2
    natoms = sum(comp.values()) if comp else 0
    if natoms <= 1:
        return 0
    if natoms == 2:
        return 1
    return 2


def _build_tran_dat(
    record_set: ExportRecordSet,
    names: dict[int, str],
    compositions: dict[int, Counter | None],
    gaps: list[ExportGap],
) -> str:
    lines: list[str] = []
    for sr in record_set.species_records:
        se_id = sr.species_entry.id
        if not sr.transports:
            continue
        tr = sr.transports[0].transport
        if tr.sigma_angstrom is None or tr.epsilon_over_k_k is None:
            gaps.append(
                ExportGap(
                    kind="transport",
                    ref=sr.species_entry.public_ref,
                    detail="selected transport lacks the LJ sigma/epsilon pair",
                )
            )
            continue
        geom = _geometry_index(sr, compositions.get(se_id))
        dipole = tr.dipole_debye or 0.0
        polar = tr.polarizability_angstrom3 or 0.0
        zrot = tr.rotational_relaxation or 0.0
        lines.append(
            f"{names[se_id]:<16}{geom:>4d}"
            f"{tr.epsilon_over_k_k:>10.3f}{tr.sigma_angstrom:>10.3f}"
            f"{dipole:>10.3f}{polar:>10.3f}{zrot:>10.3f}"
            f"    ! SMILES={sr.species.smiles} ref={sr.species.public_ref}"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def serialize_chemkin(
    record_set: ExportRecordSet,
    *,
    options: ChemkinOptions | None = None,
    collider_names: dict[int, str] | None = None,
) -> ChemkinExport:
    """Serialize an export record set to CHEMKIN files.

    Returns the three (or two, without transport) mechanism files plus the
    combined gap list (the record-set's own selection gaps, followed by any
    serialization gaps: missing NASA block, unparseable structure, missing
    LJ pair). ``collider_names`` maps third-body ``species.id`` → mechanism
    name; unmapped colliders fall back to their id.
    """
    options = options or ChemkinOptions()
    collider_names = collider_names or {}

    compositions: dict[int, Counter | None] = {
        sr.species_entry.id: _composition(sr.species.smiles)
        for sr in record_set.species_records
    }
    names = _assign_names(
        record_set.species_records,
        naming_policy=options.naming_policy,
        compositions=compositions,
    )

    # Start from the record-set gaps (no qualifying thermo/kinetics), then
    # add serialization-specific gaps as files are built.
    gaps: list[ExportGap] = list(record_set.gaps)

    files: dict[str, str] = {
        "therm.dat": _build_therm_dat(record_set, names, compositions, gaps),
        "chem.inp": _build_chem_inp(
            record_set, names, compositions, collider_names, options, gaps
        ),
    }
    if options.include_transport:
        files["tran.dat"] = _build_tran_dat(
            record_set, names, compositions, gaps
        )

    return ChemkinExport(files=files, gaps=gaps)


def validate_chemkin_mechanism(files: dict[str, str]) -> None:
    """Prove an exported CHEMKIN mechanism is loadable, using Cantera's
    ``ck2yaml`` converter as the authoritative check.

    This is the ground-truth guard against handing a user a mechanism that a
    downstream interpreter (Cantera / Chemkin) would reject — most importantly
    one containing an *undeclared duplicate* reaction. It writes the exported
    files to a temporary directory, runs the strict (non-``permissive``)
    ``ck2yaml`` converter (which fails on unparsable thermo, unbalanced
    reactions, unknown species, bad kinetics blocks), and then *loads* the
    resulting YAML with :class:`cantera.Solution`.

    The second step is essential: ``ck2yaml.convert`` only translates the
    CHEMKIN text to YAML — it does **not** run Cantera's kinetics validation.
    Undeclared / unmatched ``DUPLICATE`` reactions are caught exclusively by
    ``Kinetics::checkDuplicates``, which fires when the phase is instantiated
    (the same check ``ck2yaml``'s CLI performs via its post-conversion
    ``Solution(out_name)`` pass). Without the ``Solution`` load an undeclared
    duplicate would slip through.

    :param files: the ``ChemkinExport.files`` mapping (``chem.inp`` required;
        ``therm.dat`` / ``tran.dat`` used when present).
    :raises ValueError: if the mechanism fails to convert or fails to load
        (Cantera diagnostics attached).
    :raises RuntimeError: if Cantera is not installed (it is an optional
        dependency, present in the test environment).
    """
    try:
        from cantera import Solution, ck2yaml
    except Exception as exc:  # pragma: no cover - cantera is a test/opt dep
        raise RuntimeError(
            "Cantera is required for CHEMKIN export validation "
            "(install with `mamba install -n tckdb_env -c conda-forge cantera`)."
        ) from exc

    import os
    import tempfile

    def _write(tmp: str, name: str) -> str | None:
        content = files.get(name)
        if not content:
            return None
        path = os.path.join(tmp, name)
        with open(path, "w") as fh:
            fh.write(content)
        return path

    with tempfile.TemporaryDirectory() as tmp:
        inp = _write(tmp, "chem.inp")
        if inp is None:
            raise ValueError("no chem.inp in the exported mechanism to validate")
        out_yaml = os.path.join(tmp, "mech.yaml")
        try:
            ck2yaml.convert(
                input_file=inp,
                thermo_file=_write(tmp, "therm.dat"),
                transport_file=_write(tmp, "tran.dat"),
                out_name=out_yaml,
                quiet=True,
                permissive=False,
            )
            # Instantiate the phase so Cantera runs its kinetics validation
            # (``checkDuplicates``, thermo/transport linkage). This is what
            # actually rejects an undeclared duplicate reaction.
            Solution(out_yaml)
        except Exception as exc:
            raise ValueError(
                f"Exported CHEMKIN mechanism failed Cantera validation: {exc}"
            ) from exc


__all__ = [
    "ChemkinExport",
    "ChemkinOptions",
    "serialize_chemkin",
    "validate_chemkin_mechanism",
]
