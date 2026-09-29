"""No producer-facing write route may ask a depositor for a database key.

A depositor has a molecule, a log file and a citation. They do not have
our row ids, and they cannot get one without first querying this
database — which is exactly the client we are not designing for. So a
producer payload names things with local keys, scientific content and
public refs, and the server resolves them (``.claude/rules/schema-rules.md``,
DR-0029 Requirement 1).

That rule was already asserted — on **one** root. A walker over
``NetworkPDepUploadRequest`` lived in ``tests/workflows/`` and checked the
pressure-dependent tree only, while ten other upload roots were checked
by nobody. The consequence is on the record: ``literature_id`` was found
by hand on the reaction bundle (#118), by hand again on the PDep route
(#154), and by hand a third time on the conformer and transition-state
routes (#194) — three separate discoveries of one field, over three
weeks, because a guard pointed at one root out of eleven is not a guard.

The first generalisation then repeated that mistake one level up. It
walked the routes under ``/uploads``, ``/jobs`` and ``/bundles``, and so
missed ``SubmissionSupersedeRequest.new_submission_id`` on
``POST /submissions/{id}/supersede`` (#571), a producer route outside
those prefixes. A path-prefix list is a hand-written list with extra
steps. This module now classifies every route of the live app by what it
*is* — an authenticated, non-role-gated write that takes a body — which
is the rule the producer contract generator uses (``classify_route`` in
``backend/scripts/generate_producer_contract.py``, #570). A new producer
route is covered the moment it is registered, wherever it is mounted.

Two surfaces of each producer route are checked: the request body's whole
model tree, and the route's own path and query parameters. The supersede
route of #571 also took the *old* submission's row id in its path, and a
body-only walker cannot see that.

Escape hatches exist, and all of them cost prose:

:data:`SANCTIONED_CHAINING`
    ``existing_*_id`` is the one sanctioned exception in
    ``.claude/rules/schema-rules.md`` — programmatic chaining, where a
    client cites a record *it deposited itself* in an earlier request
    and no local key can reach backwards across the request boundary.
    Entries are listed by exact ``Model.field``, not matched by pattern,
    so inventing a new one is a visible decision rather than a silent
    match.

:data:`DEFERRED_LEAKS` and :data:`DEFERRED_PARAM_LEAKS`
    Genuine leaks this guard found that predate it and are actively read
    by the server, so removing them is a behaviour change with its own
    design and its own review. They are frozen here with a reason and a
    tracked follow-up. Freezing them is what lets the guard go green
    over every route *today* instead of after a multi-route breaking
    change — a regression anywhere else fails immediately.

Do not add to any list to make a red run go away. A new database id on a
producer surface is the defect this file exists to catch.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import get_flat_dependant
from fastapi.routing import APIRoute

from app.api.app import create_app

# ---------------------------------------------------------------------------
# The producer routes, classified from the live surface
# ---------------------------------------------------------------------------
#
# The classification is the producer contract generator's own
# (``discover_routes`` / ``classify_route`` in
# backend/scripts/generate_producer_contract.py), imported rather than
# restated, so the routes this guard checks and the routes the published
# contract documents cannot drift apart.

_GENERATOR_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "generate_producer_contract.py"
)


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_producer_contract", _GENERATOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("generate_producer_contract", module)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()
WRITE_METHODS = generator.WRITE_METHODS


def route_label(route: APIRoute) -> str:
    """``"POST /api/v1/..."`` — the key the parameter allowlist uses."""
    return f"{','.join(sorted(route.methods & WRITE_METHODS))} {route.path}"


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


def _body_models(route: APIRoute) -> list[type]:
    """The Pydantic models a route's body parameters are declared as.

    Goes through ``Optional``/unions, so ``Body | None`` still yields
    ``Body`` rather than silently yielding nothing.
    """
    models: list[type] = []
    for body_param in route.dependant.body_params:
        models.extend(_nested_models(body_param.field_info.annotation))
    return models


PRODUCER_ROUTES: list[APIRoute] = [
    info.route for info in generator.discover_routes() if info.category == "producer"
]

#: ``{model_name: model_cls}`` for every producer request body.
PRODUCER_ROOTS: dict[str, type] = {
    model.__name__: model
    for route in PRODUCER_ROUTES
    for model in _body_models(route)
}

ROUTES_BY_LABEL: dict[str, APIRoute] = {
    route_label(route): route for route in PRODUCER_ROUTES
}


# ---------------------------------------------------------------------------
# The escape hatches
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

#: ``"METHOD path:param"`` -> why a producer route still takes a row id as a
#: path or query parameter, and what has to happen to remove it.
#:
#: Both entries were found by the parameter walk added for #571. Neither is
#: the supersede route's case (no caller, so it switched cleanly): each has a
#: real caller holding only the integer, because the upload response that
#: precedes the call hands back an integer and no ref.
DEFERRED_PARAM_LEAKS: dict[str, str] = {
    "POST /api/v1/submissions/{submission_id}/rights-attestations:submission_id": (
        "A depositor attests rights on the submission an upload just opened, "
        "and every upload, job and bundle response names that submission by "
        "its integer submission_id only; none carries the sub_ ref. Taking a "
        "ref here first needs those responses to return submission_ref, then "
        "a handle (integer or ref) window per the public identifier policy. "
        "Tracked in #578."
    ),
    "POST /api/v1/calculations/{calculation_id}/artifacts:calculation_id": (
        "tckdb-client's upload_artifacts and the ARC adapter send the integer "
        "a conformer or species upload returned for the calculation. Moving "
        "to a calc_ ref needs those responses to carry the ref and a client "
        "release that sends it, with the integer kept as a handle meanwhile. "
        "Tracked in #578."
    ),
}

_ALLOWED = {**SANCTIONED_CHAINING, **DEFERRED_LEAKS}


# ---------------------------------------------------------------------------
# The walkers
# ---------------------------------------------------------------------------


def _is_id_shaped(name: str) -> bool:
    return name == "id" or (name.endswith("_id") and not name.endswith("_uuid"))


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
            if name.endswith("_hash") or name == "public_ref" or _is_id_shaped(name):
                offenders.append(f"{cls.__name__}.{name}")
            for sub in _nested_models(field.annotation):
                offenders.extend(_walk(sub, seen))
        return offenders

    return sorted(set(_walk(model_cls, set())))


def id_shaped_params(route: APIRoute) -> list[str]:
    """Return ``"METHOD path:param"`` for every id-shaped path/query param.

    Flattens the dependency tree, so a parameter declared on a dependency
    is seen as well as one on the endpoint. Headers and cookies are not
    walked: they carry credentials and idempotency keys, not record names.
    """
    flat = get_flat_dependant(route.dependant)
    label = route_label(route)
    return sorted(
        {
            f"{label}:{param.name}"
            for param in (*flat.path_params, *flat.query_params)
            if _is_id_shaped(param.name)
        }
    )


# ---------------------------------------------------------------------------
# The walk covered what it claims to
# ---------------------------------------------------------------------------


def _independent_producer_labels() -> set[str]:
    """Re-derive the producer route set a second way, from a fresh app.

    Deliberately not the generator's ``classify_route``: this walks dependencies
    recursively rather than with a stack, reads the methods and body from
    the route afresh, and builds its own app. If the two ever disagree the
    classification above has drifted from what it claims to compute.
    """

    def names(dependant: Dependant) -> set[str]:
        found: set[str] = set()
        for sub in dependant.dependencies:
            if sub.call is not None:
                found.add(sub.call.__name__)
            found |= names(sub)
        return found

    labels: set[str] = set()
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        writes = sorted(set(route.methods) & {"POST", "PUT", "PATCH", "DELETE"})
        if not writes or route.path.startswith("/api/v1/auth/"):
            continue
        deps = names(route.dependant)
        if deps & {"require_admin", "require_curator_or_admin", "require_session_user"}:
            continue
        if "get_current_user" not in deps or not route.dependant.body_params:
            continue
        labels.add(f"{','.join(writes)} {route.path}")
    return labels


def test_producer_routes_were_actually_discovered() -> None:
    """The walker is worthless if the route scan silently finds nothing.

    A refactor that renames a dependency, moves a router, or stops declaring
    bodies as Pydantic models would empty ``PRODUCER_ROUTES`` and turn every
    parametrised case below into a vacuous pass. So: the walked set is
    non-empty, it equals an independent walk of ``create_app().routes``,
    every route in it contributed a body model to walk, and the routes this
    guard has been wrong about before are in it by name.
    """
    walked = set(ROUTES_BY_LABEL)
    assert len(PRODUCER_ROUTES) > 0
    assert len(walked) == len(PRODUCER_ROUTES), "two routes share a label"
    assert walked == _independent_producer_labels()

    unwalked = [route_label(r) for r in PRODUCER_ROUTES if not _body_models(r)]
    assert unwalked == [], f"producer routes whose body was not walked: {unwalked}"

    for expected in (
        # #571: outside every prefix the old guard walked.
        "POST /api/v1/submissions/{submission_ref}/supersede",
        "POST /api/v1/submissions/{submission_id}/rights-attestations",
        "POST /api/v1/calculations/{calculation_id}/artifacts",
        "POST /api/v1/bundles/submit",
        "POST /api/v1/uploads/conformers",
        "POST /api/v1/jobs/conformer",
    ):
        assert expected in walked, sorted(walked)

    for expected in (
        "ConformerUploadRequest",
        "TransitionStateUploadRequest",
        "ComputedSpeciesUploadRequest",
        "ComputedReactionUploadRequest",
        "NetworkPDepUploadRequest",
        # The contribution-bundle root: the surface contributors are
        # documented towards, and under a different prefix from the rest.
        "ContributionBundleV0",
        "SubmissionSupersedeRequest",
    ):
        assert expected in PRODUCER_ROOTS, sorted(PRODUCER_ROOTS)


def test_role_gated_routes_are_not_classified_as_producer() -> None:
    """The classification excludes, as well as includes, what it should.

    Approving a submission is a curator action: it is a write with a body
    and an authenticated caller, and only the role gate keeps it out.
    """
    approve = [
        route
        for route in create_app().routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v1/submissions/{submission_id}/approve"
    ]
    assert len(approve) == 1
    assert generator.classify_route(approve[0]).category == "role-gated"


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("root_name", sorted(PRODUCER_ROOTS))
def test_producer_root_exposes_no_fk_ids_or_hashes(root_name: str) -> None:
    """No FK id or derived hash anywhere in this root's request tree."""
    offenders = [
        field
        for field in fk_shaped_fields(PRODUCER_ROOTS[root_name])
        if field not in _ALLOWED
    ]
    assert offenders == [], (
        f"{root_name} exposes database ids a depositor cannot know: "
        f"{offenders}. Take scientific content, a local key or a public ref "
        f"instead and resolve it on the server (.claude/rules/schema-rules.md)."
    )


@pytest.mark.parametrize("label", sorted(ROUTES_BY_LABEL))
def test_producer_route_params_expose_no_db_ids(label: str) -> None:
    """No producer route names a record by row id in its path or query."""
    offenders = [
        param
        for param in id_shaped_params(ROUTES_BY_LABEL[label])
        if param not in DEFERRED_PARAM_LEAKS
    ]
    assert offenders == [], (
        f"{label} takes database ids as parameters: {offenders}. Name the "
        f"record by its public ref and resolve it on the server."
    )


# ---------------------------------------------------------------------------
# The allowlists stay honest
# ---------------------------------------------------------------------------


def test_allowlists_are_not_carrying_dead_entries() -> None:
    """Every allowlisted field or parameter must still be reachable.

    An entry that no longer matches anything is a fix nobody noticed — and
    it leaves a name in the file that would silence a *future* field of the
    same name. Removing it is the point at which the prose gets re-read.
    """
    reachable: set[str] = set()
    for model in PRODUCER_ROOTS.values():
        reachable.update(fk_shaped_fields(model))
    for route in PRODUCER_ROUTES:
        reachable.update(id_shaped_params(route))
    stale = sorted((set(_ALLOWED) | set(DEFERRED_PARAM_LEAKS)) - reachable)
    assert stale == [], (
        f"These allowlist entries match nothing on any producer route: "
        f"{stale}. Delete them."
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
    for entry, reason in {**_ALLOWED, **DEFERRED_PARAM_LEAKS}.items():
        assert len(reason.split()) >= 12, f"{entry}: reason too thin"
