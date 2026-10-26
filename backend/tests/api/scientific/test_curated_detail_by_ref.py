"""ADR 0023, step 7: under ``curated``, detail-by-ref answers for the withheld set, not for the approved floor.

A detail read (any route that resolves ONE record by ref or id) serves the record unless its own status is ``rejected`` or
``deprecated``, and says its review status. Lists, searches and selects keep the approved floor. What a served detail embeds
(a ``/full`` participant, a supersession successor) follows the evidence rule: shown with its badge unless withheld.

Every test states its review rows and asserts they exist, and each fails when the gate in ``resolve_path_handle`` /
``_legacy_reads`` is put back to the approved floor.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.services.scientific_read import ref_guard
from tests.api.scientific._curated_sweep_crawl import enumerate_routes
from tests.api.scientific._curated_sweep_run import install_spy, run_pass
from tests.api.scientific._curated_sweep_world import build_sweep_world
from tests.services.scientific_read._factories import set_review

BASE = "/api/v1/scientific"
LEGACY = "/api/v1"
UNLISTED = {RecordReviewStatus.not_reviewed.value, RecordReviewStatus.under_review.value}

#: hidden-record key -> scientific detail route (ref in the path).
DETAIL_ROUTES = {
    "calc_h": "/calculations/{ref}",
    "sm_h": "/statmech/{ref}",
    "trn_h": "/transport/{ref}",
    "cg_h": "/conformer-groups/{ref}",
    "co_h": "/conformer-observations/{ref}",
    "ts_h": "/transition-states/{ref}",
    "tse_h": "/transition-state-entries/{ref}",
    "net_h": "/networks/{ref}",
    "nsolve_h": "/network-solves/{ref}",
    "nkin_h": "/network-kinetics/{ref}",
}

#: hidden-record key -> legacy detail route (internal id in the path): the thermo and kinetics detail reads exist there only.
LEGACY_ROUTES = {
    "thm_h": "/thermo/{id}",
    "kin_h": "/kinetics/{id}",
    "trn_h": "/transport/{id}",
    "sm_h": "/statmech/{id}",
    "calc_h": "/calculations/{id}",
    "cg_h": "/conformer-groups/{id}",
    "tse_h": "/transition-states/entries/{id}",
    "ts_h": "/transition-states/{id}",
    "nsolve_h": "/networks/{network_id}/solves/{id}",
}

@pytest.fixture
def world(db_session, client, login_as):
    w = build_sweep_world(db_session)
    login_as(w.extra["curator"].id)
    return w


def _get(client, path, profile, **params):
    return client.get(f"{BASE}{path}", params={**params, "profile": profile})


def _reviews_of(db_session, world, key):
    """The review rows the world holds for a hidden record (none = ``not_reviewed``)."""
    gated = ref_guard.GATED_TYPES[world.hidden[key].public_ref.split("_", 1)[0]]
    record_type = gated.parent_record_type or gated.record_type
    record_id = world.hidden[key].id if gated.parent_fk is None else getattr(world.hidden[key], gated.parent_fk)
    return db_session.scalars(
        select(RecordReview).where(RecordReview.record_type == record_type, RecordReview.record_id == record_id)
    ).all()


# ---------------------------------------------------------------------------
# (a) a not-reviewed or under-review record is served, badged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(DETAIL_ROUTES))
def test_a_detail_serves_an_unlisted_record_badged(client, db_session, world, key):
    ref = world.hidden[key].public_ref
    statuses = {r.status.value for r in _reviews_of(db_session, world, key)} or {RecordReviewStatus.not_reviewed.value}
    assert statuses <= UNLISTED, "the world must hold this record unlisted (below the floor, not withheld)"

    exploratory = _get(client, DETAIL_ROUTES[key].format(ref=ref), "exploratory")
    assert exploratory.status_code == 200, exploratory.text  # control: the record exists and is addressable

    curated = _get(client, DETAIL_ROUTES[key].format(ref=ref), "curated")
    assert curated.status_code == 200, curated.text
    badges = curated.json()["review_status_by_ref"]
    assert badges[ref] in UNLISTED
    assert badges[ref] in statuses


@pytest.mark.parametrize("key", sorted(LEGACY_ROUTES))
def test_a_legacy_detail_serves_an_unlisted_record_and_says_its_status(client, db_session, world, key):
    row = world.hidden[key]
    path = LEGACY_ROUTES[key].format(id=row.id, network_id=getattr(row, "network_id", 0))
    statuses = {r.status.value for r in _reviews_of(db_session, world, key)} or {RecordReviewStatus.not_reviewed.value}
    assert statuses <= UNLISTED

    assert client.get(f"{LEGACY}{path}", params={"profile": "exploratory"}).status_code == 200  # control
    curated = client.get(f"{LEGACY}{path}", params={"profile": "curated"})
    assert curated.status_code == 200, curated.text
    assert curated.headers["X-TCKDB-Review-Status"] in statuses
    exploratory = client.get(f"{LEGACY}{path}", params={"profile": "exploratory"})
    assert "X-TCKDB-Review-Status" not in exploratory.headers


def test_a_legacy_species_entry_detail_is_served(client, world):
    """A scoping parent never gated; the control that it answers 200 under curated both before and after step 7."""
    entry = world.extra["species_entry"]
    assert client.get(f"{LEGACY}/species-entries/{entry.id}", params={"profile": "curated"}).status_code == 200


# ---------------------------------------------------------------------------
# (b) a withheld record answers 404 exactly as before
# ---------------------------------------------------------------------------


def test_a_detail_of_a_rejected_or_deprecated_record_is_404(client, db_session, world):
    statuses = world.withhold_hidden(db_session)
    assert {RecordReviewStatus.rejected, RecordReviewStatus.deprecated} <= set(statuses.values())
    checked = 0
    for key, template in DETAIL_ROUTES.items():
        ref = world.hidden[key].public_ref
        exploratory = _get(client, template.format(ref=ref), "exploratory")
        curated = _get(client, template.format(ref=ref), "curated")
        assert exploratory.status_code == 200, (key, exploratory.text)  # exploratory withholds nothing as evidence
        assert curated.status_code == 404, (key, curated.status_code)
        checked += 1
    for key, template in LEGACY_ROUTES.items():
        row = world.hidden[key]
        path = template.format(id=row.id, network_id=getattr(row, "network_id", 0))
        curated = client.get(f"{LEGACY}{path}", params={"profile": "curated"})
        assert curated.status_code == 404, (key, curated.status_code)
        assert "X-TCKDB-Review-Status" not in curated.headers
        checked += 1
    assert checked == len(DETAIL_ROUTES) + len(LEGACY_ROUTES)


def test_both_withheld_statuses_are_404(client, db_session, world):
    """One record per status: rejected and deprecated both answer 404 (a single status could pass by accident)."""
    ref = world.hidden["calc_h"].public_ref
    calc_id = world.hidden["calc_h"].id
    for status in (RecordReviewStatus.rejected, RecordReviewStatus.deprecated):
        review = db_session.scalars(
            select(RecordReview).where(
                RecordReview.record_type == SubmissionRecordType.calculation, RecordReview.record_id == calc_id
            )
        ).first()
        if review is None:
            set_review(
                db_session,
                record_type=SubmissionRecordType.calculation,
                record_id=calc_id,
                status=status,
                reviewed_by=world.extra["curator"].id,
            )
        else:
            review.status = status
            review.reviewed_by = review.reviewed_by or world.extra["curator"].id
            review.reviewed_at = review.reviewed_at or datetime.now(timezone.utc)
        db_session.flush()
        assert _get(client, f"/calculations/{ref}", "curated").status_code == 404, status
        assert _get(client, f"/calculations/{ref}", "exploratory").status_code == 200, status


# ---------------------------------------------------------------------------
# (c) lists, searches and browses keep the approved floor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "path", "params"),
    [
        ("sm_hsucc", "/statmech/search", lambda w: {"species_entry_ref": w.extra["species_entry"].public_ref}),
        ("nsolve_h3", "/network-solves/search", lambda w: {"has_chebyshev": "true"}),
        ("tse_hw", "/transition-states/browse", lambda w: {}),
        (
            "kin_h_new",
            "/kinetics/search",
            lambda w: {"reaction_entry_ref": w.extra["reaction_entry"].public_ref},
        ),
        ("nkin_h3", "/network-kinetics/search", lambda w: {"has_chebyshev": "true"}),
    ],
)
def test_a_list_still_excludes_the_unlisted_record(client, world, key, path, params):
    ref = world.hidden[key].public_ref
    params = params(world)
    exploratory = _get(client, path, "exploratory", limit=100, **params)
    assert exploratory.status_code == 200, exploratory.text
    assert ref in exploratory.text, "control: exploratory lists the record, so the curated absence means something"
    curated = _get(client, path, "curated", limit=100, **params)
    assert curated.status_code == 200, curated.text
    assert curated.json()["records"], "a curated crawl that returned nothing would pass vacuously"
    # The record is never a ROW of the list (it may be named nested, badged, as evidence).
    assert ref not in {_identity_ref(row, key) for row in curated.json()["records"]}


def _identity_ref(row, key):
    """The row's own identity ref (``statmech_ref`` for a ``sm`` record, ...), searched through the row."""
    own = {
        "sm": "statmech_ref",
        "nsolve": "network_solve_ref",
        "tse": "transition_state_entry_ref",
        "kin": "kinetics_ref",
        "nkin": "network_kinetics_ref",
    }[key.split("_", 1)[0]]
    stack = [row]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for name, value in node.items():
                if name == own:
                    return value
                stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return None


# ---------------------------------------------------------------------------
# (d) /full under curated: unlisted participants badged, withheld ones gone
# ---------------------------------------------------------------------------

FULL_KEYS = ("ts_h", "tse_h", "kin_h", "cg_h_rxn", "calc_h_ts")


def test_reaction_full_shows_unlisted_participants_badged(client, world):
    rxe = world.extra["reaction_entry"].public_ref
    refs = {k: world.hidden[k].public_ref for k in FULL_KEYS}
    exploratory = _get(client, f"/reaction-entries/{rxe}/full", "exploratory", include="all")
    assert exploratory.status_code == 200, exploratory.text
    for key, ref in refs.items():
        assert ref in exploratory.text, f"control: {key} is named by the exploratory /full"
    curated = _get(client, f"/reaction-entries/{rxe}/full", "curated", include="all")
    assert curated.status_code == 200, curated.text
    badges = curated.json()["review_status_by_ref"]
    for key, ref in refs.items():
        assert ref in curated.text, f"{key} is a participant of a served detail and must be shown"
        assert badges[ref] in UNLISTED, key


def test_reaction_full_withholds_rejected_and_deprecated_participants(client, db_session, world):
    rxe = world.extra["reaction_entry"].public_ref
    refs = {k: world.hidden[k].public_ref for k in FULL_KEYS}
    statuses = world.withhold_hidden(db_session)
    assert all(statuses[k] in (RecordReviewStatus.rejected, RecordReviewStatus.deprecated) for k in FULL_KEYS)
    curated = _get(client, f"/reaction-entries/{rxe}/full", "curated", include="all")
    assert curated.status_code == 200, curated.text
    for key, ref in refs.items():
        assert ref not in curated.text, f"{key} is withheld and must not be named"
    exploratory = _get(
        client, f"/reaction-entries/{rxe}/full", "exploratory", include="all", include_rejected="true", include_deprecated="true"
    )
    assert all(ref in exploratory.text for ref in refs.values()), "control: exploratory still shows them"


# ---------------------------------------------------------------------------
# (e) a ref a curated response names always resolves under curated
# ---------------------------------------------------------------------------

#: gated prefix -> the scientific detail route that resolves it. ``thm``, ``kin`` and ``mpo`` have no scientific detail by ref
#: (thermo and kinetics are read through their entry), so they are checked for being unnamed-or-served through the legacy ids
#: elsewhere, and are listed here so a new prefix is a conscious addition.
DETAIL_OF_PREFIX = {
    "calc": "/calculations/{ref}",
    "sm": "/statmech/{ref}",
    "trn": "/transport/{ref}",
    "cg": "/conformer-groups/{ref}",
    "co": "/conformer-observations/{ref}",
    "ts": "/transition-states/{ref}",
    "tse": "/transition-state-entries/{ref}",
    "net": "/networks/{ref}",
    "nsolve": "/network-solves/{ref}",
    "nkin": "/network-kinetics/{ref}",
}
NO_SCIENTIFIC_DETAIL = {"thm", "kin", "mpo"}

def _gated_refs_in(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for match in ref_guard._REF_IN_TEXT.finditer(text):
        out.setdefault(match.group(0).split("_", 1)[0], set()).add(match.group(0))
    return out


@pytest.mark.parametrize("withheld", [False, True], ids=["unlisted", "withheld"])
def test_every_ref_a_curated_response_names_resolves_under_curated(client, db_session, login_as, monkeypatch, withheld):
    """The ref guard's job, restated for step 7: a curated response never names a record whose detail route answers 404.

    The 404 set is exactly the withheld set, so after the guard has removed the withheld refs, every ref left must resolve.
    Crawls the whole sweep world under curated and opens every gated ref that any response names.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    if withheld:
        world.withhold_hidden(db_session)
    specs = enumerate_routes(client.app)
    spy = install_spy(monkeypatch)
    curated = run_pass(client, db_session, specs, world, spy, profile="curated")
    assert curated, "the crawl returned nothing"

    named: dict[str, set[str]] = {}
    for result in curated.values():
        for prefix, refs in _gated_refs_in(result.text).items():
            named.setdefault(prefix, set()).update(refs)
    assert sum(len(v) for v in named.values()) > 50, "the crawl named too few refs for this to mean anything"
    assert set(ref_guard.GATED_TYPES) - set(named) <= NO_SCIENTIFIC_DETAIL | set(), "a gated type is never named"
    unknown = set(named) - set(DETAIL_OF_PREFIX) - NO_SCIENTIFIC_DETAIL
    assert not unknown, f"a gated prefix with no detail route registered here: {sorted(unknown)}"

    failures = []
    for prefix, refs in sorted(named.items()):
        template = DETAIL_OF_PREFIX.get(prefix)
        if template is None:
            continue
        for ref in sorted(refs):
            status = _get(client, template.format(ref=ref), "curated").status_code
            if status != 200:
                failures.append(f"{ref} named by a curated response answers {status} on {template}")
    assert not failures, "\n".join(failures)
