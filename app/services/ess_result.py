"""ESS-agnostic result contract for output log parsing.

Every ESS parser (Gaussian, ORCA, NWChem, ...) produces an ``ESSResult``
that downstream code can consume without caring which program ran.

Design notes:
- ``None`` means "this parser did not extract the field" — it may or
  may not exist in the raw output.  Downstream code must tolerate None
  for every optional field.
- Fields that are *always* present in a valid output (like
  ``software_name``) are non-optional.
- ESS-specific extras live in ``extras: dict`` — not in the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ESSJobMeta:
    """Identity metadata extracted from the ESS output header."""

    software_name: str                    # "gaussian", "orca", "nwchem"
    software_version: str | None = None   # "16", "5.0.4"
    software_build: str | None = None     # Gaussian revision string
    charge: int | None = None
    multiplicity: int | None = None
    method: str | None = None             # "wb97xd", "DLPNO-CCSD(T)"
    basis: str | None = None              # "def2-TZVP"
    aux_basis: str | None = None          # "/C" auxiliary
    job_types: list[str] = field(default_factory=list)  # ["opt", "freq"]


@dataclass(frozen=True)
class ESSFreqResult:
    """Frequency analysis results — the key to StationaryPointKind."""

    frequencies_cm1: list[float] = field(default_factory=list)
    n_imag: int = 0
    imag_freq_cm1: float | None = None    # most negative, None if n_imag == 0
    zpe_hartree: float | None = None


@dataclass(frozen=True)
class ESSOptResult:
    """Geometry optimization results."""

    converged: bool
    n_steps: int | None = None
    final_energy_hartree: float | None = None


@dataclass(frozen=True)
class ESSSPResult:
    """Single-point energy result."""

    electronic_energy_hartree: float


@dataclass(frozen=True)
class ESSSymmetry:
    """Symmetry information — availability varies widely by ESS."""

    point_group: str | None = None        # "C2v", "Td"
    is_linear: bool | None = None         # from rotational analysis


@dataclass(frozen=True)
class ESSGeometry:
    """Final geometry in Cartesian coordinates."""

    atoms: tuple[tuple[str, float, float, float], ...]  # (element, x, y, z)


@dataclass(frozen=True)
class ESSResult:
    """Common envelope for all ESS output parsers.

    Usage::

        result = parse_gaussian(log_text)   # → ESSResult
        result = parse_orca(log_text)       # → ESSResult
        result = parse_nwchem(log_text)     # → ESSResult

        # Downstream code is ESS-agnostic:
        if result.freq and result.freq.n_imag == 0:
            kind = StationaryPointKind.minimum
    """

    meta: ESSJobMeta

    # Each is None if that calculation type wasn't present in the output
    freq: ESSFreqResult | None = None
    opt: ESSOptResult | None = None
    sp: ESSSPResult | None = None
    symmetry: ESSSymmetry | None = None
    geometry: ESSGeometry | None = None

    # Raw execution parameters (canonical key/value dicts)
    parameters: list[dict] = field(default_factory=list)
    # JSONB-ready snapshot of everything parsed
    parameters_json: dict = field(default_factory=dict)

    # ESS-specific data that doesn't fit the common contract
    extras: dict = field(default_factory=dict)
    # Parser provenance
    parser_version: str = ""
