"""The method-family and DFT-rung filter fields, shared by every read request that offers them.

A family or a rung is a **classification** of a level of theory's method (:mod:`app.chemistry.method_families`), not a
measure of accuracy: the rung ladder covers density functionals only, and how accurate a method is depends on the
property. These fields restrict by that recorded classification and never order, rank or select anything.

``method_family`` takes one family or a list (any of them matches). ``dft_rung_min`` and ``dft_rung_max`` bound the DFT
rung, 1 to 5, and imply ``method_family=dft``: a request that pairs a bound with a family list that has no ``dft`` in it
matches nothing.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from app.chemistry.method_families import MAX_DFT_RUNG, MIN_DFT_RUNG, MethodFamily


def _one_or_many(value: Any) -> Any:
    if isinstance(value, (str, MethodFamily)):
        return [value]
    return value


#: One family or a list of them; always a list once parsed.
MethodFamilyFilter = Annotated[list[MethodFamily] | None, BeforeValidator(_one_or_many)]
#: A DFT rung bound.
DftRungBound = Annotated[int | None, Field(ge=MIN_DFT_RUNG, le=MAX_DFT_RUNG)]


def echo_method_class(request: Any, out: dict[str, Any]) -> None:
    """Add the supplied family and rung filters to a request echo (``out`` is changed in place)."""
    families = getattr(request, "method_family", None)
    if families is not None:
        out["method_family"] = sorted({f.value for f in families})
    for name in ("dft_rung_min", "dft_rung_max"):
        value = getattr(request, name, None)
        if value is not None:
            out[name] = value


def echo_source_provenance(request: Any, out: dict[str, Any]) -> None:
    """Add the further provenance filters and ``scientific_origin`` a thermo or kinetics request states to its echo."""
    for name in ("method", "basis", "software_version", "workflow_tool", "workflow_tool_version"):
        value = getattr(request, name, None)
        if value is not None:
            out[name] = value
    echo_method_class(request, out)
    origin = getattr(request, "scientific_origin", None)
    if origin is not None:
        out["scientific_origin"] = origin.value
