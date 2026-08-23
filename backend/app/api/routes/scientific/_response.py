"""Response-boundary helpers for scientific read routes.

Two things happen at this boundary. The Phase D internal-ID policy is
applied (``apply_internal_ids_visibility``), and **include-gated sections
the caller did not ask for are dropped from the serialized payload**.
This module owns the second one.

Why a key is dropped rather than nulled
---------------------------------------
A section that was not requested and a section that does not exist for
this record are different facts. Sharing one wire value (``null``) makes
them indistinguishable, and the failure mode of guessing wrong is silent:
a reader concludes the database is empty. So there are three states and
three representations::

    not requested                -> key absent
    requested, nothing there     -> key present, ``null`` (or ``[]``)
    requested, something there   -> key present, populated

The middle row is deliberate and must survive: collapsing it back into
the top row restores the same ambiguity from the other direction. See
``ScientificCalculationRecord``'s docstring in
``app/schemas/reads/scientific_calculation.py``, which states the rule for
the surface this machinery was first written for.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.services.scientific_read.internal_ids import apply_internal_ids_visibility

AssessmentAttacher = Callable[[Session, Any], Any]

# Envelope shapes the strip knows how to walk. ``scope`` names *where the
# records are*, never *which fields to drop* — that is the table's job and
# only the table's job.
DETAIL_SCOPE = "detail"
SEARCH_SCOPE = "search"
FULL_SCOPE = "full"
ANYWHERE_SCOPE = "anywhere"

# The composite ``/reaction-entries/{id}/full`` document embeds records
# under these three keys rather than under ``record``/``records``.
_FULL_EMBEDDED_RECORD_SECTIONS = ("kinetics", "calculations", "transition_states")


@dataclass(frozen=True)
class IncludeGatedSections:
    """A declared table of include token → response field names, per surface.

    The table is the whole safety argument, so it is worth being explicit
    about what it buys.

    **The strip is structurally incapable of touching a field this table
    does not name.** :func:`omit_unrequested_sections` computes the set of
    keys to pop from the table alone — it never inspects a value, never
    tests for ``None``, and accepts no predicate. There is no argument you
    can pass it that means "drop the nulls". That is not a convention to
    be observed; it is the only thing the function can express.

    It has to be that way, because nullability means different things in
    different places and one of them is a live protocol signal. The Python
    client (``clients/python/pagination.py``) reads an **absent**
    ``next_cursor`` as "this server predates the keyset contract, restart
    the traversal from offset zero" and a **present-and-null**
    ``next_cursor`` as "this was the last page, you are done". A blanket
    "omit null optionals" pass would turn every completed traversal into a
    restart and silently yield the entire result set twice — duplicated
    records in a chemistry dataset, with no error anywhere. The same rule
    read from the other side: ``pagination.py`` also tests
    ``"post_collapse_total" in pagination``, so that field must never
    start being emitted as an explicit ``null``. Never null a field a
    client tests for presence, and never omit a field a client tests for
    nullity.

    **The table is per-surface and explicit rather than derived from field
    names, because names collide.** One calculation response carries two
    fields called ``workflow_tool_release``: the record's own provenance
    field, which is ``null`` because the calculation references no
    workflow tool and must keep that ``null``; and one nested inside the
    include-gated ``execution_environment`` block, which disappears with
    its section. Same name, same response, opposite treatment. Any
    implementation matching on names gets one of them wrong.

    **The mapping is not the identity.** ``"review" → "review_history"``
    is a live entry, so deriving a field name from a token name would be
    wrong on the first surface it met.

    :param surface: human-readable name of the surface this table
        describes, used in error messages and when auditing the tables.
    :param sections: include token → the response field names that token
        governs. A token absent from the caller's resolved include set has
        every one of its fields popped.
    """

    surface: str
    sections: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        normalized = {token: tuple(fields) for token, fields in self.sections.items()}
        object.__setattr__(self, "sections", MappingProxyType(normalized))

    def fields_to_omit(self, requested: Iterable[str]) -> set[str]:
        """Return the response field names the resolved include set does not license."""
        licensed = set(requested)
        return {
            field
            for token, fields in self.sections.items()
            if token not in licensed
            for field in fields
        }

    def fields_by_token(self) -> dict[str, str]:
        """Return field name → gating token, for documentation surfaces."""
        return {
            field: token
            for token, fields in self.sections.items()
            for field in fields
        }


# ---------------------------------------------------------------------------
# Declared tables
# ---------------------------------------------------------------------------

# Heavy include tokens whose corresponding ``record`` field is omitted
# when the caller did not opt in, on ``/api/v1/scientific/calculations/*``.
# Each new heavy include needs one entry and nothing else.
#
# ``imaginary_mode_projections`` is in this table without being in the
# surface's ``_HEAVY_INCLUDE_TOKENS``, which is only possible because the
# table is declared rather than inferred.
CALCULATION_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/calculations",
    sections={
        "results": ("results",),
        "dependencies": ("dependencies",),
        "artifacts": ("artifacts",),
        "input_geometries": ("input_geometries",),
        "output_geometries": ("output_geometries",),
        "geometry_validation": ("geometry_validation",),
        "scf_stability": ("scf_stability",),
        "wavefunction_diagnostic": ("wavefunction_diagnostic",),
        "spin_diagnostic": ("spin_diagnostic",),
        "parameters": ("parameters",),
        "constraints": ("constraints",),
        "review": ("review_history",),
        "freq_modes": ("freq_modes",),
        "imaginary_mode_projections": ("imaginary_mode_projections",),
        "scan": ("scan",),
        "irc": ("irc",),
        "path_search": ("path_search",),
        "execution_environment": ("execution_environment",),
    },
)

# ``trust`` and ``assessments`` are single-token tables applied wherever
# their two long-standing helpers are called. They are one-entry tables
# rather than bare strings so that every strip in the codebase goes
# through the same declared-table gate.
TRUST_SECTION = IncludeGatedSections(
    surface="trust",
    sections={"trust": ("trust",)},
)

ASSESSMENTS_SECTION = IncludeGatedSections(
    surface="assessments",
    sections={"assessments": ("assessments",)},
)

# Component-scoped view of the tables above, consumed by
# ``app.api.public_openapi`` to stamp ``x-tckdb-include-gated`` on the
# hosted document.
#
# A component is listed here only when **every** operation that can return
# it already omits the property, because the marker is a claim about the
# hosted runtime and the document must not run ahead of it.
# ``ScientificCalculationRecord`` is the only such component today: it is
# returned by the three ``/calculations/*`` operations and nothing else,
# and all three strip both tables below. Components whose ``trust`` or
# ``assessments`` is omitted on some operations and nulled on others —
# ``KineticsRecord``, ``ThermoRecord``,
# ``ScientificTransitionStateEntryRecord`` — join this registry when the
# surfaces that null them are flipped, not before.
INCLUDE_GATED_COMPONENTS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "ScientificCalculationRecord": MappingProxyType(
            {
                **CALCULATION_RECORD_SECTIONS.fields_by_token(),
                **TRUST_SECTION.fields_by_token(),
            }
        ),
    }
)


# ---------------------------------------------------------------------------
# The strip
# ---------------------------------------------------------------------------


def _every_dict(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _every_dict(value)
    elif isinstance(node, list):
        for value in node:
            yield from _every_dict(value)


def _record_nodes(data: dict[str, Any], scope: str) -> Iterator[dict[str, Any]]:
    """Yield the dicts a strip of *scope* is allowed to pop keys from."""
    if scope == DETAIL_SCOPE:
        record = data.get("record")
        if isinstance(record, dict):
            yield record
    elif scope == SEARCH_SCOPE:
        for record in data.get("records", []) or []:
            if isinstance(record, dict):
                yield record
    elif scope == FULL_SCOPE:
        for section in _FULL_EMBEDDED_RECORD_SECTIONS:
            for record in data.get(section, []) or []:
                if isinstance(record, dict):
                    yield record
    elif scope == ANYWHERE_SCOPE:
        yield from _every_dict(data)
    else:
        raise ValueError(
            f"unknown response scope {scope!r}; expected one of "
            f"{DETAIL_SCOPE!r}, {SEARCH_SCOPE!r}, {FULL_SCOPE!r}, "
            f"{ANYWHERE_SCOPE!r}"
        )


def omit_unrequested_sections(
    visibility: Any,
    payload: Any,
    *,
    table: IncludeGatedSections,
    scope: str = DETAIL_SCOPE,
):
    """Drop the include-gated fields *table* declares and the caller did not request.

    ``visibility`` is whatever
    :func:`app.services.scientific_read.internal_ids.apply_internal_ids_visibility`
    returned: either the Pydantic model unchanged (when the deployment
    allows internal ids *and* the caller opted in) or a
    :class:`~fastapi.responses.JSONResponse` carrying a pre-stripped dict.
    In the JSONResponse branch the serialized dict is mutated; in the
    Pydantic branch it is re-serialized via ``model_dump`` so keys can be
    dropped too. The OpenAPI / ``response_model`` contract is preserved in
    both branches because every dropped key is declared
    ``... | None = None`` on the schema, so the document already permits
    its absence.

    Distinguishing "did not ask" (key absent) from "asked, no row" (key
    present, value ``null``) lets clients tell the two cases apart without
    having to re-read ``request.include``.

    ``table`` must be an :class:`IncludeGatedSections`. That is enforced
    rather than assumed: the type is the only thing standing between this
    helper and a blanket null-strip, and the reason it matters is written
    out on :class:`IncludeGatedSections` itself.

    ``scope`` selects which part of the envelope holds records —
    ``"detail"`` (the singular ``record``), ``"search"`` (every entry of
    ``records``), ``"full"`` (the composite ``/reaction-entries/{id}/full``
    document's embedded record lists) or ``"anywhere"`` (every dict in the
    payload, for a field that can nest at any depth).
    """
    if not isinstance(table, IncludeGatedSections):
        raise TypeError(
            "omit_unrequested_sections requires a declared IncludeGatedSections "
            f"table, got {type(table).__name__!r}. The strip is driven by a "
            "declared token -> field table and by nothing else: a field no "
            "table names keeps its null, because nullability is a live "
            "protocol signal elsewhere in the API (see IncludeGatedSections)."
        )

    to_drop = table.fields_to_omit(payload.request.include)
    if not to_drop:
        return visibility

    if isinstance(visibility, JSONResponse):
        data = json.loads(visibility.body)
    else:
        data = visibility.model_dump(mode="json")

    for record in _record_nodes(data, scope):
        for key in to_drop:
            record.pop(key, None)

    return JSONResponse(data)


def prepare_assessment_response(
    session: Session,
    payload: Any,
    *,
    attach_assessments: AssessmentAttacher,
) -> Any:
    """Attach requested assessments, then apply public-field visibility.

    The resolved read profile is stamped inside
    :func:`app.services.scientific_read.internal_ids.apply_internal_ids_visibility`,
    which is the one function every enveloped scientific route passes through
    — see :func:`app.services.scientific_read.profile.stamp_read_profile`.
    """

    if "assessments" in set(payload.request.include):
        attach_assessments(session, payload)
    visibility = apply_internal_ids_visibility(payload)
    return omit_assessments_unless_requested(visibility, payload)


def omit_trust_unless_requested(
    visibility: Any,
    payload: Any,
    *,
    scope: str = DETAIL_SCOPE,
):
    """Drop ``record.trust`` unless the caller explicitly requested it.

    ``scope`` selects which embedded shape to clean:

    - ``"detail"`` — single ``record.trust`` on the top-level object.
    - ``"search"`` — ``records[*].trust`` on a list response.
    - ``"full"`` — composite ``/reaction-entries/{id}/full`` shape;
      strips ``trust`` from each embedded kinetics record, each
      embedded calculation summary, and each embedded
      transition-state-entry record so the default ``/full`` payload
      stays byte-identical to its pre-trust-propagation shape.
    """
    return omit_unrequested_sections(
        visibility, payload, table=TRUST_SECTION, scope=scope
    )


def omit_assessments_unless_requested(visibility: Any, payload: Any):
    """Remove opt-in assessment summaries from every nested record by default."""
    return omit_unrequested_sections(
        visibility, payload, table=ASSESSMENTS_SECTION, scope=ANYWHERE_SCOPE
    )


def omit_unrequested_calculation_sections(
    visibility: Any,
    payload: Any,
    *,
    scope: str = DETAIL_SCOPE,
):
    """Drop the heavy ``/calculations/*`` sections the caller did not request.

    ``scope='detail'`` operates on the singular ``record`` field;
    ``scope='search'`` operates on every entry of the ``records`` list.
    """
    return omit_unrequested_sections(
        visibility, payload, table=CALCULATION_RECORD_SECTIONS, scope=scope
    )


__all__ = [
    "ANYWHERE_SCOPE",
    "ASSESSMENTS_SECTION",
    "CALCULATION_RECORD_SECTIONS",
    "DETAIL_SCOPE",
    "FULL_SCOPE",
    "INCLUDE_GATED_COMPONENTS",
    "SEARCH_SCOPE",
    "TRUST_SECTION",
    "IncludeGatedSections",
    "omit_assessments_unless_requested",
    "omit_trust_unless_requested",
    "omit_unrequested_calculation_sections",
    "omit_unrequested_sections",
    "prepare_assessment_response",
]
