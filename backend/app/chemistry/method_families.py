"""A documented classification of level-of-theory method names: family, and for DFT the rung.

**This is a classification, not a measure of accuracy.** A family or rung says what kind of method a name is. It does
not say how good the method is. The rung ladder covers density functionals only, so a wavefunction or composite method
sits outside it, and how accurate any method is depends on the property and the system. Nothing in TCKDB orders, ranks,
breaks a tie with, defaults to or labels "best" by a family or a rung. It is a filter fact and nothing else
(``docs/specs/browse_order.md``).

The classification is **derived** from ``level_of_theory.method`` at read time. Nothing is stored, so changing the table
needs no migration and re-classifies every level at once. It is versioned the way every versioned thing here is: a
stable name (:data:`CLASSIFICATION_NAME`) beside a separate integer version, held in a registry. A change to what a
method maps to is a new version, and the old one is no longer current.

Families
--------
``dft``
    A density functional. ``dft_rung`` is 1 to 5: LDA 1, GGA 2, meta-GGA 3, hybrid 4 (global, range-separated and
    meta-hybrid alike), double hybrid 5.
``wavefunction``
    Single-reference wave-function methods: Hartree-Fock, Moller-Plesset, coupled cluster, CI, and their F12 and
    local-correlation variants.
``multireference``
    Methods built on a multi-configurational reference: CASSCF, CASPT2, NEVPT2, MRCI.
``composite``
    Named recipes that combine several calculations: Gn, CBS-n, Wn, HEAT.
``semiempirical``
    Parametrised approximate electronic structure: AM1, PM6, GFN-xTB, DFTB.
``force_field``
    Classical force fields.
``unclassified``
    Everything the table does not name. A method is never guessed into a family.

How a name is looked up
-----------------------
The method goes through the identity key every method comparison uses
(:func:`~app.chemistry.method_names.method_identity_key`: case, whitespace and the curated aliases), then a trailing
**dispersion suffix** (``-d2``, ``-d3``, ``-d3bj``, ``-d3zero``, ``-d3(bj)``, ``-d3(0)``, ``-d3m``, ``-d3mbj``, ``-d4``,
``-gd2``, ``-gd3``, ``-gd3bj``) is removed, and the remainder is looked up. A dispersion suffix never changes the class.
A basis is a separate column and is never part of the method. A bare ``-d`` is NOT stripped: ``wb97x-d`` and ``b97-d``
are functionals in their own right and are table entries. The SQL twin
(``scientific_read/lot_identity_filters.method_stem_key_sql``) is built from the same suffix rule, and
``method_family_matches`` turns the table into a predicate, so a filter and this function cannot disagree.

Decisions the owner may want to check (every entry is in the table below)
-------------------------------------------------------------------------
* Range-separated hybrids (CAM-B3LYP, LC-wPBE, wB97X, HSE) and meta-hybrids (M06, M06-2X, wB97M-V) are rung 4, with the
  global hybrids.
* B97-D, B97-3c and PBE-based functionals without exact exchange are rung 2; B97-1 and B97-2 contain exact exchange and
  are rung 4.
* r2SCAN-3c is rung 3 (meta-GGA, no exact exchange); PBEh-3c is rung 4 (hybrid).
* XYG3 and the other XYG double hybrids are left out on purpose: they are evaluated on B3LYP orbitals, so their
  provenance needs a decision first. They stay ``unclassified``.
* HF-3c and machine-learned potentials are left out, so they are ``unclassified``.
* ``casscf(n,m)`` with the active space written in the method name is not recognised; the bare name is.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum

from tckdb_schemas.fragments.version import VersionRef

from app.chemistry.method_names import method_identity_key
from app.versioning import VersionRegistry

#: Stable name of the classification. The version is separate (:data:`CLASSIFICATION_VERSION`), never part of this.
CLASSIFICATION_NAME = "method_family_classification"
CLASSIFICATION_VERSION = 1


class MethodFamily(str, Enum):
    dft = "dft"
    wavefunction = "wavefunction"
    composite = "composite"
    multireference = "multireference"
    semiempirical = "semiempirical"
    force_field = "force_field"
    unclassified = "unclassified"


#: The lowest and highest rung of the DFT ladder.
MIN_DFT_RUNG = 1
MAX_DFT_RUNG = 5

#: Density functionals by rung. Keys are method identity keys (lower case, after the curated aliases), with no trailing
#: dispersion suffix.
DFT_BY_RUNG: dict[int, tuple[str, ...]] = {
    # 1: local density approximation
    1: ("lda", "lsda", "svwn", "svwn5", "svwn3", "vwn", "slater", "hfs", "xalpha"),
    # 2: generalised gradient approximation, no exact exchange
    2: (
        "pbe",
        "pbepbe",
        "blyp",
        "bp86",
        "bpbe",
        "bpw91",
        "pw91",
        "pwpw91",
        "pw91pw91",
        "revpbe",
        "rpbe",
        "pbesol",
        "olyp",
        "xlyp",
        "b97",
        "b97-d",
        "b97-3c",
        "hcth",
        "hcth407",
        "hcth120",
        "hcth147",
        "n12",
        "sogga11",
        "mpwpw91",
    ),
    # 3: meta-GGA, no exact exchange
    3: (
        "tpss",
        "tpsstpss",
        "revtpss",
        "scan",
        "rscan",
        "r2scan",
        "r2scan-3c",
        "m06-l",
        "m06l",
        "m11-l",
        "mn12-l",
        "mn15-l",
        "vsxc",
        "b97m-v",
        "tau-hcth",
        "tauhcth",
    ),
    # 4: hybrid: global, range-separated and meta-hybrid
    4: (
        "b3lyp",
        "b3lyp5",
        "ub3lyp",
        "rb3lyp",
        "rob3lyp",
        "b3pw91",
        "b3p86",
        "b1lyp",
        "b1b95",
        "pbe0",
        "pbe1pbe",
        "pbeh1pbe",
        "pbeh-3c",
        "revpbe0",
        "b97-1",
        "b97-2",
        "bhandh",
        "bhandhlyp",
        "bhlyp",
        "mpw1pw91",
        "mpw1k",
        "mpw1lyp",
        "mpw1pbe",
        "mpw1b95",
        "x3lyp",
        "o3lyp",
        "apfd",
        "tpssh",
        "tpss0",
        "tpss1kcis",
        "revtpssh",
        "scan0",
        "r2scan0",
        "r2scanh",
        "r2scan50",
        "pw6b95",
        "pwb6k",
        "bmk",
        "m05",
        "m05-2x",
        "m052x",
        "m06",
        "m06-hf",
        "m06hf",
        "m062x",
        "m08-hx",
        "m08-so",
        "m11",
        "mn12-sx",
        "mn15",
        "sogga11-x",
        "cam-b3lyp",
        "camb3lyp",
        "lc-wpbe",
        "lcwpbe",
        "lc-ωpbe",
        "lc-blyp",
        "lrc-wpbe",
        "lrc-wpbeh",
        "hse03",
        "hse06",
        "hse",
        "hseh1pbe",
        "wb97",
        "wb97x",
        "wb97xd",
        "wb97x-v",
        "wb97m",
        "wb97m-v",
        "ωb97",
        "ωb97x",
        "ωb97x-d",
        "ωb97x-v",
        "ωb97m-v",
    ),
    # 5: double hybrid
    5: (
        "b2plyp",
        "b2plypd",
        "b2plypd3",
        "b2gpplyp",
        "b2gp-plyp",
        "b2k-plyp",
        "b2t-plyp",
        "mpw2plyp",
        "dsd-blyp",
        "dsd-pbep86",
        "dsd-pbeb95",
        "revdsd-pbep86",
        "revdsd-pbepbe",
        "pwpb95",
        "pbe0-dh",
        "pbe0-2",
        "pbe-qidh",
    ),
}

#: Every other family. Same key rules as :data:`DFT_BY_RUNG`.
OTHER_BY_FAMILY: dict[MethodFamily, tuple[str, ...]] = {
    MethodFamily.wavefunction: (
        "hf",
        "rhf",
        "uhf",
        "rohf",
        "mp2",
        "ump2",
        "rmp2",
        "romp2",
        "mp2.5",
        "mp3",
        "mp4",
        "mp4(sdq)",
        "mp4(sdtq)",
        "scs-mp2",
        "sos-mp2",
        "lmp2",
        "ri-mp2",
        "df-mp2",
        "dlpno-mp2",
        "mp2-f12",
        "df-mp2-f12",
        "ccd",
        "cc2",
        "cc3",
        "ccsd",
        "ccsd(t)",
        "ccsdt",
        "ccsdt(q)",
        "ccsdtq",
        "rccsd(t)",
        "uccsd(t)",
        "lccsd(t)",
        "ccsd-f12",
        "ccsd-f12a",
        "ccsd-f12b",
        "ccsd(t)-f12",
        "ccsd(t)-f12a",
        "ccsd(t)-f12b",
        "ccsd(t)-f12c",
        "ccsd(t*)-f12",
        "ccsd(t*)-f12a",
        "ccsd(t*)-f12b",
        "rccsd(t)-f12",
        "uccsd(t)-f12",
        "pno-lccsd(t)-f12",
        "dlpno-ccsd",
        "dlpno-ccsd(t)",
        "dlpno-ccsd(t1)",
        "dlpno-ccsd(t)-f12",
        "cisd",
        "qcisd",
        "qcisd(t)",
        "bd",
        "bd(t)",
        "ci",
        "fci",
    ),
    MethodFamily.multireference: (
        "mcscf",
        "casscf",
        "rasscf",
        "caspt2",
        "ms-caspt2",
        "xms-caspt2",
        "rs2",
        "rs2c",
        "nevpt2",
        "sc-nevpt2",
        "pc-nevpt2",
        "mrpt2",
        "mrci",
        "mrci+q",
        "mrci(q)",
        "mrcisd",
        "mrcisd+q",
        "mrci-f12",
        "mrci-f12+q",
        "icmrci",
        "icmrci+q",
    ),
    MethodFamily.composite: (
        "g1",
        "g2",
        "g3",
        "g4",
        "g2mp2",
        "g3mp2",
        "g3b3",
        "g3mp2b3",
        "g4mp2",
        "g3x",
        "cbs-qb3",
        "rocbs-qb3",
        "cbs-4m",
        "cbs-apno",
        "w1",
        "w1u",
        "w1bd",
        "w1ro",
        "w2",
        "w2u",
        "w3",
        "w4",
        "heat",
        "heat-345q",
        "heat-345(q)",
        "heat-345qp",
    ),
    MethodFamily.semiempirical: (
        "am1",
        "pm3",
        "pm6",
        "pm6-dh2",
        "pm7",
        "rm1",
        "mndo",
        "indo",
        "cndo",
        "xtb",
        "gfn0-xtb",
        "gfn1-xtb",
        "gfn2-xtb",
        "dftb",
        "dftb2",
        "dftb3",
        "scc-dftb",
    ),
    MethodFamily.force_field: (
        "mm2",
        "mm3",
        "mm4",
        "mm+",
        "mmff",
        "mmff94",
        "mmff94s",
        "uff",
        "gaff",
        "gaff2",
        "amber",
        "opls",
        "opls-aa",
        "charmm",
        "dreiding",
        "reaxff",
        "gfn-ff",
        "gfnff",
    ),
}

#: Trailing dispersion corrections that never change a class. Longest alternatives first is not needed: the pattern is
#: anchored at the end and the alternatives are whole tokens.
DISPERSION_SUFFIXES: tuple[str, ...] = (
    "d2",
    "d3",
    "d3bj",
    "d3zero",
    "d3(bj)",
    "d3(0)",
    "d3m",
    "d3mbj",
    "d4",
    "gd2",
    "gd3",
    "gd3bj",
)
#: ``(stem)-(suffix)`` over a method key; the replacement keeps the stem. A pattern needs a non-empty stem.
DISPERSION_STRIP_PATTERN: str = "^(.+)-(?:" + "|".join(re.escape(s) for s in DISPERSION_SUFFIXES) + ")$"
_DISPERSION_STRIP = re.compile(DISPERSION_STRIP_PATTERN, re.DOTALL)


@dataclass(frozen=True)
class MethodClass:
    """What a method name is classified as: its family, and for DFT its rung (``None`` otherwise)."""

    family: MethodFamily
    dft_rung: int | None = None


UNCLASSIFIED = MethodClass(MethodFamily.unclassified)


def _build_table() -> dict[str, MethodClass]:
    table: dict[str, MethodClass] = {}

    def add(key: str, cls: MethodClass) -> None:
        if key in table:
            raise ValueError(f"{CLASSIFICATION_NAME}: {key!r} is listed twice")
        table[key] = cls

    for rung, keys in DFT_BY_RUNG.items():
        for key in keys:
            add(key, MethodClass(MethodFamily.dft, rung))
    for family, keys in OTHER_BY_FAMILY.items():
        for key in keys:
            add(key, MethodClass(family))
    return table


#: The table, one row per method key.
CLASSIFICATION: dict[str, MethodClass] = _build_table()

#: The registry the classification is held in: one name, one current version. A change to a method's class is a new
#: version, and a response says which one classified it.
CLASSIFICATIONS: VersionRegistry[dict[str, MethodClass]] = VersionRegistry(
    "method_family_classifications", startup_checked=True
)
CLASSIFICATIONS.register(CLASSIFICATION_NAME, CLASSIFICATION_VERSION, CLASSIFICATION, current=True)
CLASSIFICATIONS.validate()

#: The ref a response carries.
CLASSIFICATION_REF: VersionRef = CLASSIFICATIONS.current(CLASSIFICATION_NAME)


def method_stem_key(name: str) -> str:
    """The key a method is classified under: its identity key without a trailing dispersion suffix."""
    key = method_identity_key(name)
    stem = _DISPERSION_STRIP.sub(r"\1", key)
    # A stem can be an aliased spelling (``m06-2x-d3zero`` -> ``m06-2x`` -> ``m062x``).
    return method_identity_key(stem) if stem != key else key


def classify_method(name: str | None, version: int | None = None) -> MethodClass:
    """The class of a method name; ``unclassified`` for ``None``, blank, or a name the table does not hold."""
    if name is None or not name.strip():
        return UNCLASSIFIED
    return CLASSIFICATIONS.get(CLASSIFICATION_NAME, version).get(method_stem_key(name), UNCLASSIFIED)


@dataclass(frozen=True)
class MethodClassFilter:
    """A request to keep levels by family and/or DFT rung.

    ``families`` is the set of families to keep (``None``: any). A rung bound implies ``dft``: with a bound, only the
    ``dft`` family can match, so a ``families`` set without ``dft`` matches nothing. A lower bound above the upper
    matches nothing.
    """

    families: frozenset[MethodFamily] | None = None
    dft_rung_min: int | None = None
    dft_rung_max: int | None = None

    @classmethod
    def of(
        cls, families: "Collection[MethodFamily] | None", dft_rung_min: int | None, dft_rung_max: int | None
    ) -> "MethodClassFilter":
        """A filter from the three request fields (a request without them filters nothing)."""
        return cls(
            families=None if families is None else frozenset(MethodFamily(f) for f in families),
            dft_rung_min=dft_rung_min,
            dft_rung_max=dft_rung_max,
        )

    @classmethod
    def from_request(cls, request: object) -> "MethodClassFilter":
        """The filter a request's ``method_family``, ``dft_rung_min`` and ``dft_rung_max`` fields state."""
        return cls.of(
            getattr(request, "method_family", None),
            getattr(request, "dft_rung_min", None),
            getattr(request, "dft_rung_max", None),
        )

    @property
    def active(self) -> bool:
        return self.families is not None or self.dft_rung_min is not None or self.dft_rung_max is not None

    def keys(self) -> tuple[frozenset[str], bool]:
        """``(table keys kept, whether unclassified names are kept)``."""
        table = CLASSIFICATIONS.get(CLASSIFICATION_NAME)
        bounded = self.dft_rung_min is not None or self.dft_rung_max is not None
        families = self.families
        if bounded:
            families = frozenset({MethodFamily.dft}) & (families if families is not None else {MethodFamily.dft})
        if families is None:
            return frozenset(table), True
        lo = self.dft_rung_min if self.dft_rung_min is not None else MIN_DFT_RUNG
        hi = self.dft_rung_max if self.dft_rung_max is not None else MAX_DFT_RUNG
        keep: set[str] = set()
        for key, cls in table.items():
            if cls.family not in families:
                continue
            if cls.family is MethodFamily.dft and not (lo <= (cls.dft_rung or 0) <= hi):
                continue
            keep.add(key)
        return frozenset(keep), MethodFamily.unclassified in families and not bounded

    def matches(self, name: str | None) -> bool:
        """In-memory twin of the SQL predicate (``lot_identity_filters.method_family_matches``)."""
        if not self.active:
            return True
        keys, unclassified = self.keys()
        if name is None or not name.strip():
            return unclassified
        stem = method_stem_key(name)
        if stem in CLASSIFICATIONS.get(CLASSIFICATION_NAME):
            return stem in keys
        return unclassified


__all__ = [
    "CLASSIFICATION",
    "CLASSIFICATIONS",
    "CLASSIFICATION_NAME",
    "CLASSIFICATION_REF",
    "CLASSIFICATION_VERSION",
    "DFT_BY_RUNG",
    "DISPERSION_STRIP_PATTERN",
    "MAX_DFT_RUNG",
    "MIN_DFT_RUNG",
    "OTHER_BY_FAMILY",
    "UNCLASSIFIED",
    "MethodClass",
    "MethodClassFilter",
    "MethodFamily",
    "classify_method",
    "method_stem_key",
]
