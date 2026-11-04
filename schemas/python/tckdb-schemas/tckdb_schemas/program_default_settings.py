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
* **scf_convergence** (``scf.convergence`` tokens). Applies to every SCF-based method (HF, DFT, MP2, coupled cluster,
  DLPNO, ...) but needs a declared method, program and version, and for ORCA the calculation type.
  * Gaussian 09, any calculation type: ``tight``. Source: Gaussian 09 manual, keyword SCF ("In Gaussian 09, SCF=Tight
    is the default", knowledge-base chunk 11007) and "Changes between G09 and G03" ("The default SCF accuracy is
    SCF=Tight for all calculations, including single points", chunk 10284).
  * ORCA 5 and 6, ``opt`` only: ``tight``. Source: ORCA 5.0.4 manual 8.3.3 ("TightSCF in the SCF part is set as default
    to avoid the buildup of too much numerical noise in the gradients", chunk 13503) and ORCA 6.1 manual 4.1.12
    (same sentence, chunk 16764). The same manuals only say the plain default is "between medium and strong" (chunk
    14109), which is not a named level, so an ORCA single point, frequency or any other job type has no entry.
  * Gaussian 16, any calculation type: ``tight``. No G16 manual line is in the knowledge base; the basis is the G09
    sentence above (the keyword is unchanged) plus an observed default run accepted by the owner: a G16 Rev C.01
    ``# sp b3lyp/def2svp`` job with no grid or SCF keyword prints "Requested convergence on RMS density matrix=1.00D-08"
    (paper/notes/link1_gaussian_test_2026-11-04/water_default.log, 2026-11-04), i.e. Tight.
  * Not listed, for want of a source: Gaussian 03, other programs.

The grid default applies only to a DFT method; the SCF default does not. The version is the one the calculation declares. It is never guessed from the other fields.
"""

from __future__ import annotations

import re

from tckdb_schemas.hessian_method_assumption import is_dft_functional
from tckdb_schemas.software import normalize_software_name

__all__ = ["ASSUMED_DEFAULT_SETTINGS", "assumed_default_setting", "display_major_version", "uses_dft_grid"]

#: ``(normalised software name, major version) -> {setting: default value}``. Values are the canonical
#: ``grid.quality`` tokens the parsers emit for a requested grid (``fine``, ``ultrafine``, ``defgrid2``), so an
#: assumed value and a requested value are spelled the same way.
ASSUMED_DEFAULT_SETTINGS: dict[tuple[str, int], dict[str, str]] = {
    ("Gaussian", 9): {"grid": "fine"},
    ("Gaussian", 16): {"grid": "ultrafine"},
    ("ORCA", 5): {"grid": "defgrid2"},
    ("ORCA", 6): {"grid": "defgrid2"},
}

#: ``(normalised software name, major version) -> {calculation type or None for any type: SCF convergence token}``.
#: The tokens are the canonical ``scf.convergence`` values (``tight``, ``normal``, ...). See the module docstring for sources.
_ASSUMED_SCF_CONVERGENCE: dict[tuple[str, int], dict[str | None, str]] = {
    ("Gaussian", 9): {None: "tight"},
    ("Gaussian", 16): {None: "tight"},
    ("ORCA", 5): {"opt": "tight"},
    ("ORCA", 6): {"opt": "tight"},
}

#: Settings that exist only for a method that integrates on a DFT grid.
_DFT_ONLY = frozenset({"grid"})

#: The only words a version string may start with, per program (case-insensitive); a bare ``v`` is also accepted.
_OWN_NAMES = {"Gaussian": r"gaussian|g", "ORCA": r"orca"}
#: A leading integer outside this range is not a major version of that program.
_MAJOR_RANGE = {"Gaussian": range(1, 100), "ORCA": range(1, 21)}


def display_major_version(software: str | None, version: str | None) -> int | None:
    """The major version of a declared release, for display only: ``None`` when it cannot be read with confidence.

    Accepts the number alone (``"16"``, ``"6.0.0"``, ``"16 C.02"``), ``v`` in front, or the program's **own** name in
    front (``"Gaussian 16"``, ``"G16 Rev C.01"``, ``"ORCA 6.0.0"``). Any other prefix (``"x86_64"``, ``"release-6"``)
    and another program's name (a Gaussian release whose version reads ``"ORCA 6.0.0"``) give ``None``.

    This is **not** the parser tau resolution uses. ``hessian_method_assumption`` keeps its own stricter reading so
    that what tau judges does not move; the two answer different questions.
    """
    if not software or not software.strip() or version is None:
        return None
    name = normalize_software_name(software)
    own = _OWN_NAMES.get(name)
    if own is None:
        return None
    match = re.match(rf"^\s*(?:(?:{own})[\s_-]*|v)?(\d+)", version, re.IGNORECASE)
    if not match:
        return None
    major = int(match.group(1))
    return major if major in _MAJOR_RANGE[name] else None


_SPIN_PREFIXES = ("ro", "u", "r")


def uses_dft_grid(method: str | None) -> bool:
    """True for a DFT functional, also when written with an RI or spin prefix (``RI-B2PLYP``, ``UB3LYP``)."""
    if not method:
        return False
    token = method.strip().lower()
    candidates = {token}
    if token.startswith("ri-"):
        candidates.add(token[3:])
    for base in list(candidates):
        candidates.update(base[len(p) :] for p in _SPIN_PREFIXES if base.startswith(p))
    return any(is_dft_functional(c) for c in candidates)


def assumed_default_setting(
    software: str | None,
    version: str | None,
    setting: str,
    method: str | None = None,
    calculation_type: str | None = None,
) -> str | None:
    """The documented default of ``setting`` for this program and version, or ``None``.

    ``None`` when the program or version is not declared, when the pair is not in the table, and, for the grid, when
    ``method`` is not a DFT functional (a Hartree-Fock, MP2, coupled-cluster or composite method has no DFT grid to
    default). ``calculation_type`` (``opt``, ``sp``, ...) matters only to SCF defaults that depend on it. The functional list is the one tau uses; a functional it does not list (``APFD``) gets no default
    rather than a guess, and the list is not extended here because that would move tau.
    """
    major = display_major_version(software, version)
    if major is None or not software:
        return None
    if setting == "scf_convergence":
        # Any SCF-based method, but a method must be declared; an ORCA default depends on the job type.
        if not method or not method.strip():
            return None
        by_type = _ASSUMED_SCF_CONVERGENCE.get((normalize_software_name(software), major), {})
        job = getattr(calculation_type, "value", calculation_type)
        return by_type.get(job) or by_type.get(None)
    if setting in _DFT_ONLY and not uses_dft_grid(method):
        return None
    return ASSUMED_DEFAULT_SETTINGS.get((normalize_software_name(software), major), {}).get(setting)
