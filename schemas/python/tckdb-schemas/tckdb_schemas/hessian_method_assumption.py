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
  functional, MP2, CIS, CASSCF, and the double hybrids B2PLYP, B2PLYPD,
  B2PLYPD3, mPW2PLYP and mPW2PLYPD. Gaussian has computed analytic second
  derivatives for these method families by default since at least G03;
  a user gets `Freq=Numer` only by asking for it. For the double hybrids
  the G09 MP-methods page (the keyword page for MP2, MP3, MP4, MP5, B2PLYP
  and mPW2PLYP) says "MP2, B2PLYP[D], mPW2PLYP[D]: Energies, analytic
  gradients, and analytic frequencies", and the Freq page makes analytic the
  default for "RHF, UHF, CIS, CASSCF, MP2, and all DFT methods". Other
  double hybrids (B2GP-PLYP, DSD-*, PBE0-DH, ...) are still not assumed.
* **Finite-difference-from-gradients**
  (`TauBasis.assumed_finite_difference_gradient`): MP3, MP4(DQ),
  MP4(SDQ), QCISD, CCSD, CISD, CID (exactly CIS is analytic;
  CISD/CID have numerical frequencies only). None of these have an analytic
  Hessian implementation in Gaussian; the default frequency job
  numerically differentiates the analytic gradient, which these methods
  do have. For MP3, MP4(DQ) and MP4(SDQ) the G09 MP page says "Energies,
  analytic gradients, and numerical frequencies".
* **Finite-difference-from-energies**
  (`TauBasis.assumed_finite_difference_energy`): CCSD(T), QCISD(T), and
  MP4(SDTQ) (which is what a bare ``MP4`` means) and MP5. None has an analytic
  *or* semi-analytic (gradient-based) second derivative in Gaussian; the
  default frequency job double-differentiates the energy. The G09 MP page
  says "MP4(SDTQ) and MP5: Analytic energies, numerical gradients, and
  numerical frequencies", and "Just specifying MP4 defaults to MP4(SDTQ)".

ORCA (``Software.name == "ORCA"``):

* **Analytic** (`TauBasis.assumed_analytic_default`): HF, any DFT
  functional. ORCA computes analytic Hessians for both by default.
* **Finite-difference-from-gradients**
  (`TauBasis.assumed_finite_difference_gradient`): the MP2 variants
  (MP2, RI-MP2, DLPNO-MP2, SCS-MP2). ORCA's default `NumFreq` differentiates
  the analytic MP2 gradient. The analytic (RI-)MP2 Hessian exists only
  without frozen core (ORCA 5.0.4 section 8.1.2.4: "Frequencies can be
  calculated analytically in all-electron calculations. With frozen core
  orbitals, second derivatives of any kind are currently only available
  numerically"), and the 6.1 manual says "the analytical (RI-)MP2-Hessians
  are no longer available". So "numerical on every version" holds for the
  default, frozen-core job; an all-electron ORCA 5 MP2 job can be analytic and
  that is not assumed here.
* **Finite-difference-from-energies**
  (`TauBasis.assumed_finite_difference_energy`): coupled cluster, which has
  no analytic gradient behind the plain keyword: CCSD, CCSD(T), their F12
  variants, F12-MP2 (including RI-MP2-F12), and DLPNO-CCSD / DLPNO-CCSD(T). The
  ORCA 6.1 DLPNO section says "Analytic gradients are not available" (chunk
  16228), which covers plain DLPNO-CCSD as well as DLPNO-CCSD(T). The ORCA 6.1 manual's example of a
  method whose "analytic gradient is not available" is ``! CCSD(T)`` with
  ``! Opt NumGrad`` (p. 224); its frequency section lists analytic frequencies
  for "HF, DFT" only (p. 677); ORCA 5.0.4 says "Gradients are not available" for
  F12 methods (F12-MP2 as well as F12 coupled cluster). So a frequency job differentiates numerical gradients, which
  is finite differences of energies. ORCA 6.1 does have analytic CCSD and
  CCSD(T) gradients behind the separate ``AUTOCI-`` keyword prefix (manual
  section 3.11); that prefix is not recognised here, so such a method string gets no
  assumption. Any other ``DLPNO-*`` string (DLPNO-CEPA and the like) has no
  documented answer and gets no assumption.

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
from dataclasses import replace

from tckdb_schemas.enums import HessianMethod
from tckdb_schemas.hessian_method_detection import (
    check_recorded_method_agrees_with_deck,
    deck_hessian_method,
    echo_hessian_method,
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
# is not reused here. This recogniser is deliberately small and explicit
# instead. It lower-cases and maps ``_`` to ``-``; it does NOT strip hyphens, so
# the hyphen and no-hyphen spellings a depositor may write ("wb97x-d", "wb97xd",
# "pbe0-dh", "pbe0dh") are each listed as prefixes where they matter.
# ---------------------------------------------------------------------------


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    # Method tokens are lower-cased and ``_`` becomes ``-``, so "wb97x_d" and
    # "wb97x-d" match the same prefix. Hyphens are NOT removed: "wb97xd" and
    # "wb97x-d" are separate prefixes in the lists below, since free-text
    # `level_of_theory.method` values are not normalised at write time.
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
    "ccsd(t)", "dlpno-ccsd(t)", "dlpno-ccsd(t1)", "ccsd(t)-f12",
    "dlpno-ccsd(t)-f12", "f12-ccsd(t)", "f12/ri-ccsd(t)",
)
_CCSD_PREFIXES = ("ccsd", "dlpno-ccsd", "f12-ccsd", "f12/ri-ccsd")

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
    #: Legacy token, never returned by the classifier now. Warnings stored before 0.162.0 carry ``mp4`` for every
    #: MP4 variant; keeping the token and its label ("MP4") means they read as they did.
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

#: Options that change neither which method runs nor how its derivatives are computed. Gaussian 09: the frozen-core
#: options "are available with this keyword" (FC, Full, ...); the CCSD page lists T, E4T, T1Diag, Conver=N, MaxCyc=N,
#: SaveAmplitudes, ReadAmplitudes (knowledge-base chunk 10440). ``RO`` is deliberately not here: the restricted
#: open-shell variant has energies only (G09 MP page, chunk 10828: "RO may be combined with MP2, MP3 and MP4 for
#: energies only"; CCSD page, chunk 10441), so it is not a harmless option and the method is left unrecognised.
_IGNORED_OPTION = re.compile(
    r"^(fc\d*|nofc|full|fu|window.*|active.*|t1diag|e4t|conver|maxcyc|saveamplitudes|readamplitudes|\d+)$"
)


def _option_tokens(text: str) -> list[str]:
    return [part for part in _OPTION_SPLIT.split(text) if part]


def _mp4_variant(token: str) -> str | None:
    """MP4 token -> ``MP4_SDQ`` (DQ/SDQ), ``MP4_SDTQ`` (SDTQ, or a bare ``mp4``), or ``None`` (unknown option).

    ``mp4`` means MP4(SDTQ) (G09 MP page, chunk 10824: "Just specifying MP4 defaults to MP4(SDTQ)"). The text
    after ``mp4`` is split on parentheses, commas, slashes, equals signs, hyphens and spaces; the first of
    ``dq`` / ``sdq`` / ``sdtq`` names the truncation, and frozen-core and similar options are ignored, so
    ``MP4(SDTQ,FC)``, ``MP4(FC)``, ``MP4(SDTQ)-FC``, ``mp4(sdtq)/fc``, ``MP4=FC``, ``MP4 FC`` and ``mp4sdtq`` all mean
    SDTQ. A token that is neither a truncation nor a known option makes the method unrecognised (``None``,
    no assumption), never the old gradient row.
    """
    tokens = _option_tokens(token[len("mp4"):])
    variant = next((t for t in tokens if t in ("dq", "sdq", "sdtq")), None)
    if any(t not in ("dq", "sdq", "sdtq") and not _IGNORED_OPTION.match(t) for t in tokens):
        return None
    if variant in ("dq", "sdq"):
        return MethodFamily.MP4_SDQ
    return MethodFamily.MP4_SDTQ


#: ``keyword(options)``, ``keyword (options)`` and ``keyword=(options)`` are the same Gaussian route item.
_CC_WITH_OPTIONS = re.compile(r"^(ccsd|qcisd)(?:\s*=?\s*\(([^()]*)\))?([^()]*)$")


def _cc_with_options(token: str) -> tuple[bool, str | None]:
    """Read Gaussian's ``CCSD``/``QCISD`` options: ``(handled, family)``.

    ``CCSD(T,FC)`` is CCSD(T), ``CCSD(FC)`` is CCSD, ``CCSD-T`` is a synonym for ``CCSD(T)`` and ``QCISD(T,E4T)`` is
    QCISD(T) (G09 CCSD page, chunk 10440: options FC, T, E4T, T1Diag, ...; "CCSD-T is a synonym for CCSD(T)").
    Availability (chunks 10441 and 10973): "Analytic energies and gradients for CCD and CCSD, numerical gradients
    for CCSD(T), and numerical frequencies for all methods"; "numerical gradients for QCISD(T)". Not handled (so the
    prefix rules decide, as before): only a known suffix that is not an option, ``-f12`` and its variants
    (``ccsd(t)-f12``). Any other tail (``ccsdt``, ``ccsd[t]``, ``ccsd/ri``) and any unknown option are handled as
    unrecognised, ``(True, None)``: Gaussian has no CCSDT keyword and nothing is assumed for what it does not know.
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
    handled, family = _cc_with_options(token)
    if handled:
        return family
    if _startswith_any(token, _CCSD_T_PREFIXES):
        return MethodFamily.CCSD_T
    if _startswith_any(token, _QCISD_T_PREFIXES):
        return MethodFamily.QCISD_T
    if _startswith_any(token, _CCSD_PREFIXES):
        return MethodFamily.CCSD
    if _startswith_any(token, _QCISD_PREFIXES):
        return MethodFamily.QCISD
    if _startswith_any(token, _MP4_PREFIXES):
        return _mp4_variant(token)
    if _startswith_any(token, _MP5_PREFIXES):
        return MethodFamily.MP5
    if _startswith_any(token, _MP3_PREFIXES):
        return MethodFamily.MP3
    if "f12" in token and "mp2" in token:
        return MethodFamily.MP2_F12
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
#: ORCA MP2 (default frozen core): no analytic Hessian, so `NumFreq`
#: differentiates the analytic MP2 gradient.
_ORCA_FINITE_DIFFERENCE_GRADIENT = frozenset({MethodFamily.MP2})
#: ORCA coupled cluster behind the plain keywords: no analytic gradient, so
#: `NumFreq` differentiates numerical gradients (finite differences of energies).
_ORCA_FINITE_DIFFERENCE_ENERGY = frozenset({MethodFamily.CCSD, MethodFamily.CCSD_T, MethodFamily.MP2_F12})

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
# * MP2: ORCA 5.0.4 manual 8.1.2.4 "MP2 and RI-MP2 Gradients and Hessians"
#   (pp. 109-110): frequencies "can be calculated analytically in all-electron
#   calculations. With frozen core orbitals, second derivatives of any kind are
#   currently only available numerically"; ORCA 6.1 manual, "MP2 and RI-MP2
#   Second Derivatives" (p. 290): "the analytical (RI-)MP2-Hessians are no
#   longer available". The default is frozen core, so ORCA MP2 stays in the
#   finite-difference-from-gradients row on every version; an all-electron
#   ORCA 5 job could be analytic and is not assumed so.
# * coupled cluster: ORCA 6.1 manual 2.23 "Numerical Gradients" (p. 224) gives
#   ``! CCSD(T)`` with ``! Opt NumGrad`` as the example of a method whose
#   analytic gradient is not available; 4.6.4 (p. 677) lists analytic
#   frequencies for HF and DFT only; ORCA 5.0.4 8.1.3.6 says "Gradients are not
#   available" for F12 methods (F12-MP2 too, so RI-MP2-F12 is in the energy
#   row, not the MP2 row); ORCA 6.1 (chunk 16228) says "Analytic gradients are
#   not available" for DLPNO, which covers plain DLPNO-CCSD. ORCA 5.0.4 8.3.2
#   (chunk 13502) repeats the CCSD(T) / NumGrad example. Frequencies are therefore differences of
#   numerical gradients: finite differences of energies. The AUTOCI- prefixed
#   keywords (6.1, sections 3.11.2-3.11.3) have analytic CCSD/CCSD(T) gradients
#   and so would be finite-difference-from-gradients; the plain keywords do not,
#   and ``AUTOCI-*`` strings are not recognised, so they get no assumption.
#
# * Gaussian double hybrids: the Gaussian 09 MP-methods keyword page
#   ("MP2, B2PLYP[D], mPW2PLYP[D]: Energies, analytic gradients, and analytic
#   frequencies"; "B2PLYPD3 requests the same but with Grimme's D3BJ
#   dispersion. Energies, gradients and frequencies are available") and the
#   Freq page (Freq=Analytic is the default for "RHF, UHF, CIS, CASSCF, MP2, and
#   all DFT methods") settle B2PLYP, B2PLYPD, B2PLYPD3, mPW2PLYP and
#   mPW2PLYPD as analytic. Other double hybrids are not documented and get no
#   assumption. U-prefixed spellings (UB2PLYP, UMP4) are not in the pages read
#   and are not recognised.
#
# * Method options: Gaussian accepts options after a method (``MP4(SDTQ,FC)``,
#   ``CCSD(T,FC)``, ``QCISD(T,E4T)``, ``MP4=FC``). The classifier reads the
#   truncation and the triples flag and ignores frozen-core style options
#   (G09 CCSD page, chunk 10440). Availability for CCSD(T) and QCISD(T) is
#   "numerical gradients" (chunks 10441, 10973), so frequencies are numerical
#   differences of energies; an unknown option makes the method unrecognised.
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
        (
            _ORCA_FINITE_DIFFERENCE_ENERGY,
            TauBasis.assumed_finite_difference_energy,
        ),
    ),
}


#: The Gaussian double hybrids whose analytic frequencies the G09 MP page documents,
#: compared with hyphens, underscores and case removed ("B2PLYP-D3" is "b2plypd3").
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
    rule = "program_default"
    if (
        software_token == "gaussian"
        and family == MethodFamily.DFT
        and (_normalize(method) or "").startswith(_DOUBLE_HYBRID_PREFIXES)
        and not _is_gaussian_documented_double_hybrid(method)
    ):
        # Only the double hybrids the G09 MP-methods page names (B2PLYP[D],
        # mPW2PLYP[D], and B2PLYPD3) are documented analytic; every other double
        # hybrid has no documented Gaussian answer, so nothing is assumed.
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
) -> TauResolution:
    """Resolve tau for one calculation: reported, echo, recorded, deck, assumed, conservative.

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
       and always wins, with one refinement: a recorded ``finite_difference_gradient`` for an ORCA method with no
       analytic gradient, against a deck (or echoed input) that asks for ``NumFreq`` (read as finite differences of
       energies), resolves as ``finite_difference_energy`` (80 cm-1) and carries ``refined_from``; ORCA's
       ``NumFreq`` differences gradients, and for such a method they are numerical. A recorded ``analytic`` against
       ``NumFreq`` is still refused.
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
            normalize_software_name(software_name) if software_name else None, lot_method, recorded_exact, stated
        )

    # The log's report, then the input the log echoes, outrank the recorded parameter and the deck: the same order
    # the stored rows are ranked in (:mod:`app.services.parameter_reconciliation`).
    if reported_method is not None:
        return resolve_tau_from_parameters([*pairs, ("freq.hessian_method", reported_method.value)])
    if echo_method is not None:
        echoed = resolve_tau_from_parameters([*pairs, ("freq.hessian_method", echo_method.value)])
        if refines(echo_method):
            return replace(echoed, refined_from=recorded_exact.value)
        return echoed
    if deck_method is not None and refines(deck_method):
        # A recorded finite difference of gradients and a deck asking ORCA for NumFreq on a method with no analytic
        # gradient are one statement: the gradients are numerical, so the Hessian is built from energies. Refined,
        # not contradicted, and said so (``hessian_method_refined``).
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
        normalize_software_name(software_name), lot_method, software_version
    )
    return assumed if assumed is not None else resolved


def calculation_deck_method(calc) -> HessianMethod | None:
    release = calc.software_release
    return deck_hessian_method(
        normalize_software_name(release.name) if release is not None else None,
        getattr(calc, "artifacts", None) or (),
    )


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
    check_recorded_method_agrees_with_deck(
        parameters, calculation_deck_method(calc), calculation_key=key, software=software, lot_method=lot_method
    )
    check_recorded_method_agrees_with_deck(
        parameters,
        calculation_echo_method(calc),
        calculation_key=key,
        stated_in="output_log",
        software=software,
        lot_method=lot_method,
    )
