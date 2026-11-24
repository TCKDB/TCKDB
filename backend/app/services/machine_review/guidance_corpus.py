"""The guidance corpus the local reviewer searches, built from TCKDB's own declarations.

Entries are the rendered deposit-warning sentences, the scientific check register and the thresholds the register
names. Nothing here comes from a depositor or a model.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import Any

from app.services.machine_review.agent.guidance import GuidanceEntry, GuidanceIndex

WARNING = "deposit_warning"
CHECK = "check"
THRESHOLD = "threshold"


def _humanise(code: str) -> str:
    return code.replace("_", " ")


def _threshold_entry(threshold: Any) -> GuidanceEntry:
    fields: dict[str, Any] = {"unit": threshold.unit}
    if hasattr(threshold, "value"):
        fields["value"] = threshold.value
    else:
        fields["values"] = tuple(tuple(pair) for pair in threshold.values)
        fields["fallback"] = threshold.fallback
    return GuidanceEntry(
        source_key=f"threshold:{threshold.name}",
        kind=THRESHOLD,
        title=_humanise(threshold.name),
        text=threshold.rationale,
        fields=fields,
    )


def build_guidance_entries() -> list[GuidanceEntry]:
    """Warning sentences, then register entries, then thresholds, each in declaration order."""
    from app.scientific_checks.numbering import numbered_register
    from app.services.deposit_warning_messages import RENDERED

    entries = [
        GuidanceEntry(
            source_key=f"warning:{code}", kind=WARNING, title=_humanise(code), text=text, codes=(code,)
        )
        for code, text in RENDERED.items()
    ]
    thresholds: dict[str, GuidanceEntry] = {}
    for number, check in numbered_register():
        fields: dict[str, Any] = {"tier": check.tier.value}
        if check.escape_hatch:
            fields["escape_hatch"] = check.escape_hatch
        if check.divergence:
            fields["divergence"] = check.divergence
        codes = tuple(check.codes)
        entries.append(
            GuidanceEntry(
                source_key=f"check:{number}",
                kind=CHECK,
                title=_humanise(codes[0]) if codes else f"check {number}",
                text=check.asserts,
                fields=fields,
                codes=codes,
            )
        )
        for threshold in check.thresholds:
            entry = _threshold_entry(threshold)
            known = thresholds.setdefault(entry.source_key, entry)
            if known != entry:
                raise ValueError("Two thresholds share a name but differ.")
    return [*entries, *thresholds.values()]


@lru_cache(maxsize=1)
def default_guidance_index() -> GuidanceIndex:
    """The corpus index, built once; the register imports are heavy, so they wait for the first call."""
    return GuidanceIndex(build_guidance_entries())


def guidance_seed_codes(context: Any, summaries: Mapping[Any, Any]) -> tuple[str, ...]:
    """The sorted deposit-warning codes on the submission and on every record the review was given."""
    codes: set[str] = set()
    pack = getattr(context, "precheck_context", None)
    groups: list[Iterable[Any]] = [summary.deposit_warnings for summary in summaries.values()]
    if pack is not None:
        groups.extend(summary.deposit_warnings for summary in pack.record_summaries)
        groups.append(pack.submission_warnings)
    for group in groups:
        codes.update(warning.code for warning in group)
    return tuple(sorted(codes))


__all__ = ["build_guidance_entries", "default_guidance_index", "guidance_seed_codes"]
