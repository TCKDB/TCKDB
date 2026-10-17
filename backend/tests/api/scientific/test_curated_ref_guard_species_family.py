"""PR-2a: the species-side route families withhold hidden refs under ``?profile=curated`` (and only there).

What this pins, beyond the all-routes sweep (``test_curated_ref_sweep.py``, whose baseline no longer lists any
species-side entry):

* **Leaks.** Every species-side route, crawled under ``curated`` with and without ``internal_ids``, names no hidden
  record by ref or by integer id, and answers 200. On ``main`` before PR-2a this fails: the same crawl finds 29 refs
  and 14 ids.
* **No substitution.** Curated is a withholding of exploratory on those routes: values equal or ``null``, lists only
  shorter.
* **D1.** A visible conformer observation whose group is hidden stays visible with ``conformer_group`` null. Widening
  the field alone is not enough: the guard also needs the block registered as a pointer, or the request fails closed.
* **D4.** A hidden ref the guard cannot place still fails closed.
* **D5.** Availability flags, ``calculation_count`` and ``*_summary.ids`` on a species entry follow the curated
  floor, and are unchanged under exploratory.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, ProfileRecommendation, ReadProfile, RecordReviewStatus
from app.db.models.common import SubmissionRecordType as R
from app.db.models.species import ConformerGroup
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transport import Transport
from app.services.scientific_read import ref_guard
from app.services.scientific_read.profile import (
    EXPLORATORY,
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)
from app.services.scientific_read.species import _compute_availability, _compute_section_ids
from tests.api.scientific._curated_sweep_analysis import (
    id_leaks,
    parse_body,
    read_baseline,
    ref_leaks,
    withholding_violations,
)
from tests.api.scientific._curated_sweep_crawl import enumerate_routes, variants_for
from tests.api.scientific._curated_sweep_run import install_spy, run_pass
from tests.api.scientific._curated_sweep_world import build_sweep_world
from tests.services.scientific_read._factories import (
    make_calculation,
    make_conformer_group,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transport,
    next_inchi_key,
    set_review,
)

BASE = "/api/v1/scientific"
CURATED = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
APPROVED = RecordReviewStatus.approved
UNDER_REVIEW = RecordReviewStatus.under_review


def _is_selection_document(path: str) -> bool:
    """Hashed, replayable documents (``.../select``, ``.../relative-energies``, each with ``/manifest``).

    They never pass the boundary hook (they stamp the profile and return), carry population counts that follow the
    floor by design, and get their own seam in PR-3."""
    return path.endswith(("/select", "/select/manifest", "/relative-energies", "/relative-energies/manifest"))


def _in_species_side_family(path: str) -> bool:
    return ref_guard.route_matches(path, ref_guard.SPECIES_SIDE_PREFIXES) and not _is_selection_document(path)


def _species_side(specs):
    return [s for s in specs if _in_species_side_family(s.path)]


def _request_text(spec, world, label, *, ids):
    for variant in variants_for(spec, world, with_internal_ids=ids):
        if variant.label == label:
            return variant.request_text
    return ""


def test_species_side_routes_name_no_hidden_record_under_curated(client, db_session, login_as, monkeypatch):
    """Reproduction + fix: the crawl that finds 29 ref leaks and 14 id leaks on ``main`` finds none.

    Also: no species-side route is left unplaceable (a 500 from ``RefGuardUnplaceable``), every request answers 200,
    and curated is only ever a withholding of exploratory (no sibling is substituted into a gap).

    Kills: not enabling a family; a family whose pointer class is missing (the request would fail closed, so this
    crawl would see a 500); a hide that substitutes a visible sibling; a crawl that visits nothing.
    """
    from app.api.config import settings

    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    specs = _species_side(enumerate_routes(client.app))
    assert len(specs) > 25, "the species-side crawl is empty or truncated"
    spy = install_spy(monkeypatch)
    hidden = world.hidden_refs()

    exploratory = run_pass(client, db_session, specs, world, spy, profile="exploratory")
    curated = run_pass(client, db_session, specs, world, spy, profile="curated")
    monkeypatch.setattr(settings, "allow_public_internal_ids", True)
    curated_ids = run_pass(client, db_session, specs, world, spy, profile="curated", with_ids=True)
    monkeypatch.setattr(settings, "allow_public_internal_ids", False)

    problems: list[str] = []
    for name, results in (("curated", curated), ("curated+ids", curated_ids)):
        for (route, label), r in results.items():
            if r.status != 200:
                problems.append(f"[{name}] {route} {label} answered {r.status} (unplaceable={r.unplaceable})")
            if r.unplaceable is not None:
                problems.append(f"[{name}] {route} {label} unplaceable {r.unplaceable}")
    for (route, label), r in curated.items():
        spec = next(s for s in specs if s.key == route)
        for key, path in ref_leaks(r.text, hidden, _request_text(spec, world, label, ids=False)):
            problems.append(f"hidden ref {key} at {route[1]} {path}")
    for (route, _label), r in curated_ids.items():
        for key, path in id_leaks(r.text, world):
            problems.append(f"hidden id {key} at {route[1]} {path}")
    for (route, label), c in curated.items():
        ekind, eobj = parse_body(exploratory[(route, label)].text)
        ckind, cobj = parse_body(c.text)
        if ekind == "json" and ckind == "json":
            problems.extend(f"not a withholding: {route[1]} {p}" for p in withholding_violations(eobj, cobj))
    # The guard really ran and really removed something on these routes.
    assert sum(len(r.applied_sites) for r in curated.values()) > 20, "the enabled guard withheld almost nothing"
    # And the exploratory pass still names the hidden records (the control that makes the curated zero mean something).
    exploratory_text = "\n".join(r.text for r in exploratory.values())
    for key in ("calc_h", "cg_h", "co_h", "tse_h", "thm_h"):
        assert hidden[key] in exploratory_text, f"positive control: {key} never named under exploratory"
    assert not problems, "\n".join(problems)


def test_the_baseline_keeps_no_species_side_entry():
    """The ratchet only shrinks: PR-2a removed every species-side entry and none may come back.

    Kills: re-adding a species-side leak, hidden id or unplaceable to the baseline instead of fixing it.
    """
    baseline = read_baseline()
    assert baseline, "baseline file is missing or empty"
    for section, entries in baseline.items():
        for entry in entries:
            path = entry.split(" ", 2)[1]
            assert not _in_species_side_family(path), f"{section}: {entry}"


# ---------------------------------------------------------------------------
# D1 and D4: the owner block of a conformer observation
# ---------------------------------------------------------------------------


def _get(client, path, profile):
    return client.get(f"{BASE}{path}", params={"profile": profile})


def test_a_visible_observation_of_a_hidden_group_stays_visible_with_the_group_blanked(client, db_session, login_as):
    """D1: no cascading hide. The observation, its species and review are kept; only the group block goes.

    Kills: hiding the child (404 or dropped); leaving the group named; blanking the group when it is visible;
    touching exploratory.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    hidden_group = world.hidden["cg_h"].public_ref
    visible_group = world.visible["cg_v"].public_ref
    under_hidden = world.visible["co_v2"].public_ref
    under_visible = world.visible["co_v"].public_ref

    cur = _get(client, f"/conformer-observations/{under_hidden}", "curated")
    assert cur.status_code == 200, cur.text
    record = cur.json()["record"]
    assert record["conformer_group"] is None
    assert record["conformer_observation"]["conformer_observation_ref"] == under_hidden
    assert record["species"] and record["evidence_summary"] is not None
    assert hidden_group not in cur.text

    exp = _get(client, f"/conformer-observations/{under_hidden}", "exploratory")
    assert exp.json()["record"]["conformer_group"]["conformer_group_ref"] == hidden_group

    ok = _get(client, f"/conformer-observations/{under_visible}", "curated")
    assert ok.json()["record"]["conformer_group"]["conformer_group_ref"] == visible_group


def test_widening_the_owner_field_alone_still_fails_closed(client, db_session, login_as, monkeypatch):
    """D4 and the #770-review correction: without the group block registered as a pointer the guard has nowhere to put
    a hidden group ref, so the request fails instead of leaking.

    Kills: the guard emitting the ref when it cannot place it; treating the nullable field as enough.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    from app.schemas.reads.scientific_conformer import ConformerGroupCoreBlock

    monkeypatch.setattr(ref_guard, "POINTER_MODELS", ref_guard.POINTER_MODELS - {ConformerGroupCoreBlock})
    with pytest.raises(ref_guard.RefGuardUnplaceable) as caught:
        _get(client, f"/conformer-observations/{world.visible['co_v2'].public_ref}", "curated")
    assert caught.value.ref_prefix == "cg"


# ---------------------------------------------------------------------------
# D5: availability flags, counts and summary ids follow the floor
# ---------------------------------------------------------------------------


def _entry_with(db_session, *, approved: bool):
    """A species entry owning one of each record the availability flags look at, all approved or none."""
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("D5A")))
    rows = {
        "thermo": (R.thermo, make_thermo_scalar(db_session, species_entry=entry)),
        "statmech": (R.statmech, make_statmech(db_session, species_entry=entry)),
        "transport": (R.transport, make_transport(db_session, species_entry=entry)),
        "conformers": (R.conformer_group, make_conformer_group(db_session, entry)),
        "calc": (R.calculation, make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)),
    }
    for record_type, row in rows.values():
        if approved:
            set_review(db_session, record_type=record_type, record_id=row.id, status=APPROVED)
        else:
            set_review(db_session, record_type=record_type, record_id=row.id, status=UNDER_REVIEW)
    return entry, {k: row for k, (_, row) in rows.items()}


def _under(profile, fn):
    token = set_current_read_profile(profile)
    try:
        return fn()
    finally:
        reset_current_read_profile(token)


def test_availability_counts_and_summary_ids_follow_the_curated_floor(client, db_session):
    """D5: a species entry whose only records are hidden reports none under curated, and everything under exploratory.

    The exploratory expectation is computed here with plain queries, independent of the function under test, so it is
    the behaviour before PR-2a and not a restatement of the new code.

    Kills: counting hidden records under curated; filtering under exploratory (the default profile must stay
    byte-identical); a floor applied to only some of the five counts; summary ids that still list hidden records; a
    missing review row read as visible.
    """
    hidden_entry, hidden_rows = _entry_with(db_session, approved=False)
    shown_entry, shown_rows = _entry_with(db_session, approved=True)
    # A mixed entry: one approved and one hidden thermo; the flag stays true, the id list holds only the approved one.
    mixed = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("D5M")))
    t_ok = make_thermo_scalar(db_session, species_entry=mixed)
    t_hid = make_thermo_scalar(db_session, species_entry=mixed, h298_kj_mol=-99.0)
    set_review(db_session, record_type=R.thermo, record_id=t_ok.id, status=APPROVED)
    # An entry with records and no review row at all reads as not_reviewed, hence hidden.
    bare = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("D5B")))
    make_thermo_scalar(db_session, species_entry=bare)
    make_calculation(db_session, type=CalculationType.sp, species_entry_id=bare.id)

    entries = [hidden_entry, shown_entry, mixed, bare]
    includes = {"thermo", "statmech", "transport", "conformers"}

    def compute():
        return _compute_availability(db_session, entries), _compute_section_ids(db_session, entries, includes)

    # Exploratory: what the plain unfiltered queries say.
    def plain_count(model, entry):
        return db_session.scalar(select(func.count(model.id)).where(model.species_entry_id == entry.id))

    availability, sections = _under(EXPLORATORY, compute)
    for entry in entries:
        a = availability[entry.id]
        assert a.has_thermo == bool(plain_count(Thermo, entry))
        assert a.has_statmech == bool(plain_count(Statmech, entry))
        assert a.has_transport == bool(plain_count(Transport, entry))
        assert a.has_conformers == bool(plain_count(ConformerGroup, entry))
        assert a.calculation_count == plain_count(Calculation, entry)
    assert hidden_entry.id in availability and availability[hidden_entry.id].calculation_count == 1
    assert sections[mixed.id]["thermo"].ids == sorted([t_ok.id, t_hid.id])

    availability, sections = _under(CURATED, compute)
    gone = availability[hidden_entry.id]
    assert (gone.has_thermo, gone.has_statmech, gone.has_transport, gone.has_conformers, gone.calculation_count) == (
        False,
        False,
        False,
        False,
        0,
    )
    assert all(sections[hidden_entry.id][k].ids == [] for k in includes)
    kept = availability[shown_entry.id]
    assert (kept.has_thermo, kept.has_statmech, kept.has_transport, kept.has_conformers, kept.calculation_count) == (
        True,
        True,
        True,
        True,
        1,
    )
    assert sections[shown_entry.id]["thermo"].ids == [shown_rows["thermo"].id]
    assert sections[shown_entry.id]["conformers"].ids == [shown_rows["conformers"].id]
    assert availability[mixed.id].has_thermo is True
    assert sections[mixed.id]["thermo"].ids == [t_ok.id]
    barely = availability[bare.id]
    assert (barely.has_thermo, barely.calculation_count) == (False, 0)
    assert hidden_rows  # the hidden entry really owns records


def test_species_search_flags_follow_the_floor_over_http(client, db_session, login_as):
    """The same, through ``GET /species/search``: the wire shows the floor under curated and not under exploratory.

    Kills: applying the floor in the service helper but not on the route that serialises it.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    entry, _ = _entry_with(db_session, approved=False)
    # The entry itself must pass the curated floor to be listed; only its records are hidden.
    set_review(db_session, record_type=R.species_entry, record_id=entry.id, status=APPROVED)
    set_review(db_session, record_type=R.species, record_id=entry.species_id, status=APPROVED)
    params = {"species_entry_ref": entry.public_ref, "include": "thermo,statmech,transport,conformers", "limit": 5}

    def availability(profile):
        r = client.get(f"{BASE}/species/search", params={**params, "profile": profile})
        assert r.status_code == 200, r.text
        (record,) = r.json()["records"]
        (entry_record,) = record["entries"]
        return entry_record

    exploratory = availability("exploratory")
    assert exploratory["availability"]["has_thermo"] is True
    assert exploratory["availability"]["calculation_count"] == 1
    assert len(exploratory["thermo_summary"]["ids"]) == 1
    curated = availability("curated")
    assert curated["availability"] == {
        "has_thermo": False,
        "has_statmech": False,
        "has_transport": False,
        "has_conformers": False,
        "calculation_count": 0,
    }
    assert curated["thermo_summary"]["ids"] == [] and curated["conformers_summary"]["ids"] == []


# ---------------------------------------------------------------------------
# A withheld ref takes its integer id and its endpoint with it
# ---------------------------------------------------------------------------


def _calc_detail(client, ref, profile):
    include = "energy_corrections,scf_stability,internal_ids"
    r = client.get(f"{BASE}/calculations/{ref}", params={"include": include, "profile": profile})
    assert r.status_code == 200, r.text
    return r.json()["record"]


def test_a_withheld_ref_takes_its_id_and_its_endpoint_with_it(client, db_session, login_as, monkeypatch):
    """The ref, the integer id beside it and the URL that names the same record are all null together.

    Two shapes, both with a visible calculation naming a hidden record: an SCF-stability row whose origin
    calculation is hidden (`source_calculation_ref` + `_id`), and an applied correction that targets a hidden TS entry
    (`target_record_ref` + `_id` + `target_endpoint`). The exploratory read is the control: all of them are present.

    Kills: blanking the ref but not the `_id` beside it; blanking the ref but not the endpoint URL; blanking
    everything under exploratory; a world that never builds the shape.
    """
    from app.api.config import settings

    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    monkeypatch.setattr(settings, "allow_public_internal_ids", True)
    ref = world.visible["calc_vopt"].public_ref
    hidden_calc, hidden_tse = world.hidden["calc_h"], world.hidden["tse_h"]

    exploratory = _calc_detail(client, ref, "exploratory")
    (scf,) = exploratory["scf_stability"]
    assert (scf["source_calculation_ref"], scf["source_calculation_id"]) == (hidden_calc.public_ref, hidden_calc.id)
    (corr,) = [c for c in exploratory["energy_corrections"] if c["target_record_ref"] == hidden_tse.public_ref]
    assert corr["target_record_id"] == hidden_tse.id
    assert corr["target_endpoint"].endswith(f"/{hidden_tse.public_ref}")

    curated = _calc_detail(client, ref, "curated")
    (scf,) = curated["scf_stability"]
    assert (scf["source_calculation_ref"], scf["source_calculation_id"]) == (None, None)
    withheld = [c for c in curated["energy_corrections"] if c["target_record_ref"] is None]
    assert len(withheld) == 1
    assert (withheld[0]["target_record_id"], withheld[0]["target_endpoint"]) == (None, None)
    assert hidden_tse.public_ref not in json.dumps(curated)


def _url_forms(hidden: str) -> dict[str, str]:
    """Every shape a URL naming ``hidden`` can take. Each must be withheld."""
    encoded = "%" + format(ord(hidden[0]), "02x") + hidden[1:]  # first character percent-encoded
    base = f"{BASE}/transition-state-entries"
    return {
        "last segment": f"{base}/{hidden}",
        "trailing slash": f"{base}/{hidden}/",
        "query value": f"{base}/search?transition_state_entry_ref={hidden}",
        "query only": f"?target={hidden}",
        "fragment": f"{base}#{hidden}",
        "absolute": f"https://tckdb.example/api/v1/scientific/transition-state-entries/{hidden}",
        "middle segment": f"{BASE}/calculations/{hidden}/scan",
        "short path": f"/c/{hidden}",
        "path params": f"{base};ref={hidden}",
        "encoded slash": f"/api%2Fv1%2Fscientific%2Fcalculations%2F{hidden}",
        "encoded ref": f"{base}/{encoded}",
    }


def _withhold_one(db_session, value):
    from pydantic import BaseModel

    class Row(BaseModel):
        target_endpoint: str | None = None

    row = Row(target_endpoint=value)
    ref_guard.withhold_in_model(db_session, row, profile=CURATED)
    return row.target_endpoint


def test_a_url_naming_a_hidden_record_is_withheld_like_the_ref(client, db_session):
    """Planner level: a field holding a URL that names a hidden ref anywhere is nulled, in every URL form.

    Query, fragment, absolute, middle-segment, short, ``;params`` and percent-encoded forms are all read (none is
    emitted by an enabled family today; PR-2b's ``/full`` builds middle-segment URLs).

    Kills: reading only the last path segment; skipping the query or the fragment; keeping the 40-character shortcut
    for short paths; not decoding ``%2F``/``%63``; not splitting ``;params``.
    """
    world = build_sweep_world(db_session)
    hidden = world.hidden["tse_h"].public_ref
    for form, url in _url_forms(hidden).items():
        assert _withhold_one(db_session, url) is None, f"{form}: {url} was kept"


def test_a_url_or_text_that_names_only_visible_records_is_kept(client, db_session):
    """The URL reader does not over-reach: visible refs, prose and look-alikes are left alone.

    Kills: nulling every URL; reading prose that contains a hidden ref as a pointer (the sweep's raw-body check owns
    that); matching a ref-shaped token with the wrong prefix or length.
    """
    world = build_sweep_world(db_session)
    hidden, visible = world.hidden["tse_h"].public_ref, world.visible["tse_v"].public_ref
    kept = [
        f"{BASE}/transition-state-entries/{visible}",
        f"{BASE}/calculations/{visible}/scan?x={visible}#{visible}",
        f"see {hidden} for details",  # prose: whitespace
        f"{BASE}/lookalike/lot_{hidden.split('_', 1)[1]}",  # an ungated prefix
        f"{BASE}/transition-state-entries/{hidden}x",  # wrong length
        "/api/v1/scientific/meta/software",
    ]
    for value in kept:
        assert _withhold_one(db_session, value) == value, value


def test_ids_are_dropped_with_their_own_row_not_with_the_same_index(client, db_session):
    """``supporting_calculation_ids`` is matched to the dropped row by value.

    In step (``[h, v]`` for rows ``[h, v]``) the hidden id goes and the visible id stays. The same length in another
    order (``[v, h]`` for rows ``[h, v]``) is a list the guard cannot trust, so it fails closed instead of dropping
    the visible id and keeping the hidden one.

    Kills: deleting the aligned index without comparing the id to the row's; comparing lengths only; not deleting the
    id at all.
    """
    from pydantic import BaseModel

    from app.schemas.reads.scientific_species_calculations import CalculationProvenanceBlock, SupportingCalculationRef

    class Env(BaseModel):
        provenance: CalculationProvenanceBlock

    world = build_sweep_world(db_session)
    h, v = world.hidden["calc_h"], world.visible["calc_vf"]

    def block(ids):
        rows = [
            SupportingCalculationRef(calculation_id=h.id, calculation_ref=h.public_ref),
            SupportingCalculationRef(calculation_id=v.id, calculation_ref=v.public_ref),
        ]
        return Env(provenance=CalculationProvenanceBlock(supporting_calculation_ids=ids, supporting_calculations=rows))

    in_step = block([h.id, v.id])
    ref_guard.withhold_in_model(db_session, in_step, profile=CURATED)
    assert in_step.provenance.supporting_calculation_ids == [v.id]
    assert [r.calculation_ref for r in in_step.provenance.supporting_calculations] == [v.public_ref]

    with pytest.raises(ref_guard.RefGuardUnplaceable):
        ref_guard.withhold_in_model(db_session, block([v.id, h.id]), profile=CURATED)


def test_the_review_shapes_each_build_a_hidden_link_and_none_fails_closed(client, db_session, login_as):
    """Each shape that made a curated read fail in review is built by the world and answers 200 with the link gone.

    One request per shape, on the route the review named. A 500 here is a `RefGuardUnplaceable`: the block holding
    the hidden ref is not registered.

    Kills: removing any of the seven pointer registrations (the request fails closed); a world that stops building a
    shape (the exploratory control no longer names the hidden record).
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    hid = world.hidden
    shapes = [
        # (route, params, hidden record that must be named under exploratory and absent under curated)
        ("/species-calculations/search", {"species_entry_ref": world.extra["species_entry"].public_ref}, "calc_h"),
        ("/calculations/" + world.visible["calc_comp"].public_ref, {"include": "all"}, "calc_h"),
        ("/conformer-groups/" + world.visible["cg_v"].public_ref, {"include": "all"}, "calc_hgeo"),
        ("/conformer-observations/" + world.visible["co_v"].public_ref, {"include": "all"}, "calc_hgeo"),
        ("/conformers/search", {"conformer_group_ref": world.visible["cg_v"].public_ref, "include": "all"}, "calc_hgeo"),
        ("/geometries/" + world.extra["geometry_hidden_ts_entry"].public_ref, {}, "tse_h"),
        ("/geometries/" + world.extra["geometry_ambiguous_owners"].public_ref, {}, "tse_h"),
        ("/species-entries/" + world.extra["species_entry"].public_ref + "/thermo", {}, "calc_h"),
        ("/thermo/search", {"species_entry_ref": world.extra["species_entry"].public_ref}, "calc_h"),
    ]
    for route, params, key in shapes:
        ref = hid[key].public_ref
        exploratory = client.get(f"{BASE}{route}", params={**params, "profile": "exploratory"})
        assert exploratory.status_code == 200, (route, exploratory.text)
        assert ref in exploratory.text, f"{route}: the world does not build the shape (exploratory never names {key})"
        curated = client.get(f"{BASE}{route}", params={**params, "profile": "curated"})
        assert curated.status_code == 200, (route, curated.text)
        assert ref not in curated.text, f"{route}: curated still names {key}"
