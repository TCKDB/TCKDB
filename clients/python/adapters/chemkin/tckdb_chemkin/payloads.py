"""Stage 4: build TCKDB upload payloads from the normalized AST + identities.

Emits plain JSON-serialisable dicts matching the backend upload schemas
(``KineticsUploadRequest``, ``ThermoUploadRequest``, ``TransportUploadRequest``)
so the generic ``tckdb-client`` can POST them directly. No backend imports —
the dict shapes are the contract, verified in tests.

NASA coefficient mapping (see module note): CHEMKIN coefficients 1-7 apply to
the *upper* temperature interval and 8-14 to the *lower* interval. Per the
importer task's field definition, TCKDB ``a1..a7`` carry the **high** range and
``b1..b7`` the **low** range. This single mapping lives here (``_nasa_payload``)
so it is a one-line flip if the backend semantics turn out to be the reverse
(the backend schema docstring currently labels ``a*`` low / ``b*`` high — see
the importer's final report note).
"""

from __future__ import annotations

from dataclasses import dataclass

from .ast import Mechanism, ThermoEntry, TransportEntry
from .forms import MODEL_CHEBYSHEV
from .identity import IdentityResolver, ResolvedSpecies
from .normalizer import (
    NormalizedMechanism,
    NormalizedReaction,
    normalize_mechanism,
)


@dataclass
class ImportConfig:
    """Per-import provenance / origin knobs (spec §8)."""

    scientific_origin: str = "experimental"
    mechanism_name: str | None = None
    mechanism_version: str | None = None
    literature: dict | None = None  # a LiteratureUploadRequest-shaped dict

    def workflow_tool_release(self) -> dict | None:
        if not self.mechanism_name:
            return None
        ref: dict = {"name": self.mechanism_name}
        if self.mechanism_version:
            ref["version"] = self.mechanism_version
        return ref


# ---------------------------------------------------------------------------
# Thermo
# ---------------------------------------------------------------------------


def _nasa_payload(entry: ThermoEntry) -> dict:
    """NASA-7 block.

    TCKDB convention (verified against ``tckdb_schemas.thermo.ThermoNASABase``
    and the read serialization ``scientific_read/thermo.py``): ``a1..a7`` are
    the LOW-temperature coefficients, ``b1..b7`` are the HIGH-temperature
    coefficients. In the CHEMKIN NASA-7 layout the HIGH-temperature block is
    listed first (``coeffs_high``) and the LOW-temperature block second
    (``coeffs_low``). Hence: coeffs_low -> a*, coeffs_high -> b*.
    """
    payload = {
        "t_low": entry.t_low,
        "t_mid": entry.t_common,
        "t_high": entry.t_high,
    }
    for i in range(7):
        payload[f"a{i + 1}"] = entry.coeffs_low[i]
        payload[f"b{i + 1}"] = entry.coeffs_high[i]
    return payload


def build_thermo_payload(
    entry: ThermoEntry,
    species: ResolvedSpecies,
    config: ImportConfig,
) -> dict:
    """Build a ``ThermoUploadRequest`` dict for one species."""
    payload: dict = {
        "species_entry": species.identity_payload(),
        "scientific_origin": config.scientific_origin,
        "nasa": _nasa_payload(entry),
        "tmin_k": entry.t_low,
        "tmax_k": entry.t_high,
    }
    if config.literature:
        payload["literature"] = config.literature
    wtr = config.workflow_tool_release()
    if wtr:
        payload["workflow_tool_release"] = wtr
    return payload


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def build_transport_payload(
    entry: TransportEntry,
    species: ResolvedSpecies,
    config: ImportConfig,
) -> dict:
    """Build a ``TransportUploadRequest`` dict for one species."""
    payload: dict = {
        "species_entry": species.identity_payload(),
        "scientific_origin": config.scientific_origin,
        "sigma_angstrom": entry.sigma_angstrom,
        "epsilon_over_k_k": entry.eps_over_k,
        "dipole_debye": entry.dipole_debye,
        "polarizability_angstrom3": entry.polarizability_angstrom3,
        "rotational_relaxation": entry.rot_relaxation,
    }
    if config.literature:
        payload["literature"] = config.literature
    wtr = config.workflow_tool_release()
    if wtr:
        payload["workflow_tool_release"] = wtr
    return payload


# ---------------------------------------------------------------------------
# Kinetics
# ---------------------------------------------------------------------------


def _participants(
    names: list[str], resolved: dict[str, ResolvedSpecies]
) -> list[dict]:
    return [{"species_entry": resolved[name].identity_payload()} for name in names]


def _falloff_payload(rxn: NormalizedReaction) -> dict:
    fo = rxn.falloff
    assert fo is not None
    payload: dict = {
        "low_a": fo.low_a,
        "low_a_units": fo.low_a_units,
        "low_n": fo.low_n,
        "low_ea_kj_mol": fo.low_ea_kj_mol,
    }
    for name in ("troe_alpha", "troe_t3", "troe_t1", "troe_t2",
                 "sri_a", "sri_b", "sri_c", "sri_d", "sri_e"):
        val = getattr(fo, name)
        if val is not None:
            payload[name] = val
    return payload


def build_kinetics_payload(
    rxn: NormalizedReaction,
    resolved: dict[str, ResolvedSpecies],
    config: ImportConfig,
) -> dict:
    """Build a ``KineticsUploadRequest`` dict for one normalized reaction.

    Covers every §6 form: modified/plain Arrhenius, a ``multi_arrhenius`` sum
    (a collapsed CHEMKIN ``DUPLICATE`` group), Lindemann/Troe/SRI falloff,
    third-body efficiencies, PLOG, and Chebyshev.
    """
    reaction = {
        "reversible": rxn.reversible,
        "reactants": _participants(rxn.reactant_names, resolved),
        "products": _participants(rxn.product_names, resolved),
    }
    payload: dict = {
        "reaction": reaction,
        "scientific_origin": config.scientific_origin,
        "model_kind": rxn.model_kind,
    }

    # A *simple* third-body reaction (bare ``+M`` collider, no falloff) has a
    # ``[M]`` term on the main line, so its main-line Arrhenius A-units are one
    # concentration order higher than the reactant molecularity. The backend
    # validator needs this flag to accept those units. Falloff reactions leave
    # it False: their main line is the high-pressure limit k∞ (marked by the
    # falloff block + model_kind), and the low-pressure k0 order is carried by
    # ``falloff.low_a_units``.
    if rxn.is_third_body and not rxn.is_falloff:
        payload["is_third_body"] = True

    # Top-level (high-pressure / plain) Arrhenius parameters.
    #
    # A Chebyshev reaction carries its full k(T,P) in the ``chebyshev`` block;
    # its CHEMKIN main line is a *required-but-ignored* placeholder that RMG
    # writes as a dummy ``1.0 0 0``. Persisting that placeholder as a real
    # Arrhenius rate would be a fabricated rate constant, so it is dropped
    # here (the exporter re-synthesises the placeholder main line on the way
    # out). Every other form keeps its main-line rate.
    if rxn.model_kind != MODEL_CHEBYSHEV:
        if rxn.a is not None:
            payload["a"] = rxn.a
            payload["a_units"] = rxn.a_units
        if rxn.n is not None:
            payload["n"] = rxn.n
        if rxn.reported_ea is not None:
            payload["reported_ea"] = rxn.reported_ea
            payload["reported_ea_units"] = rxn.reported_ea_units

    if rxn.falloff is not None:
        payload["falloff"] = _falloff_payload(rxn)

    if rxn.efficiencies:
        payload["third_body_efficiencies"] = [
            {
                "collider": resolved[name].identity_payload(),
                "efficiency": eff,
            }
            for name, eff in rxn.efficiencies.items()
        ]

    if rxn.plog:
        payload["plog_entries"] = [
            {
                "entry_index": p.entry_index,
                "pressure_bar": p.pressure_bar,
                "a": p.a,
                "a_units": p.a_units,
                "n": p.n,
                "ea_kj_mol": p.ea_kj_mol,
            }
            for p in rxn.plog
        ]

    # A collapsed CHEMKIN ``DUPLICATE`` group -> one ``multi_arrhenius`` rate
    # whose N summed modified-Arrhenius terms live here (scalar a/n/Ea stay
    # unset, per the backend upload contract). Each term's ``a_units`` shares
    # the reaction's main-line molecularity (validated backend-side, PR #43).
    if rxn.arrhenius_entries:
        payload["arrhenius_entries"] = [
            {
                "entry_index": e.entry_index,
                "a": e.a,
                "a_units": e.a_units,
                "n": e.n,
                "reported_ea": e.reported_ea,
                "reported_ea_units": e.reported_ea_units,
            }
            for e in rxn.arrhenius_entries
        ]

    if rxn.chebyshev is not None:
        c = rxn.chebyshev
        payload["chebyshev"] = {
            "n_temperature": c.n_temperature,
            "n_pressure": c.n_pressure,
            "tmin_k": c.tmin_k,
            "tmax_k": c.tmax_k,
            "pmin_bar": c.pmin_bar,
            "pmax_bar": c.pmax_bar,
            "coefficients": c.coefficients,
        }

    if config.literature:
        payload["literature"] = config.literature
    wtr = config.workflow_tool_release()
    if wtr:
        payload["workflow_tool_release"] = wtr

    notes: list[str] = []
    if rxn.arrhenius_entries:
        notes.append(
            "CHEMKIN DUPLICATE group collapsed to a multi_arrhenius "
            f"sum of {len(rxn.arrhenius_entries)} modified-Arrhenius terms."
        )
    elif rxn.duplicate:
        notes.append("CHEMKIN DUPLICATE rate (append-only result row).")
    if rxn.is_third_body and not rxn.is_falloff:
        notes.append("Third-body (+M) reaction imported from CHEMKIN.")
    if rxn.falloff_collider and rxn.falloff_collider.upper() != "M":
        notes.append(
            f"Falloff third body is a specific collider: {rxn.falloff_collider}."
        )
    if notes:
        payload["note"] = " ".join(notes)

    return payload


# ---------------------------------------------------------------------------
# Whole-mechanism assembly
# ---------------------------------------------------------------------------


@dataclass
class BuiltPayloads:
    """All payloads produced from a mechanism, plus collected warnings."""

    thermo: list[dict]
    transport: list[dict]
    kinetics: list[dict]
    warnings: list[str]

    def counts(self) -> dict[str, int]:
        return {
            "thermo": len(self.thermo),
            "transport": len(self.transport),
            "kinetics": len(self.kinetics),
        }


def build_all_payloads(
    mech: Mechanism,
    resolver: IdentityResolver,
    config: ImportConfig,
    normalized: NormalizedMechanism | None = None,
) -> BuiltPayloads:
    """Build thermo + transport + kinetics payloads for a whole mechanism.

    Identity is resolved up front (fail-loud, all-or-nothing). A CHEMKIN
    ``DUPLICATE`` group of summable Arrhenius rates is collapsed (in the
    normalizer) into a single ``multi_arrhenius`` kinetics payload carrying one
    term per line.
    """
    resolved = resolver.resolve_mechanism(mech)
    if normalized is None:
        normalized = normalize_mechanism(mech)

    warnings: list[str] = []

    thermo_payloads: list[dict] = []
    for name, entry in mech.thermo.items():
        if name not in resolved:
            continue
        thermo_payloads.append(
            build_thermo_payload(entry, resolved[name], config)
        )

    transport_payloads: list[dict] = []
    for name, entry in mech.transport.items():
        if name not in resolved:
            continue
        transport_payloads.append(
            build_transport_payload(entry, resolved[name], config)
        )

    kinetics_payloads: list[dict] = []
    for rxn in normalized.reactions:
        warnings.extend(rxn.warnings)
        # ``normalized.reactions`` has already collapsed each CHEMKIN
        # ``DUPLICATE`` group of summable (modified-)Arrhenius rates into one
        # ``multi_arrhenius`` reaction (spec §6, DR-0036), so this is one
        # payload per logical reaction.
        kinetics_payloads.append(build_kinetics_payload(rxn, resolved, config))

    return BuiltPayloads(
        thermo=thermo_payloads,
        transport=transport_payloads,
        kinetics=kinetics_payloads,
        warnings=warnings,
    )
