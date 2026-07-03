"""Raw CHEMKIN AST dataclasses (stage 1 output).

These are *dependency-light* value objects produced by the parser. They carry
the mechanism exactly as written (native units, native kinetics constructs)
with only enough structure to make normalization and payload building simple.
No RDKit, no TCKDB coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SpeciesDecl:
    """A single entry from the ``SPECIES`` block.

    :param name: The CHEMKIN species name exactly as written.
    :param comment: Trailing ``!`` comment text on the declaration line, if any
        (some tools annotate species with SMILES/InChI there).
    :param line_no: 1-based source line number for diagnostics.
    """

    name: str
    comment: str | None = None
    line_no: int | None = None


@dataclass
class ThermoEntry:
    """A NASA-7 two-range polynomial entry from a ``THERMO`` block / therm.dat.

    CHEMKIN convention: coefficients 1-7 (``coeffs_high``) apply to the *upper*
    temperature interval ``[t_common, t_high]``; coefficients 8-14
    (``coeffs_low``) apply to the *lower* interval ``[t_low, t_common]``.

    :param name: Species name (card columns 1-18).
    :param composition: Element symbol -> atom count, from the card columns.
    :param phase: Phase character (usually ``G``).
    :param t_low: Lower temperature bound (K).
    :param t_high: Upper temperature bound (K).
    :param t_common: Common / mid switch temperature (K).
    :param coeffs_high: 7 coefficients for the upper interval.
    :param coeffs_low: 7 coefficients for the lower interval.
    :param line_no: 1-based source line number of the first card line.
    """

    name: str
    composition: dict[str, int]
    phase: str
    t_low: float
    t_high: float
    t_common: float
    coeffs_high: list[float]
    coeffs_low: list[float]
    line_no: int | None = None


@dataclass
class TransportEntry:
    """One row from a ``tran.dat`` transport file.

    :param name: Species name.
    :param geometry_index: 0=atom, 1=linear, 2=nonlinear.
    :param eps_over_k: Lennard-Jones well depth epsilon/k_B in K.
    :param sigma_angstrom: Lennard-Jones collision diameter in Angstrom.
    :param dipole_debye: Dipole moment in Debye.
    :param polarizability_angstrom3: Polarizability in Angstrom^3.
    :param rot_relaxation: Rotational relaxation collision number (Z_rot at 298 K).
    :param line_no: 1-based source line number.
    """

    name: str
    geometry_index: int
    eps_over_k: float
    sigma_angstrom: float
    dipole_debye: float
    polarizability_angstrom3: float
    rot_relaxation: float
    line_no: int | None = None


@dataclass
class PlogPoint:
    """One pressure point of a PLOG rate expression (native units)."""

    pressure_atm: float
    a: float
    n: float
    ea: float


@dataclass
class ChebyshevBlock:
    """A Chebyshev k(T,P) surface as written (native units).

    :param n_temperature: Number of temperature basis functions (rows).
    :param n_pressure: Number of pressure basis functions (columns).
    :param tmin: Lower temperature bound (K).
    :param tmax: Upper temperature bound (K).
    :param pmin_atm: Lower pressure bound (atm).
    :param pmax_atm: Upper pressure bound (atm).
    :param coefficients: n_temperature x n_pressure matrix (row-major, T-major).
    """

    n_temperature: int
    n_pressure: int
    tmin: float
    tmax: float
    pmin_atm: float
    pmax_atm: float
    coefficients: list[list[float]]


@dataclass
class Reaction:
    """A single reaction as parsed (native units, native constructs).

    ``reactants``/``products`` are ordered ``(coefficient, species_name)`` pairs
    with the third body (``M`` / ``(+M)``) already removed — the third-body
    behaviour is carried by ``is_third_body`` / ``is_falloff`` / the collider
    fields instead, per DR-0032B.
    """

    reactants: list[tuple[int, str]]
    products: list[tuple[int, str]]
    reversible: bool
    a: float
    n: float
    ea: float

    is_third_body: bool = False
    is_falloff: bool = False
    falloff_collider: str | None = None  # e.g. "M", "N2" from "(+N2)"

    low: tuple[float, float, float] | None = None  # LOW/ A n Ea /
    troe: list[float] | None = None  # TROE/ a T3 T1 [T2] /
    sri: list[float] | None = None  # SRI/ a b c [d e] /

    efficiencies: dict[str, float] = field(default_factory=dict)

    plog: list[PlogPoint] = field(default_factory=list)
    chebyshev: ChebyshevBlock | None = None

    rev: tuple[float, float, float] | None = None  # explicit reverse (parse+warn)
    duplicate: bool = False

    # Aux lines we recognised but do not support in v1 (LT, FORD, RORD, ...).
    unsupported_aux: list[str] = field(default_factory=list)

    line_no: int | None = None

    @property
    def reactant_names(self) -> list[str]:
        return [name for _, name in self.reactants]

    @property
    def product_names(self) -> list[str]:
        return [name for _, name in self.products]

    @property
    def molecularity(self) -> int:
        """Reactant-side molecularity (sum of stoichiometric coefficients).

        Excludes the third body (already stripped). This drives A-unit
        selection for the high-pressure / plain rate.
        """
        return sum(coeff for coeff, _ in self.reactants)


@dataclass
class Mechanism:
    """The whole parsed mechanism (stage 1 output).

    :param elements: Element symbols from the ELEMENTS block.
    :param species: Ordered species declarations.
    :param thermo: Species name -> NASA-7 entry.
    :param reactions: Ordered reactions.
    :param transport: Species name -> transport entry (from tran.dat if given).
    :param ea_units: Ea-units token from the REACTIONS header (e.g. ``CAL/MOLE``).
    :param a_conc_basis: ``MOLES`` or ``MOLECULES`` from the REACTIONS header.
    """

    elements: list[str] = field(default_factory=list)
    species: list[SpeciesDecl] = field(default_factory=list)
    thermo: dict[str, ThermoEntry] = field(default_factory=dict)
    reactions: list[Reaction] = field(default_factory=list)
    transport: dict[str, TransportEntry] = field(default_factory=dict)
    ea_units: str = "CAL/MOLE"
    a_conc_basis: str = "MOLES"

    @property
    def species_names(self) -> list[str]:
        return [s.name for s in self.species]
