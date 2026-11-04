"""The setting a program uses when the input and the log say nothing, by program and major version.

This is a display aid, not an observation. It is computed when a record is read and **never stored**, in the same way
an optimisation energy is offered as a single point when read and never stored. It sits beside
:mod:`tckdb_schemas.hessian_method_assumption`, which holds the same kind of assumption for the Hessian method; the
two are separate tables because they answer different questions.

Only defaults the group knowledge base or a program manual documents are listed. A program, version or setting that is
not here has no assumed default, and the read says "not stated" rather than guess:

* **grid** (the DFT integration grid)
  * Gaussian 09: ``FineGrid``. Gaussian 16: ``UltraFine``. Source: knowledge-base entries "Gaussian 09 vs Gaussian 16
    - Default integration grid (changes energies)" and "Gaussian 09/16 input essentials and gotchas - Grid and SCF
    defaults"; Foresman and Frisch, *Exploring Chemistry with Electronic Structure Methods*, "Advanced Topics"
    ("In Gaussian 09, the default numerical integration grid is the FineGrid").
  * ORCA 5 and 6: ``DefGrid2``. Source: ORCA 6.1 manual, "Numerical Integration" ("the second [DEFGRID2] is the new
    default"); knowledge-base entry "ORCA input essentials and gotchas - DFT defaults that changed".
* **scf_convergence**: no program's default threshold is documented in the knowledge base or the manual excerpts the
  group holds, so there is no entry. Do not add one from memory.

The version is the one the calculation declares. It is never guessed from the other fields.
"""

from __future__ import annotations

import re

from tckdb_schemas.software import normalize_software_name

__all__ = ["ASSUMED_DEFAULT_SETTINGS", "assumed_default_setting", "major_version"]

#: ``(normalised software name, major version) -> {setting: default value}``.
ASSUMED_DEFAULT_SETTINGS: dict[tuple[str, int], dict[str, str]] = {
    ("Gaussian", 9): {"grid": "FineGrid"},
    ("Gaussian", 16): {"grid": "UltraFine"},
    ("ORCA", 5): {"grid": "DefGrid2"},
    ("ORCA", 6): {"grid": "DefGrid2"},
}

_LEADING_MAJOR = re.compile(r"^\s*[Gg]?(\d+)")


def major_version(version: str | None) -> int | None:
    """``"6.0.0"`` -> 6, ``"16"`` -> 16, ``"09"`` -> 9, ``"G16 Rev C.01"`` -> 16; ``None`` when no number leads."""
    if not version:
        return None
    match = _LEADING_MAJOR.match(version)
    return int(match.group(1)) if match else None


def assumed_default_setting(software: str | None, version: str | None, setting: str) -> str | None:
    """The documented default of ``setting`` for this program and version, or ``None`` (not documented, not declared)."""
    major = major_version(version)
    if not software or not software.strip() or major is None:
        return None
    return ASSUMED_DEFAULT_SETTINGS.get((normalize_software_name(software), major), {}).get(setting)
