"""The ref guard's planner, on synthetic response models (no database).

Every row of the design's action table has a test, and each test names the mutation it kills. ``hidden_refs`` is
replaced with a fixed set, so what is under test is only where a hidden ref sits and what the guard does about it.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.db.models.common import ProfileRecommendation, ReadProfile
from app.services.scientific_read import internal_ids, ref_guard
from app.services.scientific_read.profile import (
    EXPLORATORY,
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)

#: The real registry, captured before the autouse fixture swaps in synthetic pointer classes.
REAL_POINTER_MODELS = ref_guard.POINTER_MODELS

CURATED = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)


def ref(prefix: str, n: int) -> str:
    return f"{prefix}_{str(n).zfill(26)}"


HIDDEN_CALC, VISIBLE_CALC, OTHER_HIDDEN_CALC = ref("calc", 1), ref("calc", 2), ref("calc", 3)
HIDDEN_CG = ref("cg", 1)
HIDDEN_TSE = ref("tse", 1)


class Ptr(BaseModel):
    """A pointer block: its subject is the linked calculation."""

    calculation_ref: str
    calculation_id: int | None = None
    role: str = "freq"


class OptPtr(BaseModel):
    """A pointer that names a second record, to check that one removal covers both."""

    calculation_ref: str
    group_ref: str | None = None


class Outer(BaseModel):
    """A pointer that contains pointers."""

    calculation_ref: str
    inner: list[Ptr] = []


class Facts(BaseModel):
    """Not a pointer: a visible record's own facts."""

    label: str = "x"
    scan_ref: str | None = None
    scan_id: int | None = None
    required_ref: str | None = None
    calc_refs: list[str] = []
    calc_ids: list[int] = []
    blob: dict[str, Any] = {}
    open_list: list[Any] = []
    pointers: list[Ptr | OptPtr] = []
    pointer: Ptr | None = None
    must_have: Ptr | None = None


class Request(BaseModel):
    filter_ref: str | None = None


class Envelope(BaseModel):
    request: Request = Request()
    record: Facts | None = None
    records: list[Facts] = []
    extra: Facts | None = None


class RequiredHolder(BaseModel):
    """A visible record whose required field holds a ref."""

    owner_ref: str


class RequiredEnvelope(BaseModel):
    record: RequiredHolder | None = None
    records: list[RequiredHolder] = []
    block: RequiredHolder | None = None


class PtrRequiredEnvelope(BaseModel):
    """A pointer reached only through a required field, inside a list pointer."""

    rows: list[Outer] = []


@pytest.fixture(autouse=True)
def _register_synthetic_pointers(monkeypatch):
    monkeypatch.setattr(ref_guard, "POINTER_MODELS", frozenset({Ptr, OptPtr, Outer}))
    ref_guard._FIELD_CACHE.clear()


def hide(monkeypatch, *refs: str):
    monkeypatch.setattr(ref_guard, "hidden_refs", lambda session, found, *, profile: set(refs) & set(found))


def run(payload, *, apply: bool = True):
    return ref_guard.withhold_in_model(None, payload, profile=CURATED, apply=apply)


def test_a_pointer_in_a_list_is_deleted_whole(monkeypatch):
    """Kills: blanking the ref field of a pointer row instead of removing the row (its role and level would remain)."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(extra=Facts(pointers=[Ptr(calculation_ref=HIDDEN_CALC), Ptr(calculation_ref=VISIBLE_CALC)]))
    report = run(payload)
    assert [p.calculation_ref for p in payload.extra.pointers] == [VISIBLE_CALC]
    assert [s.action for s in report.sites] == ["delete_pointer"]


def test_every_hidden_row_of_one_list_goes_and_the_rest_keep_their_order(monkeypatch):
    """Kills: deleting in ascending index order (the second deletion hits the wrong element)."""
    hide(monkeypatch, HIDDEN_CALC, OTHER_HIDDEN_CALC)
    rows = [Ptr(calculation_ref=r) for r in (HIDDEN_CALC, VISIBLE_CALC, OTHER_HIDDEN_CALC, ref("calc", 9))]
    payload = Envelope(extra=Facts(pointers=rows))
    run(payload)
    assert [p.calculation_ref for p in payload.extra.pointers] == [VISIBLE_CALC, ref("calc", 9)]


def test_one_pointer_naming_two_hidden_refs_is_removed_once(monkeypatch):
    """Kills: planning one deletion per ref without deduplicating (the next row would be deleted as well)."""
    hide(monkeypatch, HIDDEN_CALC, HIDDEN_CG)
    payload = Envelope(
        extra=Facts(
            pointers=[
                OptPtr(calculation_ref=HIDDEN_CALC, group_ref=HIDDEN_CG),
                Ptr(calculation_ref=VISIBLE_CALC),
            ]
        )
    )
    run(payload)
    assert [p.calculation_ref for p in payload.extra.pointers] == [VISIBLE_CALC]


def test_a_pointer_in_a_nullable_field_becomes_none(monkeypatch):
    """Kills: deleting nothing for a pointer held by an optional field; blanking only the inner ref."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(extra=Facts(pointer=Ptr(calculation_ref=HIDDEN_CALC, calculation_id=7)))
    report = run(payload)
    assert payload.extra.pointer is None
    assert [s.action for s in report.sites] == ["null_pointer"]


def test_the_nearest_pointer_wins(monkeypatch):
    """Kills: climbing to the outermost pointer (the visible outer block would be lost with the hidden inner row)."""
    hide(monkeypatch, HIDDEN_CALC)
    outer = Outer(
        calculation_ref=VISIBLE_CALC,
        inner=[Ptr(calculation_ref=HIDDEN_CALC), Ptr(calculation_ref=ref("calc", 5))],
    )
    payload = PtrRequiredEnvelope(rows=[outer])
    run(payload)
    assert [r.calculation_ref for r in payload.rows] == [VISIBLE_CALC]
    assert [p.calculation_ref for p in payload.rows[0].inner] == [ref("calc", 5)]


def test_a_pointer_held_by_a_required_field_defers_to_the_next_pointer_out(monkeypatch):
    """Kills: stopping at the first pointer even when it cannot be removed (it would raise or write None)."""
    hide(monkeypatch, HIDDEN_CALC)

    class Wrapper(BaseModel):
        calculation_ref: str
        held: Ptr  # required: cannot be blanked

    class Env(BaseModel):
        rows: list[Wrapper] = []

    monkeypatch.setattr(ref_guard, "POINTER_MODELS", frozenset({Ptr, Wrapper}))
    payload = Env(
        rows=[
            Wrapper(calculation_ref=VISIBLE_CALC, held=Ptr(calculation_ref=HIDDEN_CALC)),
            Wrapper(calculation_ref=ref("calc", 5), held=Ptr(calculation_ref=ref("calc", 6))),
        ]
    )
    run(payload)
    assert [w.calculation_ref for w in payload.rows] == [ref("calc", 5)]


def test_a_ref_in_a_string_list_is_deleted_with_its_aligned_id(monkeypatch):
    """Kills: leaving the id behind (the integer would still identify the hidden record); misaligned deletion."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(extra=Facts(calc_refs=[VISIBLE_CALC, HIDDEN_CALC, ref("calc", 4)], calc_ids=[10, 11, 12]))
    run(payload)
    assert payload.extra.calc_refs == [VISIBLE_CALC, ref("calc", 4)]
    assert payload.extra.calc_ids == [10, 12]


def test_misaligned_ref_and_id_lists_are_unplaceable_not_guessed(monkeypatch):
    """Kills: deleting from the ref list and trusting that the id list lines up."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(extra=Facts(calc_refs=[VISIBLE_CALC, HIDDEN_CALC], calc_ids=[10]))
    with pytest.raises(ref_guard.RefGuardUnplaceable):
        run(payload)


def test_a_nullable_ref_becomes_none_and_so_does_its_sibling_id(monkeypatch):
    """Kills: nulling the ref but leaving ``*_id`` (the integer pk of the hidden record)."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(extra=Facts(scan_ref=HIDDEN_CALC, scan_id=42))
    report = run(payload)
    assert payload.extra.scan_ref is None and payload.extra.scan_id is None
    assert [s.action for s in report.sites] == ["null_field"]


def test_a_value_inside_an_untyped_dict_or_list_is_blanked(monkeypatch):
    """Kills: raising for refs inside ``dict[str, Any]`` blobs (stored protocols carry refs verbatim)."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(
        extra=Facts(blob={"supporting": {"calc": HIDDEN_CALC, "other": VISIBLE_CALC}}, open_list=[HIDDEN_CALC, 3])
    )
    run(payload)
    assert payload.extra.blob == {"supporting": {"calc": None, "other": VISIBLE_CALC}}
    assert payload.extra.open_list == [None, 3]


def test_a_ref_list_inside_an_untyped_dict_is_blanked_with_its_aligned_ids(monkeypatch):
    """Kills: raising for a list owned by a dict (a stored protocol's ``supporting_calculations``), or leaving the
    aligned ``*_ids`` entry (the integer pk of the hidden record)."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(
        extra=Facts(
            blob={"protocol": {"calculation_refs": [VISIBLE_CALC, HIDDEN_CALC], "calculation_ids": [10, 11], "n": 2}}
        )
    )
    run(payload)
    assert payload.extra.blob["protocol"]["calculation_refs"] == [VISIBLE_CALC, None]
    assert payload.extra.blob["protocol"]["calculation_ids"] == [10, None]
    assert payload.extra.blob["protocol"]["n"] == 2


def test_a_blanked_ref_in_an_untyped_dict_takes_its_same_stem_id_with_it(monkeypatch):
    """Kills: blanking ``calculation_ref`` and leaving ``calculation_id`` beside it (``KineticsProvenance.irc``)."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(extra=Facts(blob={"irc": {"calculation_ref": HIDDEN_CALC, "calculation_id": 42, "kind": "irc"}}))
    run(payload)
    assert payload.extra.blob["irc"] == {"calculation_ref": None, "calculation_id": None, "kind": "irc"}


def test_a_hidden_ref_used_as_a_dict_key_fails_closed(monkeypatch):
    """Kills: letting a hidden ref through because it is a key, not a value (fails open against D4)."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(extra=Facts(blob={HIDDEN_CALC: {"role": "freq"}}))
    with pytest.raises(ref_guard.RefGuardUnplaceable) as caught:
        run(payload)
    assert "dict key" in caught.value.field_path
    assert HIDDEN_CALC in payload.extra.blob  # nothing was changed on the way to failing


def test_a_visible_dict_key_is_left_alone_and_a_hidden_one_inside_a_pointer_goes_with_it(monkeypatch):
    """Kills: refusing every ref-shaped key; refusing a key that a pointer removal already covers."""
    hide(monkeypatch, HIDDEN_CALC)
    visible = Envelope(extra=Facts(blob={VISIBLE_CALC: 1}))
    assert run(visible).sites == []

    class Keyed(BaseModel):
        calculation_ref: str
        by_ref: dict[str, Any] = {}

    class Env(BaseModel):
        rows: list[Keyed] = []

    monkeypatch.setattr(ref_guard, "POINTER_MODELS", frozenset({Keyed}))
    payload = Env(
        rows=[Keyed(calculation_ref=VISIBLE_CALC, by_ref={HIDDEN_CALC: 1}), Keyed(calculation_ref=ref("calc", 8))]
    )
    run(payload)
    assert [r.calculation_ref for r in payload.rows] == [ref("calc", 8)]


def test_the_unplaceable_hint_does_not_claim_widening_alone_helps(monkeypatch):
    """Kills: the old hint "make the field optional", which is not enough when the ref sits in a required block."""
    message = str(ref_guard.RefGuardUnplaceable("M", "a.b", "tse"))
    assert "POINTER_MODELS" in message and "widening an owner field alone is not enough" in message


def test_routes_match_on_whole_path_segments():
    """Kills: plain string-prefix matching (``/meta`` exempting a future ``/metadata``)."""
    assert ref_guard.route_matches("/api/v1/scientific/meta", ["/api/v1/scientific/meta"])
    assert ref_guard.route_matches("/api/v1/scientific/meta/software", ["/api/v1/scientific/meta"])
    assert not ref_guard.route_matches("/api/v1/scientific/metadata", ["/api/v1/scientific/meta"])
    assert not ref_guard.route_matches("/api/v1/scientific/metadata/x", ["/api/v1/scientific/meta"])
    assert not ref_guard.route_matches("/api/v1/scientific/thermo", [])


def test_the_hook_does_not_exempt_by_a_shared_string_prefix(hook_env):
    """Kills: exemptions matching by plain prefix."""
    publish, calls = hook_env
    ctx = publish(CURATED, "/api/v1/scientific/metadata/x")
    ref_guard.withhold_for_current_request(Envelope())
    assert ctx.applied == 1  # not exempt: shares only a string prefix with /meta


def test_a_required_field_with_no_pointer_is_unplaceable_and_says_where(monkeypatch):
    """Kills: writing None into a required field, or silently emitting the ref (fail closed, owner decision D4)."""
    hide(monkeypatch, HIDDEN_TSE)
    payload = RequiredEnvelope(block=RequiredHolder(owner_ref=HIDDEN_TSE))
    with pytest.raises(ref_guard.RefGuardUnplaceable) as caught:
        run(payload)
    assert caught.value.model_name == "RequiredHolder"
    assert caught.value.field_path == "block.owner_ref"
    assert caught.value.ref_prefix == "tse"
    assert payload.block.owner_ref == HIDDEN_TSE  # nothing was changed on the way to failing


def test_the_subject_of_a_response_is_never_deleted_as_a_pointer(monkeypatch):
    """Kills: treating ``record`` / ``records[*]`` as removable. A hidden subject ref means a service forgot its filter."""
    hide(monkeypatch, HIDDEN_CALC)
    monkeypatch.setattr(ref_guard, "POINTER_MODELS", frozenset({RequiredHolder}))

    as_record = RequiredEnvelope(record=RequiredHolder(owner_ref=HIDDEN_CALC))
    with pytest.raises(ref_guard.RefGuardUnplaceable):
        run(as_record)
    assert as_record.record is not None

    as_records = RequiredEnvelope(records=[RequiredHolder(owner_ref=HIDDEN_CALC)])
    with pytest.raises(ref_guard.RefGuardUnplaceable):
        run(as_records)
    assert len(as_records.records) == 1

    # The same class is a pointer anywhere else.
    as_block = RequiredEnvelope(block=RequiredHolder(owner_ref=HIDDEN_CALC))
    run(as_block)
    assert as_block.block is None


def test_the_request_echo_is_left_alone(monkeypatch):
    """Kills: scanning ``request`` (the caller's own filter ref would be blanked out of its own echo)."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(request=Request(filter_ref=HIDDEN_CALC))
    report = run(payload)
    assert payload.request.filter_ref == HIDDEN_CALC
    assert report.refs_seen == 0


def test_visible_refs_ungated_prefixes_and_lookalikes_are_untouched(monkeypatch):
    """Kills: a regex that matches too much (a ``lot_`` ref is a fact), or hidden_refs answering for refs it was not asked about."""
    hide(monkeypatch, HIDDEN_CALC)
    lookalikes = [ref("lot", 1), ref("geom", 2), "calc_" + "A" * 26, "calc_short", "xcalc_" + "0" * 26, VISIBLE_CALC]
    payload = Envelope(extra=Facts(calc_refs=lookalikes, calc_ids=list(range(len(lookalikes)))))
    report = run(payload)
    assert payload.extra.calc_refs == lookalikes
    assert report.sites == []
    assert report.refs_seen == 1  # only the one well-formed gated ref was even looked up


def test_a_dry_run_reports_and_changes_nothing(monkeypatch):
    """Kills: a report mode that mutates; a dry run that does not plan."""
    hide(monkeypatch, HIDDEN_CALC)
    payload = Envelope(extra=Facts(pointers=[Ptr(calculation_ref=HIDDEN_CALC)], scan_ref=HIDDEN_CALC))
    before = payload.model_dump()
    report = run(payload, apply=False)
    assert payload.model_dump() == before
    assert len(report.sites) == 2


def test_nothing_is_looked_up_without_a_review_floor(monkeypatch):
    """Kills: running the lookup (a statement) under the exploratory profile."""
    called = []
    monkeypatch.setattr(ref_guard, "hidden_refs", lambda *a, **k: called.append(1) or set())
    payload = Envelope(extra=Facts(pointers=[Ptr(calculation_ref=HIDDEN_CALC)]))
    report = ref_guard.withhold_in_model(None, payload, profile=EXPLORATORY)
    assert not called and report.sites == [] and len(payload.extra.pointers) == 1


def test_no_lookup_when_the_response_names_no_gated_ref(monkeypatch):
    """Kills: querying with an empty set (a wasted statement on every curated response)."""
    called = []
    monkeypatch.setattr(ref_guard, "hidden_refs", lambda *a, **k: called.append(1) or set())
    run(Envelope(extra=Facts(label="no refs here", scan_ref=ref("lot", 1))))
    assert not called


# ---------------------------------------------------------------------------
# The boundary hook
# ---------------------------------------------------------------------------


@pytest.fixture
def hook_env(monkeypatch):
    """Publish a profile and a guard context the way the profile dependency does, with a recording withhold."""
    calls = []

    def fake_withhold(session, payload, *, profile, apply=True):
        calls.append((session, payload))
        return ref_guard.WithholdReport()

    monkeypatch.setattr(ref_guard, "withhold_in_model", fake_withhold)
    tokens = []

    def publish(profile, route_path="/api/v1/scientific/statmech/{statmech_ref_or_id}"):
        ctx = ref_guard.RefGuardContext(session="SESSION", route_path=route_path)
        tokens.append((set_current_read_profile(profile), ref_guard.set_ref_guard_context(ctx)))
        return ctx

    yield publish, calls
    for profile_token, ctx_token in reversed(tokens):
        ref_guard.reset_ref_guard_context(ctx_token)
        reset_current_read_profile(profile_token)


def test_the_hook_does_nothing_outside_a_scientific_request(hook_env):
    """Kills: assuming a context exists (the hook is also reached from tests and workers)."""
    _, calls = hook_env
    ref_guard.withhold_for_current_request(Envelope())
    assert calls == []


def test_the_hook_has_no_rollout_allowlist_and_the_exemptions_are_the_registry():
    """Kills: bringing an allowlist back (a route outside it would be unguarded); an exemption without a reason; an
    unreviewed new exemption (the registry is the one place that names them, each with its reason)."""
    assert not hasattr(ref_guard, "_ENABLED_ROUTE_PREFIXES")
    base = "/api/v1/scientific/"
    assert dict.fromkeys(ref_guard.EXEMPT_ROUTE_PREFIXES) == dict.fromkeys(
        {
            f"{base}releases",
            f"{base}meta",
            f"{base}artifacts/{{sha256}}/download",
        }
    )
    assert all(reason.strip() for reason in ref_guard.EXEMPT_ROUTE_PREFIXES.values())
    # Nothing that carries records is exempt.
    for route in ("species/search", "thermo/search", "export/ndjson", "statmech/select", "structure/x"):
        assert not ref_guard.route_matches(f"{base}{route}", ref_guard.EXEMPT_ROUTE_PREFIXES), route


def test_the_hook_withholds_on_a_curated_route(hook_env):
    """Kills: a hook that never calls the guard."""
    publish, calls = hook_env
    ctx = publish(CURATED)
    payload = Envelope()
    ref_guard.withhold_for_current_request(payload)
    assert (ctx.reached, ctx.applied) == (1, 1)
    assert calls == [("SESSION", payload)]  # the request's own session is what the guard is given


@pytest.mark.parametrize(
    ("profile", "route_path"),
    [
        (EXPLORATORY, "/api/v1/scientific/statmech/{statmech_ref_or_id}"),
        (
            ResolvedReadProfile(
                profile=ReadProfile.curated, recommendation=ProfileRecommendation.tckdb_curated_release
            ),
            "/api/v1/scientific/statmech/{statmech_ref_or_id}",
        ),
        (CURATED, "/api/v1/scientific/releases/{release_handle}/selections"),
        (CURATED, "/api/v1/scientific/meta/software"),
    ],
    ids=["exploratory", "release-backed", "exempt-releases", "exempt-meta"],
)
def test_the_hook_stands_down(hook_env, profile, route_path):
    """Kills: running under exploratory; running on release-backed reads; ignoring an exemption."""
    publish, calls = hook_env
    ctx = publish(profile, route_path)
    ref_guard.withhold_for_current_request(Envelope())
    assert (ctx.applied, calls) == (0, [])


def test_an_unplaceable_ref_in_the_hook_propagates(hook_env, monkeypatch):
    """Kills: swallowing RefGuardUnplaceable in the hook (it must fail the request, owner decision D4)."""
    publish, _ = hook_env

    def boom(session, payload, *, profile, apply=True):
        raise ref_guard.RefGuardUnplaceable("M", "a.b", "calc")

    monkeypatch.setattr(ref_guard, "withhold_in_model", boom)
    ctx = publish(CURATED)
    with pytest.raises(ref_guard.RefGuardUnplaceable):
        ref_guard.withhold_for_current_request(Envelope())
    assert ctx.applied == 0


def test_apply_internal_ids_visibility_calls_the_hook(monkeypatch):
    """Kills: unwiring the hook from the one function every enveloped scientific route returns through."""
    seen = []
    monkeypatch.setattr(internal_ids, "withhold_for_current_request", lambda payload: seen.append(payload))
    payload = Envelope()
    internal_ids.apply_internal_ids_visibility(payload)
    assert seen == [payload]


# ---------------------------------------------------------------------------
# The seeded registry, with the real response blocks
# ---------------------------------------------------------------------------


def test_the_seeded_pointer_models_are_the_blocks_the_per_surface_tests_pin():
    """Kills: dropping a pointer class from the registry (m7). The per-surface hides of #764/#765/#767 are the
    behaviour the guard replaces in the last PR, so the classes they hide whole must be registered."""
    assert {m.__name__ for m in REAL_POINTER_MODELS} == {
        "StatmechSourceCalculationSummary",
        "StatmechCurvatureWitness",
        "StatmechConformerContextItem",
        "TransitionStateStatmechSummary",
        "TransitionStateStatmechSourceCalculation",
        "CalculationEvidenceSummary",
        # PR-2a
        "CalculationCompositeInputSummary",
        "CalculationConformerSummary",
        "CalculationDependencySummary",
        "ConformerCalculationSummary",
        "ConformerGeometryLink",
        "GeometryIdentityOwnerRef",
        "GeometryTransitionStateIdentity",
        "StoredThermoProtocolCalculationRef",
        "SupportingCalculationRef",
        "ConformerContextBlock",
        "ConformerGroupCoreBlock",
        "ConformerObservationGroupSummary",
        "ConformerObservationSiblingSummary",
        "GeometryProvenanceCalcLink",
        "StatmechTransitionStateContext",
        "SupersessionNotice",
        "TransitionStateEntryOwnerSummary",
        "TransportSourceCalculationSummary",
        # PR-2b
        "TransitionStateCoreBlock",
        "TransitionStateCalculationSummary",
        "TransitionStateEntryStatmech",
        "TransitionStateEntryValidationEvidence",
        "ScientificTransitionStateEntryRecord",
        "TransitionStateSaddlePointEvidence",
        "TransitionStateValidationEvidenceSummary",
        "TransitionStateComparedEnergySummary",
        "ReactionFullCalculationEvidenceSummary",
        "ReactionFullIRCItem",
        "ReactionFullScanItem",
        "ReactionFullPathSearchItem",
        "ReactionFullCalculationArtifacts",
        "ReactionFullNetworkMembership",
        "ReactionAtomMapBadge",
        "ReactionAtomMapDetail",
        "TransitionStateCalculationSlot",
        "TransitionStateDependency",
        "KineticsDeterminationBlock",
        "KineticsInterpretationAssignmentBlock",
        "KineticsTunnelingApplicationBlock",
        "PathSearchSummary",
        "ValidationSummary",
        "SCFStabilitySummary",
        "StoredKineticsProtocolCalculationRef",
        # #723
        "TransitionStateScopedAssessmentUnit",
        # PR-2c
        "NetworkContextSummary",
        "NetworkKineticsNetworkContext",
        "NetworkSolveSummary",
        "NetworkKineticsSummary",
        "NetworkSourceCalculationSummary",
        "NetworkChannelMicroreactionSummary",
        "NetworkSolveStateEnergySourceSummary",
        "NetworkKineticsBatchFitEvaluation",
        "FrequencyScaleFactorUsageSummary",
    }


def test_real_pointer_blocks_are_deleted_from_their_lists(monkeypatch):
    """Kills: a registry entry that the planner does not honour for the real class (m7 at the behaviour level)."""
    from app.db.models.common import CalculationType, StatmechCalculationRole
    from app.schemas.reads.scientific_statmech import StatmechCurvatureWitness
    from app.schemas.reads.scientific_transition_state import TransitionStateStatmechSourceCalculation

    monkeypatch.setattr(ref_guard, "POINTER_MODELS", REAL_POINTER_MODELS)

    class Env(BaseModel):
        witnesses: list[StatmechCurvatureWitness] = []
        sources: list[TransitionStateStatmechSourceCalculation] = []

    def witness(calc_ref):
        return StatmechCurvatureWitness(
            calculation_ref=calc_ref,
            role=StatmechCalculationRole.freq,
            calculation_type=CalculationType.freq,
            has_frequency_result=True,
            has_hessian=False,
        )

    hide(monkeypatch, HIDDEN_CALC)
    payload = Env(
        witnesses=[witness(HIDDEN_CALC), witness(VISIBLE_CALC)],
        sources=[
            TransitionStateStatmechSourceCalculation(role="freq", calculation_ref=HIDDEN_CALC),
            TransitionStateStatmechSourceCalculation(role="opt", calculation_ref=VISIBLE_CALC),
        ],
    )
    run(payload)
    assert [w.calculation_ref for w in payload.witnesses] == [VISIBLE_CALC]
    assert [s.calculation_ref for s in payload.sources] == [VISIBLE_CALC]
