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
# The document root itself. ``/reaction-entries/{id}/full`` keeps its ten
# include-gated sections *beside* ``request`` and ``review_summary`` rather
# than under any ``record``/``records`` key, so none of the scopes above can
# reach them. ``FULL_SCOPE`` is the trap in the neighbourhood: it is named
# after the same route but yields that document's *embedded record lists*,
# which is where ``trust`` lives and where the document's own sections do
# not. Reaching for it here is a silent no-op, not an error.
DOCUMENT_SCOPE = "document"

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
        "energy_corrections": ("energy_corrections",),
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

# ---------------------------------------------------------------------------
# Per-surface tables
#
# Every entry below was read off the service module that owns the token
# vocabulary — the ``if "<token>" in includes:`` that decides whether the
# field is built — and never off a list of nullable fields on the record
# schema. That distinction is the whole of decision 2 and it is not
# stylistic: most of what is ``| None`` on these records is an ungated
# scientific fact. ``nasa9``, ``chebyshev``, ``plog_entries``, ``wilhoit``,
# ``group_additivity``, ``assignment_scheme``, ``supersession``,
# ``literature``, ``software_release`` and their kind are *absent facts
# about the chemistry*, not unrequested sections, and none of them appears
# in any table here. If one ever does, the wire stops being able to say
# "this record has no NASA-9 polynomial" — which is the original defect
# restored from the other side.
# ---------------------------------------------------------------------------

# ``GET /species/search``. The four summaries sit two levels below the
# record root, at ``records[*].entries[*]``, and no token name equals its
# field name. ``include=review`` is legal here and gates nothing — every
# entry already carries its review badge (``services/…/species.py``).
SPECIES_SEARCH_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/species/search",
    sections={
        "thermo": ("thermo_summary",),
        "statmech": ("statmech_summary",),
        "transport": ("transport_summary",),
        "conformers": ("conformers_summary",),
    },
)

# ``/species-calculations/search``, GET and POST. One token. The record's
# ``frequency`` summary is deliberately **not** here: like ``energy``
# beside it, it is an ungated scientific fact whose ``null`` says "this
# kind of calculation produces no such result", and a strip that removed
# it would take that statement off the wire. Only the per-mode array is a
# section — the one block on this surface whose size grows with the
# molecule and again with the page.
SPECIES_CALCULATIONS_SEARCH_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/species-calculations/search",
    sections={"freq_modes": ("freq_modes",)},
)

# ``GET /reaction-entries/{id}/kinetics``. One token, two fields — the
# reason the table maps to a *tuple* rather than a name. A live probe sees
# only ``interpretation_assignments`` move, because the record measured
# carried no tunneling block; source is what says both are gated.
#
# Deliberately not applied to ``/kinetics/search`` or to the kinetics
# records embedded in ``/full``: ``interpretations`` is not a legal token
# on either, so applying it there would not omit an *unrequested* section,
# it would delete a section no request on that surface can ever produce.
# Those two keep their permanent ``null`` and the marker registry below
# reflects that.
KINETICS_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/reaction-entries/{id}/kinetics",
    sections={
        "interpretations": ("interpretation_assignments", "tunneling_application"),
    },
)

# ``GET /reaction-entries/{id}/full``. Ten sections at the **document
# root**, which is why ``DOCUMENT_SCOPE`` had to exist. ``review_records``
# is not here: it is produced by the separate ``include_review`` query
# parameter, not by any include token, so no include-driven strip can
# describe it correctly and it keeps its ``null``. ``review`` is a legal
# token on this surface that gates nothing at all.
REACTION_FULL_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/reaction-entries/{id}/full",
    sections={
        "species": ("species",),
        "kinetics": ("kinetics",),
        "transition_states": ("transition_states",),
        "calculations": ("calculations",),
        "path_search": ("path_search",),
        "irc": ("irc",),
        "scans": ("scans",),
        "conformers": ("conformers",),
        "artifacts": ("artifacts",),
        "atom_map": ("atom_map",),
    },
)

# The three transition-state surfaces share one record vocabulary and one
# builder, so they share one table. Applied at ``ANYWHERE_SCOPE`` because
# every one of these fields is materialised at two depths: on the record
# itself and again on each ``entries[*]`` sibling record beneath it.
# Verified against a live payload that each name occurs at exactly those
# depths and nowhere else.
TRANSITION_STATE_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/transition-states",
    sections={
        "entries": ("entries",),
        "calculations": ("calculations",),
        "geometries": ("geometries",),
        "review": ("review_history",),
        "validation_evidence": ("validation_evidence",),
    },
)

# ``/conformers/search``, ``/conformer-groups/{ref}``,
# ``/conformer-observations/{ref}`` — one vocabulary, two record shapes
# that carry the same five fields, and the same two-depth nesting as the
# transition-state family (``observations[*].selections`` and friends).
# ``assignment_scheme`` is *not* here: no token names it, and it is ``null``
# because this observation has no scheme.
CONFORMER_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/conformers",
    sections={
        "observations": ("observations",),
        "selections": ("selections",),
        "calculations": ("calculations",),
        "geometries": ("geometries",),
        "review": ("review_history",),
    },
)

# ``/statmech/search``, ``/statmech/{ref}``,
# ``/species-entries/{id}/statmech`` — one builder, no nesting.
STATMECH_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/statmech",
    sections={
        "source_calculations": ("source_calculations",),
        "torsions": ("torsions",),
        "electronic_levels": ("electronic_levels",),
        "frequencies": ("frequencies",),
        "conformers": ("conformers",),
        "review": ("review_history",),
    },
)

# ``/transport/search``, ``/transport/{ref}``,
# ``/species-entries/{id}/transport``.
TRANSPORT_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/transport",
    sections={
        "source_calculations": ("source_calculations",),
        "review": ("review_history",),
    },
)

NETWORK_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/networks",
    sections={
        "species": ("species",),
        "reactions": ("reactions",),
        "states": ("states",),
        "channels": ("channels",),
        "solves": ("solves",),
        "kinetics": ("kinetics",),
        "source_calculations": ("source_calculations",),
        "review": ("review_history",),
    },
)

NETWORK_SOLVE_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/network-solves",
    sections={
        "bath_gas": ("bath_gas",),
        "energy_transfer": ("energy_transfer",),
        "state_energies": ("state_energies",),
        "channel_barriers": ("channel_barriers",),
        "kinetics": ("kinetics",),
        "source_calculations": ("source_calculations",),
        "review": ("review_history",),
    },
)

# ``plog`` and ``points`` each govern a section *and* the two truncation
# companions that describe it. The companions are built inside the same
# ``if``, so a caller who did not ask for ``points`` has no more business
# seeing ``point_count_total`` than ``points`` itself.
# ``coefficients`` has no top-level companions — its truncation metadata is
# nested inside the payload it describes and travels with it.
NETWORK_KINETICS_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/network-kinetics",
    sections={
        "coefficients": ("coefficients",),
        "plog": ("plog", "plog_entry_count_total", "plog_entries_truncated"),
        "points": ("points", "point_count_total", "points_truncated"),
        "source_calculations": ("source_calculations",),
        "review": ("review_history",),
    },
)

# ``literature`` is a legal token on both provenance surfaces below and
# gates nothing: the record's ``literature`` field is built unconditionally
# from the row's own foreign key, so it is an ungated fact and stays out of
# both tables.
FREQUENCY_SCALE_FACTOR_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/frequency-scale-factors",
    sections={"used_by": ("used_by",)},
)

ENERGY_CORRECTION_SCHEME_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/energy-correction-schemes",
    sections={
        "corrections": ("corrections",),
        "used_by": ("used_by",),
    },
)

# ``/artifacts/search``. ``review`` is legal and gates no data field (the
# history is built and discarded); ``calculation`` gates three summaries
# *nested inside* the always-present calculation context rather than a
# section on the record, and those three are the exact names the
# collision warning on :class:`IncludeGatedSections` is about. Neither is
# in the table.
ARTIFACT_RECORD_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/artifacts/search",
    sections={"owner": ("owner",)},
)

# ``GET /literature/{ref}/records``. Note the requested-but-empty case is
# live here for a second reason: a linked record whose type is not
# reviewable keeps ``review: null`` even when the token is present.
LITERATURE_RECORDS_SECTIONS = IncludeGatedSections(
    surface="/api/v1/scientific/literature/{ref}/records",
    sections={"review": ("review",)},
)

# Component-scoped view of the tables above, consumed by
# ``app.api.public_openapi`` to stamp ``x-tckdb-include-gated`` on the
# hosted document.
#
# The marker is stamped on a *component property*; the strip is applied by
# an *operation*. Those do not align on their own, so the registry follows
# one rule: **a property is marked when it is include-gated on at least one
# operation that returns the component, and never when any operation
# returns it unconditionally.** The second half is what keeps the document
# honest — a marker on a key some operation always sends would tell a
# machine consumer that a permanently-present field is requestable.
#
# Two consequences are visible below and are deliberate.
#
# ``KineticsRecord`` is marked for ``trust`` alone. Its ``assessments`` is
# stripped on both kinetics surfaces but emitted unconditionally by the
# ``KineticsRecord`` list embedded in ``/reaction-entries/{id}/full``, and
# its ``interpretation_assignments`` / ``tunneling_application`` are gated
# only on ``/reaction-entries/{id}/kinetics`` — ``interpretations`` is not
# a legal token on ``/kinetics/search`` or on ``/full``, so both surfaces
# emit those two keys unconditionally as ``null``. All three properties
# therefore fail the second half of the rule and stay unmarked.
#
# ``ScientificCalculationRecord`` keeps the two residue properties the
# rule exists for: ``imaginary_mode_projections`` and ``trust`` are gated
# on ``/calculations/{ref}`` and unconditionally *absent* on
# ``/calculations/search``, whose vocabulary lacks both tokens. Absent more
# often than the marker promises is the harmless direction; present when it
# promises absent is not.
INCLUDE_GATED_COMPONENTS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "ScientificCalculationRecord": MappingProxyType(
            {
                **CALCULATION_RECORD_SECTIONS.fields_by_token(),
                **TRUST_SECTION.fields_by_token(),
            }
        ),
        "SpeciesEntryScientificRecord": MappingProxyType(
            SPECIES_SEARCH_SECTIONS.fields_by_token()
        ),
        "SpeciesCalculationsSearchRecord": MappingProxyType(
            SPECIES_CALCULATIONS_SEARCH_SECTIONS.fields_by_token()
        ),
        "KineticsRecord": MappingProxyType(TRUST_SECTION.fields_by_token()),
        "ThermoRecord": MappingProxyType(
            {
                **TRUST_SECTION.fields_by_token(),
                **ASSESSMENTS_SECTION.fields_by_token(),
            }
        ),
        "ScientificStatmechRecord": MappingProxyType(
            {
                **STATMECH_RECORD_SECTIONS.fields_by_token(),
                **TRUST_SECTION.fields_by_token(),
                **ASSESSMENTS_SECTION.fields_by_token(),
            }
        ),
        "ScientificTransportRecord": MappingProxyType(
            {
                **TRANSPORT_RECORD_SECTIONS.fields_by_token(),
                **TRUST_SECTION.fields_by_token(),
                **ASSESSMENTS_SECTION.fields_by_token(),
            }
        ),
        "ScientificTransitionStateRecord": MappingProxyType(
            TRANSITION_STATE_RECORD_SECTIONS.fields_by_token()
        ),
        "ScientificTransitionStateEntryRecord": MappingProxyType(
            {
                **TRANSITION_STATE_RECORD_SECTIONS.fields_by_token(),
                **TRUST_SECTION.fields_by_token(),
            }
        ),
        "ScientificConformerGroupRecord": MappingProxyType(
            CONFORMER_RECORD_SECTIONS.fields_by_token()
        ),
        "ScientificConformerObservationRecord": MappingProxyType(
            CONFORMER_RECORD_SECTIONS.fields_by_token()
        ),
        "ScientificNetworkRecord": MappingProxyType(
            NETWORK_RECORD_SECTIONS.fields_by_token()
        ),
        "ScientificNetworkSolveRecord": MappingProxyType(
            NETWORK_SOLVE_RECORD_SECTIONS.fields_by_token()
        ),
        "ScientificNetworkKineticsRecord": MappingProxyType(
            NETWORK_KINETICS_RECORD_SECTIONS.fields_by_token()
        ),
        "ScientificFrequencyScaleFactorRecord": MappingProxyType(
            FREQUENCY_SCALE_FACTOR_RECORD_SECTIONS.fields_by_token()
        ),
        "ScientificEnergyCorrectionSchemeRecord": MappingProxyType(
            ENERGY_CORRECTION_SCHEME_RECORD_SECTIONS.fields_by_token()
        ),
        "ScientificArtifactRecord": MappingProxyType(
            ARTIFACT_RECORD_SECTIONS.fields_by_token()
        ),
        "LiteratureLinkedRecordSummary": MappingProxyType(
            LITERATURE_RECORDS_SECTIONS.fields_by_token()
        ),
        "ScientificReactionFullResponse": MappingProxyType(
            REACTION_FULL_SECTIONS.fields_by_token()
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
    if scope == DOCUMENT_SCOPE:
        yield data
    elif scope == DETAIL_SCOPE:
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
            f"{DOCUMENT_SCOPE!r}, {DETAIL_SCOPE!r}, {SEARCH_SCOPE!r}, "
            f"{FULL_SCOPE!r}, {ANYWHERE_SCOPE!r}"
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
    ``"document"`` (the response object itself), ``"detail"`` (the singular
    ``record``), ``"search"`` (every entry of ``records``), ``"full"`` (the
    composite ``/reaction-entries/{id}/full`` document's embedded record
    lists) or ``"anywhere"`` (every dict in the payload, for a field that
    can nest at any depth).

    ``"document"`` and ``"full"`` are the two that name the same route and
    mean opposite things. ``/reaction-entries/{id}/full`` keeps its own ten
    sections at the document root, which only ``"document"`` yields;
    ``"full"`` yields the records embedded *inside* those sections, which is
    where the nested ``trust`` lives. Neither is a substitute for the other,
    and the wrong one strips nothing and raises nothing.
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


#: Every declared table, keyed by the constant name. The behaviour test
#: enumerates *these objects* — not a copy of them — so a table that is
#: declared and never wired, or wired and never declared, cannot pass.
ALL_INCLUDE_GATED_TABLES: Mapping[str, IncludeGatedSections] = MappingProxyType(
    {
        "ARTIFACT_RECORD_SECTIONS": ARTIFACT_RECORD_SECTIONS,
        "ASSESSMENTS_SECTION": ASSESSMENTS_SECTION,
        "CALCULATION_RECORD_SECTIONS": CALCULATION_RECORD_SECTIONS,
        "CONFORMER_RECORD_SECTIONS": CONFORMER_RECORD_SECTIONS,
        "ENERGY_CORRECTION_SCHEME_RECORD_SECTIONS": (
            ENERGY_CORRECTION_SCHEME_RECORD_SECTIONS
        ),
        "FREQUENCY_SCALE_FACTOR_RECORD_SECTIONS": (
            FREQUENCY_SCALE_FACTOR_RECORD_SECTIONS
        ),
        "KINETICS_RECORD_SECTIONS": KINETICS_RECORD_SECTIONS,
        "LITERATURE_RECORDS_SECTIONS": LITERATURE_RECORDS_SECTIONS,
        "NETWORK_KINETICS_RECORD_SECTIONS": NETWORK_KINETICS_RECORD_SECTIONS,
        "NETWORK_RECORD_SECTIONS": NETWORK_RECORD_SECTIONS,
        "NETWORK_SOLVE_RECORD_SECTIONS": NETWORK_SOLVE_RECORD_SECTIONS,
        "REACTION_FULL_SECTIONS": REACTION_FULL_SECTIONS,
        "SPECIES_CALCULATIONS_SEARCH_SECTIONS": (
            SPECIES_CALCULATIONS_SEARCH_SECTIONS
        ),
        "SPECIES_SEARCH_SECTIONS": SPECIES_SEARCH_SECTIONS,
        "STATMECH_RECORD_SECTIONS": STATMECH_RECORD_SECTIONS,
        "TRANSITION_STATE_RECORD_SECTIONS": TRANSITION_STATE_RECORD_SECTIONS,
        "TRANSPORT_RECORD_SECTIONS": TRANSPORT_RECORD_SECTIONS,
        "TRUST_SECTION": TRUST_SECTION,
    }
)


__all__ = [
    "ALL_INCLUDE_GATED_TABLES",
    "ANYWHERE_SCOPE",
    "ARTIFACT_RECORD_SECTIONS",
    "ASSESSMENTS_SECTION",
    "CALCULATION_RECORD_SECTIONS",
    "CONFORMER_RECORD_SECTIONS",
    "DETAIL_SCOPE",
    "DOCUMENT_SCOPE",
    "ENERGY_CORRECTION_SCHEME_RECORD_SECTIONS",
    "FREQUENCY_SCALE_FACTOR_RECORD_SECTIONS",
    "FULL_SCOPE",
    "INCLUDE_GATED_COMPONENTS",
    "KINETICS_RECORD_SECTIONS",
    "LITERATURE_RECORDS_SECTIONS",
    "NETWORK_KINETICS_RECORD_SECTIONS",
    "NETWORK_RECORD_SECTIONS",
    "NETWORK_SOLVE_RECORD_SECTIONS",
    "REACTION_FULL_SECTIONS",
    "SEARCH_SCOPE",
    "SPECIES_CALCULATIONS_SEARCH_SECTIONS",
    "SPECIES_SEARCH_SECTIONS",
    "STATMECH_RECORD_SECTIONS",
    "TRANSITION_STATE_RECORD_SECTIONS",
    "TRANSPORT_RECORD_SECTIONS",
    "TRUST_SECTION",
    "IncludeGatedSections",
    "omit_assessments_unless_requested",
    "omit_trust_unless_requested",
    "omit_unrequested_calculation_sections",
    "omit_unrequested_sections",
    "prepare_assessment_response",
]
