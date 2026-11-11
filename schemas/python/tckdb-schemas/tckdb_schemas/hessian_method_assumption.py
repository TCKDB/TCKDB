"""Assume a frequency job's Hessian method when the record does not say.

**Where this applies.** This table is the single owner of the assumption, and
it is applied where tau is *decided*: at upload validation, through
:func:`resolve_tau_with_assumption`, by every adapter that judges a
calculation's imaginary modes. Persistence then stores exactly the tau and
basis that validation judged.

When ``freq.hessian_method`` is not recorded, ``resolve_tau`` returns the
conservative ``protocol_not_recorded`` row of the tau table. This module
instead *assumes* the producing program's documented default for the
level-of-theory's method family, records that it was assumed, and gives the
assumed method the same tau as its recorded counterpart. Gaussian never names
its analytic default in the output, and
``app/services/gaussian_parameter_parser.py`` records only explicit
statements (``Freq=Numer``), so without the assumption every Gaussian record
that relies on the analytic default falls to the conservative row. The assumption does not touch tau's
magnitude, only which basis is credited and whether one is credited at all.

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
and always wins over anything this module would have
assumed.

The table
---------

Gaussian (``Software.name == "Gaussian"``, matched case-insensitively):

* Analytic: HF, any DFT functional, MP2, CIS, CASSCF, B2PLYP, B2PLYPD,
  B2PLYPD3, mPW2PLYP, mPW2PLYPD. Other double hybrids assume nothing.
* Finite differences of gradients: MP3, MP4(DQ), MP4(SDQ), QCISD, CCSD, CISD, CID.
* Finite differences of energies: CCSD(T), QCISD(T), MP4(SDTQ) (a bare ``MP4``), MP5.

ORCA (``Software.name == "ORCA"``):

* Analytic: HF, any DFT functional (exceptions below).
* Finite differences of gradients: MP2, RI-MP2, SCS-MP2, closed-shell DLPNO-MP2;
  ORCA 6 and later ``AUTOCI-CCSD``, ``-CCSD(T)``, ``-MP2``, ``-MP3``, ``-MP4`` when
  the level of theory states ``core_treatment`` all-electron and ``ri_approximation``
  none (otherwise nothing is assumed).
* Finite differences of energies: CCSD, CCSD(T), DLPNO coupled cluster, F12 methods,
  open-shell DLPNO-MP2 and ROHF MP2. Open shell is read from the level of theory's
  ``spin_treatment`` or a ``UHF`` / ``ROHF`` method prefix; an unstated spin
  treatment keeps the closed-shell row. Other ``DLPNO-*`` methods assume nothing.

Why some things stay ``None``
------------------------------
Every software not spelled "Gaussian" or "ORCA" (Molpro, Arkane, ARC,
RMG, an empty or garbled name) returns ``None`` regardless of method —
this module was not asked to research Molpro's or Arkane's defaults, and
guessing one would be the "assume the better case" error the conservative
fallback exists to avoid. Likewise a method
string this module's recognisers do not match (an unfamiliar functional
spelling, a composite method, a semi-empirical method, garbage) returns
``None`` rather than a best-effort guess: the table is a documented
allowlist, not a classifier that tries to cover everything.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace

from tckdb_schemas.enums import HessianMethod
from tckdb_schemas.hessian_method_detection import (
    check_recorded_method_agrees_with_deck,
    deck_hessian_method,
    echo_hessian_method,
    numfreq_energy_implied,
    orca_lacks_analytic_gradient,
    orca_numfreq_refines_gradient_to_energy,
)
from tckdb_schemas.hessian_method_reported import reported_method_of_artifacts
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
# is not reused here. Method tokens are lower-cased with ``_`` mapped to ``-``;
# hyphen and no-hyphen spellings are separate prefixes.
# ---------------------------------------------------------------------------


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    # Lower-case; ``_`` becomes ``-``. Hyphens are kept.
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

#: MP5 (Gaussian: analytic energies, numerical gradients).
_MP5_PREFIXES = ("mp5", "ump5")

#: QCISD(T) is checked before bare QCISD so the "(t)" tail is not lost to
#: the shorter prefix.
_QCISD_T_PREFIXES = ("qcisd(t)",)
_QCISD_PREFIXES = ("qcisd",)

#: CCSD(T) (and its F12/DLPNO-tagged relatives) before bare CCSD, for the
#: same reason as QCISD(T)/QCISD.
_CCSD_T_PREFIXES = (
    "ccsd(t)", "dlpno-ccsd(t)", "dlpno2013-ccsd(t)", "dlpno-ccsd(t1)", "ccsd(t)-f12",
    "dlpno-ccsd(t)-f12", "f12-ccsd(t)", "f12/ri-ccsd(t)",
)
_CCSD_PREFIXES = ("ccsd", "dlpno-ccsd", "dlpno2013-ccsd", "f12-ccsd", "f12/ri-ccsd")

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
    # Double hybrids and meta-hybrids the ORCA / Gaussian tables treat
    # separately. They must be recognised as DFT to reach those rows (a name the
    # classifier did not know would silently get no assumption at all).
    "dsd", "revdsd", "pwpb95", "mpw2plyp", "b2gp-plyp", "b2gpplyp", "b2k-plyp",
    "b2t-plyp", "pbe-qidh", "wb2plyp", "wb2gpplyp", "ub2plyp", "pr2scan",
    "kpr2scan", "wpr2scan", "rsx-qidh", "rsx-0dh", "mpw1b95", "bb1k", "mpw1kcis",
    "rscan", "b2kplyp", "b2tplyp",
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
    #: Older warnings use this token for every MP4 variant.
    MP4 = "mp4"
    #: MP4(DQ) and MP4(SDQ): analytic gradients exist in Gaussian.
    MP4_SDQ = "mp4(sdq)"
    #: MP4(SDTQ), and the bare keyword MP4 (which means MP4(SDTQ)): numerical gradients.
    MP4_SDTQ = "mp4(sdtq)"
    MP5 = "mp5"
    CIS = "cis"
    CISD = "cisd"
    CASSCF = "casscf"
    QCISD = "qcisd"
    QCISD_T = "qcisd(t)"
    CCSD = "ccsd"
    CCSD_T = "ccsd(t)"
    DLPNO_OTHER = "dlpno_other"
    #: F12-MP2 (ORCA): no analytic gradient.
    MP2_F12 = "mp2-f12"
    #: ORCA 6 ``AUTOCI-CCSD`` and ``AUTOCI-CCSD(T)``: analytic gradients (no frozen core, no RI).
    AUTOCI = "autoci"


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


#: Separators between a method and the options Gaussian accepts after it: ``MP4(SDTQ,FC)``, ``MP4=FC``, ``MP4 FC``,
#: ``MP4(SDTQ)-FC``, ``mp4(sdtq)/fc``.
_OPTION_SPLIT = re.compile(r"[(),/\s=-]+")

#: Gaussian options that do not change the method (frozen core, CC page options). ``RO`` is excluded: energies only.
_IGNORED_OPTION = re.compile(
    r"^(fc\d*|nofc|full|fu|window.*|active.*|t1diag|e4t|conver|maxcyc|saveamplitudes|readamplitudes|\d+)$"
)


def _option_tokens(text: str) -> list[str]:
    return [part for part in _OPTION_SPLIT.split(text) if part]


def _mp4_variant(token: str) -> str | None:
    """The MP4 family for ``token``: SDQ for DQ/SDQ, SDTQ for SDTQ or a bare ``mp4``, ``None`` for an unknown option."""
    tokens = _option_tokens(token[len("mp4"):])
    variant = next((t for t in tokens if t in ("dq", "sdq", "sdtq")), None)
    if any(t not in ("dq", "sdq", "sdtq") and not _IGNORED_OPTION.match(t) for t in tokens):
        return None
    if variant in ("dq", "sdq"):
        return MethodFamily.MP4_SDQ
    return MethodFamily.MP4_SDTQ


#: ``keyword(options)``, ``keyword (options)`` and ``keyword=(options)`` are the same Gaussian route item.
_CC_WITH_OPTIONS = re.compile(r"^(ccsd|qcisd)(?:\s*(?:=\s*)?\(([^()]*)\))?([^()]*)$")


def _cc_with_options(token: str) -> tuple[bool, str | None]:
    """``(handled, family)`` for Gaussian ``CCSD``/``QCISD`` with options, e.g. ``CCSD(T,FC)``, ``CCSD-T``, ``QCISD(T,E4T)``.

    ``-F12`` suffixes are left to the prefix rules; other tails and unknown options give ``(True, None)``.
    """
    m = _CC_WITH_OPTIONS.match(token)
    if m is None:
        return False, None
    base, inner, tail = m.groups()
    tail_tokens = _option_tokens(tail)
    if tail and not _OPTION_SPLIT.match(tail):
        return True, None  # "ccsdt": not "ccsd" plus an option
    if any(t != "t" and not _IGNORED_OPTION.match(t) for t in tail_tokens):
        if tail_tokens[0].startswith("f12"):
            return False, None  # a known suffix: the prefix rules read ``ccsd(t)-f12``
        return True, None
    inner_tokens = _option_tokens(inner or "")
    if any(t != "t" and not _IGNORED_OPTION.match(t) for t in inner_tokens):
        return True, None
    triples = "t" in inner_tokens or "t" in tail_tokens
    if base == "ccsd":
        return True, MethodFamily.CCSD_T if triples else MethodFamily.CCSD
    return True, MethodFamily.QCISD_T if triples else MethodFamily.QCISD


#: ORCA 6.1 manual, AUTOCI chapter: these methods have analytic gradients.
_AUTOCI_WITH_GRADIENT = frozenset({
    "autoci-ccsd", "autoci-ccsd(t)", "autoci-mp2", "autoci-mp3", "autoci-mp4", "autoci-mp4(sdq)",
})


#: Method strings are short; longer ones are not classified (and never reach the option regexes).
_MAX_METHOD_LENGTH = 128


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
    if token is None or len(token) > _MAX_METHOD_LENGTH:
        return None

    if token in _HF_METHODS:
        return MethodFamily.HF
    if token in _CIS_METHODS:
        return MethodFamily.CIS
    if _startswith_any(token, _CISD_PREFIXES):
        return MethodFamily.CISD
    if _startswith_any(token, _CASSCF_PREFIXES):
        return MethodFamily.CASSCF
    cc_token = re.sub(r"^(rohf|uhf|rhf)-", "", token)
    handled, family = _cc_with_options(cc_token)
    if handled:
        return family
    if _startswith_any(cc_token, _CCSD_T_PREFIXES):
        return MethodFamily.CCSD_T
    if _startswith_any(cc_token, _QCISD_T_PREFIXES):
        return MethodFamily.QCISD_T
    if _startswith_any(cc_token, _CCSD_PREFIXES):
        return MethodFamily.CCSD
    if _startswith_any(cc_token, _QCISD_PREFIXES):
        return MethodFamily.QCISD
    if _startswith_any(token, _MP4_PREFIXES):
        return _mp4_variant(token)
    if _startswith_any(token, _MP5_PREFIXES):
        return MethodFamily.MP5
    if _startswith_any(token, _MP3_PREFIXES):
        return MethodFamily.MP3
    if token in _AUTOCI_WITH_GRADIENT:
        return MethodFamily.AUTOCI
    if "f12" in token and "mp2" in token:
        return MethodFamily.MP2_F12
    spinless = re.sub(r"^(rohf|uhf|rhf)-", "", token)
    if _startswith_any(spinless, _MP2_PREFIXES):
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
    MethodFamily.MP3, MethodFamily.MP4_SDQ, MethodFamily.QCISD,
    MethodFamily.CCSD, MethodFamily.CISD,
})
#: Gaussian families with neither an analytic Hessian nor an analytic
#: gradient documented -- the default frequency job differentiates the
#: energy twice.
_GAUSSIAN_FINITE_DIFFERENCE_ENERGY = frozenset({
    MethodFamily.CCSD_T, MethodFamily.QCISD_T,
    MethodFamily.MP4_SDTQ, MethodFamily.MP5,
})

#: ORCA families whose default frequency job is analytic.
_ORCA_ANALYTIC = frozenset({MethodFamily.HF, MethodFamily.DFT})
#: ORCA MP2: finite differences of the analytic gradient.
_ORCA_FINITE_DIFFERENCE_GRADIENT = frozenset({MethodFamily.MP2})
#: ORCA methods with no analytic gradient: finite differences of energies.
_ORCA_FINITE_DIFFERENCE_ENERGY = frozenset({MethodFamily.CCSD, MethodFamily.CCSD_T, MethodFamily.MP2_F12})

# ---------------------------------------------------------------------------
# ORCA: functional class and release gate
#
# Sources (ORCA manuals):
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
# * MP2: ORCA 5.0.4 manual 8.1.2.4: analytic MP2 Hessian only without frozen core; ORCA 6.0 removed it.
# * Coupled cluster: ORCA 6.1 manual (Numerical Gradients, MDCI): no analytic gradient for CCSD(T), DLPNO or F12.
# * AUTOCI: ORCA 6.1 manual, AUTOCI chapter: analytic CC and MPn gradients, no frozen core, no RI.
# * Open-shell MP2: ORCA 6.1 manual, MP2 chapter: no gradient for UHF-DLPNO-MP2 or high-spin ROHF.
# * Gaussian double hybrids: Gaussian 09/16 MP keyword page: analytic frequencies for B2PLYP[D] and mPW2PLYP[D].
# * Gaussian MP4, CCSD, QCISD: Gaussian 09/16 MP, CC, QCISD pages: gradient and frequency availability.
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

#: Double-hybrid functionals (also matches dispersion-suffixed spellings). Shared by
#: the ORCA row (assumed numerical) and the Gaussian row (no assumption).
_DOUBLE_HYBRID_PREFIXES = (
    "b2plyp", "b2gp-plyp", "b2gpplyp", "b2k-plyp", "b2t-plyp", "mpw2plyp",
    "dsd", "revdsd", "pwpb95", "pbe0-dh", "pbe0dh", "pbe0-2", "pbe-qidh", "pbeqidh", "pbe0-qidh",
    "wb97x2", "b2kplyp", "b2tplyp",
    "wb2plyp", "wb2gpplyp", "ub2plyp", "wb97x-2", "wb97m(2)", "wb97m-2",
    "pr2scan", "kpr2scan", "wpr2scan", "rsx-qidh", "rsx-0dh",
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
    if token.startswith(_DOUBLE_HYBRID_PREFIXES):
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
    MethodFamily.MP4_SDQ: "MP4(DQ/SDQ)",
    MethodFamily.MP2_F12: "F12-MP2",
    MethodFamily.AUTOCI: "AUTOCI",
    MethodFamily.MP4_SDTQ: "MP4(SDTQ)",
    MethodFamily.MP5: "MP5",
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
#: assumed basis's recorded counterpart: an assumption changes *which basis
#: is credited*, never the number.
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
        (
            _ORCA_FINITE_DIFFERENCE_ENERGY,
            TauBasis.assumed_finite_difference_energy,
        ),
    ),
}


#: Gaussian double hybrids with analytic frequencies (Gaussian MP keyword page), compared without hyphens.
_GAUSSIAN_ANALYTIC_DOUBLE_HYBRIDS = frozenset({
    "b2plyp", "b2plypd", "b2plypd3", "b2plypd3bj", "b2plypd3(bj)", "mpw2plyp", "mpw2plypd",
})


def _is_gaussian_documented_double_hybrid(method: str | None) -> bool:
    token = _normalize(method)
    return token is not None and token.replace("-", "") in _GAUSSIAN_ANALYTIC_DOUBLE_HYBRIDS


def infer_hessian_method(
    software_name: str | None,
    method: str | None,
    software_version: str | None = None,
    *,
    spin_treatment: object = None,
    core_treatment: object = None,
    ri_approximation: object = None,
) -> TauResolution | None:
    """Assume a Hessian method's tau basis from software and LOT method.

    ``spin_treatment``, ``core_treatment`` and ``ri_approximation`` are the level of theory's qualifiers; they matter
    only for ORCA open-shell MP2 and AUTOCI, and an unstated one never decides.

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
    rule = "program_default"
    if software_token == "gaussian" and (_normalize(method) or "").startswith("rohf-"):
        return None  # Gaussian's restricted open-shell MP methods are energies only
    if software_token == "orca" and family == MethodFamily.AUTOCI:
        return _orca_autoci(software_version, core_treatment, ri_approximation)
    if software_token == "orca" and family == MethodFamily.MP2 and _orca_mp2_without_gradient(
        method, spin_treatment
    ):
        return _orca_numerical_from_energies(
            family,
            "orca_open_shell_no_gradient",
            "ORCA has no analytic gradient for open-shell DLPNO-MP2 or for high-spin ROHF MP2",
        )
    if (
        software_token == "gaussian"
        and family == MethodFamily.DFT
        and (_normalize(method) or "").startswith(_DOUBLE_HYBRID_PREFIXES)
        and not _is_gaussian_documented_double_hybrid(method)
    ):
        # Other Gaussian double hybrids: nothing is assumed.
        return None
    if software_token == "orca" and family == MethodFamily.DFT:
        functional = _orca_functional_class(_normalize(method) or "")
        if functional in ("vv10", "double_hybrid"):
            return _orca_numerical_documented(functional, family)
        if functional == "meta_gga":
            major = _orca_major_version(software_version)
            if major is None:
                return None
            if major < _ORCA_ANALYTIC_META_GGA_FROM:
                return _orca_pre5_meta_gga(family)
            version_rule = (
                " ORCA 5 and later has analytic Hessians for meta-GGA "
                "functionals."
            )
            rule = "orca_meta_gga_analytic_from_5"

    for families, basis in rows:
        if family in families:
            if software_token == "orca" and basis is TauBasis.assumed_finite_difference_energy:
                rule = "orca_no_analytic_gradient"
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
                assumed_software=software_token,
                assumed_family=family,
                assumption_rule=rule,
            )
    return None


def _token_value(value: object) -> str | None:
    value = getattr(value, "value", value)
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _orca_mp2_without_gradient(method: str | None, spin_treatment: object) -> bool:
    """True for ORCA open-shell DLPNO-MP2 or ROHF MP2 (ORCA 6.1 manual, MP2 chapter: no analytic gradient).

    Open shell is read from the stated ``spin_treatment`` or a ``UHF`` / ``ROHF`` prefix on the method.
    """
    token = _normalize(method) or ""
    spin = _token_value(spin_treatment)
    rohf = token.startswith("rohf-") or spin == "restricted_open"
    open_shell = rohf or token.startswith("uhf-") or spin == "unrestricted"
    return open_shell if "dlpno" in token else rohf


def _orca_lacks_gradient(method: str | None, spin_treatment: object) -> bool:
    """Whether an ORCA job's method has no analytic gradient: coupled cluster, F12, open-shell DLPNO-MP2, ROHF MP2."""
    if orca_lacks_analytic_gradient(method):
        return True
    return classify_method_family(method) == MethodFamily.MP2 and _orca_mp2_without_gradient(method, spin_treatment)


def _orca_numerical_from_energies(family: str, rule: str, why: str) -> TauResolution:
    basis = TauBasis.assumed_finite_difference_energy
    tau_cm1 = _ASSUMED_TAU_BY_BASIS[basis]
    return TauResolution(
        tau_cm1=tau_cm1,
        basis=basis,
        reason=(
            f"tau = {tau_cm1:.0f} cm-1: 'freq.hessian_method' is not recorded, so this is an assumption, not a "
            f"recorded statement. {why}, so numerical frequencies from numerical gradients are assumed "
            f"({basis.value}). A later upload that states the Hessian method explicitly supersedes this assumption."
        ),
        assumed_software="orca",
        assumed_family=family,
        assumption_rule=rule,
    )


_ORCA_AUTOCI_GRADIENT_FROM = 6


def _orca_autoci(software_version: str | None, core_treatment: object, ri_approximation: object) -> TauResolution | None:
    """The AUTOCI assumption (finite differences of gradients), or ``None`` unless ORCA is 6 or later and the
    level of theory states all-electron and no RI (ORCA 6.1 manual, AUTOCI chapter)."""
    major = _orca_major_version(software_version)
    if major is None or major < _ORCA_AUTOCI_GRADIENT_FROM:
        return None
    if _token_value(core_treatment) != "all_electron" or _token_value(ri_approximation) != "none":
        return None
    basis = TauBasis.assumed_finite_difference_gradient
    tau_cm1 = _ASSUMED_TAU_BY_BASIS[basis]
    return TauResolution(
        tau_cm1=tau_cm1,
        basis=basis,
        reason=(
            f"tau = {tau_cm1:.0f} cm-1: 'freq.hessian_method' is not recorded, so this is an assumption, not a "
            f"recorded statement. ORCA 6 and later has analytic gradients for the AUTOCI methods "
            f"without frozen core or RI, so numerical frequencies from analytic gradients are assumed "
            f"({basis.value}). A later upload that states the Hessian method explicitly supersedes this assumption."
        ),
        assumed_software="orca",
        assumed_family=MethodFamily.AUTOCI,
        assumption_rule="orca_autoci_gradient_from_6",
    )


def _orca_numerical_documented(functional: str, family: str) -> TauResolution:
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
        assumed_software="orca",
        assumed_family=family,
        assumption_rule=f"orca_numerical_documented_{functional}",
    )


def _orca_pre5_meta_gga(family: str) -> TauResolution:
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
        assumed_software="orca",
        assumed_family=family,
        assumption_rule="orca_pre5_meta_gga_no_analytic",
    )


def resolve_tau_with_assumption(
    parameters: Iterable[tuple[str | None, str | None]],
    software_name: str | None,
    lot_method: str | None,
    software_version: str | None = None,
    deck_method: HessianMethod | None = None,
    reported_method: HessianMethod | None = None,
    echo_method: HessianMethod | None = None,
    *,
    spin_treatment: object = None,
    core_treatment: object = None,
    ri_approximation: object = None,
    numfreq_energy_implied: bool = False,
) -> TauResolution:
    """Resolve tau for one calculation: reported, echo, recorded, deck, assumed, conservative.

    ``spin_treatment``, ``core_treatment`` and ``ri_approximation`` are passed to :func:`infer_hessian_method`.
    ``numfreq_energy_implied`` says a deck or echoed input has a bare ``NumFreq`` that reads as energies
    (:func:`~tckdb_schemas.hessian_method_detection.numfreq_energy_implied`).

    The single resolution every validation adapter and the persistence step
    share, so what a deposit is judged at is what is stored.

    0. The method an attached output log REPORTS the frequency job ran
       (:mod:`tckdb_schemas.hessian_method_reported`) wins over every request:
       it is what ran, and the recorded parameter, the deck and the echoed
       input only say what was asked for. It refines nothing and invents
       nothing: it is read only from a header the log prints, and its tau is the
       same number a recorded statement of that method gets. A reported method
       that contradicts a request is warned about when the log is read.
    0b. Else the method the input a log echoes requests
       (:func:`~tckdb_schemas.hessian_method_detection.echo_hessian_method`),
       which the stored rows also rank above the deck.
    1. A recorded ``freq.hessian_method`` (and the grid / convergence
       parameters that refine it) resolves through
       :func:`~tckdb_schemas.stationary_point.resolve_tau_from_parameters`,
       and wins, except that a recorded ``finite_difference_gradient`` against an ORCA ``NumFreq`` deck (or echoed
       input) for a method with no analytic gradient resolves as ``finite_difference_energy`` with ``refined_from`` set.
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
    :param reported_method: The method the inline output logs report, if any.
    :param echo_method: The method the input echoed in the inline output logs requests, if any.
    """
    pairs = list(parameters)
    recorded_exact: HessianMethod | None = None
    for key, value in pairs:
        if key == "freq.hessian_method" and value and value.strip():
            try:
                recorded_exact = HessianMethod(value.strip().lower())
            except ValueError:
                recorded_exact = None

    def refines(stated: HessianMethod | None) -> bool:
        return orca_numfreq_refines_gradient_to_energy(
            normalize_software_name(software_name) if software_name else None,
            lot_method,
            recorded_exact,
            stated,
            lacks_gradient=numfreq_energy_implied or _orca_lacks_gradient(lot_method, spin_treatment),
        )

    # The log's report, then the input the log echoes, outrank the recorded parameter and the deck: the same order
    # the stored rows are ranked in (:mod:`app.services.parameter_reconciliation`).
    if reported_method is not None:
        return resolve_tau_from_parameters([*pairs, ("freq.hessian_method", reported_method.value)])
    if echo_method is not None:
        echoed = resolve_tau_from_parameters([*pairs, ("freq.hessian_method", echo_method.value)])
        if recorded_exact is not None and refines(echo_method):
            return replace(echoed, refined_from=recorded_exact.value)
        return echoed
    if deck_method is not None and recorded_exact is not None and refines(deck_method):
        # Recorded gradient method plus ORCA NumFreq on a method without a gradient: refined to energies.
        deck_refined = resolve_tau_from_parameters([*pairs, ("freq.hessian_method", deck_method.value)])
        return replace(deck_refined, refined_from=recorded_exact.value)
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
        normalize_software_name(software_name),
        lot_method,
        software_version,
        spin_treatment=spin_treatment,
        core_treatment=core_treatment,
        ri_approximation=ri_approximation,
    )
    return assumed if assumed is not None else resolved


def calculation_deck_method(calc) -> HessianMethod | None:
    release = calc.software_release
    return deck_hessian_method(
        normalize_software_name(release.name) if release is not None else None,
        getattr(calc, "artifacts", None) or (),
    )


def calculation_numfreq_energy_implied(calc) -> bool:
    """Whether the calculation's inline deck or echoed input has a bare ORCA ``NumFreq`` that reads as energies."""
    release = calc.software_release
    return numfreq_energy_implied(release.name if release is not None else None, getattr(calc, "artifacts", None) or ())


def calculation_reported_method(calc) -> HessianMethod | None:
    """The Hessian method the calculation's inline output logs REPORT, or ``None``.

    Mirrors what the backend stores as ``reported`` rows (one per log that reports): the method every inline log that
    reports one agrees on, and ``None`` when they disagree or none does. A program with no reader reports nothing.
    """
    release = calc.software_release
    return reported_method_of_artifacts(
        release.name if release is not None else None, getattr(calc, "artifacts", None) or ()
    )


def calculation_echo_method(calc) -> HessianMethod | None:
    """The Hessian method the input echoed in the calculation's inline output logs REQUESTS, or ``None``."""
    release = calc.software_release
    return echo_hessian_method(release.name if release is not None else None, getattr(calc, "artifacts", None) or ())


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
        calculation_reported_method(calc),
        calculation_echo_method(calc),
        spin_treatment=getattr(calc.level_of_theory, "spin_treatment", None),
        core_treatment=getattr(calc.level_of_theory, "core_treatment", None),
        ri_approximation=getattr(calc.level_of_theory, "ri_approximation", None),
        numfreq_energy_implied=calculation_numfreq_energy_implied(calc),
    )


def check_calculation_deck_agrees(calc) -> None:
    """Refuse a bundle calculation whose recorded method contradicts its deck or the input its log echoes.

    The input a log echoes is a request exactly as a deck is, so a typed ``freq.hessian_method`` that contradicts
    either is refused with the same code. A method a log REPORTS is not checked here: that contradiction only warns.
    """
    parameters = [(o.canonical_key, o.canonical_value) for o in (calc.parameters or ())]
    key = getattr(calc, "key", None)
    release = calc.software_release
    software = release.name if release is not None else None
    lot_method = calc.level_of_theory.method
    lacks_gradient = _orca_lacks_gradient(
        lot_method, getattr(calc.level_of_theory, "spin_treatment", None)
    ) or calculation_numfreq_energy_implied(calc)
    check_recorded_method_agrees_with_deck(
        parameters,
        calculation_deck_method(calc),
        calculation_key=key,
        software=software,
        lot_method=lot_method,
        lacks_gradient=lacks_gradient,
    )
    check_recorded_method_agrees_with_deck(
        parameters,
        calculation_echo_method(calc),
        calculation_key=key,
        stated_in="output_log",
        software=software,
        lot_method=lot_method,
        lacks_gradient=lacks_gradient,
    )
