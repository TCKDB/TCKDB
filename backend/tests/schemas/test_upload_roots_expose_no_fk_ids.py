"""No upload root may ask a depositor for a database primary key.

A depositor has a molecule, a log file and a citation. They do not have
our row ids, and they cannot get one without first querying this
database — which is exactly the client we are not designing for. So an
upload payload names things with local keys and scientific content, and
the workflow layer resolves them (``.claude/rules/schema-rules.md``,
DR-0029 Requirement 1).

That rule was already asserted — on **one** root. A walker over
``NetworkPDepUploadRequest`` lived in ``tests/workflows/`` and checked the
pressure-dependent tree only, while ten other upload roots were checked
by nobody. The consequence is on the record: ``literature_id`` was found
by hand on the reaction bundle (#118), by hand again on the PDep route
(#154), and by hand a third time on the conformer and transition-state
routes (#194) — three separate discoveries of one field, over three
weeks, because a guard pointed at one root out of eleven is not a guard.

This module points it at all of them, and derives the root set from the
**live route table** rather than a hand-written list: a new upload route
is covered the moment it is registered, without anyone remembering to
add it here. That is the part that makes the rule enforceable rather
than merely stated.

Two escape hatches exist, and both cost prose:

:data:`SANCTIONED_CHAINING`
    ``existing_*_id`` is the one sanctioned exception in
    ``.claude/rules/schema-rules.md`` — programmatic chaining, where a
    client cites a record *it deposited itself* in an earlier request
    and no local key can reach backwards across the request boundary.
    Entries are listed by exact ``Model.field``, not matched by pattern,
    so inventing a new one is a visible decision rather than a silent
    match.

:data:`DEFERRED_LEAKS`
    Genuine leaks this guard found that predate it and are actively read
    by the server, so removing them is a behaviour change with its own
    design and its own review. They are frozen here with a reason and a
    tracked follow-up. Freezing them is what lets the guard go green
    over every root *today* instead of after a multi-root breaking
    change — a regression anywhere else fails immediately.

Do not add to either list to make a red run go away. A new FK id on an
upload surface is the defect this file exists to catch.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.api.app import create_app

# ---------------------------------------------------------------------------
# The roots, discovered from the live surface
# ---------------------------------------------------------------------------

#: Path prefixes whose POST bodies are depositor-facing upload payloads.
#: ``/uploads`` is the synchronous surface; ``/jobs`` is the async twin and
#: takes the same models, so it is included for the case where the two ever
#: diverge. ``/bundles`` is the contribution-bundle root — the surface a
#: contributor is actually pointed at — and it reaches many of the same
#: nested models, so leaving it out would have reproduced this issue's own
#: mistake at the level of the guard.
_UPLOAD_PREFIXES = ("/api/v1/uploads/", "/api/v1/jobs/", "/api/v1/bundles/")


def _upload_root_models() -> dict[str, type]:
    """Return ``{model_name: model_cls}`` for every upload request body."""
    app = create_app()
    roots: dict[str, type] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or "POST" not in route.methods:
            continue
        if not route.path.startswith(_UPLOAD_PREFIXES):
            continue
        for body_param in route.dependant.body_params:
            annotation = body_param.field_info.annotation
            if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
                roots[annotation.__name__] = annotation
    return roots


UPLOAD_ROOTS = _upload_root_models()


# ---------------------------------------------------------------------------
# The two escape hatches
# ---------------------------------------------------------------------------

#: ``Model.field`` -> why this id is a sanctioned citation, not a leak.
SANCTIONED_CHAINING: dict[str, str] = {
    "StatmechSourceCalculationIn.existing_calculation_id": (
        "A statmech record may be built from calculations deposited by an "
        "earlier request. Those rows exist and are the client's own, but no "
        "local key survives across the request boundary, so the id it was "
        "handed back is the only way to name them. Sanctioned in #129."
    ),
    "ThermoSourceCalculationIn.existing_calculation_id": (
        "Same case as the statmech spelling: thermo cites the calculations "
        "its numbers came from, and those may predate this request."
    ),
    "ThermoUploadRequest.existing_statmech_id": (
        "Thermo derived from a statmech record deposited earlier. The "
        "alternative — re-uploading the statmech block to get a local key — "
        "would create a duplicate record to express a reference."
    ),
}

#: ``Model.field`` -> why it is still here and what has to happen to remove it.
#:
#: Every entry below was found by this walker on the run that generalised it.
#: None is defensible as a permanent shape; each is deferred because the
#: server reads the value today, so removing the field is a behaviour change
#: needing its own design rather than a schema edit.
DEFERRED_LEAKS: dict[str, str] = {
    "SCFStabilityPayload.source_calculation_id": (
        "Cites the calculation whose log carries the SCF stability evidence. "
        "Read at app/services/calculation_resolution.py:784. The class "
        "docstring justifies these ids by saying 'the primitive upload routes "
        "take this shape' — but no primitive route takes SCFStabilityPayload "
        "at all; it is reached ONLY from /uploads/{conformers,transition-"
        "states,statmech,thermo,transport}, which are the depositor-facing "
        "roots the docstring says take SCFStabilityContent instead. The "
        "stated rationale does not describe the code. Removing the ids needs "
        "a local-key spelling for the citation; tracked separately."
    ),
    "SCFStabilityPayload.source_artifact_id": (
        "Same class, same five roots, same stale rationale — cites a "
        "calculation_artifact row instead of a calculation. Read at "
        "app/services/calculation_resolution.py:785."
    ),
    "CalculationScanPointCreate.geometry_id": (
        "Mutually exclusive alternative to the inline 'geometry' fragment, "
        "and the fragment is the shape a depositor can actually produce. "
        "Read at app/services/calculation_scan_resolution.py:84. Documented "
        "as being 'for primitive/internal callers', but it is reachable from "
        "the computed-species and computed-reaction bundle roots, where by "
        "that same reasoning it does not belong."
    ),
    "ReactionParticipantUpload.species_entry_id": (
        "Programmatic chaining in substance — 'exactly one of "
        "species_entry_id or species_entry' — but not in name: without the "
        "'existing_' prefix nothing distinguishes it from a plain FK, and it "
        "does not carry the ownership/role checks schema-rules.md requires "
        "of a sanctioned chaining field. Renaming it is a breaking change to "
        "a published route and needs those checks added at the same time."
    ),
}

_ALLOWED = {**SANCTIONED_CHAINING, **DEFERRED_LEAKS}


# ---------------------------------------------------------------------------
# The walker
# ---------------------------------------------------------------------------


def _nested_models(annotation) -> list[type]:
    """Every Pydantic model reachable through a field annotation."""
    found: list[type] = []
    stack = [annotation]
    while stack:
        current = stack.pop()
        if isinstance(current, type) and hasattr(current, "model_fields"):
            found.append(current)
            continue
        stack.extend(getattr(current, "__args__", ()) or ())
    return found


def fk_shaped_fields(model_cls: type) -> list[str]:
    """Return ``Model.field`` for every FK- or hash-shaped field in the tree.

    Walks the whole nested model tree rooted at ``model_cls``. Ignores the
    allowlists — callers filter — so a caller can always see the raw truth.
    """

    def _walk(cls: type, seen: set[type]) -> list[str]:
        if cls in seen:
            return []
        seen.add(cls)
        offenders: list[str] = []
        for name, field in cls.model_fields.items():
            if name.endswith("_hash") or name in {"id", "public_ref"}:
                offenders.append(f"{cls.__name__}.{name}")
            elif name.endswith("_id") and not name.endswith("_uuid"):
                offenders.append(f"{cls.__name__}.{name}")
            for sub in _nested_models(field.annotation):
                offenders.extend(_walk(sub, seen))
        return offenders

    return sorted(set(_walk(model_cls, set())))


def test_upload_roots_were_actually_discovered() -> None:
    """The walker is worthless if the route scan silently finds nothing.

    A refactor that moves the upload routers, renames the prefix, or stops
    declaring bodies as Pydantic models would empty ``UPLOAD_ROOTS`` and turn
    every parametrised case below into a vacuous pass. Assert the surface is
    non-trivial and that the roots #194 was about are in it.
    """
    assert len(UPLOAD_ROOTS) >= 12, sorted(UPLOAD_ROOTS)
    for expected in (
        "ConformerUploadRequest",
        "TransitionStateUploadRequest",
        "ComputedSpeciesUploadRequest",
        "ComputedReactionUploadRequest",
        "NetworkPDepUploadRequest",
        # The contribution-bundle root. Named explicitly because it is the
        # surface contributors are documented towards, and because it lives
        # under a different prefix from the rest — the exact way a root gets
        # left out of a check that looks complete.
        "ContributionBundleV0",
    ):
        assert expected in UPLOAD_ROOTS, sorted(UPLOAD_ROOTS)


@pytest.mark.parametrize("root_name", sorted(UPLOAD_ROOTS))
def test_upload_root_exposes_no_fk_ids_or_hashes(root_name: str) -> None:
    """No FK id or derived hash anywhere in this root's request tree."""
    offenders = [
        field
        for field in fk_shaped_fields(UPLOAD_ROOTS[root_name])
        if field not in _ALLOWED
    ]
    assert offenders == [], (
        f"{root_name} exposes database ids a depositor cannot know: "
        f"{offenders}. Take scientific content or a local key instead and "
        f"resolve it in the workflow layer (.claude/rules/schema-rules.md)."
    )


def test_allowlists_are_not_carrying_dead_entries() -> None:
    """Every allowlisted field must still be reachable from some root.

    An entry that no longer matches anything is a fix nobody noticed — and
    it leaves a name in the file that would silence a *future* field of the
    same name. Removing it is the point at which the prose gets re-read.
    """
    reachable: set[str] = set()
    for model in UPLOAD_ROOTS.values():
        reachable.update(fk_shaped_fields(model))
    stale = sorted(set(_ALLOWED) - reachable)
    assert stale == [], (
        f"These allowlist entries match nothing on any upload root: {stale}. "
        "Delete them."
    )


def test_sanctioned_and_deferred_lists_do_not_overlap() -> None:
    """A field is either a sanctioned citation or a leak awaiting removal."""
    both = sorted(set(SANCTIONED_CHAINING) & set(DEFERRED_LEAKS))
    assert both == [], both


def test_sanctioned_chaining_entries_are_all_existing_prefixed() -> None:
    """The sanctioned exception is ``existing_*_id`` and nothing else.

    Guards the list against being used as a general-purpose muzzle: a field
    that is not spelled ``existing_*_id`` has not met the naming half of the
    rule, whatever its intent, and belongs in ``DEFERRED_LEAKS`` until it is.
    """
    for entry in SANCTIONED_CHAINING:
        field = entry.split(".", 1)[1]
        assert field.startswith("existing_") and field.endswith("_id"), entry


def test_every_allowlist_entry_states_a_reason() -> None:
    """A bare name would let the next person silence a real regression."""
    for entry, reason in _ALLOWED.items():
        assert len(reason.split()) >= 12, f"{entry}: reason too thin"
