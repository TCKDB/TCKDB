"""The setting a program uses when the input and the log say nothing, by program and major version.

This is a display aid, not an observation. It is computed when a record is read and **never stored**, in the same way
an optimisation energy is offered as a single point when read and never stored. It sits beside
:mod:`tckdb_schemas.hessian_method_assumption`, which holds the same kind of assumption for the Hessian method; the
two are separate tables because they answer different questions.

Only defaults the group knowledge base or a program manual documents are listed. A program, version or setting that is
not here has no assumed default, and the read says "not stated" rather than guess:

* **grid** (the DFT integration grid)
  * Gaussian 09: FineGrid (``fine``). Gaussian 16: UltraFine (``ultrafine``). Source: knowledge-base entries "Gaussian 09 vs Gaussian 16
    - Default integration grid (changes energies)" and "Gaussian 09/16 input essentials and gotchas - Grid and SCF
    defaults"; Foresman and Frisch, *Exploring Chemistry with Electronic Structure Methods*, "Advanced Topics"
    ("In Gaussian 09, the default numerical integration grid is the FineGrid").
  * ORCA 5 and 6: DefGrid2 (``defgrid2``). Source: ORCA 6.1 manual, "Numerical Integration" ("the second [DEFGRID2] is the new
    default"); knowledge-base entry "ORCA input essentials and gotchas - DFT defaults that changed".
* **scf_convergence**: no program's default threshold is documented in the knowledge base or the manual excerpts the
  group holds, so there is no entry. Do not add one from memory.

The grid default applies only to a DFT method. The version is the one the calculation declares. It is never guessed from the other fields.
"""

from __future__ import annotations

from tckdb_schemas.hessian_method_assumption import is_dft_functional, major_version
from tckdb_schemas.software import normalize_software_name

__all__ = ["ASSUMED_DEFAULT_SETTINGS", "assumed_default_setting"]

#: ``(normalised software name, major version) -> {setting: default value}``. Values are the canonical
#: ``grid.quality`` tokens the parsers emit for a requested grid (``fine``, ``ultrafine``, ``defgrid2``), so an
#: assumed value and a requested value are spelled the same way.
ASSUMED_DEFAULT_SETTINGS: dict[tuple[str, int], dict[str, str]] = {
    ("Gaussian", 9): {"grid": "fine"},
    ("Gaussian", 16): {"grid": "ultrafine"},
    ("ORCA", 5): {"grid": "defgrid2"},
    ("ORCA", 6): {"grid": "defgrid2"},
}

#: Settings that exist only for a method that integrates on a DFT grid.
_DFT_ONLY = frozenset({"grid"})

#: A leading integer outside this range is not a Gaussian or ORCA major version.
_MAJOR_RANGE = range(1, 100)


def assumed_default_setting(
    software: str | None, version: str | None, setting: str, method: str | None = None
) -> str | None:
    """The documented default of ``setting`` for this program and version, or ``None``.

    ``None`` when the program or version is not declared, when the pair is not in the table, and, for the grid, when
    ``method`` is not a DFT functional (a Hartree-Fock, MP2, coupled-cluster or composite method has no DFT grid to
    default). The version is parsed by :func:`tckdb_schemas.hessian_method_assumption.major_version`.
    """
    major = major_version(version, _MAJOR_RANGE)
    if not software or not software.strip() or major is None:
        return None
    if setting in _DFT_ONLY and not is_dft_functional(method):
        return None
    return ASSUMED_DEFAULT_SETTINGS.get((normalize_software_name(software), major), {}).get(setting)
