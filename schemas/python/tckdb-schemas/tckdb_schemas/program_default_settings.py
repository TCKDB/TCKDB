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
* **scf_convergence** (``scf.convergence`` tokens). Applies to a method on an allowlist of SCF-based methods (DFT,
  Hartree-Fock, MPn, coupled cluster including DLPNO/PNO/local/F12 and QCISD, CIS/CISD, and for Gaussian its composite
  methods CBS-QB3, CBS-4M, CBS-APNO, G1-G4 and W1). Molecular mechanics, semi-empirical, extended Hueckel, xTB, CASSCF,
  MRCI, NEVPT2 and any unrecognised method get no default. It needs a declared method, program and version, and for
  ORCA the calculation type.
  * Gaussian 09, any calculation type: ``tight``. Source: Gaussian 09 manual, keyword SCF ("In Gaussian 09, SCF=Tight
    is the default") and "Changes between G09 and G03" ("The default SCF accuracy is
    SCF=Tight for all calculations, including single points").
  * ORCA 5 and 6, ``opt`` only: ``tight``. Source: ORCA 5.0.4 manual 8.3.3 ("TightSCF in the SCF part is set as default
    to avoid the buildup of too much numerical noise in the gradients") and ORCA 6.1 manual 4.1.12
    (same sentence). The same manuals only say the plain default is "between medium and strong", which is
    not a named level, so an ORCA single point, frequency or any other job type has no entry.
  * Gaussian 16, any calculation type: ``tight``. No G16 manual line is cited; the basis is the G09
    sentence above (the keyword is unchanged) plus an observed Gaussian 16 Rev C.01 default run
    (``# sp b3lyp/def2svp``, no grid or SCF keyword) printing "Requested convergence on RMS density matrix=1.00D-08",
    which is the Tight convergence value.
  * Not listed, for want of a source: Gaussian 03, other programs.

The grid default applies only to a DFT method; the SCF default applies to the allowlist above. The version is the one the calculation declares. It is never guessed from the other fields.
"""

from __future__ import annotations

import re

from tckdb_schemas.hessian_method_assumption import (
    MethodFamily,
    classify_method_family,
    is_dft_functional,
)
from tckdb_schemas.software import normalize_software_name

__all__ = ["ASSUMED_DEFAULT_SETTINGS", "assumed_default_setting", "display_major_version", "uses_dft_grid", "uses_scf"]

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

#: Gaussian composite methods (they run standard SCF steps): CBS-QB3, CBS-4M, CBS-APNO, G1-G4 and variants, W1 variants.
#: Gaussian 09 keywords only: (RO)CBS-QB3, (RO)CBS-4M, CBS-APNO, G1, G2, G2MP2, G3, G3MP2, G3B3, G3MP2B3, G4, G4MP2 (also
#: written G4(MP2)), W1U, W1BD, W1RO. ROCBS-QB3 is in the G09 manual.
_GAUSSIAN_COMPOSITE = re.compile(
    r"^((ro)?cbs-(qb3|4m)|cbs-apno|g1|g2|g2mp2|g3|g3mp2|g3b3|g3mp2b3|g4|g4mp2|g4\(mp2\)|w1(u|bd|ro))$"
)
#: Methods the family classifier does not list that are SCF-reference methods: HF-3c, CCD, Brueckner doubles.
_OTHER_SCF = re.compile(r"^(hf-3c|ccd|bd|bd\(t\))$")
#: Prefixes that wrap an SCF-based method (resolution of identity, local pair natural orbitals, equation of motion).
_WRAPPER_PREFIXES = ("ri-", "lpno-", "eom-")


def _normalize(value: str | None) -> str | None:
    """Lower-case, trim, ``_`` to ``-`` and the Greek omega to ``w`` (``ωB97X-D`` is ``wB97X-D``); ``None`` if empty."""
    if value is None:
        return None
    stripped = value.strip().lower().replace("\u03c9", "w")
    return stripped.replace("_", "-") if stripped else None


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


def uses_scf(method: str | None, software: str | None = None) -> bool:
    """True for an allowlisted SCF-based method; unrecognised, absent, MM, semi-empirical, xTB and multireference are not.

    Allowed: DFT, Hartree-Fock, MPn, coupled cluster (also DLPNO/PNO/local/F12), QCISD, CIS and CISD, each also with a
    spin or ``RI-`` prefix, and, for Gaussian only, its composite methods. Uses the family classifier tau uses, read
    only; CASSCF is excluded and a name it does not recognise gets nothing.
    """
    token = _normalize(method)
    if token is None:
        return False
    if software and normalize_software_name(software) == "Gaussian" and _GAUSSIAN_COMPOSITE.match(token):
        return True
    candidates = {token}
    for prefix in _WRAPPER_PREFIXES:
        if token.startswith(prefix):
            candidates.add(token[len(prefix) :])
    for base in list(candidates):
        candidates.update(base[len(p) :] for p in _SPIN_PREFIXES if base.startswith(p))
    for candidate in candidates:
        if _OTHER_SCF.match(candidate):
            return True
        family = classify_method_family(candidate)
        if family is not None and family != MethodFamily.CASSCF:
            return True
    return False


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
    default). For the SCF default ``method`` must be on the SCF-based allowlist (:func:`uses_scf`).
    ``calculation_type`` (``opt``, ``sp``, ...) matters only to SCF defaults that depend on it. The functional list is
    the one tau uses; a functional it does not list (``APFD``) gets no default rather than a guess, and the list is not
    extended here because that would move tau.
    """
    major = display_major_version(software, version)
    if major is None or not software:
        return None
    if setting == "scf_convergence":
        # An allowlisted SCF-based method must be declared; an ORCA default depends on the job type.
        if not uses_scf(method, software):
            return None
        by_type = _ASSUMED_SCF_CONVERGENCE.get((normalize_software_name(software), major), {})
        job = getattr(calculation_type, "value", calculation_type)
        return by_type.get(job) or by_type.get(None)
    if setting in _DFT_ONLY and not uses_dft_grid(method):
        return None
    return ASSUMED_DEFAULT_SETTINGS.get((normalize_software_name(software), major), {}).get(setting)
