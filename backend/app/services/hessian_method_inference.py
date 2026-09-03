"""Assume a frequency job's Hessian method when the record does not say.

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
  variant — SDQ, SDTQ, DQ), QCISD, CCSD. None of these have an analytic
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

from tckdb_schemas.stationary_point import (
    TAU_ANALYTIC_DEFAULT_CM1,
    TAU_FINITE_DIFFERENCE_ENERGY_CM1,
    TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
    TauBasis,
    TauResolution,
)

__all__ = [
    "MethodFamily",
    "classify_method_family",
    "infer_hessian_method",
    "is_dft_functional",
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
_CIS_PREFIXES = ("cis",)

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
    if _startswith_any(token, _CIS_PREFIXES):
        return MethodFamily.CIS
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
    MethodFamily.CCSD,
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
    software_name: str | None, method: str | None
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

    for families, basis in rows:
        if family in families:
            tau_cm1 = _ASSUMED_TAU_BY_BASIS[basis]
            return TauResolution(
                tau_cm1=tau_cm1,
                basis=basis,
                reason=(
                    f"tau = {tau_cm1:.0f} cm-1: 'freq.hessian_method' is "
                    f"not recorded, so this is an assumption, not a "
                    f"recorded statement. {software_name!r}'s documented "
                    f"default second-derivative method for the "
                    f"{method!r} method family is assumed ({basis.value}) "
                    f"and given the same tau as the equivalent recorded "
                    f"basis. A later upload that states the Hessian "
                    f"method explicitly supersedes this assumption."
                ),
            )
    return None
