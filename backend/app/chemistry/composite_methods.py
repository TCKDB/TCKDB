"""Catalogue of named composite methods (ADR 0021, plan P1).

A *named composite method* (CBS-QB3, G4, W1U, ...) is a fixed recipe that one
program run executes: an internal geometry and frequency level, several single
points, an extrapolation and empirical terms. TCKDB stores it as an ordinary
level-of-theory row whose ``method`` is the recipe's name (the key below); this
module is the curated record of what each recipe is.

The rule for an entry
---------------------
Every value carries a citation, and every value the cited paper or manual does
not state is ``None`` with the reason written beside it. Nothing is filled in
from memory. A ``None`` is information (the source is silent, or a primary
text could not be read) and is never a default: ``recipe_zpe_scale_factor``
in particular is ``None`` for any recipe whose scale factor is not cited
here, and a reader must not substitute 1.0.

Where a value is quoted from a secondary source because the publisher's text
could not be retrieved, the citation says so (``secondary``); the primary
paper is still named so a reviewer can check it.

Keys
----
The key is the identity key of the Gaussian / ARC spelling
(``method_identity_key``), so rows already stored under that spelling keep their
hash. ``W1``, ``W1U``, ``W1BD`` and ``W1RO`` are four keys: they are four
different recipes (UCCSD vs ROCCSD vs Brueckner doubles; a different
scalar-relativistic correction). ``CBS-QB3`` and ``ROCBS-QB3`` are two keys
(restricted-open-shell wavefunctions, no spin correction).

Program scope
-------------
``programs`` lists the programs whose manual, as held in the group knowledge
base, documents the recipe as a keyword. Only Gaussian 09 does. ORCA documents
compound protocols of its own (ORCA 5, section 9.47: ``W2-2``, ``G2-MP2`` and
its variants, the ``ccCA`` family; ORCA 6 adds the 3c methods and the Compound
scripting facility), none of which is one of the methods below: ``W2-2`` is
not ``W2`` and is not aliased to it. The Molpro 2024 / 2026 manuals document
no composite method name (explicitly correlated composite thermochemistry is a
user procedure). Nothing is claimed for ORCA or Molpro. An empty tuple means no
program keyword is established (``W1`` and ``W2`` are the Martin-de Oliveira
protocols; Gaussian implements the ``W1U`` / ``W1BD`` / ``W1RO`` variants, not a
bare ``W1``).
Gaussian 16's manual is not in the knowledge base; the Gaussian 09 manual is
cited.

Sources, in order of preference: the defining paper or its preprint, the
open-access benchmark of Kesharwani, Brauer and Martin (JPCA 2015) which quotes
each recipe's scale factor, the Gaussian manual, then the Zipse group's
teaching pages. A value taken from the last is labelled ``secondary`` in its
citation and names the paper it reports; the paper's own text was not
retrievable (the publisher returns 403).

This module is pure. The resolution path reads it
(``app/services/composite_scheme_resolution.py``) to bind a level of theory to
its named-method scheme the first time the level is resolved.

Adding or removing an entry ships with a backfill revision
----------------------------------------------------------
Binding is lazy: a level of theory that already exists is bound only when it is
next resolved, and until then it has no binding row, which a read reports as
"not composite". That is only true if no level of theory was stored before its
method joined the catalogue. So **every change to the set of keys here comes with
an Alembic revision that backfills the bindings for the levels already stored**,
frozen as ``d7a3f1b9c284`` froze the first set (its ``_NAMED_METHODS``).
``tests/services/test_composite_scheme_resolution.py`` fails when this module's key
set differs from the latest revision's frozen set, so an entry cannot land without
its backfill. Changing a value inside an existing entry does not change a scheme
row that already exists (identity rows are never updated in place).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.chemistry.method_names import method_identity_key

_G09 = "Gaussian 09 manual, g09ur/k_cbs.htm (CBS Methods) and g09ur/k_g1.htm (G1-G4)"
_G09_W1 = "Gaussian 09 manual, g09ur/k_w1u.htm (W1 methods)"
_KESHARWANI = (
    "Kesharwani, Brauer, Martin, J. Phys. Chem. A 119, 1701 (2015), doi:10.1021/jp508422u "
    "(open-access reprint webhome.weizmann.ac.il/home/comartin/OAreprints/260.pdf)"
)
_ZIPSE = "secondary: Zipse group teaching pages, zipse.cup.uni-muenchen.de, 'Overview of Gaussian theories'"
_ARC_METHODS = (
    "ARC data/ess_methods.yml (d9f47ab9), gaussian list: the spelling ARC writes for this method."
)


@dataclass(frozen=True)
class InternalLevel:
    """A level of theory the recipe runs internally.

    :param method: Method as the source writes it.
    :param basis: Basis set as the source writes it, ``None`` when the source
        gives the method alone.
    :param citations: Where the source states it.
    :param core_treatment: ``"frozen_core"`` / ``"all_electron"`` when the source
        states which electrons the level correlates; ``None`` (the default, and
        every catalogued level today) leaves it unstated, so the level resolves
        exactly as it did before the field existed.
    """

    method: str
    basis: str | None
    citations: tuple[str, ...]
    core_treatment: str | None = None


@dataclass(frozen=True)
class CompositeMethod:
    """One named composite method.

    :param key: Identity key of the Gaussian / ARC spelling.
    :param name: The method as its paper writes it.
    :param paper_doi: DOI of the defining paper, checked against Crossref.
    :param paper: Citation of the defining paper.
    :param geometry_level: Level the recipe optimises at; ``None`` when not
        stated by a source cited here.
    :param frequency_level: Level the recipe's frequencies (and ZPE) come
        from; ``None`` when not stated.
    :param recipe_zpe_scale_factor: The recipe's own ZPE scale factor;
        ``None`` unless cited.
    :param zpe_citations: Source of ``recipe_zpe_scale_factor``.
    :param programs: Programs whose manual documents it as a keyword.
    :param not_stated: One line per ``None`` field saying why it is ``None``.
    :param citations: Sources for the entry as a whole (spelling, scope).
    """

    key: str
    name: str
    paper_doi: str
    paper: str
    geometry_level: InternalLevel | None
    frequency_level: InternalLevel | None
    recipe_zpe_scale_factor: float | None
    zpe_citations: tuple[str, ...]
    programs: tuple[str, ...]
    not_stated: tuple[str, ...]
    citations: tuple[str, ...]


_B3LYP_CBSB7 = InternalLevel(
    method="B3LYP",
    basis="CBSB7",
    citations=(
        "Montgomery, Frisch, Ochterski, Petersson, J. Chem. Phys. 110, 2822 (1999), "
        "doi:10.1063/1.477924, abstract: CBS-Q 'is modified to use B3LYP hybrid density "
        "functional geometries and frequencies'.",
        "Gaussian 09 manual, Basis Sets: 'CBSB7: Selects the 6-311G(2d,d,p) basis set used "
        "by CBS-QB3 high accuracy energy method [Montgomery99]'.",
        "ARC arc/settings/settings.py:237-240 (d9f47ab9): 'B3LYP/CBSB7 ... the frequency "
        "level of CBS-QB3'.",
        _KESHARWANI + ": 'The CBS-QB3 thermochemistry protocol specifies a B3LYP/CBSB7 ZPVE "
        "scaled by 0.9900'; CBSB7 is 'effectively 6-311G(d) on first row and 6-311G(2d) on "
        "second row'.",
    ),
)

_B3LYP_631GD = InternalLevel(
    method="B3LYP",
    basis="6-31G(d)",
    citations=(
        "Baboul, Curtiss, Redfern, Raghavachari, J. Chem. Phys. 110, 7650 (1999), "
        "doi:10.1063/1.478676, abstract: 'geometries and zero-point energies are obtained "
        "from B3LYP density functional theory [B3LYP/6-31G(d)]'.",
    ),
)

_W1_B3LYP = InternalLevel(
    method="B3LYP",
    basis="cc-pVTZ+1",
    citations=(
        "Martin, de Oliveira, J. Chem. Phys. 111, 1843 (1999), doi:10.1063/1.479454; "
        "protocol text of the preprint arXiv:physics/9904038, W1 section: 'geometry "
        "optimization at the B3LYP/VTZ+1 level (B3LYP/VTZ if only first-row atoms are "
        "present)' and 'zero-point energy obtained from B3LYP/VTZ+1 ... harmonic "
        "frequencies scaled by 0.985'. VTZ+1 is cc-pVTZ with one high-exponent d "
        "function on second-row atoms.",
    ),
)

_G4_B3LYP = InternalLevel(
    method="B3LYP",
    basis="6-31G(2df,p)",
    citations=(
        "Curtiss, Redfern, Raghavachari, J. Chem. Phys. 126, 084108 (2007), "
        "doi:10.1063/1.2436888, abstract: 'optimized geometries and zero-point energies "
        "are obtained with the B3LYP density functional'.",
        _KESHARWANI + ": 'GTBas3 (effectively 6-31G(2df,p)) in G4 and G4MP2 theory'; "
        "'the 0.9854 scale factor for B3LYP/6-31G(2df,p) specified in G4 and G4MP2 theory'.",
    ),
)

_G4_ZPE = (
    _KESHARWANI + ": 'the 0.9854 scale factor for B3LYP/6-31G(2df,p) specified in G4 and "
    "G4MP2 theory agrees almost perfectly with 0.9862' (their own fit).",
)

_G3B3_ZPE = (
    _ZIPSE + ", G3B3 page: 'ZPE = 0.960 * ZPE[B3LYP/6-31G(d)]', citing Baboul et al., "
    "J. Chem. Phys. 110, 7650 (1999), doi:10.1063/1.478676, whose text was not retrievable.",
)

_G3_ZPE = (
    _ZIPSE + ": 'ZPE = 0.8929 * ZPE[HF/6-31G(d)]', citing the G3 paper "
    "(J. Chem. Phys. 109, 7764 (1998), doi:10.1063/1.477422), whose text was not "
    "retrievable.",
)

_NOT_STATED_SCALE = "recipe_zpe_scale_factor: not stated by a source cited here (abstract or manual)."

CATALOGUE: tuple[CompositeMethod, ...] = (
    CompositeMethod(
        key="cbs-qb3",
        name="CBS-QB3",
        paper_doi="10.1063/1.477924",
        paper="J. A. Montgomery Jr., M. J. Frisch, J. W. Ochterski, G. A. Petersson, "
        "J. Chem. Phys. 110, 2822 (1999): A complete basis set model chemistry. VI. "
        "Gaussian's CBS-QB3 keyword is the later re-parametrisation of this recipe "
        "(Montgomery, Ochterski, Petersson, J. Chem. Phys. 112, 6532 (2000), "
        "doi:10.1063/1.481224); the 1999 parametrisation is the obsolete keyword CBS-QB3O.",
        geometry_level=_B3LYP_CBSB7,
        frequency_level=_B3LYP_CBSB7,
        recipe_zpe_scale_factor=0.99,
        zpe_citations=(
            _KESHARWANI + ": 'The CBS-QB3 thermochemistry protocol specifies a B3LYP/CBSB7 "
            "ZPVE scaled by 0.9900'.",
            "ARC data/freq_scale_factors.yml:174-178 (d9f47ab9), source 5 = "
            "doi:10.1063/1.477924: 'the 0.99 value is the ZPE scale factor of CBS-QB3'.",
        ),
        programs=("gaussian",),
        not_stated=(),
        citations=(
            _G09,
            _ARC_METHODS,
            "Gaussian 09 manual: 'CBS-QB3 (0 K)' is 'Zero-point-corrected electronic "
            "energy: E0 = Eelec + ZPE', so the printed number includes the recipe ZPE.",
            "Gaussian 09 manual, Obsolete Keywords: 'CBS-QB3O: Uses the original "
            "parametrization [Montgomery99]. It is obsolete'; so the current CBS-QB3 "
            "keyword is the 2000 parametrisation (Montgomery 2000, "
            "doi:10.1063/1.481224, 'incorporated into the CBS-QB3 ... model chemistries').",
        ),
    ),
    CompositeMethod(
        key="rocbs-qb3",
        name="ROCBS-QB3",
        paper_doi="10.1063/1.2335438",
        paper="G. P. F. Wood, L. Radom, G. A. Petersson, E. C. Barnes, M. J. Frisch, "
        "J. A. Montgomery Jr., J. Chem. Phys. 125, 094106 (2006): A restricted-open-shell "
        "complete-basis-set model chemistry.",
        geometry_level=None,
        frequency_level=None,
        recipe_zpe_scale_factor=None,
        zpe_citations=(),
        programs=("gaussian",),
        not_stated=(
            "geometry_level, frequency_level: the abstract says the model is 'based on "
            "CBS-QB3' with spin-restricted wavefunctions for the energy components; it "
            "does not state the internal geometry or frequency level.",
            _NOT_STATED_SCALE,
        ),
        citations=(
            _G09 + ": 'ROCBS-QB3 [Wood06] is available for restricted open-shell "
            "calculations using this method'.",
            _ARC_METHODS,
            "Wood 2006 abstract: ROCBS-QB3 'eliminates the spin correction in standard "
            "CBS-QB3', so it is a different recipe from CBS-QB3 and a different key.",
        ),
    ),
    CompositeMethod(
        key="cbs-apno",
        name="CBS-APNO (CBS-QCI/APNO)",
        paper_doi="10.1063/1.467306",
        paper="J. A. Montgomery Jr., J. W. Ochterski, G. A. Petersson, J. Chem. Phys. "
        "101, 5900 (1994): A complete basis set model chemistry. IV. An improved atomic "
        "pair natural orbital method.",
        geometry_level=None,
        frequency_level=None,
        recipe_zpe_scale_factor=None,
        zpe_citations=(),
        programs=("gaussian",),
        not_stated=(
            "geometry_level, frequency_level: the abstract does not state them and the "
            "Gaussian 09 manual does not either.",
            _NOT_STATED_SCALE,
        ),
        citations=(
            _G09 + ": 'CBS-APNO is available for first row atoms only'.",
            _ARC_METHODS,
        ),
    ),
    CompositeMethod(
        key="cbs-4m",
        name="CBS-4M",
        paper_doi="10.1063/1.481224",
        paper="J. A. Montgomery Jr., J. W. Ochterski, G. A. Petersson, J. Chem. Phys. "
        "112, 6532 (2000): A complete basis set model chemistry. VII. Use of the minimum "
        "population localization method.",
        geometry_level=None,
        frequency_level=None,
        recipe_zpe_scale_factor=None,
        zpe_citations=(),
        programs=("gaussian",),
        not_stated=(
            "geometry_level, frequency_level: the abstract does not state them and the "
            "Gaussian 09 manual does not either.",
            _NOT_STATED_SCALE,
        ),
        citations=(
            _G09 + ": 'CBS-4M and CBS-QB3 are available for first and second row atoms'; "
            "'CBS-4M (M referring to the use of Minimal Population localization) is "
            "recommended for new studies'.",
            _ARC_METHODS,
        ),
    ),
    CompositeMethod(
        key="g3",
        name="G3",
        paper_doi="10.1063/1.477422",
        paper="L. A. Curtiss, K. Raghavachari, P. C. Redfern, V. Rassolov, J. A. Pople, "
        "J. Chem. Phys. 109, 7764 (1998): Gaussian-3 (G3) theory for molecules containing "
        "first and second-row atoms.",
        geometry_level=InternalLevel(
            method="MP2(FU)",
            basis="6-31G(d)",
            citations=(
                "Baboul 1999 (doi:10.1063/1.478676) abstract, describing G3: 'geometries "
                "from second-order perturbation theory [MP2(FU)/6-31G(d)]'.",
            ),
        ),
        frequency_level=InternalLevel(
            method="HF",
            basis="6-31G(d)",
            citations=(
                "Baboul 1999 (doi:10.1063/1.478676) abstract, describing G3: 'zero-point "
                "energies from Hartree-Fock theory [HF/6-31G(d)]'.",
            ),
        ),
        recipe_zpe_scale_factor=0.8929,
        zpe_citations=_G3_ZPE,
        programs=("gaussian",),
        not_stated=(),
        citations=(_G09, _ARC_METHODS),
    ),
    CompositeMethod(
        key="g3b3",
        name="G3//B3LYP (G3B3)",
        paper_doi="10.1063/1.478676",
        paper="A. G. Baboul, L. A. Curtiss, P. C. Redfern, K. Raghavachari, J. Chem. Phys. "
        "110, 7650 (1999): Gaussian-3 theory using density functional geometries and "
        "zero-point energies.",
        geometry_level=_B3LYP_631GD,
        frequency_level=_B3LYP_631GD,
        recipe_zpe_scale_factor=0.96,
        zpe_citations=_G3B3_ZPE,
        programs=("gaussian",),
        not_stated=(),
        citations=(
            _G09 + ": 'The G3 variants using B3LYP structures and frequencies [Baboul99] "
            "are requested with the G3B3 and G3MP2B3 keywords'.",
            _ARC_METHODS,
        ),
    ),
    CompositeMethod(
        key="g3mp2",
        name="G3(MP2)",
        paper_doi="10.1063/1.478385",
        paper="L. A. Curtiss, P. C. Redfern, K. Raghavachari, V. Rassolov, J. A. Pople, "
        "J. Chem. Phys. 110, 4703 (1999): Gaussian-3 theory using reduced Moller-Plesset "
        "order.",
        geometry_level=None,
        frequency_level=InternalLevel(
            method="HF",
            basis="6-31G(d)",
            citations=(
                _ZIPSE + ", G3(MP2) page: 'Optimization and frequency calculation at the "
                "HF/6-31G(d) level of theory'; 'ZPE = 0.8929 * ZPE[HF/6-31G(d)]', citing "
                "Curtiss et al., J. Chem. Phys. 110, 4703 (1999), doi:10.1063/1.478385.",
            ),
        ),
        recipe_zpe_scale_factor=0.8929,
        zpe_citations=(
            _ZIPSE + ", G3(MP2) page: 'ZPE = 0.8929 * ZPE[HF/6-31G(d)]', citing Curtiss "
            "et al., J. Chem. Phys. 110, 4703 (1999), doi:10.1063/1.478385.",
        ),
        programs=("gaussian",),
        not_stated=(
            "geometry_level: the G3(MP2) abstract does not state it, and the Zipse "
            "G3(MP2) page says 'optimization ... at HF/6-31G(d)' where the G3 page says "
            "MP2(FULL)/6-31G(d); the two are not reconciled here, so it is not filled.",
        ),
        citations=(
            _G09 + ": 'G3MP2 requests the similarly modified G3(MP2) method [Curtiss99]'.",
            _ARC_METHODS,
        ),
    ),
    CompositeMethod(
        key="g3mp2b3",
        name="G3(MP2)//B3LYP (G3MP2B3)",
        paper_doi="10.1063/1.478676",
        paper="A. G. Baboul, L. A. Curtiss, P. C. Redfern, K. Raghavachari, J. Chem. Phys. "
        "110, 7650 (1999): Gaussian-3 theory using density functional geometries and "
        "zero-point energies (the manual cites it for this keyword).",
        geometry_level=InternalLevel(
            method="B3LYP",
            basis="6-31G(d)",
            citations=(
                _G09 + ": the G3MP2B3 keyword uses 'B3LYP structures and frequencies "
                "[Baboul99]'.",
                _ZIPSE + ", G3(MP2)B3 page: 'Optimization and frequency calculation at the "
                "Becke3LYP/6-31G(d) level of theory'.",
            ),
        ),
        frequency_level=InternalLevel(
            method="B3LYP",
            basis="6-31G(d)",
            citations=(
                _G09 + ": 'B3LYP structures and frequencies [Baboul99]'.",
                _ZIPSE + ", G3(MP2)B3 page: 'Optimization and frequency calculation at the "
                "Becke3LYP/6-31G(d) level of theory'.",
            ),
        ),
        recipe_zpe_scale_factor=0.96,
        zpe_citations=(
            _ZIPSE + ", G3(MP2)B3 page: 'ZPE = 0.960 * ZPE[B3LYP/6-31G(d)]', citing "
            "Baboul et al., J. Chem. Phys. 110, 7650 (1999), doi:10.1063/1.478676.",
        ),
        programs=("gaussian",),
        not_stated=(),
        citations=(
            _G09,
            _ARC_METHODS,
            _ZIPSE + ": the page is titled and written 'G3(MP2)B3', the spelling the "
            "parenthesised alias joins.",
        ),
    ),
    CompositeMethod(
        key="g4",
        name="G4",
        paper_doi="10.1063/1.2436888",
        paper="L. A. Curtiss, P. C. Redfern, K. Raghavachari, J. Chem. Phys. 126, 084108 "
        "(2007): Gaussian-4 theory.",
        geometry_level=_G4_B3LYP,
        frequency_level=_G4_B3LYP,
        recipe_zpe_scale_factor=0.9854,
        zpe_citations=_G4_ZPE,
        programs=("gaussian",),
        not_stated=(),
        citations=(_G09, _ARC_METHODS),
    ),
    CompositeMethod(
        key="g4mp2",
        name="G4(MP2)",
        paper_doi="10.1063/1.2770701",
        paper="L. A. Curtiss, P. C. Redfern, K. Raghavachari, J. Chem. Phys. 127, 124105 "
        "(2007): Gaussian-4 theory using reduced order perturbation theory.",
        geometry_level=_G4_B3LYP,
        frequency_level=_G4_B3LYP,
        recipe_zpe_scale_factor=0.9854,
        zpe_citations=_G4_ZPE,
        programs=("gaussian",),
        not_stated=(),
        citations=(
            _G09 + ": 'G4 and G4MP2 request the fourth generation methods "
            "[Curtiss07, Curtiss07a]'.",
            _ARC_METHODS,
        ),
    ),
    CompositeMethod(
        key="w1",
        name="W1",
        paper_doi="10.1063/1.479454",
        paper="J. M. L. Martin, G. de Oliveira, J. Chem. Phys. 111, 1843 (1999): Towards "
        "standard methods for benchmark quality ab initio thermochemistry - W1 and W2 "
        "theory.",
        geometry_level=_W1_B3LYP,
        frequency_level=_W1_B3LYP,
        recipe_zpe_scale_factor=0.985,
        zpe_citations=(
            "Martin, de Oliveira 1999, preprint arXiv:physics/9904038, W1 protocol: "
            "'harmonic frequencies scaled by 0.985'.",
            _KESHARWANI + " restates it as 'The 0.985 scaling factor for B3LYP/cc-pV(T+d)Z "
            "in W1 theory' and fits about 0.989; the recipe's own value is 0.985, and its "
            "basis is cc-pVTZ+1 (cc-pVTZ with one high-exponent d function on second-row "
            "atoms), not cc-pV(T+d)Z.",
        ),
        programs=(),
        not_stated=(),
        citations=(
            "The protocol of the paper. The Gaussian 09 manual documents the W1U, W1BD and "
            "W1RO keywords (" + _G09_W1 + ") and no bare W1; ARC lists the same three. "
            "Kept apart from them: they are different recipes.",
        ),
    ),
    CompositeMethod(
        key="w1u",
        name="W1U",
        paper_doi="10.1021/ct900260g",
        paper="E. C. Barnes, G. A. Petersson, J. A. Montgomery Jr., M. J. Frisch, "
        "J. M. L. Martin, J. Chem. Theory Comput. 5, 2687 (2009): Unrestricted coupled "
        "cluster and Brueckner doubles variations of W1 theory.",
        geometry_level=None,
        frequency_level=None,
        recipe_zpe_scale_factor=None,
        zpe_citations=(),
        programs=("gaussian",),
        not_stated=(
            "geometry_level, frequency_level, recipe_zpe_scale_factor: neither the "
            "Gaussian 09 manual nor the retrievable bibliographic record states them.",
        ),
        citations=(
            _G09_W1 + ": 'The W1U keyword specifies the W1 method modified to use UCCSD "
            "instead of ROCCSD for open shell systems [Barnes09]'.",
            _ARC_METHODS,
        ),
    ),
    CompositeMethod(
        key="w1bd",
        name="W1BD",
        paper_doi="10.1021/ct900260g",
        paper="E. C. Barnes, G. A. Petersson, J. A. Montgomery Jr., M. J. Frisch, "
        "J. M. L. Martin, J. Chem. Theory Comput. 5, 2687 (2009): Unrestricted coupled "
        "cluster and Brueckner doubles variations of W1 theory.",
        geometry_level=None,
        frequency_level=None,
        recipe_zpe_scale_factor=None,
        zpe_citations=(),
        programs=("gaussian",),
        not_stated=(
            "geometry_level, frequency_level, recipe_zpe_scale_factor: neither the "
            "Gaussian 09 manual nor the retrievable bibliographic record states them.",
        ),
        citations=(
            _G09_W1 + ": 'W1BD requests a related method which replaces coupled cluster "
            "with BD [Barnes09]'.",
            _ARC_METHODS,
        ),
    ),
    CompositeMethod(
        key="w1ro",
        name="W1RO",
        paper_doi="10.1021/ct900260g",
        paper="E. C. Barnes, G. A. Petersson, J. A. Montgomery Jr., M. J. Frisch, "
        "J. M. L. Martin, J. Chem. Theory Comput. 5, 2687 (2009): Unrestricted coupled "
        "cluster and Brueckner doubles variations of W1 theory.",
        geometry_level=None,
        frequency_level=None,
        recipe_zpe_scale_factor=None,
        zpe_citations=(),
        programs=("gaussian",),
        not_stated=(
            "geometry_level, frequency_level, recipe_zpe_scale_factor: neither the "
            "Gaussian 09 manual nor the retrievable bibliographic record states them.",
        ),
        citations=(
            _G09_W1 + ": 'W1RO is the W1 method described in [Martin99] with a slightly "
            "improved scalar relativistic correction as described in [Barnes09]'.",
            _ARC_METHODS,
        ),
    ),
    CompositeMethod(
        key="w2",
        name="W2",
        paper_doi="10.1063/1.479454",
        paper="J. M. L. Martin, G. de Oliveira, J. Chem. Phys. 111, 1843 (1999): Towards "
        "standard methods for benchmark quality ab initio thermochemistry - W1 and W2 "
        "theory.",
        geometry_level=InternalLevel(
            method="CCSD(T)",
            basis="cc-pVQZ+1",
            citations=(
                "Martin, de Oliveira 1999, preprint arXiv:physics/9904038, W2 protocol: "
                "'geometry optimization at the CCSD(T)/VQZ+1 level, i.e. CCSD(T)/VQZ if "
                "only first-row atoms are present'.",
            ),
        ),
        frequency_level=None,
        recipe_zpe_scale_factor=None,
        zpe_citations=(),
        programs=(),
        not_stated=(
            "frequency_level, recipe_zpe_scale_factor: the W2 protocol takes the ZPE from "
            "'a CCSD(T)/VTZ+1 anharmonic force field or, failing that, B3LYP/VTZ+1 "
            "frequencies scaled by 0.985': two routes, so no single level or scale "
            "factor is the recipe's.",
        ),
        citations=(
            "The protocol of the paper; no program keyword for W2 is documented in the "
            "knowledge base (Gaussian 09 documents W1U, W1BD and W1RO only).",
        ),
    ),
)

BY_KEY: dict[str, CompositeMethod] = {entry.key: entry for entry in CATALOGUE}


def composite_method_for(method: str) -> CompositeMethod | None:
    """Return the catalogue entry a method name keys to, if any.

    :param method: A method name as a producer wrote it.
    :returns: The entry for ``method_identity_key(method)``, or ``None``. Only
        the curated aliases join spellings: ``W1-BD`` and
        ``cbs-qb3-paraskevas`` have no entry.
    """
    return BY_KEY.get(method_identity_key(method))
