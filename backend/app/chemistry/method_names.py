"""The identity key of a level-of-theory method name (issue #585).

A method name is written in whatever case a producer prefers. ARC lower-cases
every method it emits (``arc/level.py``, ``Level.lower``); Gaussian, ORCA and
Molpro inputs are written in mixed or upper case:

====================  =======================  =======================
method                ARC                      Gaussian / ORCA / Molpro
====================  =======================  =======================
DLPNO-CCSD(T)-F12     ``dlpno-ccsd(t)-f12``    ``DLPNO-CCSD(T)-F12``
CCSD(T)-F12a          ``ccsd(t)-f12a``         ``CCSD(T)-F12a``
B3LYP                 ``b3lyp``                ``B3LYP``
====================  =======================  =======================

``level_of_theory.lot_hash`` hashed the method as written, so an ARC upload
of ``ccsd(t)-f12/cc-pvtz-f12`` and a stored ``CCSD(T)-F12/cc-pVTZ-F12`` were
two levels of theory.

:func:`method_identity_key` is the string the hash is taken over. The verbatim
name is still what the row stores and what every read shows.

The case rule, and why it is safe
---------------------------------
The key is the name with surrounding whitespace removed, lower-cased (as the
basis key is, and as Postgres's ``lower`` does over the stored column).
Every program above reads method names case-insensitively, and no two
methods differ only by case: ``F12a`` and ``F12b`` are different
approximations, but they differ by a letter, not by its case.

Curated aliases (issue #618)
----------------------------
Some methods are spelled with different punctuation by different producers
while naming one functional. :data:`NAME_ALIASES` and :data:`SUFFIX_ALIASES`
are a curated table of exactly those, and nothing else. Every entry carries
its citations and a scope. The rule for an entry is that a chemist can
justify it from a manual or a paper, not that the two strings look alike.

**Scope is by program, and this key cannot see the program.** The hash of a
level of theory is taken over ``method``, ``basis``, ``aux_basis``,
``cabs_basis``, ``dispersion``, ``solvent``, ``solvent_model``, ``keywords``
and ``spin_treatment`` (``_level_of_theory_hash``). The software that ran a
calculation is a different table (``software_release``, reached through the
calculation), and ``LevelOfTheoryRef`` carries none. So the key has no program
to consult, and an entry is admitted only if it is true for every program
checked (Gaussian, ORCA 5/6, Psi4, Q-Chem, PySCF; Molpro and TeraChem are not
checked):

* the alias is program-independent (``programs=None``), meaning every program
  checked that accepts the spelling means the same functional by it. This
  concerns alias *spellings*, not stems: B3LYP itself differs between codes
  (VWN3 in Gaussian, VWN5 in ORCA), and stems already join across codes under
  the case rule, which this table neither causes nor worsens. And
* a spelling that means different things in different programs is left out.
  Gaussian ``wB97XD`` is the Chai and Head-Gordon 2008 functional with its
  own damped dispersion, and ORCA's ``wB97X-D3`` is a different
  parametrisation (zero-damped D3), so they stay two keys, and so do
  ``wb97x-d3``, ``wb97x-d4`` and ``wb97x-d3bj``. A program-scoped entry needs
  the program in the hash first; a test refuses one until then.

Method strings in which dispersion is folded in (``b3lyp-d3(bj)``) are the
same identity as a method with the dispersion in its own column
(``b3lyp`` with ``dispersion=d3bj``), for the stems and suffixes listed in
``dispersion_names.py`` (#630). That join is made by
:func:`~app.chemistry.dispersion_names.level_identity_keys`, which needs both
fields; this key is the method alone and equates only spellings of the same
folded name.

The table is deliberately small and stays out of anything it cannot cite:
``hf`` / ``rhf``, ``f12a`` / ``f12b``, ``b3lyp`` / ``ub3lyp``, ``b3lyp-d3``
(zero damping in Psi4, BJ damping in ORCA), and every ``wb97x-d3`` family
member are separate keys. A split is recoverable, because the merge script
joins it later; a false merge is not.

The canonical key of each alias is the spelling ARC and Gaussian write, so
rows already stored that way keep their hash.

Named composite methods (ADR 0021)
----------------------------------
``cbsqb3``, ``rocbsqb3``, ``cbs4m`` and ``cbsapno`` (Arkane's hyphen-free
spelling) key to the hyphenated forms, and ``g4(mp2)``, ``g3(mp2)`` and
``g3(mp2)b3`` to the Gaussian keywords ``g4mp2``, ``g3mp2`` and ``g3mp2b3``.
What stays apart, on purpose: ``w1`` / ``w1u`` / ``w1bd`` / ``w1ro`` (four
recipes), ``cbs-qb3`` / ``rocbs-qb3`` (restricted-open-shell, no spin
correction), ``w1-bd`` (no source writes it, so no alias), and every
correction-table name (``cbs-qb3-paraskevas``, ``cbsqb32023``): those select a
set of BAC parameters in Arkane, not a method, and are warned about on upload
(``level_of_theory_method_names_correction_table``) and never aliased.
:mod:`app.chemistry.composite_methods` records what each named method is.

The SQL twin used by the search filters
(``scientific_read/lot_identity_filters.py``) is built from this same table
and agrees with this key on every real method name; its documented
divergences are exotic Unicode and edge whitespace that the wire schema
already removes.

This module is pure. The Alembic revisions that re-key existing rows carry a
frozen copy of the rule they ran (``c8424fe82997``: case only; the #618
revision: aliases), and tests hold each to the application.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MethodAlias:
    """One curated method spelling and the spelling it is keyed as.

    :param alias: The lower-cased spelling to rewrite. For a suffix alias this
        is what follows the ``-`` at the end of the method name.
    :param canonical: What it is keyed as, in the same position.
    :param programs: ``None`` when the alias is true in every program. A tuple
        of program names would scope it; the key cannot see a program, so the
        table refuses a scoped entry (see the module docstring).
    :param citations: Program-manual sections or papers that justify it.
    """

    alias: str
    canonical: str
    programs: tuple[str, ...] | None
    citations: tuple[str, ...]


#: Whole-name aliases, matched after strip and lower-case.
NAME_ALIASES: tuple[MethodAlias, ...] = (
    MethodAlias(
        alias="wb97x-d",
        canonical="wb97xd",
        programs=None,
        citations=(
            "J.-D. Chai and M. Head-Gordon, Phys. Chem. Chem. Phys. 10, 6615 (2008): "
            "omega-B97X-D, with its own damped dispersion.",
            "Gaussian 09/16 manual, DFT Methods: keyword wB97XD, "
            "'the latest functional from Head-Gordon and coworkers, which includes "
            "empirical dispersion' (Chai08a).",
            "Q-Chem manual (METHOD wB97X-D) and Psi4 manual (functional 'wb97x-d'): "
            "the same 2008 functional under the hyphenated spelling.",
            "ORCA 5.0.4 and 6.1 manuals, DFT keyword tables: no bare wB97X-D exists "
            "(only wB97X-D3, -D3BJ, -D4, -V), so the hyphenated spelling cannot "
            "mean anything else there. wB97X-D3 is a different key.",
            "PySCF 2.13.1 (pyscf/scf/dispersion.py) lists wb97x-d as not supported, "
            "so it accepts no competing meaning; older PySCF mapped WB97X-D to libxc "
            "WB97X_D, which is exchange-correlation only (no dispersion): a "
            "different calculation under the same name, but one that PySCF itself "
            "has dropped. A record whose producer was such a PySCF is the known "
            "risk of this entry.",
            "Not checked: Molpro and TeraChem. The claim is 'every program "
            "checked', not every program.",
        ),
    ),
    MethodAlias(
        alias="m06-2x",
        canonical="m062x",
        programs=None,
        citations=(
            "Y. Zhao and D. G. Truhlar, Theor. Chem. Acc. 120, 215 (2008): M06-2X.",
            "Gaussian keyword M062X and ORCA 6.1 manual Table 3.12 keyword M062X "
            "(hyb_mgga_x_m06_2x + mgga_c_m06_2x); Q-Chem and Psi4 write M06-2X.",
        ),
    ),
    # Named composite methods (ADR 0021). The canonical key is the Gaussian /
    # ARC spelling, so rows stored that way keep their hash. Every spelling
    # below names the same recipe in every program that accepts it; only
    # Gaussian documents these as keywords (app/chemistry/composite_methods.py).
    MethodAlias(
        alias="cbsqb3",
        canonical="cbs-qb3",
        programs=None,
        citations=(
            "Arkane (RMG-Py arkane/modelchem.py, standardize_name, 62eb728c0) removes "
            "hyphens and spaces and lower-cases every model-chemistry name, so it writes "
            "CBS-QB3 as 'cbsqb3'.",
            "Gaussian 09 manual, CBS Methods: keyword CBS-QB3 (Montgomery et al., "
            "J. Chem. Phys. 110, 2822 (1999), doi:10.1063/1.477924); ARC "
            "data/ess_methods.yml (d9f47ab9) writes 'cbs-qb3'.",
            "rag-drg level-of-theory card: CBS-QB3 'also written: cbs-qb3, cbsqb3'.",
            "ROCBS-QB3 is a different recipe (Wood et al., doi:10.1063/1.2335438) and "
            "keeps its own key.",
        ),
    ),
    MethodAlias(
        alias="rocbsqb3",
        canonical="rocbs-qb3",
        programs=None,
        citations=(
            "Arkane standardize_name (hyphens removed), as for cbsqb3.",
            "Gaussian 09 manual, CBS Methods: 'ROCBS-QB3 [Wood06]'; ARC "
            "data/ess_methods.yml writes 'rocbs-qb3'.",
            "Wood et al., J. Chem. Phys. 125, 094106 (2006), doi:10.1063/1.2335438: "
            "restricted-open-shell variant, a different recipe from CBS-QB3.",
        ),
    ),
    MethodAlias(
        alias="cbs4m",
        canonical="cbs-4m",
        programs=None,
        citations=(
            "Arkane standardize_name (hyphens removed), as for cbsqb3.",
            "Gaussian 09 manual, CBS Methods: keyword CBS-4M; ARC data/ess_methods.yml "
            "writes 'cbs-4m'.",
            "Montgomery, Ochterski, Petersson, J. Chem. Phys. 112, 6532 (2000), "
            "doi:10.1063/1.481224.",
        ),
    ),
    MethodAlias(
        alias="cbsapno",
        canonical="cbs-apno",
        programs=None,
        citations=(
            "Arkane standardize_name (hyphens removed), as for cbsqb3.",
            "Gaussian 09 manual, CBS Methods: keyword CBS-APNO; ARC "
            "data/ess_methods.yml writes 'cbs-apno'.",
            "Montgomery, Ochterski, Petersson, J. Chem. Phys. 101, 5900 (1994), "
            "doi:10.1063/1.467306 (CBS-QCI/APNO).",
        ),
    ),
    MethodAlias(
        alias="g4(mp2)",
        canonical="g4mp2",
        programs=None,
        citations=(
            "Curtiss, Redfern, Raghavachari, J. Chem. Phys. 127, 124105 (2007), "
            "doi:10.1063/1.2770701: the paper names the method G4(MP2).",
            "Gaussian 09 manual, G1-G4: keyword G4MP2 requests the fourth-generation "
            "methods [Curtiss07, Curtiss07a]; ARC data/ess_methods.yml writes 'g4mp2'.",
        ),
    ),
    MethodAlias(
        alias="g3(mp2)",
        canonical="g3mp2",
        programs=None,
        citations=(
            "Gaussian 09 manual, G1-G4: 'G3MP2 requests the similarly modified G3(MP2) "
            "method [Curtiss99]'.",
            "Curtiss et al., J. Chem. Phys. 110, 4703 (1999), doi:10.1063/1.478385.",
        ),
    ),
    MethodAlias(
        alias="g3(mp2)b3",
        canonical="g3mp2b3",
        programs=None,
        citations=(
            "Gaussian 09 manual, G1-G4: the G3 variants using B3LYP structures and "
            "frequencies [Baboul99] 'are requested with the G3B3 and G3MP2B3 keywords'; "
            "G3MP2 is the manual's gloss of G3(MP2), so G3(MP2)B3 is the same recipe "
            "with the parenthesis written. ARC data/ess_methods.yml writes 'g3mp2b3'.",
            "Baboul et al., J. Chem. Phys. 110, 7650 (1999), doi:10.1063/1.478676.",
            "secondary: the Zipse group's teaching pages (zipse.cup.uni-muenchen.de, "
            "'Overview of Gaussian theories') write the method literally as 'G3(MP2)B3' "
            "and describe it as G3(MP2) with Becke3LYP/6-31G(d) geometries and ZPE.",
        ),
    ),
)

#: Trailing ``-<alias>`` spellings of Grimme's D3 dispersion with Becke-Johnson
#: damping, folded into a method name. Matched after strip and lower-case.
SUFFIX_ALIASES: tuple[MethodAlias, ...] = (
    MethodAlias(
        alias="d3(bj)",
        canonical="d3bj",
        programs=None,
        citations=(
            "Psi4 manual, DFTD3 interface: '-D3BJ2B, -D3BJ, -D3(BJ)' are one row, "
            "D3 with Becke-Johnson rational damping.",
            "ORCA 6.1 manual 3.4 (Dispersion Corrections): keyword D3BJ.",
            "S. Grimme, S. Ehrlich, L. Goerigk, J. Comput. Chem. 32, 1456 (2011).",
        ),
    ),
    MethodAlias(
        alias="gd3bj",
        canonical="d3bj",
        programs=None,
        citations=(
            "Gaussian 16 manual, EmpiricalDispersion=GD3BJ: Grimme's D3 with "
            "Becke-Johnson damping.",
            "S. Grimme, S. Ehrlich, L. Goerigk, J. Comput. Chem. 32, 1456 (2011).",
        ),
    ),
)

_NAME_MAP = {a.alias: a.canonical for a in NAME_ALIASES}

#: One ``(pattern, replacement)`` per suffix alias, read by the Python key and
#: by its SQL twin, so they cannot list different suffixes. A pattern needs a
#: non-empty stem before the hyphen. ``.`` matches a newline in Postgres, so
#: the Python side compiles with DOTALL. No canonical spelling is itself an
#: alias, so the rules are order-independent and the key is idempotent.
SUFFIX_RULES: tuple[tuple[str, str], ...] = tuple(
    ("^(.+)-" + re.escape(a.alias) + "$", "\\1-" + a.canonical) for a in SUFFIX_ALIASES
)
_SUFFIX_COMPILED = tuple((re.compile(p, re.DOTALL), r) for p, r in SUFFIX_RULES)


def method_identity_key(name: str) -> str:
    """Return the identity key of a method name.

    The key is only for hashing and comparison. It is never stored in place
    of the verbatim name and never shown.

    :param name: A method name as a producer wrote it.
    :returns: The name, stripped and lower-cased, then rewritten through the
        curated alias table (:data:`NAME_ALIASES`, :data:`SUFFIX_ALIASES`).
        Idempotent.
    """

    key = name.strip().lower()
    if key in _NAME_MAP:
        return _NAME_MAP[key]
    for pattern, replacement in _SUFFIX_COMPILED:
        key = pattern.sub(replacement, key)
    return key
