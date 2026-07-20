"""Stage 2: normalize native CHEMKIN values into TCKDB-ready form.

Resolves the REACTIONS-header units, converts activation energies and
pressures, selects molecularity-aware Arrhenius A-units, and tags every
reaction with its TCKDB ``KineticsModelKind``. The output
(:class:`NormalizedReaction`) is *payload-ready*: the builder in
``payloads.py`` assembles dicts from it without any further unit math.

Dependency-light: no RDKit, no TCKDB imports (uses the string constants in
``forms.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ast import ChebyshevBlock, Mechanism, Reaction
from .forms import (
    EA_TOKEN_TO_KJ_MOL,
    EA_TOKEN_TO_TCKDB_UNIT,
    MODEL_ARRHENIUS,
    MODEL_CHEBYSHEV,
    MODEL_LINDEMANN,
    MODEL_MODIFIED_ARRHENIUS,
    MODEL_MULTI_ARRHENIUS,
    MODEL_PLOG,
    MODEL_SRI,
    MODEL_TROE,
    a_units_for,
    atm_to_bar,
)


class NormalizationError(ValueError):
    """Raised when a reaction cannot be normalized (unknown units, etc.)."""


@dataclass
class NormalizedFalloff:
    low_a: float
    low_a_units: str
    low_n: float
    low_ea_kj_mol: float
    troe_alpha: float | None = None
    troe_t3: float | None = None
    troe_t1: float | None = None
    troe_t2: float | None = None
    sri_a: float | None = None
    sri_b: float | None = None
    sri_c: float | None = None
    sri_d: float | None = None
    sri_e: float | None = None


@dataclass
class NormalizedPlogEntry:
    entry_index: int
    pressure_bar: float
    a: float
    a_units: str
    n: float
    ea_kj_mol: float


@dataclass
class NormalizedMultiArrheniusEntry:
    """One modified-Arrhenius term of a Chemkin ``DUPLICATE`` (multi_arrhenius)
    channel. The channel rate is the *sum* of these terms; there is no pressure
    (unlike a PLOG entry). ``a_units`` shares the reaction's main-line
    molecularity, and ``reported_ea``/``reported_ea_units`` carry the native
    (lossless) Ea unit exactly like the top-level Arrhenius fields."""

    entry_index: int
    a: float
    a_units: str
    n: float
    reported_ea: float
    reported_ea_units: str


@dataclass
class NormalizedChebyshev:
    n_temperature: int
    n_pressure: int
    tmin_k: float
    tmax_k: float
    pmin_bar: float
    pmax_bar: float
    coefficients: list[list[float]]


@dataclass
class NormalizedReaction:
    """A reaction with units resolved and its TCKDB model_kind tagged."""

    reactant_names: list[str]
    product_names: list[str]
    reversible: bool
    model_kind: str

    a: float | None = None
    a_units: str | None = None
    n: float | None = None
    # Ea reported in its native (lossless) TCKDB unit when one exists, else
    # pre-converted to kJ/mol (KELVIN/EVOLTS have no ActivationEnergyUnits home).
    reported_ea: float | None = None
    reported_ea_units: str | None = None

    is_third_body: bool = False
    is_falloff: bool = False
    falloff_collider: str | None = None

    falloff: NormalizedFalloff | None = None
    efficiencies: dict[str, float] = field(default_factory=dict)
    plog: list[NormalizedPlogEntry] = field(default_factory=list)
    arrhenius_entries: list[NormalizedMultiArrheniusEntry] = field(
        default_factory=list
    )
    chebyshev: NormalizedChebyshev | None = None

    duplicate: bool = False
    has_explicit_reverse: bool = False
    unsupported_aux: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    line_no: int | None = None


@dataclass
class NormalizedMechanism:
    reactions: list[NormalizedReaction]
    mechanism: Mechanism  # keep the raw AST for thermo/transport/identity


def _ea_to_kj(value: float, ea_token: str) -> float:
    token = ea_token.upper()
    if token not in EA_TOKEN_TO_KJ_MOL:
        raise NormalizationError(f"Unknown Ea units token: {ea_token!r}")
    return value * EA_TOKEN_TO_KJ_MOL[token]


def _reported_ea(value: float, ea_token: str) -> tuple[float, str]:
    """Return ``(reported_ea, reported_ea_units)``.

    Passes the native magnitude/units through when the CHEMKIN token has a
    TCKDB ActivationEnergyUnits home; otherwise converts to kJ/mol.
    """
    token = ea_token.upper()
    if token in EA_TOKEN_TO_TCKDB_UNIT:
        return value, EA_TOKEN_TO_TCKDB_UNIT[token]
    return _ea_to_kj(value, token), "kj_mol"


def _tag_model_kind(rxn: Reaction) -> str:
    if rxn.chebyshev is not None:
        return MODEL_CHEBYSHEV
    if rxn.plog:
        return MODEL_PLOG
    if rxn.is_falloff:
        if rxn.troe is not None:
            return MODEL_TROE
        if rxn.sri is not None:
            return MODEL_SRI
        return MODEL_LINDEMANN
    # Plain or simple third-body: arrhenius vs modified by the n exponent.
    return MODEL_ARRHENIUS if rxn.n == 0.0 else MODEL_MODIFIED_ARRHENIUS


def _normalize_falloff(rxn: Reaction, a_basis: str, ea_token: str) -> NormalizedFalloff:
    if rxn.low is None:
        raise NormalizationError(
            f"Falloff reaction (line {rxn.line_no}) has no LOW/ block."
        )
    low_a, low_n, low_ea = rxn.low
    # k0 is one concentration order higher than the high-pressure limit.
    low_order = rxn.molecularity + 1
    low_units = a_units_for(a_basis, low_order)
    fo = NormalizedFalloff(
        low_a=low_a,
        low_a_units=low_units,
        low_n=low_n,
        low_ea_kj_mol=_ea_to_kj(low_ea, ea_token),
    )
    if rxn.troe is not None:
        t = rxn.troe
        fo.troe_alpha = t[0]
        fo.troe_t3 = t[1] if len(t) > 1 else None
        fo.troe_t1 = t[2] if len(t) > 2 else None
        fo.troe_t2 = t[3] if len(t) > 3 else None
    if rxn.sri is not None:
        s = rxn.sri
        fo.sri_a = s[0] if len(s) > 0 else None
        fo.sri_b = s[1] if len(s) > 1 else None
        fo.sri_c = s[2] if len(s) > 2 else None
        fo.sri_d = s[3] if len(s) > 3 else None
        fo.sri_e = s[4] if len(s) > 4 else None
    return fo


def _normalize_chebyshev(cheb: ChebyshevBlock) -> NormalizedChebyshev:
    return NormalizedChebyshev(
        n_temperature=cheb.n_temperature,
        n_pressure=cheb.n_pressure,
        tmin_k=cheb.tmin,
        tmax_k=cheb.tmax,
        pmin_bar=atm_to_bar(cheb.pmin_atm),
        pmax_bar=atm_to_bar(cheb.pmax_atm),
        coefficients=cheb.coefficients,
    )


def normalize_reaction(
    rxn: Reaction, a_basis: str, ea_token: str
) -> NormalizedReaction:
    """Normalize a single reaction against the header units."""
    model_kind = _tag_model_kind(rxn)
    # A-unit order = concentration order of the rate constant (spec §7).
    #   * plain reaction: reactant molecularity
    #   * simple third body (bare +M): molecularity + 1 (the [M] term adds an
    #     order, so O+O+M is cm6_mol2_s, not cm3_mol_s)
    #   * falloff k-inf: molecularity (M excluded); k0 handled separately (+1)
    order = rxn.molecularity
    if rxn.is_third_body and not rxn.is_falloff:
        order += 1

    out = NormalizedReaction(
        reactant_names=rxn.reactant_names,
        product_names=rxn.product_names,
        reversible=rxn.reversible,
        model_kind=model_kind,
        is_third_body=rxn.is_third_body,
        is_falloff=rxn.is_falloff,
        falloff_collider=rxn.falloff_collider,
        efficiencies=dict(rxn.efficiencies),
        duplicate=rxn.duplicate,
        unsupported_aux=list(rxn.unsupported_aux),
        line_no=rxn.line_no,
    )

    # High-pressure / plain Arrhenius on the main line.
    out.a = rxn.a
    out.a_units = a_units_for(a_basis, order)
    out.n = rxn.n
    out.reported_ea, out.reported_ea_units = _reported_ea(rxn.ea, ea_token)

    # A ``(+M)`` reaction that carries a Chebyshev block is a Chebyshev
    # pressure-dependent reaction, not a Lindemann/Troe falloff: RMG writes
    # ``R(+M)<=>P(+M)`` with a dummy ``1.0 0 0`` main line followed by
    # TCHEB/PCHEB/CHEB and no LOW/ block. Only treat ``(+M)`` as falloff when
    # there is no Chebyshev block.
    if rxn.is_falloff and rxn.chebyshev is None:
        out.falloff = _normalize_falloff(rxn, a_basis, ea_token)

    if rxn.plog:
        for i, p in enumerate(rxn.plog, start=1):
            out.plog.append(
                NormalizedPlogEntry(
                    entry_index=i,
                    pressure_bar=atm_to_bar(p.pressure_atm),
                    a=p.a,
                    a_units=a_units_for(a_basis, order),
                    n=p.n,
                    ea_kj_mol=_ea_to_kj(p.ea, ea_token),
                )
            )

    if rxn.chebyshev is not None:
        out.chebyshev = _normalize_chebyshev(rxn.chebyshev)

    if rxn.rev is not None:
        out.has_explicit_reverse = True
        out.warnings.append(
            f"Explicit REV/ reverse-rate on reaction (line {rxn.line_no}) "
            "dropped; TCKDB has no reverse-linkage field yet (spec §6.7)."
        )

    for aux in rxn.unsupported_aux:
        out.warnings.append(
            f"Unsupported aux construct on reaction (line {rxn.line_no}): "
            f"{aux!r} — skipped (spec §2 out-of-scope)."
        )

    return out


def _is_collapsible_duplicate(rxn: NormalizedReaction) -> bool:
    """A DUPLICATE reaction that is a plain / simple-third-body (modified-)
    Arrhenius rate — i.e. exactly a modified-Arrhenius term that can be summed
    into a ``multi_arrhenius`` channel (DR-0036). Falloff / PLOG / Chebyshev
    duplicates are *not* collapsed (they are not a sum-of-Arrhenius form) and
    keep their existing one-payload-per-line handling.
    """
    return (
        rxn.duplicate
        and rxn.model_kind in (MODEL_ARRHENIUS, MODEL_MODIFIED_ARRHENIUS)
        and rxn.falloff is None
        and not rxn.plog
        and rxn.chebyshev is None
    )


def _duplicate_key(rxn: NormalizedReaction) -> tuple:
    """Identity of a DUPLICATE group at the CHEMKIN-name level. Repeated
    DUPLICATE lines carry the *same* equation text, so the reactant/product
    name lists plus reversibility identify the group without needing resolved
    structures."""
    return (
        tuple(rxn.reactant_names),
        tuple(rxn.product_names),
        rxn.reversible,
    )


def _members_agree_third_body(members: list[NormalizedReaction]) -> bool:
    """Whether every member of a DUPLICATE group shares the SAME third-body
    context — i.e. the same ``is_third_body`` flag, the same ``falloff_collider``
    label, and an *identical* collider-efficiency mapping.

    Summing the members into one ``multi_arrhenius`` rate is only correct when
    they describe the same reaction under the same bath-gas conditions. CHEMKIN
    permits duplicate ``+M`` lines to carry *different* per-line efficiency
    lists (a genuinely different effective [M]); those must NOT be fused, or the
    non-first lines' efficiencies would be silently discarded and the stored
    rate would be scientifically wrong.
    """
    first = members[0]
    for m in members[1:]:
        if m.is_third_body != first.is_third_body:
            return False
        if m.falloff_collider != first.falloff_collider:
            return False
        if m.efficiencies != first.efficiencies:
            return False
    return True


def _merge_multi_arrhenius(
    members: list[NormalizedReaction],
) -> NormalizedReaction:
    """Collapse a DUPLICATE group of (modified-)Arrhenius rates into a single
    ``multi_arrhenius`` reaction carrying one term per member (DR-0036).

    The scalar main-line ``a``/``n``/``reported_ea`` are left unset — the
    coefficients live in ``arrhenius_entries`` — matching the backend
    ``multi_arrhenius`` upload contract.
    """
    first = members[0]
    entries = [
        NormalizedMultiArrheniusEntry(
            entry_index=i,
            a=m.a,
            a_units=m.a_units,
            n=m.n,
            reported_ea=m.reported_ea,
            reported_ea_units=m.reported_ea_units,
        )
        for i, m in enumerate(members, start=1)
    ]
    merged = NormalizedReaction(
        reactant_names=list(first.reactant_names),
        product_names=list(first.product_names),
        reversible=first.reversible,
        model_kind=MODEL_MULTI_ARRHENIUS,
        # Scalar main-line rate intentionally unset for multi_arrhenius.
        a=None,
        a_units=None,
        n=None,
        reported_ea=None,
        reported_ea_units=None,
        is_third_body=first.is_third_body,
        is_falloff=False,
        falloff_collider=first.falloff_collider,
        efficiencies=dict(first.efficiencies),
        arrhenius_entries=entries,
        duplicate=True,
        line_no=first.line_no,
    )
    for m in members:
        merged.warnings.extend(m.warnings)
    return merged


def _collapse_duplicates(
    reactions: list[NormalizedReaction],
) -> list[NormalizedReaction]:
    """Collapse each DUPLICATE group of summable Arrhenius rates into one
    ``multi_arrhenius`` reaction, preserving first-occurrence order. Groups of
    a single line (an unmatched ``DUP``) and non-collapsible duplicates pass
    through unchanged."""
    groups: dict[tuple, list[NormalizedReaction]] = {}
    order: list[tuple[str, object]] = []
    for rxn in reactions:
        if _is_collapsible_duplicate(rxn):
            key = _duplicate_key(rxn)
            if key not in groups:
                groups[key] = []
                order.append(("group", key))
            groups[key].append(rxn)
        else:
            order.append(("single", rxn))

    out: list[NormalizedReaction] = []
    for kind, val in order:
        if kind == "single":
            out.append(val)  # type: ignore[arg-type]
            continue
        members = groups[val]  # type: ignore[index]
        if len(members) < 2:
            # A lone DUP with no matching partner is not a valid sum; keep the
            # single line as its own (modified-)Arrhenius payload.
            out.extend(members)
            continue
        if not _members_agree_third_body(members):
            # Same reactants/products/reversibility but a MISMATCHED third-body
            # context (different is_third_body / collider / efficiency mapping).
            # Summing would silently drop the differing bath-gas information, so
            # pass the members through unchanged (separate scalar DUP rows, as
            # before the multi_arrhenius collapse existed) and flag why.
            lines = ", ".join(
                str(m.line_no) for m in members if m.line_no is not None
            )
            for m in members:
                m.warnings.append(
                    "CHEMKIN DUPLICATE group NOT collapsed to multi_arrhenius: "
                    "members disagree on third-body context (is_third_body / "
                    "collider / efficiencies differ across the duplicate lines"
                    + (f" {lines}" if lines else "")
                    + "); kept as separate rates to avoid discarding per-line "
                    "efficiencies."
                )
            out.extend(members)
            continue
        out.append(_merge_multi_arrhenius(members))
    return out


def normalize_mechanism(mech: Mechanism) -> NormalizedMechanism:
    """Normalize every reaction in the mechanism against its header units.

    A Chemkin ``DUPLICATE`` group of summable (modified-)Arrhenius rates is
    collapsed into a single ``multi_arrhenius`` reaction (DR-0036); every other
    reaction passes through one-to-one.
    """
    normalized = [
        normalize_reaction(rxn, mech.a_conc_basis, mech.ea_units)
        for rxn in mech.reactions
    ]
    return NormalizedMechanism(
        reactions=_collapse_duplicates(normalized), mechanism=mech
    )
