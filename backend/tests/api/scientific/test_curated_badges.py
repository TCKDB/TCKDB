"""Linked evidence is shown WITH its review badge (ADR 0023, step 2): ``review_status_by_ref``.

Most nested pointers are bare refs with no badge field of their own, so every scientific response envelope carries a map
from each gated ref it names to that record's review status. This module pins it on every route of the sweep:

* **Coverage.** Under curated, every gated ref a response names as a whole value (the request echo and the map itself
  excepted) has an entry. That includes the unlisted records the evidence rule now lets through.
* **Nothing withheld leaks through it.** With the hidden records rejected or deprecated, no hidden ref is a key of any map.
* **Exploratory is untouched.** The field is null and no review lookup is made for it.
* **No extra statement.** The map is filled from statuses the guard's one lookup already fetched.
"""

from __future__ import annotations

import json
import re

import pytest

from app.api.config import settings
from app.services.scientific_read import ref_guard
from tests.api.scientific._curated_sweep_crawl import enumerate_routes
from tests.api.scientific._curated_sweep_run import install_spy, run_pass
from tests.api.scientific._curated_sweep_world import build_sweep_world

GATED_VALUE = re.compile("^(?:" + "|".join(ref_guard.GATED_TYPES) + ")_[a-z0-9]{26}$")
#: Responses that are not an enveloped read, or whose map is deliberately absent (the export streams carry theirs per line,
#: the selection manifests are documents that are hashed and replayed and carry per-candidate review statuses).
NO_ENVELOPE = ("/export/", "/manifest")


def _enveloped(route_key, body):
    """Whether the body is a scientific envelope that must carry the map. A JSON body without the field on such a route FAILS
    (a missing field is not a skip); the export streams, the hashed manifests and the exempt release reads are not envelopes."""
    if any(p in route_key[1] for p in NO_ENVELOPE) or "/releases" in route_key[1]:
        return False
    if not isinstance(body, dict):
        return False
    assert "review_status_by_ref" in body, f"{route_key}: a scientific envelope without review_status_by_ref"
    return True


def _named(node, out, top=True):
    if isinstance(node, dict):
        for key, value in node.items():
            if top and key in ("request", "review_status_by_ref"):
                continue
            _named(value, out, top=False)
    elif isinstance(node, list):
        for item in node:
            _named(item, out, top=False)
    elif isinstance(node, str) and GATED_VALUE.match(node):
        out.add(node)


def _parse(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


@pytest.fixture
def swept(client, db_session, login_as, monkeypatch):
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    specs = enumerate_routes(client.app)
    spy = install_spy(monkeypatch)
    return world, specs, spy


def test_every_ref_a_curated_response_names_has_a_badge_and_exploratory_has_none(client, db_session, swept, monkeypatch):
    """Kills: a map that misses the nested unlisted records, one filled under exploratory, and one that costs a statement."""
    world, specs, spy = swept
    exploratory = run_pass(client, db_session, specs, world, spy, profile="exploratory")
    curated = run_pass(client, db_session, specs, world, spy, profile="curated")
    monkeypatch.setattr(ref_guard, "attach_review_statuses", lambda payload: None)
    without = run_pass(client, db_session, specs, world, spy, profile="curated")
    monkeypatch.undo()

    covered = 0
    problems = []
    for (route, label), r in curated.items():
        body = _parse(r.text)
        if not _enveloped(route, body):
            continue
        badges = body["review_status_by_ref"]
        if badges is None:
            if route[1].startswith("/api/v1/scientific/releases") or any(p in route[1] for p in ("/releases",)):
                continue  # release reads are exempt from the guard
            problems.append(f"{route} {label}: curated response has no review_status_by_ref")
            continue
        named: set[str] = set()
        _named(body, named)
        for ref in sorted(named - set(badges)):
            problems.append(f"{route} {label}: {ref} is named and has no badge")
        for ref, status in badges.items():
            assert GATED_VALUE.match(ref) and status in {"not_reviewed", "under_review", "approved", "rejected", "deprecated"}
        covered += len(named)
        assert r.statements == without[(route, label)].statements, f"{route} {label}: the badge map cost a statement"
    assert covered > 100, "the check is vacuous: almost no ref was covered"
    assert not problems, "\n".join(problems[:40])

    # Unlisted records are in the maps with their real status: this is the point of the map.
    statuses = {s for r in curated.values() if (b := _parse(r.text)) and b.get("review_status_by_ref") for s in b["review_status_by_ref"].values()}
    assert {"approved", "not_reviewed", "under_review"} <= statuses

    for (route, label), r in exploratory.items():
        body = _parse(r.text)
        if _enveloped(route, body):
            assert body["review_status_by_ref"] is None, f"{route} {label}: exploratory carries a badge map"


def test_no_withheld_ref_is_a_key_of_any_badge_map(client, db_session, swept, monkeypatch):
    """Kills: filling the map before withholding, or from the lookup rather than from what the response still names."""
    world, specs, spy = swept
    world.withhold_hidden(db_session)
    monkeypatch.setattr(settings, "allow_public_internal_ids", False)
    withheld = run_pass(client, db_session, specs, world, spy, profile="curated")
    hidden = set(world.hidden_refs().values())
    seen = 0
    for (route, label), r in withheld.items():
        body = _parse(r.text)
        if not _enveloped(route, body) or not body["review_status_by_ref"]:
            continue
        seen += 1
        leaked = hidden & set(body["review_status_by_ref"])
        assert not leaked, f"{route} {label}: withheld refs in the badge map: {sorted(leaked)}"
    assert seen > 30


#: Response models that are NOT envelopes carrying the map, each with the reason. Hashed and replayed manifests cannot carry a
#: field their digest would have to cover; the release reads are exempt from the guard; the rest are not scientific reads.
NOT_ENVELOPES = {
    "ThermoSelectionManifest": "hashed, replayed manifest; records each candidate's review status",
    "KineticsSelectionManifest": "hashed, replayed manifest",
    "StatmechSelectionManifest": "hashed, replayed manifest",
    "StructureSelectionManifest": "hashed, replayed manifest",
    "NetworkSelectionManifest": "hashed, replayed manifest",
    "RelativeConformerEnergyManifest": "hashed, replayed manifest",
    "RelativeConformerEnergyCompareManifest": "hashed, replayed manifest",
    "ScientificReleaseManifestResponse": "dataset release artifact (exempt, ADR 0023 Q7)",
    "ScientificReleaseDetailResponse": "dataset release read (exempt)",
    "ScientificReleaseListResponse": "dataset release read (exempt)",
    "ScientificReleaseSelectionsResponse": "dataset release read (exempt)",
}


def test_every_scientific_response_envelope_inherits_the_badge_base(client):
    """Kills: a new response model for a ``/scientific`` route that cannot carry the badge map (so a gated ref it names would
    go unbadged). Every response model is a ``ScientificResponse`` or is named in NOT_ENVELOPES with a reason."""
    import typing

    from pydantic import BaseModel

    from app.schemas.reads.scientific_common import ScientificResponse

    def models(annotation):
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield annotation
        for arg in typing.get_args(annotation):
            yield from models(arg)

    missing = set()
    seen_exempt = set()
    for route in client.app.routes:
        if not getattr(route, "path", "").startswith("/api/v1/scientific"):
            continue
        for model in models(getattr(route, "response_model", None)):
            if issubclass(model, ScientificResponse):
                continue
            if model.__name__ in NOT_ENVELOPES:
                seen_exempt.add(model.__name__)
                continue
            missing.add(model.__name__)
    assert not missing, f"response models that are neither a ScientificResponse nor in NOT_ENVELOPES: {sorted(missing)}"
    assert seen_exempt == set(NOT_ENVELOPES), f"stale NOT_ENVELOPES entries: {sorted(set(NOT_ENVELOPES) - seen_exempt)}"


def test_the_map_helper_keeps_only_refs_still_present():
    """Kills: a map built from every status the lookup fetched (a withheld ref would be in it)."""
    from app.db.models.common import RecordReviewStatus as S

    statuses = {"calc_" + "a" * 26: S.approved, "calc_" + "b" * 26: S.rejected}
    assert ref_guard.badges_for({"calc_" + "a" * 26}, statuses) == {"calc_" + "a" * 26: S.approved}
    assert ref_guard.badges_for({"calc_" + "c" * 26}, statuses) == {}
