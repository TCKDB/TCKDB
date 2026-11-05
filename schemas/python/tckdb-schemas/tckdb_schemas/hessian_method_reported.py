"""Read the Hessian method an ORCA output log REPORTS the frequency job ran.

A deck or the input a log echoes says what was *requested* (``NumFreq``, ``AnFreq``); an unqualified ``Freq`` names
nothing. The log itself prints which routine produced the Hessian, at default print level, and that is the better
evidence for the tolerance (ADR 0012): it is what ran. This module is the single owner of that reading, so upload
validation (``tckdb_schemas.hessian_method_assumption``) and the backend's reported-settings parser
(``app.services.reported_settings_parser``) cannot disagree.

Every pattern below was confirmed on a real log (excerpts are in ``backend/tests/parsers/test_reported_hessian_method.py``):

* analytic: a ``SCF HESSIAN`` section header. ORCA 4.1.2 and 5.0.4 print it as ``ORCA SCF HESSIAN`` between rules;
  ORCA 6.1.0 prints ``SCF HESSIAN`` between short rules.
* numerical: an ``ORCA NUMERICAL FREQUENCIES`` section header, and displaced-geometry lines that say what was
  differentiated. ORCA 6.1.0 prints ``<< Calculating gradient on displaced geometry N (of M) >>``: finite differences
  of gradients. ORCA 5.0.4 with a numerical gradient (``NumGrad``, coupled cluster) prints ``<< Calculating on
  displaced geometry ...``, which does not say whether gradients or energies were differenced, so it is not read.

Rules, all deliberate:

* **Observations only.** A method is returned only for a header the log prints. Absence is never a report, and
  nothing is derived from the manual.
* **Only a Hessian that fed a frequency listing counts.** An analytic or numerical Hessian is also computed for an
  optimiser's or an IRC's starting point; those sections are not followed by ``VIBRATIONAL FREQUENCIES``, and a
  header is bound to a listing only when it is the last Hessian header before it with no optimiser step between.
* **All listings must agree.** A log with several listings (``OptTS`` with ``NumFreq`` prints one per optimiser
  step) is read only when each one has a method and they are the same. Disagreement, or a listing with no bindable
  method, reports nothing: a value is never taken from one listing for another.
* **Several jobs in one file report nothing.** ``$new_job`` and ``%compound`` print one banner for several jobs, so a
  listing cannot be bound to one calculation. Several banners (concatenated runs) must each report the same method.

DB-free and Pydantic-free: text in, :class:`HessianMethod` out.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from tckdb_schemas.enums import HessianMethod

__all__ = ["ReportedHessianMethod", "orca_reported_hessian", "orca_reported_hessian_method", "reported_hessian_method"]


class ReportedHessianMethod(NamedTuple):
    """The method a log reports and the header text it was read from (``raw_value`` of the stored row)."""

    method: HessianMethod
    header: str


_ORCA_BANNER = re.compile(r"^\s*\*{3,}\s*\n\s*\*\s+O\s+R\s+C\s+A", re.M)
_ORCA_MULTI_JOB = re.compile(r"^\|\s*\d+>.*(\$new_job|%compound)", re.I | re.M)
_VIBRATIONAL = re.compile(r"^VIBRATIONAL FREQUENCIES[ \t]*$", re.M)
_ANALYTIC = re.compile(r"^[ \t]*(?:ORCA )?SCF HESSIAN[ \t]*$", re.M)
_NUMERICAL = re.compile(r"^[ \t]*ORCA NUMERICAL FREQUENCIES[ \t]*$", re.M)
_OPTIMISER_STEP = re.compile(r"ORCA GEOMETRY RELAXATION STEP")
_DISPLACED_GRADIENT = re.compile(r"<< Calculating gradient on displaced geometry")


def _split(text: str) -> list[str]:
    starts = [m.start() for m in _ORCA_BANNER.finditer(text)]
    if not starts:
        return [text]
    return [text[a:b] for a, b in zip(starts, [*starts[1:], len(text)], strict=True)]


def _listing(segment: str) -> ReportedHessianMethod | None:
    """The method of the Hessian that fed the listing ending ``segment``: its last Hessian header, nothing in between."""
    last: tuple[int, str, bool] | None = None
    for pattern, analytic in ((_ANALYTIC, True), (_NUMERICAL, False)):
        for m in pattern.finditer(segment):
            if last is None or m.start() > last[0]:
                last = (m.start(), m.group(0).strip(), analytic)
    if last is None:
        return None
    start, header, analytic = last
    after = segment[start:]
    if _OPTIMISER_STEP.search(after):
        return None
    if analytic:
        return ReportedHessianMethod(HessianMethod.analytic, header)
    if _DISPLACED_GRADIENT.search(after):
        return ReportedHessianMethod(HessianMethod.finite_difference_gradient, header)
    return None


def _job(job: str) -> ReportedHessianMethod | None:
    found: list[ReportedHessianMethod] = []
    previous = 0
    for vib in _VIBRATIONAL.finditer(job):
        listing = _listing(job[previous : vib.start()])
        if listing is None:
            return None
        found.append(listing)
        previous = vib.end()
    if not found or len({f.method for f in found}) != 1:
        return None
    return found[-1]


def orca_reported_hessian(text: str) -> ReportedHessianMethod | None:
    """The Hessian method an ORCA log reports for its frequency listing(s), or ``None`` (see the module rules)."""
    text = text.replace("\r\n", "\n")
    if _ORCA_MULTI_JOB.search(text):
        return None
    per_job = [_job(job) for job in _split(text)]
    if not per_job or any(j is None for j in per_job) or len({j.method for j in per_job if j}) != 1:
        return None
    return per_job[0]


def orca_reported_hessian_method(text: str) -> HessianMethod | None:
    found = orca_reported_hessian(text)
    return found.method if found is not None else None


def reported_hessian_method(software: str | None, text: str) -> HessianMethod | None:
    """The Hessian method ``text`` (an output log) reports for ``software``, or ``None``.

    Only ORCA is read: Gaussian's logs name no Hessian method that holds across the logs measured (the nuclear
    coupled-perturbed block is absent from some analytic frequency jobs and present in optimisations that
    compute a Hessian), so nothing is reported for it.
    """
    return orca_reported_hessian_method(text) if software == "orca" else None
