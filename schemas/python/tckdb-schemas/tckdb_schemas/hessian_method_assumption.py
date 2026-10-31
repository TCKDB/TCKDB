"""Assume a frequency job's Hessian method when the record does not say.

**Where this applies (owner decision 2026-10-31).** This table is the one
owner of the assumption, and it is applied where tau is *decided*: at upload
validation, through :func:`resolve_tau_with_assumption`, by every adapter that
judges a calculation's imaginary modes. Persistence then stores exactly the
tau and basis that validation judged. Before 2026-10-31 the assumption was
applied only after validation, at persistence, so a record could be judged at
the conservative 50 cm-1 and stored at an assumed 30.

ADR 0012's original rule was strict: an unrecorded ``freq.hessian_method``
resolves to the conservative ``protocol_not_recorded`` row of the tau
table, full stop. Measured 2026-09-04: 0 of 132 stored frequency results
carry that parameter, because Gaussian never names its analytic default
in the output and ``app/services/gaussian_parameter_parser.py`` records
only explicit statements (``Freq=Numer``) — so the conservative row was,
in practice, *every* Gaussian record TCKDB has ever stored.

The archive owner's 2026-09-04 amendment (see the addendum to
``docs/adr/0012-imaginary-modes-are-judged-by-magnitude-not-counted.md``):
when the method is not recorded, TCKDB may *assume* the producing
program's documented default for the level-of-theory's method family,
record that it was assumed, and give the assumed method the same tau as
its recorded counterpart. This module is that assumption, and nothing
else — it does not touch tau's magnitude, only which basis is credited
and whether one is credited at all.

**Pure function, table-driven.** Takes two plain strings (a software
name and a level-of-theory method), returns a
:class:`~tckdb_schemas.stationary_point.TauResolution` carrying one of
the three ``assumed_*`` bases, or ``None`` when the pair is not in the
table. ``None`` is the conservative default for everything this module
was not explicitly taught: an unrecognised program, an unrecognised
method, or a program with no analytic-Hessian convention worth assuming
(see "Why some things stay ``None``" below). No I/O, no ORM, no
Pydantic — callers extract the two scalars from wherever they hold them
(:mod:`app.services.calculation_resolution` at upload time, and
``backend/scripts/ops/backfill_assumed_tau.py`` for stored rows) and pass
them in.

**An assumption is never a recorded statement.** ``resolve_tau`` (in
``tckdb_schemas.stationary_point``) is untouched by this module and stays
the single owner of what a *recorded* ``freq.hessian_method`` means; this
module only ever fires on its conservative fallback, and only when the
caller has independently confirmed no method was recorded. A later
upload that states the method explicitly is read by ``resolve_tau``
exactly as before and always wins over anything this module would have
assumed.

The table
---------

Gaussian (``Software.name == "Gaussian"``, matched case-insensitively —
see :func:`tckdb_schemas.software.normalize_software_name`):

* **Analytic** (`TauBasis.assumed_analytic_default`): HF, any DFT
  functional, MP2, CIS, CASSCF. Gaussian has computed analytic second
  derivatives for these method families by default since at least G03;
  a user gets `Freq=Numer` only by asking for it.
* **Finite-difference-from-gradients**
  (`TauBasis.assumed_finite_difference_gradient`): MP3, MP4 (any
  variant — SDQ, SDTQ, DQ), QCISD, CCSD, CISD, CID (exactly CIS is analytic;
  CISD/CID have numerical frequencies only). None of these have an analytic
  Hessian implementation in Gaussian; the default frequency job
  numerically differentiates the analytic gradient, which these methods
  do have.
* **Finite-difference-from-energies**
  (`TauBasis.assumed_finite_difference_energy`): CCSD(T) and QCISD(T).
  Neither has an analytic *or* semi-analytic (gradient-based) second
  derivative in Gaussian; the default frequency job double-differentiates
  the energy.

ORCA (``Software.name == "ORCA"``):

* **Analytic** (`TauBasis.assumed_analytic_default`): HF, any DFT
  functional. ORCA computes analytic Hessians for both by default.
* **Finite-difference-from-gradients**
  (`TauBasis.assumed_finite_difference_gradient`): correlated
  wavefunction methods without an analytic Hessian in ORCA — MP2
  variants (MP2, RI-MP2, DLPNO-MP2, SCS-MP2), coupled cluster (CCSD,
  CCSD(T)), and every ``DLPNO-*`` method. ORCA's default `NumFreq` for
  these differentiates the analytic gradient, which all of them have;
  the table does not split a gradient/energy sub-case for ORCA because
  the amendment's own wording ("finite-difference for correlated
  wavefunction methods") does not ask for one, and ORCA does not
  publish a documented energy-only fallback the way Gaussian's CCSD(T)
  path is documented.

Why some things stay ``None``
------------------------------
Every software not spelled "Gaussian" or "ORCA" (Molpro, Arkane, ARC,
RMG, an empty or garbled name) returns ``None`` regardless of method —
this module was not asked to research Molpro's or Arkane's defaults, and
guessing one would be exactly the "assume the better case" mistake ADR
0012's original conservative fallback exists to avoid. Likewise a method
string this module's recognisers do not match (an unfamiliar functional
spelling, a composite method, a semi-empirical method, garbage) returns
``None`` rather than a best-effort guess: the table is a documented
allowlist, not a classifier that tries to cover everything.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from tckdb_schemas.enums import HessianMethod
from tckdb_schemas.hessian_method_detection import (
    check_recorded_method_agrees_with_deck,
    deck_hessian_method,
)
from tckdb_schemas.software import normalize_software_name
from tckdb_schemas.stationary_point import (
    TAU_ANALYTIC_DEFAULT_CM1,
    TAU_FINITE_DIFFERENCE_ENERGY_CM1,
    TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
    TauBasis,
    TauResolution,
    resolve_tau_from_parameters,
)

__all__ = [
    "MethodFamily",
    "classify_method_family",
    "infer_hessian_method",
    "is_dft_functional",
    "calculation_deck_method",
    "check_calculation_deck_agrees",
    "resolve_tau_for_calculation",
    "resolve_tau_with_assumption",
]


# ---------------------------------------------------------------------------
# Method-family recognition
#
# No reusable generic "is this method DFT / MP2 / CC" classifier exists
# elsewhere in the tree (checked: app/chemistry and app/services). The
# nearest relative is app/services/orca_parameter_parser.py's
# `_METHOD_PREFIXES` / `_is_method_token`, which classifies raw ORCA `!`
# line keywords into level-of-theory vs. parameter buckets for *parsing*
# ORCA input -- a different job with a different input shape (it never
# sees "wb97xd" or "m062x" written without a hyphen, both of which a
# depositor's `level_of_theory.method` field legitimately holds), so it
# is not reused here. This recogniser is deliberately small and explicit
# instead, normalising away the hyphen/no-hyphen and case variation that
# free-text `level_of_theory.method` values carry.
# ---------------------------------------------------------------------------


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    # Method tokens are compared with hyphens stripped as well as
    # lower-cased, so "wb97xd", "wb97x-d", and "wb97x_d" all match the
    # same prefix -- free-text `level_of_theory.method` values are not
    # normalised at write time, and depositors spell these differently.
    stripped = value.strip().lower()
    if not stripped:
        return None
    return stripped.replace("_", "-")


def _startswith_any(token: str, prefixes: tuple[str, ...]) -> bool:
    return any(token.startswith(prefix) for prefix in prefixes)


#: Hartree-Fock, any spin treatment.
_HF_METHODS = frozenset({"hf", "rhf", "uhf", "rohf"})

#: Configuration-interaction-singles and its common spellings. Checked
#: before nothing else collides with "cis".
_CIS_METHODS = frozenset({"cis", "ucis", "rcis", "rocis"})

#: CISD and CID: Gaussian has no analytic second derivatives for these (the
#: Freq documentation lists numerical frequencies only), unlike CIS. Matched
#: before anything that could mistake them for a CIS prefix.
_CISD_PREFIXES = ("cisd", "ucisd", "rcisd", "cid", "ucid", "rcid")

#: Complete active-space SCF. Deliberately excludes CASPT2 and NEVPT2,
#: which are post-CASSCF correlation treatments this module has not been
#: taught a Gaussian/ORCA analytic-Hessian answer for.
_CASSCF_PREFIXES = ("casscf",)

#: MP2 and its common restricted/resolution-of-identity/local variants.
#: Checked before MP3/MP4 so "mp2" cannot be shadowed by a broader "mp"
#: prefix (there is none here, but the ordering documents the intent).
_MP2_PREFIXES = (
    "mp2", "ri-mp2", "rimp2", "dlpno-mp2", "scs-mp2", "sos-mp2", "oo-mp2",
)

_MP3_PREFIXES = ("mp3",)

#: MP4 and its truncations: mp4(sdq), mp4(sdtq), mp4(dq).
_MP4_PREFIXES = ("mp4",)

#: QCISD(T) is checked before bare QCISD so the "(t)" tail is not lost to
#: the shorter prefix.
_QCISD_T_PREFIXES = ("qcisd(t)",)
_QCISD_PREFIXES = ("qcisd",)

#: CCSD(T) (and its F12/DLPNO-tagged relatives) before bare CCSD, for the
#: same reason as QCISD(T)/QCISD.
_CCSD_T_PREFIXES = (
    "ccsd(t)", "dlpno-ccsd(t)", "dlpno-ccsd(t1)", "ccsd(t)-f12",
    "dlpno-ccsd(t)-f12",
)
_CCSD_PREFIXES = ("ccsd", "dlpno-ccsd")

#: DLPNO-tagged local correlation methods not already matched above by a
#: more specific CC/MP2 prefix -- e.g. DLPNO-CEPA, DLPNO-QCISD(T) should
#: a depositor write one. Matched last, as the family's catch-all.
_DLPNO_PREFIXES = ("dlpno-",)

#: Common DFT functionals and their frequent spellings, hyphen and
#: no-hyphen alike (this module normalises hyphens away before matching,
#: so "wb97xd" and "wb97x-d" both match "wb97xd"/"wb97x-d" style
#: entries; both spellings are listed anyway for readability). Not
#: exhaustive -- new functionals are added here as they are needed,
#: never guessed for.
_DFT_PREFIXES = (
    "b3lyp", "b3pw91", "o3lyp", "x3lyp", "bhandhlyp", "bhandh",
    "pbe0", "pbe", "bp86", "bpbe", "blyp", "svwn", "pw91",
    "tpssh", "tpss", "revtpss",
    "m06-2x", "m062x", "m06-l", "m06l", "m06-hf", "m06hf", "m06",
    "m05-2x", "m052x", "m05",
    "wb97x-d3", "wb97xd3", "wb97x-d", "wb97xd", "wb97x-v", "wb97xv",
    "wb97x", "wb97-d3", "wb97",
    "cam-b3lyp", "camb3lyp",
    "b97-d3", "b97-d", "b97",
    "scan", "r2scan", "b2plyp", "mpw1k", "mpwb1k", "bmk",
    "lc-wpbe", "hse06", "pw6b95",
)


class MethodFamily:
    """Sentinel tokens :func:`classify_method_family` can return.

    Not an :class:`enum.Enum`: this is an internal classification with no
    wire presence, kept as plain string constants so the dispatch tables
    in :func:`infer_hessian_method` can key off them directly.
    """

    HF = "hf"
    DFT = "dft"
    MP2 = "mp2"
    MP3 = "mp3"
    MP4 = "mp4"
    CIS = "cis"
    CISD = "cisd"
    CASSCF = "casscf"
    QCISD = "qcisd"
    QCISD_T = "qcisd(t)"
    CCSD = "ccsd"
    CCSD_T = "ccsd(t)"
    DLPNO_OTHER = "dlpno_other"


def is_dft_functional(method: str | None) -> bool:
    """True when ``method`` is a recognised DFT functional token.

    Exposed on its own because "is this a DFT functional" is the one
    piece of this module's table other code is likely to want directly
    (a trust rubric, a future report) without the rest of the family
    dispatch.
    """
    token = _normalize(method)
    if token is None:
        return False
    return _startswith_any(token, _DFT_PREFIXES)


def classify_method_family(method: str | None) -> str | None:
    """Classify a free-text ``level_of_theory.method`` into a family.

    Order matters: methods sharing a prefix are checked longest/most
    specific first (``ccsd(t)`` before ``ccsd``, ``qcisd(t)`` before
    ``qcisd``) so a truncation is never mistaken for its parent method.

    :param method: Free-text method, as stored on ``level_of_theory``.
    :returns: A :class:`MethodFamily` token, or ``None`` when the method
        does not match anything this module recognises.
    """
    token = _normalize(method)
    if token is None:
        return None

    if token in _HF_METHODS:
        return MethodFamily.HF
    if token in _CIS_METHODS:
        return MethodFamily.CIS
    if _startswith_any(token, _CISD_PREFIXES):
        return MethodFamily.CISD
    if _startswith_any(token, _CASSCF_PREFIXES):
        return MethodFamily.CASSCF
    if _startswith_any(token, _CCSD_T_PREFIXES):
        return MethodFamily.CCSD_T
    if _startswith_any(token, _QCISD_T_PREFIXES):
        return MethodFamily.QCISD_T
    if _startswith_any(token, _CCSD_PREFIXES):
        return MethodFamily.CCSD
    if _startswith_any(token, _QCISD_PREFIXES):
        return MethodFamily.QCISD
    if _startswith_any(token, _MP4_PREFIXES):
        return MethodFamily.MP4
    if _startswith_any(token, _MP3_PREFIXES):
        return MethodFamily.MP3
    if _startswith_any(token, _MP2_PREFIXES):
        return MethodFamily.MP2
    if is_dft_functional(token):
        return MethodFamily.DFT
    if _startswith_any(token, _DLPNO_PREFIXES):
        return MethodFamily.DLPNO_OTHER
    return None


# ---------------------------------------------------------------------------
# Software -> family -> assumed basis
# ---------------------------------------------------------------------------

#: Gaussian families whose default frequency job is analytic.
_GAUSSIAN_ANALYTIC = frozenset({
    MethodFamily.HF, MethodFamily.DFT, MethodFamily.MP2,
    MethodFamily.CIS, MethodFamily.CASSCF,
})
#: Gaussian families whose default is finite differences of the analytic
#: gradient (no analytic Hessian, but an analytic gradient exists).
_GAUSSIAN_FINITE_DIFFERENCE_GRADIENT = frozenset({
    MethodFamily.MP3, MethodFamily.MP4, MethodFamily.QCISD,
    MethodFamily.CCSD, MethodFamily.CISD,
})
#: Gaussian families with neither an analytic Hessian nor an analytic
#: gradient documented -- the default frequency job differentiates the
#: energy twice.
_GAUSSIAN_FINITE_DIFFERENCE_ENERGY = frozenset({
    MethodFamily.CCSD_T, MethodFamily.QCISD_T,
})

#: ORCA families whose default frequency job is analytic.
_ORCA_ANALYTIC = frozenset({MethodFamily.HF, MethodFamily.DFT})
#: ORCA's correlated-wavefunction families: no analytic Hessian, so the
#: default `NumFreq` differentiates the analytic gradient (all of MP2,
#: CC and DLPNO-* have one in ORCA).
_ORCA_FINITE_DIFFERENCE_GRADIENT = frozenset({
    MethodFamily.MP2, MethodFamily.CCSD, MethodFamily.CCSD_T,
    MethodFamily.DLPNO_OTHER,
})

# ---------------------------------------------------------------------------
# ORCA: functional class and release gate
#
# Sources (ORCA manuals read through the group knowledge base, 2026-10-31):
#
# * meta-GGA: ORCA 5.0.4 manual, "ORCA 5.0 Foreword" (pp. 29-38): "Meta-GGA
#   analytic Hessians are now available." So below 5 a meta-GGA frequency job
#   cannot be analytic and is assumed numerical; 5 and later is analytic.
# * VV10 / DFT-NL: ORCA 5.0.4 manual, 9.4.2.10 "DFT-NL", "Self-consistent
#   computations with the DFT-NL dispersion correction" (pp. 519-520), and
#   ORCA 6.1 manual, "Non-Local Dispersion Correction (VV10): DFT-NL" (pp.
#   263-265): analytic gradients exist, but "any calculation that requires
#   second derivatives of the NL functional is not yet possible", analytic
#   Hessians included. The manuals confirm that frequencies for these
#   functionals can only be numerical (from the analytic gradient), on every
#   release from 5.0.4 to 6.1, so they are assumed
#   ``assumed_finite_difference_gradient`` on any ORCA version, known or not.
# * double hybrids: ORCA 5.0.4 manual, 8.1.4.5 (pp. 155-156): double-hybrid DFT
#   is "available for ... numerical polarizabilities and frequencies". The
#   manual names numerical frequencies as what is available, so they are
#   assumed ``assumed_finite_difference_gradient`` on any ORCA version. (No
#   statement about an analytic double-hybrid Hessian was found in the 5.0.4
#   or 6.1 manuals.)
# * MP2: ORCA 6.1 manual, "MP2 and RI-MP2 Second Derivatives" (p. 290): the
#   analytic (RI-)MP2 Hessians are no longer available. ORCA MP2 stays in the
#   finite-difference-from-gradients row.
#
# Name lists are explicit and conservative; a name in no list is a plain
# GGA/hybrid only if the family classifier already calls it DFT. Overlaps take
# the more conservative row: VV10 and double hybrid before meta-GGA.
# ---------------------------------------------------------------------------

#: VV10 / non-local correlation functionals (and the ``-NL`` / ``-SCNL`` suffixes).
_ORCA_VV10_PREFIXES = (
    "wb97x-v", "wb97xv", "wb97m-v", "wb97mv", "b97m-v", "b97mv", "vv10",
)
_ORCA_VV10_SUFFIXES = ("-nl", "-scnl")

#: Double-hybrid functionals (also matches dispersion-suffixed spellings).
_ORCA_DOUBLE_HYBRID_PREFIXES = (
    "b2plyp", "b2gp-plyp", "b2gpplyp", "b2k-plyp", "b2t-plyp", "mpw2plyp",
    "dsd-", "revdsd", "pwpb95", "pbe0-dh", "pbe0-2", "pbe-qidh",
    "wb2plyp", "wb2gpplyp", "ub2plyp", "wb97x-2", "wb97m(2)", "wb97m-2",
    "pr2scan50", "kpr2scan50", "pr2scan69", "wpr2scan50", "rsx-qidh",
    "rsx-0dh",
)

#: Meta-GGA and meta-hybrid functionals (tau-dependent).
_ORCA_META_GGA_PREFIXES = (
    "m06", "m05", "m11", "mn12", "mn15", "tpss", "revtpss", "scan", "rscan",
    "r2scan", "bmk", "pw6b95", "b97m", "wb97m",
    # Meta-hybrids the DFT classifier recognises or Gaussian-style inputs may
    # name. ORCA's keyword lists (5.0.4, "Keyword Lines" and "Choice of
    # Functional") do not offer the last four, but a meta functional must never
    # get the analytic assumption on ORCA < 5 or an unknown version.
    "mpwb1k", "mpw1b95", "bb1k", "mpw1kcis",
)

_ORCA_ANALYTIC_META_GGA_FROM = 5

_VERSION_MAJOR = re.compile(r"^\s*v?(\d+)")


#: A leading integer outside this range is not an ORCA major version ("45",
#: "2024.1" are a build number or a calendar version) and counts as unknown.
_ORCA_MAJOR_RANGE = range(1, 21)


def _orca_major_version(version: str | None) -> int | None:
    if version is None:
        return None
    m = _VERSION_MAJOR.match(version)
    if not m:
        return None
    major = int(m.group(1))
    return major if major in _ORCA_MAJOR_RANGE else None


def _orca_functional_class(token: str) -> str:
    """``"vv10"``, ``"double_hybrid"``, ``"meta_gga"`` or ``"plain"`` for a DFT token."""
    if token.startswith(_ORCA_VV10_PREFIXES) or token.endswith(_ORCA_VV10_SUFFIXES):
        return "vv10"
    if token.startswith(_ORCA_DOUBLE_HYBRID_PREFIXES):
        return "double_hybrid"
    if token.startswith(_ORCA_META_GGA_PREFIXES):
        return "meta_gga"
    return "plain"


#: Human label for each method family, used in the reason text.
_FAMILY_LABEL: dict[str, str] = {
    MethodFamily.HF: "HF",
    MethodFamily.DFT: "DFT",
    MethodFamily.MP2: "MP2",
    MethodFamily.MP3: "MP3",
    MethodFamily.MP4: "MP4",
    MethodFamily.CIS: "CIS",
    MethodFamily.CISD: "CISD/CID",
    MethodFamily.CASSCF: "CASSCF",
    MethodFamily.QCISD: "QCISD",
    MethodFamily.QCISD_T: "QCISD(T)",
    MethodFamily.CCSD: "CCSD",
    MethodFamily.CCSD_T: "CCSD(T)",
    MethodFamily.DLPNO_OTHER: "DLPNO",
}

#: Display spelling for each table row's program, used in the reason text.
_DISPLAY_NAME: dict[str, str] = {"gaussian": "Gaussian", "orca": "ORCA"}

#: One row per ``(software, assumed basis)`` pair. tau is always the
#: assumed basis's recorded counterpart -- the owner's explicit decision
#: that an assumption changes *which basis is credited*, never the
#: number.
_ASSUMED_TAU_BY_BASIS: dict[TauBasis, float] = {
    TauBasis.assumed_analytic_default: TAU_ANALYTIC_DEFAULT_CM1,
    TauBasis.assumed_finite_difference_gradient: TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
    TauBasis.assumed_finite_difference_energy: TAU_FINITE_DIFFERENCE_ENERGY_CM1,
}

#: ``(normalised software name) -> (family-set, assumed basis)`` rows,
#: walked in order. The first matching family set wins; within one
#: software the three sets are disjoint by construction (see the
#: ``MethodFamily`` frozensets above), so order does not change the
#: result, but it is kept analytic-first to match the table in this
#: module's docstring.
_SOFTWARE_TABLE: dict[str, tuple[tuple[frozenset[str], TauBasis], ...]] = {
    "gaussian": (
        (_GAUSSIAN_ANALYTIC, TauBasis.assumed_analytic_default),
        (
            _GAUSSIAN_FINITE_DIFFERENCE_GRADIENT,
            TauBasis.assumed_finite_difference_gradient,
        ),
        (
            _GAUSSIAN_FINITE_DIFFERENCE_ENERGY,
            TauBasis.assumed_finite_difference_energy,
        ),
    ),
    "orca": (
        (_ORCA_ANALYTIC, TauBasis.assumed_analytic_default),
        (
            _ORCA_FINITE_DIFFERENCE_GRADIENT,
            TauBasis.assumed_finite_difference_gradient,
        ),
    ),
}


def infer_hessian_method(
    software_name: str | None,
    method: str | None,
    software_version: str | None = None,
) -> TauResolution | None:
    """Assume a Hessian method's tau basis from software and LOT method.

    Called only when the caller has already confirmed no
    ``freq.hessian_method`` was recorded for this calculation — this
    module has no way to check that itself, and does not try to; it is
    conservative-by-construction the other way (an unrecognised software
    or method returns ``None``), not by re-deriving what "not recorded"
    means.

    :param software_name: The calculation's software, e.g. as stored on
        ``Software.name``. Matched case-insensitively against
        "Gaussian" and "ORCA" (the only two programs this module has a
        documented default for); anything else returns ``None``.
    :param method: The calculation's level-of-theory method, free text
        as stored on ``level_of_theory.method``.
    :param software_version: The release's version string. Used only for ORCA
        meta-GGA functionals (analytic Hessians from ORCA 5); an unknown or
        unparseable version keeps the plain GGA/hybrid assumption and gives
        meta-GGA functionals no assumption. VV10 and double-hybrid functionals
        are assumed numerical whatever the version.
    :returns: A :class:`TauResolution` carrying one of the three
        ``assumed_*`` bases and its counterpart's tau, with a
        producer-facing ``reason`` naming the software/method pair and
        the default that was assumed. ``None`` when the software or the
        method is not one this module has been taught, in which case
        the caller keeps ``protocol_not_recorded``.
    """
    software_token = _normalize(software_name)
    if software_token is None:
        return None
    rows = _SOFTWARE_TABLE.get(software_token)
    if rows is None:
        return None

    family = classify_method_family(method)
    if family is None:
        return None

    version_rule = ""
    if software_token == "orca" and family == MethodFamily.DFT:
        functional = _orca_functional_class(_normalize(method) or "")
        if functional in ("vv10", "double_hybrid"):
            return _orca_numerical_documented(functional)
        if functional == "meta_gga":
            major = _orca_major_version(software_version)
            if major is None:
                return None
            if major < _ORCA_ANALYTIC_META_GGA_FROM:
                return _orca_pre5_meta_gga()
            version_rule = (
                " ORCA 5 and later has analytic Hessians for meta-GGA "
                "functionals."
            )

    for families, basis in rows:
        if family in families:
            tau_cm1 = _ASSUMED_TAU_BY_BASIS[basis]
            # The reason is TCKDB prose, shown on warnings and refusals.
            # It names the table's own canonical program and the method
            # *family* the recogniser assigned, never the depositor's raw
            # strings.
            return TauResolution(
                tau_cm1=tau_cm1,
                basis=basis,
                reason=(
                    f"tau = {tau_cm1:.0f} cm-1: 'freq.hessian_method' is "
                    f"not recorded, so this is an assumption, not a "
                    f"recorded statement. {_DISPLAY_NAME[software_token]}'s "
                    f"documented default second-derivative method for "
                    f"{_FAMILY_LABEL[family]} calculations is assumed ({basis.value}) "
                    f"and given the same tau as the equivalent recorded "
                    f"basis.{version_rule} A later upload that states the "
                    f"Hessian method explicitly supersedes this assumption."
                ),
            )
    return None


def _orca_numerical_documented(functional: str) -> TauResolution:
    label = "VV10 (non-local)" if functional == "vv10" else "double-hybrid"
    basis = TauBasis.assumed_finite_difference_gradient
    tau_cm1 = _ASSUMED_TAU_BY_BASIS[basis]
    return TauResolution(
        tau_cm1=tau_cm1,
        basis=basis,
        reason=(
            f"tau = {tau_cm1:.0f} cm-1: 'freq.hessian_method' is not "
            f"recorded, so this is an assumption, not a recorded statement. "
            f"ORCA's manuals document numerical frequencies from analytic "
            f"gradients for {label} functionals, so numerical frequencies "
            f"are assumed ({basis.value}). A later upload that states the "
            f"Hessian method explicitly supersedes this assumption."
        ),
    )


def _orca_pre5_meta_gga() -> TauResolution:
    basis = TauBasis.assumed_finite_difference_gradient
    tau_cm1 = _ASSUMED_TAU_BY_BASIS[basis]
    return TauResolution(
        tau_cm1=tau_cm1,
        basis=basis,
        reason=(
            f"tau = {tau_cm1:.0f} cm-1: 'freq.hessian_method' is not "
            f"recorded, so this is an assumption, not a recorded statement. "
            f"ORCA releases before version 5 have no analytic Hessian for "
            f"meta-GGA functionals, so numerical frequencies from analytic "
            f"gradients are assumed ({basis.value}). A later upload that "
            f"states the Hessian method explicitly supersedes this "
            f"assumption."
        ),
    )


def resolve_tau_with_assumption(
    parameters: Iterable[tuple[str | None, str | None]],
    software_name: str | None,
    lot_method: str | None,
    software_version: str | None = None,
    deck_method: HessianMethod | None = None,
) -> TauResolution:
    """Resolve tau for one calculation: recorded, deck, assumed, conservative.

    The single resolution every validation adapter and the persistence step
    share, so what a deposit is judged at is what is stored.

    1. A recorded ``freq.hessian_method`` (and the grid / convergence
       parameters that refine it) resolves through
       :func:`~tckdb_schemas.stationary_point.resolve_tau_from_parameters`,
       and always wins.
    2. A method the calculation's own inline input deck states
       (:mod:`tckdb_schemas.hessian_method_detection`). A deposited deck is
       evidence, not an assumption; where it and a recorded parameter
       disagree the deposit is refused before this is called.
    3. A present but unrecognised ``freq.hessian_method`` (``numer``) keeps
       ``protocol_not_recorded`` and skips the assumption: the depositor
       stated something, and an assumption never replaces a statement.
    4. The producing program's assumed default for the level-of-theory's
       method family (ORCA meta-GGA functionals gate on ``software_version``).
    5. ``protocol_not_recorded`` (50 cm-1): an unknown program or method
       family, or software / level of theory that are not visible. Never a
       guess.

    :param parameters: ``(canonical_key, canonical_value)`` pairs.
    :param software_name: The calculation's software name as deposited or
        stored; normalised here to the registry's canonical spelling.
    :param lot_method: The calculation's level-of-theory method.
    :param software_version: The software release's version string.
    :param deck_method: The method the inline input deck states, if any.
    """
    pairs = list(parameters)
    resolved = resolve_tau_from_parameters(pairs)
    if resolved.basis is not TauBasis.protocol_not_recorded:
        return resolved
    if deck_method is not None:
        # A recognised deck directive refines an unrecognised recorded value
        # too: both are statements, and the deck is the more specific.
        return resolve_tau_from_parameters([*pairs, ("freq.hessian_method", deck_method.value)])
    if any(key == "freq.hessian_method" and value and value.strip() for key, value in pairs):
        return resolved
    if not software_name:
        return resolved
    assumed = infer_hessian_method(
        normalize_software_name(software_name), lot_method, software_version
    )
    return assumed if assumed is not None else resolved


def calculation_deck_method(calc) -> HessianMethod | None:
    release = calc.software_release
    return deck_hessian_method(
        normalize_software_name(release.name) if release is not None else None,
        getattr(calc, "artifacts", None) or (),
    )


def resolve_tau_for_calculation(calc) -> TauResolution:
    """:func:`resolve_tau_with_assumption` for a bundle calculation payload.

    Reads the payload's recorded ``parameters``, its software release and
    level-of-theory method, and any inline ``input`` deck in ``artifacts``.
    Duck-typed so the bundle ``CalculationIn`` and ``CalculationInBundle``
    classes, which re-declare the same fields, share one function.
    """
    release = calc.software_release
    return resolve_tau_with_assumption(
        (
            (observation.canonical_key, observation.canonical_value)
            for observation in (calc.parameters or ())
        ),
        release.name if release is not None else None,
        calc.level_of_theory.method,
        release.version if release is not None else None,
        calculation_deck_method(calc),
    )


def check_calculation_deck_agrees(calc) -> None:
    """Refuse a bundle calculation whose recorded method contradicts its deck."""
    check_recorded_method_agrees_with_deck(
        ((o.canonical_key, o.canonical_value) for o in (calc.parameters or ())),
        calculation_deck_method(calc),
        calculation_key=getattr(calc, "key", None),
    )
