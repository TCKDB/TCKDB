"""Numerical grid comparison of a CHEMKIN mechanism against TCKDB's export of it.

Both mechanisms are loaded by the pinned Cantera (``ck2yaml`` then
``Solution``) and evaluated on one declared grid of temperatures, pressures
and one composition. Per species the reduced thermo functions Cp/R, H/RT and
S/R are compared under the Phase A printed-precision bound
(``tests/services/scientific_read/test_thermo_phase_a_numerical.py``); per
reaction the forward rate constant and the forward rate of progress are
compared under a bound derived from the printed precision of every rate
parameter the export writes. Unsupported forms and export gaps are listed in
the summary, never dropped.

Bound derivation for a rate constant
------------------------------------

The export prints each stored parameter ``p`` on a decimal grid of unit
``u(p)`` (``chemkin_serialize._kinetics_lines``): the Arrhenius ``A`` with
five significant digits (``.4E``), the activation energy with four decimals
in cal/mol (``.4f``), the temperature exponent with three decimals (``.3f``),
Troe coefficients with four significant digits (``.4G``), collider
efficiencies with three (``.3G``), Chebyshev coefficients with seven
(``.6E``). The stored value is the original file's printed value, parsed,
after the importer's unit conversion. Printing rounds, so the exported value
differs from the stored one by at most half a unit of the export's grid, and
the stored value differs from the original by the unit-conversion chain's
floating-point error ``eps(p)`` (kcal/mol -> kJ/mol -> cal/mol for ``Ea``;
identity for ``A`` and ``n``). Hence, for every parameter,

    |p_out - p_in| <= u(p)/2 + |p_in| * eps(p).

Where the export's grid is at least as fine as the original's and the chain
is the identity (``n``, Troe coefficients, efficiencies, Chebyshev
coefficients and ranges, NASA temperature limits), correctly-rounded
formatting reproduces the original digits exactly. Those parameters are
checked for equality (to a 1e-12 floating-point allowance) and contribute
nothing to the bound: a dropped efficiency or an altered Troe coefficient is
a hard failure, not a tolerance question. The parameters that can move are
``A`` (five printed digits against the original's seven) and ``Ea`` (through
the unit chain).

For a modified Arrhenius channel ``k = A T^n exp(-Ea/RT)``,

    |d ln k| <= dA/A + dEa/(R T),   dA = 10^(floor(log10 A) - 4) / 2,
                                    dEa = (10^-4 cal/mol)/2 * 4184 J/kmol per cal/mol
                                          + |Ea| * eps_Ea,

with ``R`` in J/kmol/K as Cantera stores ``Ea``. ``dA/A`` is unit-free
because every unit rescaling the export or Cantera applies to ``A`` is a
power of ten, which leaves the mantissa alone.

For a falloff reaction the two Arrhenius channels enter through Cantera's
falloff function, ``k = k_inf * Pr/(1+Pr) * F``. The first-order propagation
is ``|d ln k| <= |w_inf| d_inf + |w_0| d_0`` with the sensitivities
``w = d ln k / d ln k_channel`` measured on the original rate by a central
finite difference of relative step ``h = 1e-4`` (Cantera evaluates the
perturbed ``TroeRate``; nothing is re-implemented). The step's own error
and the second-order remainder are covered by adding ``h`` to each weight
and ``(d_inf + d_0)^2`` to the total.

A Chebyshev rate has every parameter on the exact-grid list, so its bound is
the floating-point allowance alone.

For a DUPLICATE sum ``k = sum_i k_i`` the absolute bound is the sum of the
members' absolute bounds, i.e. relative bound
``sum_i k_i (exp(d ln k_i) - 1) / sum_i k_i``. Every relative bound gains the
floating-point allowance ``1e-10`` (the Phase A value). The forward rate of
progress is compared under the same relative bound: at one state the
concentration product and the effective third-body concentration are
identical on both sides once the efficiencies have been checked equal.

No blanket percentage is used anywhere.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cantera as ct
import numpy as np
from cantera import ck2yaml
from rdkit import Chem

from app.services.scientific_read.chemkin_serialize import _KJ_PER_MOL_TO_CAL_PER_MOL
from tests.services.scientific_read.test_thermo_phase_a_numerical import (
    print_error_bound,
    reduced,
)

SUMMARY_SCHEMA = "tckdb.chemkin_numerical_grid.v1"
SUMMARY_PATH_ENV = "TCKDB_CHEMKIN_GRID_SUMMARY_PATH"
PINNED_CANTERA = "3.2.0"

GRID_TEMPERATURES_K: tuple[float, ...] = (300.0, 500.0, 800.0, 1000.0, 1500.0, 2000.0)
GRID_PRESSURES_ATM: tuple[float, ...] = (0.1, 1.0, 10.0)
COMPOSITION = "equimolar over every species of the mechanism"

#: Floating-point allowance: relative on rate constants, absolute on the
#: reduced thermo functions (the Phase A value).
IEEE_ALLOWANCE = 1e-10
#: Two doubles printed from one stored value on the same grid are the same
#: number; this is the allowance for the unit chain's last-bit noise.
EXACT_PARAMETER_RTOL = 1e-12

#: Cantera reaction types this module has a bound derivation for.
SUPPORTED_FORMS = frozenset(
    {"Arrhenius", "three-body-Arrhenius", "falloff-Troe", "falloff-Lindemann", "Chebyshev"}
)

# Printed precision the exporter declares (chemkin_serialize._kinetics_lines).
EXPORT_A_SIGNIFICANT_DIGITS = 5  # f"{a:.4E}"
EXPORT_EA_DECIMALS_CAL_PER_MOL = 4  # f"{ea:.4f}"
EXPORT_T_LOW_HIGH_DECIMALS = 3  # _nasa_card: f"{t:>10.3f}"
EXPORT_T_MID_DECIMALS = 2  # _nasa_card: f"{t_mid:>8.2f}"

#: The importer's kcal/mol -> kJ/mol factor (tckdb_chemkin.forms) and the
#: exporter's kJ/mol -> cal/mol factor do not compose to exactly 1000.
_IMPORTER_KCAL_TO_KJ = 4.184
EA_CHAIN_RELATIVE_ERROR = abs(_IMPORTER_KCAL_TO_KJ * _KJ_PER_MOL_TO_CAL_PER_MOL / 1000.0 - 1.0)
#: Cantera's ``cal/mol`` -> J/kmol factor.
_CAL_PER_MOL_TO_J_PER_KMOL = 4184.0
_FALLOFF_FD_STEP = 1e-4


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_solution(files: dict[str, str]) -> ct.Solution:
    """``ck2yaml`` (strict) then ``Solution``, exactly as the export validator does."""
    with tempfile.TemporaryDirectory() as tmp:
        paths: dict[str, str] = {}
        for name in ("chem.inp", "therm.dat"):
            content = files.get(name)
            if content:
                path = os.path.join(tmp, name)
                Path(path).write_text(content)
                paths[name] = path
        out = os.path.join(tmp, "mech.yaml")
        ck2yaml.convert(
            input_file=paths["chem.inp"],
            thermo_file=paths.get("therm.dat"),
            transport_file=None,
            out_name=out,
            quiet=True,
            permissive=False,
        )
        return ct.Solution(out)


def canonical(smiles: str) -> str:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"unparseable SMILES {smiles!r}")
    return Chem.MolToSmiles(mol)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _side(participants: dict[str, float], smiles_of: dict[str, str]) -> tuple[tuple[str, float], ...]:
    return tuple(sorted((smiles_of[name], float(coef)) for name, coef in participants.items()))


def _third_body_key(rxn, smiles_of: dict[str, str]) -> str | None:
    tb = getattr(rxn, "third_body", None)
    if tb is None:
        return None
    return "M" if tb.name == "M" else smiles_of[tb.name]


def reaction_key(rxn, smiles_of: dict[str, str]) -> tuple:
    return (
        _side(rxn.reactants, smiles_of),
        _side(rxn.products, smiles_of),
        bool(rxn.reversible),
        str(rxn.reaction_type),
        _third_body_key(rxn, smiles_of),
    )


def key_label(key: tuple) -> str:
    reactants, products, reversible, form, tb = key

    def side(items):
        return " + ".join(f"{coef:g} {s}" if coef != 1 else s for s, coef in items)

    arrow = "<=>" if reversible else "=>"
    body = f"{side(reactants)} {arrow} {side(products)}"
    if tb is not None:
        body += f" [{tb}]"
    return f"{body} | {form}"


def group_reactions(gas: ct.Solution, smiles_of: dict[str, str]) -> dict[tuple, list[int]]:
    groups: dict[tuple, list[int]] = {}
    for index, rxn in enumerate(gas.reactions()):
        groups.setdefault(reaction_key(rxn, smiles_of), []).append(index)
    return groups


def _member_signature(rxn) -> tuple[float, ...]:
    rate = rxn.rate
    if isinstance(rate, ct.FalloffRate):
        return (rate.high_rate.pre_exponential_factor, rate.low_rate.pre_exponential_factor)
    if isinstance(rate, ct.ChebyshevRate):
        return (float(rate.data[0, 0]),)
    return (rate.pre_exponential_factor, rate.temperature_exponent, rate.activation_energy)


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def half_unit_relative(value: float, significant_digits: int) -> float:
    """Half a unit in the last of ``significant_digits`` printed digits, relative to ``value``.

    The exponent is read from the value *as the export prints it* (the same
    ``E`` format), not recomputed from ``log10``: a mantissa that rounds up to
    ``10`` at the printed precision is printed as ``1.0000E+(e+1)``, and the
    unit of its last digit is ten times larger. Reading the printed text is
    exact; a floating-point ``log10`` at that edge is not.
    """
    if value == 0:
        return 0.0
    printed = f"{abs(value):.{significant_digits - 1}E}"
    exponent = int(printed.split("E")[1])
    return 0.5 * 10.0 ** (exponent - (significant_digits - 1)) / abs(value)


def arrhenius_log_bound(rate: ct.ArrheniusRate, temperature: float) -> float:
    """``|d ln k|`` for one modified-Arrhenius channel at ``temperature`` (module docstring)."""
    d_a = half_unit_relative(rate.pre_exponential_factor, EXPORT_A_SIGNIFICANT_DIGITS)
    d_ea = (
        0.5 * 10.0 ** (-EXPORT_EA_DECIMALS_CAL_PER_MOL) * _CAL_PER_MOL_TO_J_PER_KMOL
        + abs(rate.activation_energy) * EA_CHAIN_RELATIVE_ERROR
    )
    return d_a + d_ea / (ct.gas_constant * temperature)


def _perturbed_falloff(rate, *, high_factor: float = 1.0, low_factor: float = 1.0):
    high, low = rate.high_rate, rate.low_rate
    return type(rate)(
        low=ct.ArrheniusRate(
            low.pre_exponential_factor * low_factor, low.temperature_exponent, low.activation_energy
        ),
        high=ct.ArrheniusRate(
            high.pre_exponential_factor * high_factor, high.temperature_exponent, high.activation_energy
        ),
        falloff_coeffs=rate.falloff_coeffs,
    )


def falloff_log_bound(rate, temperature: float, m_eff: float) -> float:
    """``|d ln k|`` for a falloff reaction (module docstring), sensitivities by finite difference."""
    h = _FALLOFF_FD_STEP
    base = rate(temperature, m_eff)

    def weight(**factors) -> float:
        up = _perturbed_falloff(rate, **dict.fromkeys(factors, 1.0 + h))(temperature, m_eff)
        down = _perturbed_falloff(rate, **dict.fromkeys(factors, 1.0 - h))(temperature, m_eff)
        return (math.log(up / base) - math.log(down / base)) / (math.log1p(h) - math.log1p(-h))

    w_high = weight(high_factor=True)
    w_low = weight(low_factor=True)
    d_high = arrhenius_log_bound(rate.high_rate, temperature)
    d_low = arrhenius_log_bound(rate.low_rate, temperature)
    return (abs(w_high) + h) * d_high + (abs(w_low) + h) * d_low + (d_high + d_low) ** 2


def member_log_bound(rxn, temperature: float, m_eff: float) -> float:
    rate = rxn.rate
    if isinstance(rate, ct.FalloffRate):
        return falloff_log_bound(rate, temperature, m_eff)
    if isinstance(rate, ct.ChebyshevRate):
        return 0.0
    if isinstance(rate, ct.ArrheniusRate):
        return arrhenius_log_bound(rate, temperature)
    raise ValueError(f"no bound derivation for {rxn.reaction_type}")


def _effective_third_body(rxn, concentrations: dict[str, float]) -> float:
    tb = getattr(rxn, "third_body", None)
    if tb is None:
        return 0.0
    efficiencies = dict(tb.efficiencies)
    return sum(c * efficiencies.get(name, tb.default_efficiency) for name, c in concentrations.items())


# ---------------------------------------------------------------------------
# Exact-grid parameter checks
# ---------------------------------------------------------------------------


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= EXACT_PARAMETER_RTOL * max(abs(a), abs(b), 1.0)


def _efficiencies(rxn, smiles_of: dict[str, str]) -> tuple[str | None, float | None, dict[str, float]]:
    tb = getattr(rxn, "third_body", None)
    if tb is None:
        return None, None, {}
    name = "M" if tb.name == "M" else smiles_of[tb.name]
    return name, float(tb.default_efficiency), {smiles_of[k]: float(v) for k, v in tb.efficiencies.items()}


def parameter_violations(
    label: str, rxn_in, rxn_out, smiles_in: dict[str, str], smiles_out: dict[str, str]
) -> list[str]:
    """Every parameter the export prints on a grid no coarser than the original must be identical."""
    problems: list[str] = []
    rate_in, rate_out = rxn_in.rate, rxn_out.rate
    if type(rate_in) is not type(rate_out):
        return [f"{label}: rate form {type(rate_in).__name__} became {type(rate_out).__name__}"]

    def channel(name: str, a: ct.ArrheniusRate, b: ct.ArrheniusRate) -> None:
        if not _close(a.temperature_exponent, b.temperature_exponent):
            problems.append(
                f"{label}: {name} n {a.temperature_exponent!r} became {b.temperature_exponent!r}"
            )

    if isinstance(rate_in, ct.FalloffRate):
        channel("high-pressure", rate_in.high_rate, rate_out.high_rate)
        channel("low-pressure", rate_in.low_rate, rate_out.low_rate)
        coeffs_in, coeffs_out = list(rate_in.falloff_coeffs), list(rate_out.falloff_coeffs)
        if len(coeffs_in) != len(coeffs_out) or not all(_close(a, b) for a, b in zip(coeffs_in, coeffs_out, strict=True)):
            problems.append(f"{label}: falloff coefficients {coeffs_in} became {coeffs_out}")
    elif isinstance(rate_in, ct.ChebyshevRate):
        for attr in ("temperature_range", "pressure_range"):
            a, b = getattr(rate_in, attr), getattr(rate_out, attr)
            if not all(_close(x, y) for x, y in zip(a, b, strict=True)):
                problems.append(f"{label}: Chebyshev {attr} {a} became {b}")
        if rate_in.data.shape != rate_out.data.shape or not np.all(
            np.abs(rate_in.data - rate_out.data) <= EXACT_PARAMETER_RTOL * np.maximum(np.abs(rate_in.data), 1.0)
        ):
            problems.append(f"{label}: Chebyshev coefficients differ")
    else:
        channel("", rate_in, rate_out)

    name_in, default_in, eff_in = _efficiencies(rxn_in, smiles_in)
    name_out, default_out, eff_out = _efficiencies(rxn_out, smiles_out)
    if name_in != name_out:
        problems.append(f"{label}: third body {name_in!r} became {name_out!r}")
    elif name_in is not None:
        if not _close(default_in, default_out):
            problems.append(f"{label}: default efficiency {default_in} became {default_out}")
        if set(eff_in) != set(eff_out) or not all(_close(eff_in[k], eff_out[k]) for k in eff_in):
            problems.append(f"{label}: efficiencies {eff_in} became {eff_out}")
    return problems


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


@dataclass
class GridComparison:
    fixture: str
    species: list[dict[str, Any]] = field(default_factory=list)
    reactions: list[dict[str, Any]] = field(default_factory=list)
    unsupported: list[dict[str, Any]] = field(default_factory=list)
    export_gaps: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    parameter_violations: list[str] = field(default_factory=list)
    bound_violations: list[str] = field(default_factory=list)

    @property
    def violations(self) -> list[str]:
        return [*self.unmatched, *self.parameter_violations, *self.bound_violations]

    @property
    def compared_keys(self) -> set[str]:
        return {entry["key"] for entry in self.reactions}

    def summary(self) -> dict[str, Any]:
        return {
            "schema": SUMMARY_SCHEMA,
            "fixture": self.fixture,
            "engine": {"cantera": ct.__version__},
            "grid": {
                "temperatures_k": list(GRID_TEMPERATURES_K),
                "pressures_atm": list(GRID_PRESSURES_ATM),
                "composition": COMPOSITION,
            },
            "bounds": {
                "thermo": "Phase A printed-precision bound on each NASA-7 coefficient (half a unit "
                "of the export's nine printed digits) plus 1e-10, per reduced function",
                "kinetics": "half a unit of the export's printed grid on A (5 digits) and Ea "
                "(4 decimals, cal/mol) plus the unit-chain error, propagated per form; "
                "exact-grid parameters checked equal; plus 1e-10 relative",
                "ea_chain_relative_error": EA_CHAIN_RELATIVE_ERROR,
                "ieee_allowance": IEEE_ALLOWANCE,
                "supported_forms": sorted(SUPPORTED_FORMS),
            },
            "counts": {
                "species_compared": len(self.species),
                "reactions_compared": len(self.reactions),
                "rate_terms_compared": sum(entry["n_terms"] for entry in self.reactions),
                "unsupported": len(self.unsupported),
                "export_gaps": len(self.export_gaps),
                "unmatched": len(self.unmatched),
                "parameter_violations": len(self.parameter_violations),
                "bound_violations": len(self.bound_violations),
            },
            "forms": dict(sorted(Counter(entry["form"] for entry in self.reactions).items())),
            "species": sorted(self.species, key=lambda e: e["smiles"]),
            "reactions": sorted(self.reactions, key=lambda e: e["key"]),
            "unsupported": self.unsupported,
            "export_gaps": self.export_gaps,
            "unmatched": self.unmatched,
            "parameter_violations": self.parameter_violations,
            "bound_violations": self.bound_violations,
        }

    def write_summary(self, path: str | os.PathLike[str]) -> None:
        Path(path).write_text(json.dumps(self.summary(), indent=2, sort_keys=True) + "\n")


def _thermo_comparison(
    smiles: str, name_in: str, name_out: str, gas_in: ct.Solution, gas_out: ct.Solution, violations: list[str]
) -> dict[str, Any]:
    th_in = gas_in.species(name_in).thermo
    th_out = gas_out.species(name_out).thermo
    entry: dict[str, Any] = {"smiles": smiles, "name_in": name_in, "name_out": name_out}
    if not isinstance(th_in, ct.NasaPoly2) or not isinstance(th_out, ct.NasaPoly2):
        violations.append(f"{smiles}: thermo model {type(th_in).__name__} became {type(th_out).__name__}")
        entry["within_bound"] = False
        return entry

    limits = {
        "t_low": (th_in.min_temp, th_out.min_temp, 0.5 * 10.0**-EXPORT_T_LOW_HIGH_DECIMALS),
        "t_high": (th_in.max_temp, th_out.max_temp, 0.5 * 10.0**-EXPORT_T_LOW_HIGH_DECIMALS),
        "t_mid": (th_in.coeffs[0], th_out.coeffs[0], 0.5 * 10.0**-EXPORT_T_MID_DECIMALS),
    }
    for name, (a, b, bound) in limits.items():
        if abs(a - b) > bound + IEEE_ALLOWANCE:
            violations.append(f"{smiles}: {name} {a} became {b} (bound {bound})")

    quantities = ("cp_over_r", "h_over_rt", "s_over_r")
    max_dev = np.zeros(3)
    max_bound = np.zeros(3)
    worst_ratio = 0.0
    within = True
    for t in GRID_TEMPERATURES_K:
        if not (th_in.min_temp <= t <= th_in.max_temp) or not (th_out.min_temp <= t <= th_out.max_temp):
            violations.append(f"{smiles}: grid point {t} K outside the fitted range")
            within = False
            continue
        block_out = list(th_out.coeffs[8:15]) if t <= th_out.coeffs[0] else list(th_out.coeffs[1:8])
        bound = print_error_bound(block_out, t)
        deviation = np.abs(reduced(th_out, t) - reduced(th_in, t))
        max_dev = np.maximum(max_dev, deviation)
        max_bound = np.maximum(max_bound, bound)
        worst_ratio = max(worst_ratio, float(np.max(deviation / bound)))
        if np.any(deviation > bound):
            within = False
            violations.append(
                f"{smiles}: reduced thermo deviation {deviation.tolist()} exceeds bound {bound.tolist()} at {t} K"
            )
    entry.update(
        max_abs_deviation={q: float(v) for q, v in zip(quantities, max_dev, strict=True)},
        max_bound={q: float(v) for q, v in zip(quantities, max_bound, strict=True)},
        worst_deviation_over_bound=worst_ratio,
        coefficient_max_abs_diff=float(np.max(np.abs(np.asarray(th_in.coeffs) - np.asarray(th_out.coeffs)))),
        within_bound=within,
    )
    return entry


def compare_mechanisms(
    gas_in: ct.Solution,
    gas_out: ct.Solution,
    smiles_by_name_in: dict[str, str],
    smiles_by_name_out: dict[str, str],
    *,
    fixture: str,
    export_gaps=(),
) -> GridComparison:
    result = GridComparison(fixture=fixture)
    result.export_gaps = [
        {"kind": gap.kind, "ref": gap.ref, "detail": gap.detail} for gap in export_gaps
    ]

    # --- species -----------------------------------------------------------
    name_out_by_smiles = {smiles: name for name, smiles in smiles_by_name_out.items()}
    for name_in, smiles in sorted(smiles_by_name_in.items(), key=lambda item: item[1]):
        name_out = name_out_by_smiles.get(smiles)
        if name_out is None:
            result.unmatched.append(f"species {smiles} ({name_in}) missing from the export")
            continue
        result.species.append(
            _thermo_comparison(smiles, name_in, name_out, gas_in, gas_out, result.bound_violations)
        )
    for smiles in set(smiles_by_name_out.values()) - set(smiles_by_name_in.values()):
        result.unmatched.append(f"species {smiles} present only in the export")

    # --- reactions ---------------------------------------------------------
    groups_in = group_reactions(gas_in, smiles_by_name_in)
    groups_out = group_reactions(gas_out, smiles_by_name_out)
    supported_in: dict[tuple, list[int]] = {}
    for key, indices in groups_in.items():
        if key[3] in SUPPORTED_FORMS:
            supported_in[key] = indices
        else:
            result.unsupported.append(
                {"kind": "form", "key": key_label(key), "form": key[3],
                 "equations": [gas_in.reaction(i).equation for i in indices],
                 "detail": "no printed-precision bound derivation for this form"}
            )
    for key in sorted(set(groups_out) - set(groups_in), key=key_label):
        result.unmatched.append(
            f"reaction only in the export: {key_label(key)} "
            f"({[gas_out.reaction(i).equation for i in groups_out[key]]})"
        )

    states = [(t, p * ct.one_atm) for t in GRID_TEMPERATURES_K for p in GRID_PRESSURES_ATM]
    composition_in = dict.fromkeys(gas_in.species_names, 1.0)
    composition_out = dict.fromkeys(gas_out.species_names, 1.0)

    # Pre-compute everything per state once; reactions are then read by index.
    per_state: list[dict[str, Any]] = []
    for t, p in states:
        gas_in.TPX = t, p, composition_in
        gas_out.TPX = t, p, composition_out
        per_state.append(
            {
                "t": t,
                "p_atm": p / ct.one_atm,
                "kf_in": gas_in.forward_rate_constants.copy(),
                "kf_out": gas_out.forward_rate_constants.copy(),
                "rop_in": gas_in.forward_rates_of_progress.copy(),
                "rop_out": gas_out.forward_rates_of_progress.copy(),
                "conc_in": dict(zip(gas_in.species_names, gas_in.concentrations, strict=True)),
            }
        )

    for key in sorted(supported_in, key=key_label):
        label = key_label(key)
        indices_in = supported_in[key]
        indices_out = groups_out.get(key)
        if indices_out is None:
            result.unmatched.append(
                f"reaction missing from the export: {label} "
                f"({[gas_in.reaction(i).equation for i in indices_in]})"
            )
            continue
        if len(indices_in) != len(indices_out):
            result.unmatched.append(
                f"{label}: {len(indices_in)} rate terms became {len(indices_out)}"
            )
            continue
        members_in = sorted(indices_in, key=lambda i: _member_signature(gas_in.reaction(i)))
        members_out = sorted(indices_out, key=lambda i: _member_signature(gas_out.reaction(i)))
        for i, j in zip(members_in, members_out, strict=True):
            result.parameter_violations.extend(
                parameter_violations(label, gas_in.reaction(i), gas_out.reaction(j), smiles_by_name_in, smiles_by_name_out)
            )

        entry: dict[str, Any] = {
            "key": label,
            "form": key[3],
            "equation_in": gas_in.reaction(indices_in[0]).equation,
            "equation_out": gas_out.reaction(indices_out[0]).equation,
            "n_terms": len(indices_in),
            "duplicate": len(indices_in) > 1,
        }
        max_dev_kf = max_dev_rop = max_bound = 0.0
        worst_ratio = 0.0
        worst_point: dict[str, float] | None = None
        within = True
        for state in per_state:
            members = [gas_in.reaction(i) for i in members_in]
            kf_members = [float(state["kf_in"][i]) for i in members_in]
            kf_in = sum(kf_members)
            kf_out = float(sum(state["kf_out"][j] for j in members_out))
            rop_in = float(sum(state["rop_in"][i] for i in members_in))
            rop_out = float(sum(state["rop_out"][j] for j in members_out))
            bound_abs = 0.0
            for rxn, k in zip(members, kf_members, strict=True):
                m_eff = _effective_third_body(rxn, state["conc_in"])
                bound_abs += k * math.expm1(member_log_bound(rxn, state["t"], m_eff))
            bound_rel = bound_abs / kf_in + IEEE_ALLOWANCE
            dev_kf = abs(kf_out - kf_in) / kf_in
            dev_rop = abs(rop_out - rop_in) / rop_in if rop_in else 0.0
            max_dev_kf = max(max_dev_kf, dev_kf)
            max_dev_rop = max(max_dev_rop, dev_rop)
            max_bound = max(max_bound, bound_rel)
            ratio = max(dev_kf, dev_rop) / bound_rel
            if ratio > worst_ratio:
                worst_ratio = ratio
                worst_point = {"t_k": state["t"], "p_atm": state["p_atm"]}
            if dev_kf > bound_rel or dev_rop > bound_rel:
                within = False
                result.bound_violations.append(
                    f"{label}: k_f deviation {dev_kf:.3e} / rate-of-progress deviation {dev_rop:.3e} "
                    f"exceeds bound {bound_rel:.3e} at {state['t']} K, {state['p_atm']} atm"
                )
        entry.update(
            max_rel_deviation_kf=max_dev_kf,
            max_rel_deviation_rop=max_dev_rop,
            max_rel_bound=max_bound,
            worst_deviation_over_bound=worst_ratio,
            worst_point=worst_point,
            within_bound=within and not any(v.startswith(label) for v in result.parameter_violations),
        )
        result.reactions.append(entry)

    return result


def supported_keys(gas: ct.Solution, smiles_of: dict[str, str]) -> set[str]:
    """The labels of every reaction group in ``gas`` whose form this module can bound."""
    return {key_label(key) for key in group_reactions(gas, smiles_of) if key[3] in SUPPORTED_FORMS}


# ---------------------------------------------------------------------------
# Text mutations of an exported mechanism (for the mutation checks)
# ---------------------------------------------------------------------------


def replace_unique(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"{old!r} occurs {count} times, expected exactly once")
    return text.replace(old, new)


def swap_nasa_blocks(therm_dat: str, species_name: str) -> str:
    """Swap the high- and low-temperature coefficient blocks of one species card."""
    lines = therm_dat.splitlines()
    index = _nasa_card_index(lines, species_name)
    fields = lines[index + 1][0:75] + lines[index + 2][0:75] + lines[index + 3][0:60]
    coeffs = [float(fields[k : k + 15]) for k in range(0, 14 * 15, 15)]
    swapped = coeffs[7:14] + coeffs[0:7]

    def fmt(values: list[float]) -> str:
        return "".join(f"{v:>15.8E}" for v in values)

    lines[index + 1] = f"{fmt(swapped[0:5]):<75}    2"
    lines[index + 2] = f"{fmt(swapped[5:10]):<75}    3"
    lines[index + 3] = f"{fmt(swapped[10:14]):<75}    4"
    return "\n".join(lines) + "\n"


def _nasa_card_index(lines: list[str], species_name: str) -> int:
    cards = [
        i for i, line in enumerate(lines)
        if len(line) >= 80 and line[79] == "1" and line[:18].strip() == species_name
    ]
    if len(cards) != 1:
        raise ValueError(f"expected one thermo card for {species_name!r}, found {len(cards)}")
    return cards[0]


def perturb_nasa_coefficient(therm_dat: str, species_name: str, coefficient: int, printed_digit: int) -> str:
    """Add one unit in the ``printed_digit``-th significant digit of one coefficient.

    ``coefficient`` indexes the card's 14 values in CHEMKIN order (0-6 the
    high-temperature block, 7-13 the low). ``printed_digit`` 9 is the last
    digit the export prints (``.8E``); 10 is one place below print precision,
    written by dropping the leading zero of a two-digit exponent so the
    15-column field still holds it (``2.731180001E+0``), which ``ck2yaml``'s
    fixed-width Fortran float reader accepts.
    """
    lines = therm_dat.splitlines()
    index = _nasa_card_index(lines, species_name)
    line_no, slot = divmod(coefficient, 5)
    row = lines[index + 1 + line_no]
    field = row[slot * 15 : slot * 15 + 15]
    value = float(field)
    exponent = int(field.split("E")[1])
    perturbed = value + 10.0 ** (exponent - (printed_digit - 1))
    if printed_digit <= 9:
        text = f"{perturbed:>15.{printed_digit - 1}E}"
    else:
        text = f"{perturbed:.{printed_digit - 1}E}"
        mantissa, exp_text = text.split("E")
        text = f"{mantissa}E{exp_text[0]}{exp_text[1:].lstrip('0') or '0'}".rjust(15)
    if len(text) != 15:
        raise ValueError(f"perturbed field {text!r} does not fit the 15-column slot")
    lines[index + 1 + line_no] = row[: slot * 15] + text + row[slot * 15 + 15 :]
    return "\n".join(lines) + "\n"


def _equation_tokens(line: str) -> tuple[Counter, Counter] | None:
    head = line.split("   ")[0]
    if "<=>" in head:
        lhs, rhs = head.split("<=>")
    elif "=>" in head:
        lhs, rhs = head.split("=>")
    else:
        return None

    def tokens(side: str) -> Counter:
        return Counter(
            t.strip()
            for t in side.replace("(+M)", "").split("+")
            if t.strip() and t.strip() != "M"
        )

    return tokens(lhs), tokens(rhs)


def find_reaction_line(chem_inp: str, reactant_names: list[str], product_names: list[str]) -> int:
    lines = chem_inp.splitlines()
    wanted = (Counter(reactant_names), Counter(product_names))
    matches = [i for i, line in enumerate(lines) if not line.startswith(" ") and _equation_tokens(line) == wanted]
    if len(matches) != 1:
        raise ValueError(f"expected one reaction line for {reactant_names} -> {product_names}, found {len(matches)}")
    return matches[0]


def drop_efficiency(chem_inp: str, reactant_names: list[str], product_names: list[str], collider: str) -> str:
    """Remove one collider's efficiency token from a reaction's auxiliary block."""
    lines = chem_inp.splitlines()
    start = find_reaction_line(chem_inp, reactant_names, product_names)
    index = start + 1
    while index < len(lines) and lines[index].startswith("    "):
        tokens = lines[index].split()
        if tokens and all(t.count("/") == 2 and t.endswith("/") for t in tokens):
            kept = [t for t in tokens if not t.startswith(f"{collider}/")]
            if len(kept) == len(tokens):
                raise ValueError(f"no efficiency for {collider!r} on that reaction")
            if kept:
                lines[index] = "    " + " ".join(kept)
            else:
                del lines[index]
            return "\n".join(lines) + "\n"
        index += 1
    raise ValueError("no efficiency line found for that reaction")
