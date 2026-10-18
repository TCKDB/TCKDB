"""PR-4: the four export streams withhold hidden refs under ``?profile=curated``.

The exports (``/export/ndjson``, ``/export/ml/species.ndjson``, ``/export/ml/reactions.ndjson``,
``/export/chemkin``) stream and so bypass the response boundary the other routes return through. They now reach the
guard through the document seam, one visibility statement per batch of :data:`EXPORT_BATCH_SIZE` records. What this
pins, beyond the all-routes sweep:

* **Positions.** For each export, the hidden record in every position it can be named (a linearity witness, a
  conflict, a hidden calculation's energy, frequencies and Hessian, a conformer group, a transition-state energy that
  would set the barrier, a visible entry of a hidden transition state, hidden thermo and kinetics candidates).
* **Population before backstop.** Where a hidden record supplies a *value* (a linearity, an energy, a barrier), it is
  left out before anything is derived from it; blanking its ref beside its value would still say it exists.
* **Batching.** The bytes do not depend on the batch size; a stream makes one visibility statement per batch.
* **Context.** A stream consumed after the request context is gone judges visibility by the profile it captured.
* **Counts.** Gap and skip counts follow the floor.
* **Exploratory.** Nothing is buffered, asked or changed.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.db.models.common import ProfileRecommendation, ReadProfile, RecordReviewStatus
from app.services.scientific_read import export as export_core
from app.services.scientific_read import export_withhold, ml_dataset, ref_guard
from app.services.scientific_read.profile import (
    EXPLORATORY,
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)
from tests.api.scientific._curated_sweep_inputs import _export_species_refs
from tests.api.scientific._curated_sweep_world import build_sweep_world

BASE = "/api/v1/scientific"
CURATED = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
NOT_REVIEWED = RecordReviewStatus.not_reviewed


@pytest.fixture
def world(db_session, client, login_as):
    w = build_sweep_world(db_session)
    login_as(w.extra["curator"].id)
    return w


def _query(w, **extra):
    return {"all": True, "species_ref": _export_species_refs(w), "min_review_status": "not_reviewed", **extra}


def _lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line]


def _normalise(lines: list[dict]) -> list[dict]:
    out = []
    for line in lines:
        line = dict(line)
        line.pop("generated_at", None)
        out.append(line)
    return out


def _get(client, path: str, profile: str, params: dict) -> str:
    response = client.get(f"{BASE}{path}", params={**params, "profile": profile})
    assert response.status_code == 200, response.text
    return response.text


def _chemkin(client, w, profile: str) -> dict[str, str]:
    body = {
        "seed": {"all_reactions": True, "species_refs": _export_species_refs(w)},
        "min_review_status": "not_reviewed",
    }
    response = client.post(f"{BASE}/export/chemkin", params={"profile": profile}, json=body)
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return {name: archive.read(name).decode() for name in archive.namelist()}


def _by(lines, key, value):
    return next(line for line in lines if line.get(key) == value)


def _hidden(w) -> dict[str, str]:
    return w.hidden_refs()


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


def test_native_ndjson_names_no_hidden_record_and_keeps_the_visible_ones(client, world):
    """Kills: no withhold on /export/ndjson; a hidden linearity witness or conflict member; a hidden thermo candidate.

    Species: ``sx`` (hidden collinear calculation is its only witness), ``sy`` (a hidden and a visible statmech
    disagree), ``sz`` (the only link is a hidden calculation), ``sg`` (only a hidden thermo).
    """
    w = world
    hidden = _hidden(w)
    for collapse in ("first", "all"):
        curated = _get(client, "/export/ndjson", "curated", _query(w, collapse=collapse))
        exploratory = _get(client, "/export/ndjson", "exploratory", _query(w, collapse=collapse))
        for key, ref in hidden.items():
            assert ref not in curated, f"{collapse}: curated names {key}"
        # positive control: the world meets the positions
        for key in ("calc_h_xlin", "sm_h_y", "thm_h_g"):
            assert hidden[key] in exploratory, f"{collapse}: exploratory does not name {key}: the world is vacuous"

    first = _lines(_get(client, "/export/ndjson", "curated", _query(w)))
    ex_first = _lines(_get(client, "/export/ndjson", "exploratory", _query(w)))
    sx, sy, sz = (
        w.extra[k].public_ref for k in ("species_entry_export", "species_entry_conflict", "species_entry_hidden_link")
    )

    # sx: exploratory states linearity from the hidden calculation; curated has no witness at all.
    assert _by(ex_first, "species_entry_ref", sx)["is_linear_source"] == {
        "kind": "calculation_geometry",
        "ref": hidden["calc_h_xlin"],
    }
    assert _by(first, "species_entry_ref", sx)["is_linear"] is None
    assert _by(first, "species_entry_ref", sx)["is_linear_source"] is None
    # sy: exploratory reports a conflict naming both; curated sees only the visible record and uses it.
    conflict = _by(ex_first, "species_entry_ref", sy)["is_linear_conflict"]
    assert hidden["sm_h_y"] in conflict["refs"] and w.visible["sm_v_y"].public_ref in conflict["refs"]
    shown = _by(first, "species_entry_ref", sy)
    assert shown["is_linear_conflict"] is None
    assert shown["is_linear_source"] == {"kind": "statmech", "ref": w.visible["sm_v_y"].public_ref}
    assert shown["is_linear"] is False
    # sz: a visible statmech reached only through a hidden calculation is not a witness.
    assert _by(ex_first, "species_entry_ref", sz)["is_linear"] is True
    assert _by(first, "species_entry_ref", sz)["is_linear"] is None

    # every candidate: the hidden thermo and kinetics are not among them
    sa = w.extra["species_entry"].public_ref
    every = _lines(_get(client, "/export/ndjson", "curated", _query(w, collapse="all")))
    shown_thermos = {t["thermo_ref"] for t in _by(every, "species_entry_ref", sa)["thermos"]}
    visible_thermos = {w.visible[k].public_ref for k in ("thm_v", "thm_v2", "thm_vg")}  # thm_vg: guard PR-3's world
    assert shown_thermos and shown_thermos <= visible_thermos


def test_ml_species_leaves_out_the_hidden_calculations_and_drops_hidden_conformer_refs(client, world):
    """Kills: no withhold on the ML species stream; a hidden calculation's energy, frequencies or Hessian kept; a
    hidden conformer group kept in ``conformer_group_refs``."""
    w = world
    hidden = _hidden(w)
    curated = _get(client, "/export/ml/species.ndjson", "curated", {**_query(w), "include_hessian": True})
    exploratory = _get(client, "/export/ml/species.ndjson", "exploratory", {**_query(w), "include_hessian": True})
    for key, ref in hidden.items():
        assert ref not in curated, f"curated names {key}"
    for key in ("calc_h_xlin", "calc_h_xsp", "cg_h"):
        assert hidden[key] in exploratory, f"exploratory does not name {key}"

    sx = w.extra["species_entry_export"].public_ref
    ex_sx = [r for r in _lines(exploratory) if r.get("species_entry_ref") == sx]
    cu_sx = [r for r in _lines(curated) if r.get("species_entry_ref") == sx]
    # The hidden calculations carry the collinear geometry, the frequencies and the Hessian: with them gone only the
    # visible bent geometry (and its energy) is a record of this species.
    assert len(ex_sx) == 2 and len(cu_sx) == 1
    assert [e["calculation_ref"] for e in cu_sx[0]["energies"]] == [w.visible["calc_xv"].public_ref]
    assert cu_sx[0]["frequencies"] is None and cu_sx[0]["hessian"] is None
    # The conformer group arrives as a list of strings: the hidden one is dropped (the list stays, now empty) and the
    # visible observation beside it stays.
    sa = w.extra["species_entry"].public_ref
    cu_sa = [r for r in _lines(curated) if r.get("species_entry_ref") == sa]
    ex_sa = [r for r in _lines(exploratory) if r.get("species_entry_ref") == sa]
    groups = lambda rows: {g for r in rows for g in r["conformer_group_refs"]}  # noqa: E731
    assert hidden["cg_h"] in groups(ex_sa)
    assert hidden["cg_h"] not in groups(cu_sa) and groups(cu_sa) <= groups(ex_sa)
    assert w.visible["co_v2"].public_ref in {o for r in cu_sa for o in r["conformer_observation_refs"]}


def test_ml_reactions_barrier_comes_from_shown_energies_and_a_hidden_owner_is_null(client, world):
    """Kills: no withhold on the ML reactions stream; a hidden calculation setting the barrier; a hidden TS entry
    picked or named; the owner ref of a visible entry of a hidden transition state kept."""
    w = world
    hidden = _hidden(w)
    curated = _get(client, "/export/ml/reactions.ndjson", "curated", _query(w))
    exploratory = _get(client, "/export/ml/reactions.ndjson", "exploratory", _query(w))
    for key, ref in hidden.items():
        assert ref not in curated, f"curated names {key}"
    for key in ("calc_h_xts", "ts_h_x", "kin_h", "ts_hw"):
        assert hidden[key] in exploratory, f"exploratory does not name {key}"

    rx = w.extra["reaction_entry"].public_ref
    cu, ex = (_by(_lines(t), "reaction_entry_ref", rx) for t in (curated, exploratory))
    # the lowest TS energy is the hidden single point; the barrier moves with it
    assert ex["transition_state"]["energy"]["calculation_ref"] == hidden["calc_h_xts"]
    assert cu["transition_state"]["energy"]["calculation_ref"] == w.visible["calc_ts_vsp"].public_ref
    assert cu["transition_state"]["energy"]["electronic_energy_hartree"] == -89.0
    assert cu["barrier"]["electronic_forward_kj_mol"] != ex["barrier"]["electronic_forward_kj_mol"]
    assert hidden["kin_h"] not in json.dumps(cu["kinetics"])

    # a visible entry of a hidden transition state: the entry stays, its owner is null
    owner = w.extra["reaction_entry_hidden_ts_owner"].public_ref
    d = _by(_lines(curated), "reaction_entry_ref", owner)["transition_state"]
    assert d["transition_state_entry_ref"] == w.visible["tse_xd"].public_ref
    assert d["transition_state_ref"] is None
    assert (
        _by(_lines(exploratory), "reaction_entry_ref", owner)["transition_state"]["transition_state_ref"]
        == (hidden["ts_h_x"])
    )
    # a reaction whose transition state and entry are both hidden has no transition state block
    all_hidden = w.extra["reaction_entry_all_hidden"].public_ref
    assert _by(_lines(curated), "reaction_entry_ref", all_hidden)["transition_state"] is None


def test_chemkin_files_and_manifest_name_no_hidden_record(client, world):
    """Kills: no withhold on /export/chemkin; the CHEMKIN transport comment naming a hidden witness."""
    w = world
    hidden = _hidden(w)
    curated = _chemkin(client, w, "curated")
    exploratory = _chemkin(client, w, "exploratory")
    joined_curated = "\n".join(curated.values())
    joined_exploratory = "\n".join(exploratory.values())
    for key, ref in hidden.items():
        assert ref not in joined_curated, f"curated names {key}"
    for key in ("calc_h_xlin", "sm_h_y"):
        assert hidden[key] in joined_exploratory, f"exploratory does not name {key}"
    assert "tran.dat" in curated and "manifest.json" in curated
    manifest = json.loads(curated["manifest.json"])
    assert manifest["profile"] == "curated"


# ---------------------------------------------------------------------------
# The witness, directly
# ---------------------------------------------------------------------------


def test_the_linearity_witness_leaves_out_hidden_records(db_session, world):
    """Kills: ``_transport_linearity_witness`` without ``hidden_refs`` (a hidden calculation or statmech as a witness)."""
    w = world
    hidden = _hidden(w)
    s = db_session

    def witness(entry_key, transport_key, profile):
        entry, transport = w.extra[entry_key], w.visible[transport_key]
        return export_core._transport_linearity_witness(s, entry.id, transport.id, profile=profile)

    # sx: the hidden collinear calculation is the only witness
    exploratory = witness("species_entry_export", "trn_xv", EXPLORATORY)
    assert exploratory[0] is not None and exploratory[0].source_ref == hidden["calc_h_xlin"]
    assert witness("species_entry_export", "trn_xv", CURATED) == (None, None)
    # sy: the conflict shrinks to the one shown record, which then decides
    witness_y, conflict_y = witness("species_entry_conflict", "trn_yv", EXPLORATORY)
    assert witness_y is None and set(conflict_y.refs) == {hidden["sm_h_y"], w.visible["sm_v_y"].public_ref}
    witness_y, conflict_y = witness("species_entry_conflict", "trn_yv", CURATED)
    assert (
        conflict_y is None and witness_y.source_ref == w.visible["sm_v_y"].public_ref and witness_y.is_linear is False
    )
    # sz: a shown statmech, a hidden calculation linking it
    assert witness("species_entry_hidden_link", "trn_zv", EXPLORATORY)[0] is not None
    assert witness("species_entry_hidden_link", "trn_zv", CURATED) == (None, None)


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def _all_exports(client, w, profile):
    return {
        "ndjson": _normalise(_lines(_get(client, "/export/ndjson", profile, _query(w)))),
        "ndjson_all": _normalise(_lines(_get(client, "/export/ndjson", profile, _query(w, collapse="all")))),
        "ml_species": _normalise(
            _lines(_get(client, "/export/ml/species.ndjson", profile, {**_query(w), "include_hessian": True}))
        ),
        "ml_reactions": _normalise(_lines(_get(client, "/export/ml/reactions.ndjson", profile, _query(w)))),
    }


def test_the_bodies_do_not_depend_on_the_batch_size(client, world, monkeypatch):
    """Kills: a batch boundary that changes what is withheld or the order (batch of 1 vs 200), a flush that is lost."""
    w = world
    wide = _all_exports(client, w, "curated")
    assert all(len(lines) > 2 for lines in wide.values())  # manifest + records + summary: the comparison is not empty
    for size in (1, 2, 3):
        monkeypatch.setattr(export_withhold, "EXPORT_BATCH_SIZE", size)
        assert _all_exports(client, w, "curated") == wide, f"batch size {size} changed the bodies"


def test_one_visibility_statement_per_batch(client, world, monkeypatch):
    """Kills: a withhold per record, a withhold per stream regardless of size, and a flush that is skipped."""
    w = world
    calls: list[int] = []
    real = ref_guard.withhold_in_document

    def counting(session, documents, *, profile, mode):
        calls.append(len(documents))
        return real(session, documents, profile=profile, mode=mode)

    monkeypatch.setattr(ref_guard, "withhold_in_document", counting)

    _get(client, "/export/ml/reactions.ndjson", "curated", _query(w))
    n_records = sum(calls)
    assert n_records > 3 and calls == [n_records], "one batch of 200 holds the whole small stream"
    calls.clear()
    monkeypatch.setattr(export_withhold, "EXPORT_BATCH_SIZE", 2)
    _get(client, "/export/ml/reactions.ndjson", "curated", _query(w))
    assert sum(calls) == n_records and len(calls) == -(-n_records // 2) and max(calls) == 2

    # and each call is one statement: hidden_refs once per call
    statements: list[int] = []
    real_hidden = ref_guard.hidden_refs
    inside = {"on": False}

    def spying_hidden(session, refs, *, profile, **kwargs):
        if inside["on"]:
            statements.append(1)
        return real_hidden(session, refs, profile=profile, **kwargs)

    def wrapped(session, documents, *, profile, mode):
        inside["on"] = True
        try:
            return real(session, documents, profile=profile, mode=mode)
        finally:
            inside["on"] = False

    monkeypatch.setattr(ref_guard, "hidden_refs", spying_hidden)
    monkeypatch.setattr(ref_guard, "withhold_in_document", wrapped)
    monkeypatch.setattr(export_withhold, "EXPORT_BATCH_SIZE", 200)
    _get(client, "/export/ml/reactions.ndjson", "curated", _query(w))
    assert len(statements) == 1


def test_the_exploratory_profile_buffers_nothing_and_asks_nothing(client, world, monkeypatch):
    """Kills: running the guard (or a statement) under the exploratory profile."""
    w = world
    monkeypatch.setattr(
        ref_guard,
        "withhold_in_document",
        lambda *a, **k: pytest.fail("the document seam ran under the exploratory profile"),
    )
    for lines in _all_exports(client, w, "exploratory").values():
        assert len(lines) > 2


# ---------------------------------------------------------------------------
# The request context
# ---------------------------------------------------------------------------


def _consume_after_the_context_is_gone(open_stream):
    token = set_current_read_profile(CURATED)
    try:
        stream = open_stream()
    finally:
        reset_current_read_profile(token)
    return _normalise(_lines("".join(stream)))


def test_a_stream_consumed_after_the_request_context_is_gone_keeps_its_profile(db_session, world):
    """Kills: reading the profile from the context variable inside a generator (it is the exploratory default there)."""
    w = world
    hidden = _hidden(w)
    species = [w.extra[k].public_ref for k in ("species_entry", "species_entry_export", "species_entry_conflict")]
    filters = ml_dataset.MLFilters(min_review_status=NOT_REVIEWED, include_hessian=True)

    def native():
        return export_core.iter_export_ndjson(
            db_session,
            seed=export_core.SeedSelection(species_refs=species, all_reactions=True),
            min_review_status=NOT_REVIEWED,
            collapse=export_core.CollapseMode.all,
        )

    def ml_species():
        return ml_dataset.iter_ml_species_ndjson(db_session, species_refs=species, filters=filters)

    def ml_reactions():
        return ml_dataset.iter_ml_reactions_ndjson(db_session, all_reactions=True, filters=filters)

    for name, open_stream in (("native", native), ("ml_species", ml_species), ("ml_reactions", ml_reactions)):
        gone = _consume_after_the_context_is_gone(open_stream)
        text = json.dumps(gone)
        for key, ref in hidden.items():
            assert ref not in text, f"{name}: {key} named after the context was gone"
        assert gone[0]["profile"] == "curated", name
        # and it equals the stream consumed with the context still live
        token = set_current_read_profile(CURATED)
        try:
            live = _normalise(_lines("".join(open_stream())))
        finally:
            reset_current_read_profile(token)
        assert gone == live, name


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def test_gap_and_skip_counts_follow_the_floor(client, world):
    """Kills: an export count that ignores the floor (a hidden thermo not reported as a gap; a skipped entry not counted).

    ``sg`` holds only a hidden thermo, so under curated it is a gap exactly as a species with none; the exploratory
    stream (floor-less, ``not_reviewed``) has a thermo for it and so no gap.
    """
    w = world
    sg = w.extra["species_entry_hidden_thermo_only"].public_ref

    def summary(profile):
        lines = _lines(_get(client, "/export/ndjson", profile, _query(w)))
        return lines[-1], lines

    curated, curated_lines = summary("curated")
    exploratory, exploratory_lines = summary("exploratory")
    assert any(g["ref"] == sg and g["kind"] == "thermo" for g in curated["gaps"])
    assert not any(g["ref"] == sg and g["kind"] == "thermo" for g in exploratory["gaps"])
    assert (
        curated["counts"]["gaps"] == len(curated["gaps"]) and curated["counts"]["gaps"] > exploratory["counts"]["gaps"]
    )
    assert _by(curated_lines, "species_entry_ref", sg)["thermos"] == []

    # ML: an entry below the floor is skipped and counted under curated, not under the exploratory profile
    unreviewed = w.extra["species_entry_unreviewed"].public_ref
    query = {"species_ref": unreviewed, "min_review_status": "not_reviewed"}
    curated_ml = _lines(_get(client, "/export/ml/species.ndjson", "curated", query))[-1]["counts"]
    exploratory_ml = _lines(_get(client, "/export/ml/species.ndjson", "exploratory", query))[-1]["counts"]
    assert curated_ml["skipped_below_min_review_status"] == 1
    assert exploratory_ml["skipped_below_min_review_status"] == 0
    # the reaction stream counts the same way
    rxe_u = w.extra["reaction_entry_unreviewed"].public_ref
    query = {"reaction_ref": rxe_u, "min_review_status": "not_reviewed"}
    curated_rx = _lines(_get(client, "/export/ml/reactions.ndjson", "curated", query))[-1]["counts"]
    exploratory_rx = _lines(_get(client, "/export/ml/reactions.ndjson", "exploratory", query))[-1]["counts"]
    assert curated_rx["skipped_below_min_review_status"] == 1 and exploratory_rx["skipped_below_min_review_status"] == 0


# ---------------------------------------------------------------------------
# The document mode and the text refusal
# ---------------------------------------------------------------------------


def test_null_mode_blanks_values_and_drops_list_strings_on_a_copy(db_session, world):
    """Kills: a hidden ref kept as a value or list element; a list element blanked instead of dropped; free text kept;
    the input modified (the guard returns a copy)."""
    import copy

    w = world
    hidden_calc, visible_calc = w.hidden["calc_h"].public_ref, w.visible["calc_vf"].public_ref
    documents = [
        {
            "a": hidden_calc,
            "b": visible_calc,
            "c": [visible_calc, hidden_calc, visible_calc],
            "d": {"e": [{"ref": hidden_calc, "keep": 1.5}]},
            "note": f"see {hidden_calc} for details",
        }
    ]
    before = copy.deepcopy(documents)
    report = ref_guard.withhold_in_document(db_session, documents, profile=CURATED, mode="null")
    assert documents == before, "the input was modified"
    assert report.facts == [
        {
            "a": None,
            "b": visible_calc,
            "c": [visible_calc, visible_calc],
            "d": {"e": [{"ref": None, "keep": 1.5}]},
            "note": None,
        }
    ]
    assert report.hidden == {hidden_calc}
    assert report.tokens == {} and [b.path for b in report.blanked] == ["note"]
    # exploratory: a copy, unchanged, no site
    again = [{"a": hidden_calc}]
    report = ref_guard.withhold_in_document(db_session, again, profile=EXPLORATORY, mode="null")
    assert report.sites == [] and report.facts is again  # nothing withheld: no copy is made


def test_the_mode_is_required_and_placeholder_is_not_null(db_session, world):
    """Kills: a default mode; a placeholder mode that quietly behaves like null."""
    with pytest.raises(TypeError):
        ref_guard.withhold_in_document(db_session, [{}], profile=CURATED)  # type: ignore[call-arg]
    hidden_calc = world.hidden["calc_h"].public_ref
    as_null = ref_guard.withhold_in_document(db_session, [{"a": hidden_calc}], profile=CURATED, mode="null")
    as_placeholder = ref_guard.withhold_in_document(db_session, [{"a": hidden_calc}], profile=CURATED, mode="placeholder")
    assert as_null.facts == [{"a": None}] and as_placeholder.facts == [{"a": "withheld:1"}]
    with pytest.raises(ValueError):
        ref_guard.withhold_in_document(db_session, [{}], profile=CURATED, mode="other")  # type: ignore[arg-type]


def test_a_ref_that_names_no_row_is_hidden(db_session, world):
    """Kills: a well-formed ref of nothing passing through (hidden equals unknown)."""
    ghost = "calc_" + "a" * 26
    report = ref_guard.withhold_in_document(
        db_session, [{"x": ghost, "y": [ghost, "kept"]}], profile=CURATED, mode="null"
    )
    assert report.facts == [{"x": None, "y": ["kept"]}] and report.hidden == {ghost}
    with pytest.raises(ref_guard.RefGuardUnplaceable):
        ref_guard.refuse_hidden_refs_in_text(db_session, {"f": f"! {ghost}"}, profile=CURATED)


def test_the_batcher_releases_the_withheld_copy_and_not_its_input(db_session, world):
    """Kills: a batcher that ignores the return value of the guard (and so emits the hidden ref)."""
    hidden_calc = world.hidden["calc_h"].public_ref
    batcher = export_withhold.WithheldBatcher(db_session, CURATED)
    record = {"calculation_ref": hidden_calc, "value": 1.0}
    assert batcher.add(record) == []
    lines = batcher.flush()
    assert [json.loads(line) for line in lines] == [{"calculation_ref": None, "value": 1.0}]
    assert hidden_calc not in "".join(lines)
    assert record["calculation_ref"] == hidden_calc  # the input is the caller's, untouched


def test_the_guard_is_active_by_one_predicate(db_session):
    """Kills: a second, diverging definition of "the guard is in force" (floor, release-backed)."""
    release = ResolvedReadProfile(
        profile=ReadProfile.curated, recommendation=ProfileRecommendation.tckdb_curated_release
    )
    assert ref_guard.guard_active(CURATED) and not ref_guard.guard_active(EXPLORATORY)
    assert not ref_guard.guard_active(release)
    assert not export_withhold.WithheldBatcher(db_session, release)._active
    ghost = "calc_" + "a" * 26
    assert ref_guard.withhold_in_document(db_session, [{"x": ghost}], profile=release, mode="null").facts == [
        {"x": ghost}
    ]
    ref_guard.refuse_hidden_refs_in_text(db_session, {"f": ghost}, profile=release)


def test_text_whose_encoding_never_settles_is_refused_not_a_422(db_session, world):
    """Kills: a ``ValueError`` escaping the text refusal (it became a 422)."""
    from urllib.parse import quote

    text = "%41"
    for _ in range(8):
        text = quote(text)
    with pytest.raises(ref_guard.RefGuardUnplaceable):
        ref_guard.refuse_hidden_refs_in_text(db_session, {"tran.dat": text}, profile=CURATED)


def test_a_group_is_named_only_through_a_shown_observation(client, world):
    """Kills: the observation-to-group join without a floor (a visible group named beside an empty observation list).

    ``calc_vsp`` is a shown calculation on the HIDDEN observation ``co_h`` of the SHOWN group ``cg_v``: exploratory names
    both; curated names neither (the group was reachable only through a hidden observation).
    """
    w = world
    geometry_ref = w.extra["geometry_vsp"].public_ref
    cg_v, co_h = w.visible["cg_v"].public_ref, w.hidden["co_h"].public_ref

    def record(profile):
        lines = _lines(_get(client, "/export/ml/species.ndjson", profile, _query(w)))
        return next(r for r in lines if r.get("geometry", {}).get("geometry_ref") == geometry_ref)

    assert record("exploratory")["conformer_group_refs"] == [cg_v]
    assert record("exploratory")["conformer_observation_refs"] == [co_h]
    shown = record("curated")
    assert shown["conformer_group_refs"] == [] and shown["conformer_observation_refs"] == []


def test_the_reactant_energy_floor_moves_the_barrier(client, world):
    """Kills: reactant energies read without the floor (a hidden reactant single point lowering the reactant sum).

    The shown energies at the shared level of theory: transition state -89.0 (shown single point), reactants -50.0 and
    -40.0. A hidden single point of -60.0 on the first reactant would make the reactant sum -100.0 and, with the hidden
    TS energy of -91.0, the exploratory barrier 9.0 Eh; the curated barrier is (-89.0) - (-90.0) = 1.0 Eh.
    """
    from app.services.scientific_read.ml_dataset import HARTREE_TO_KJ_MOL

    w = world
    rx = w.extra["reaction_entry"].public_ref

    def barrier(profile):
        lines = _lines(_get(client, "/export/ml/reactions.ndjson", profile, _query(w)))
        return _by(lines, "reaction_entry_ref", rx)["barrier"]["electronic_forward_kj_mol"]

    assert barrier("curated") == pytest.approx(1.0 * HARTREE_TO_KJ_MOL)
    assert barrier("exploratory") == pytest.approx((-91.0 - (-60.0 + -40.0)) * HARTREE_TO_KJ_MOL)


def test_generated_text_that_names_a_hidden_ref_is_refused(db_session, world):
    """Kills: emitting CHEMKIN text that names a hidden record."""
    hidden_calc = world.hidden["calc_h"].public_ref
    ref_guard.refuse_hidden_refs_in_text(
        db_session,
        {"tran.dat": f"! linearity=calculation_geometry:{world.visible['calc_vf'].public_ref}"},
        profile=CURATED,
    )
    with pytest.raises(ref_guard.RefGuardUnplaceable):
        ref_guard.refuse_hidden_refs_in_text(
            db_session, {"tran.dat": f"! linearity=calculation_geometry:{hidden_calc}"}, profile=CURATED
        )
    ref_guard.refuse_hidden_refs_in_text(db_session, {"tran.dat": f"! {hidden_calc}"}, profile=EXPLORATORY)


# ---------------------------------------------------------------------------
# Every visibility statement, on every stream
# ---------------------------------------------------------------------------

#: A visibility statement of the guard is the batched ref lookup (``ref_guard.hidden_refs``): it selects
#: ``<table>.public_ref`` through ``LEFT OUTER JOIN record_review``. Floors embedded in the statements that read the rows
#: are subqueries of those statements (``EXISTS (SELECT record_review.id ...)``), not statements of their own.
_VISIBILITY_STATEMENT = "LEFT OUTER JOIN record_review"


def _count_visibility_statements(db_session, send) -> tuple[int, int, int]:
    """``(visibility statements, statements that mention record_review at all, all statements)`` while ``send`` runs."""
    from sqlalchemy import event

    statements: list[str] = []

    def hook(conn, cursor, statement, *args):
        statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", hook)
    try:
        send()
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    visibility = sum(1 for s in statements if _VISIBILITY_STATEMENT in s)
    return visibility, sum(1 for s in statements if "record_review" in s), len(statements)


def test_every_stream_issues_at_most_one_visibility_statement_per_batch(client, db_session, world, monkeypatch):
    """Kills: a visibility statement per record or per source (the CHEMKIN/NDJSON witness asked ``hidden_refs`` up to
    twice per transport, outside the batch seam); a batch that asks twice.

    The count is of EVERY statement of the guard's lookup shape the stream issues, not of calls through the seam. Budget:
    at most one per batch of ``EXPORT_BATCH_SIZE`` records, and a fixed setup cost of **zero** for the three NDJSON
    streams (their floors are subqueries of the statements that already read the rows). CHEMKIN builds its whole record
    set first; its fixed cost is the two net calls on the finished files: the manifest withhold and the text refusal
    (at most one lookup each, and only when the manifest or a file names a gated ref at all).
    """
    w = world
    streams = {
        "ndjson": lambda: _get(client, "/export/ndjson", "curated", _query(w)),
        "ndjson_all": lambda: _get(client, "/export/ndjson", "curated", _query(w, collapse="all")),
        "ml_species": lambda: _get(
            client, "/export/ml/species.ndjson", "curated", {**_query(w), "include_hessian": True}
        ),
        "ml_reactions": lambda: _get(client, "/export/ml/reactions.ndjson", "curated", _query(w)),
        "chemkin": lambda: _chemkin(client, w, "curated"),
    }
    n_records = {name: len(_lines(streams[name]())) - 2 for name in streams if name != "chemkin"}
    counts = {}
    for size in (200, 2):
        monkeypatch.setattr(export_withhold, "EXPORT_BATCH_SIZE", size)
        for name, send in streams.items():
            visibility, review, total = _count_visibility_statements(db_session, send)
            counts[(name, size)] = (visibility, review, total)
            if name == "chemkin":
                assert visibility <= 2, f"chemkin, batch {size}: {visibility} visibility statements"
                continue
            batches = -(-n_records[name] // size)
            assert n_records[name] > 3, f"{name}: the stream is too small to mean anything"
            assert visibility <= batches, (
                f"{name}, batch {size}: {visibility} visibility statements for {batches} batches"
            )
    # the NDJSON streams at 200 records per batch: exactly the one statement, nothing else
    for name in ("ndjson", "ndjson_all", "ml_species", "ml_reactions"):
        assert counts[(name, 200)][0] == 1, f"{name}: {counts[(name, 200)]}"
    print("STATEMENT COUNTS (visibility, mention record_review, all):", counts)
