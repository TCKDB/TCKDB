"""The versioned engineering bounds of a structure selection, and the refusals that enforce them.

A bound is a resource limit, not a measure of confidence. Every count below is taken over the *complete
authorized population*, so exceeding a bound refuses the decision outright: there is never a selection from a
prefix. A refusal names the bound and the visible count (what the caller is entitled to know); it never names
or counts rows the read profile hides. Raising a limit is a documented revision of
:class:`~app.services.structure_selection.models.SelectionBounds`, not a scientific rule change.

Discovery is limit-plus-one where the population is listed, so the count refused is never larger than the limit
plus the one extra row that proved the overflow.
"""

from __future__ import annotations

from app.api.error_contract import CodedValueError
from app.services.structure_selection.models import DEFERRED_QUANTITIES, Quantity, SelectionBounds

CODE_POPULATION_TOO_LARGE = "structure_selection_population_too_large"
CODE_EVIDENCE_TOO_LARGE = "structure_selection_evidence_too_large"
CODE_TRAVERSAL_TOO_DEEP = "structure_selection_traversal_too_deep"
CODE_UNSUPPORTED = "structure_selection_unsupported"


def check_candidates(bounds: SelectionBounds, visible: int) -> None:
    """Refuse (422 ``structure_selection_population_too_large``) when more units than the bound are visible.

    A count equal to the limit is allowed: the limit is the largest population that is decided.
    """
    if visible > bounds.candidates:
        raise CodedValueError(
            CODE_POPULATION_TOO_LARGE,
            f"{visible} visible candidate units exceed the selection limit of {bounds.candidates}; "
            "nothing was assessed and nothing was chosen from a subset.",
            context={"bound": "candidates", "visible": visible, "limit": bounds.candidates, "bounds_version": bounds.version},
            message_prefix=False,
        )


def check_nested_rows(bounds: SelectionBounds, rows: int) -> None:
    """Refuse (422 ``structure_selection_evidence_too_large``) when the necessary nested rows exceed the bound."""
    if rows > bounds.nested_rows:
        raise CodedValueError(
            CODE_EVIDENCE_TOO_LARGE,
            f"the necessary nested evidence is {rows} rows, over the limit of {bounds.nested_rows}; "
            "nothing was assessed and nothing was chosen from a subset.",
            context={"bound": "nested_rows", "visible": rows, "limit": bounds.nested_rows, "bounds_version": bounds.version},
            message_prefix=False,
        )


def traversal_too_deep(bounds: SelectionBounds) -> CodedValueError:
    """The refusal for a required dependency traversal that continues past the depth bound."""
    return CodedValueError(
        CODE_TRAVERSAL_TOO_DEEP,
        f"a required dependency traversal continues past the depth limit of {bounds.dependency_depth}; "
        "completeness cannot be claimed, so nothing was assessed.",
        context={"bound": "dependency_depth", "limit": bounds.dependency_depth, "bounds_version": bounds.version},
        message_prefix=False,
    )


def unsupported(reason: str, message: str, **context: object) -> CodedValueError:
    """A structured refusal for an operation this release does not evaluate."""
    return CodedValueError(
        CODE_UNSUPPORTED,
        message,
        context={"reason": reason, **context},
        message_prefix=False,
    )


def parse_quantity(raw: str) -> Quantity:
    """The energy a wire request names.

    A recognised deferred quantity (an enthalpy, a Gibbs energy, a barrier, a rate, ...) is a structured
    422 ``structure_selection_unsupported``: it is never answered with an energy that was not asked for. A name that
    is no quantity at all is an invalid request (``ValueError``, which the request schema reports as such).
    """
    try:
        return Quantity(raw)
    except ValueError:
        if raw in DEFERRED_QUANTITIES:
            raise unsupported(
                "deferred_quantity",
                f"{raw!r} is a recognised quantity this release does not evaluate: a structure selection compares "
                "an electronic energy or a zero-kelvin energy, and never substitutes one for another.",
                quantity=raw,
            ) from None
        raise ValueError(f"unknown quantity {raw!r}") from None
