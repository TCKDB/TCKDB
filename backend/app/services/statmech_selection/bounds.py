"""The bounds of a statmech selection, and the coded refusals that enforce them.

A bound is a resource limit, not a measure of confidence. Every count is taken over the *complete authorized population*,
so exceeding a bound refuses the answer outright: there is never a decision from a prefix. A refusal names the bound and
the visible count (what the caller is entitled to know); it never names or counts a row the read profile hides.

One machine code, ``statmech_selection_bound_exceeded``, carries the bound in its context; the unsupported-population
refusal is separate because it is a different repair (ask for something this release evaluates).
"""

from __future__ import annotations

from app.api.error_contract import CodedValueError
from app.services.statmech_selection.models import SelectionBounds
from app.services.statmech_selection.vocabulary import CODE_BOUND_EXCEEDED

CODE_UNSUPPORTED = "statmech_selection_unsupported"


def _refuse(message: str, bounds: SelectionBounds, bound: str, **context: object) -> CodedValueError:
    return CodedValueError(
        CODE_BOUND_EXCEEDED,
        message,
        context={"bound": bound, "bounds_version": bounds.version, **context},
        message_prefix=False,
    )


def check_candidates(bounds: SelectionBounds, visible: int) -> None:
    """Refuse when more candidate units than the bound are visible. A count equal to the limit is decided."""
    if visible > bounds.candidates:
        raise _refuse(
            f"{visible} visible candidates exceed the selection limit of {bounds.candidates}; nothing was assessed and "
            "nothing was chosen from a subset.",
            bounds,
            "candidates",
            visible=visible,
            limit=bounds.candidates,
        )


def check_nested_rows(bounds: SelectionBounds, rows: int) -> None:
    """Refuse when the necessary nested rows (members, sources, modes, levels, torsions, findings) exceed the bound."""
    if rows > bounds.nested_rows:
        raise _refuse(
            f"the necessary nested evidence is {rows} rows, over the limit of {bounds.nested_rows}; nothing was assessed "
            "and nothing was chosen from a subset.",
            bounds,
            "nested_rows",
            visible=rows,
            limit=bounds.nested_rows,
        )


def traversal_too_deep(bounds: SelectionBounds) -> CodedValueError:
    """The refusal for a required dependency traversal that continues past the depth bound."""
    return _refuse(
        f"a required dependency traversal continues past the depth limit of {bounds.dependency_depth}; completeness cannot "
        "be claimed, so nothing was assessed.",
        bounds,
        "dependency_depth",
        limit=bounds.dependency_depth,
    )


def check_manifest_size(bounds: SelectionBounds, size_bytes: int) -> None:
    """Refuse when the canonical manifest exceeds its bound: no decision is returned without its whole manifest."""
    if size_bytes > bounds.manifest_bytes:
        raise _refuse(
            f"the replayable manifest is {size_bytes} bytes, over the limit of {bounds.manifest_bytes}; no decision is "
            "returned without its complete manifest.",
            bounds,
            "manifest_bytes",
            size=size_bytes,
            limit=bounds.manifest_bytes,
        )


def unsupported(reason: str, message: str) -> CodedValueError:
    """A structured refusal for a population this release does not evaluate."""
    return CodedValueError(CODE_UNSUPPORTED, message, context={"reason": reason}, message_prefix=False)
