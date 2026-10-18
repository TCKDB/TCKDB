"""Guard PR-3: the selection documents withhold hidden refs at the loader to engine seam, and prove it.

A selection engine is a pure function of the facts its loader captured. Under ``?profile=curated`` every hidden gated
ref in those facts becomes a placeholder token (``withheld:<n>``) BEFORE anything is assessed, decided or hashed, so
the decision is the decision over the real refs with the refs renamed, the manifest's checksums are over the tokens and
its replay reproduces it byte for byte. This module holds the properties, over the sweep world that has a hidden ref in
every nested position of every engine (``_curated_sweep_selection_world``):

* **Positions.** The token stands in each nested position; the real ref is nowhere in the document.
* **Rename.** The curated document equals the document over the same population with the seam off (real refs) after
  each real ref is renamed to its token and the checksums are set aside. Nothing is added, removed or substituted.
* **Replay.** The curated manifest of every engine replays, and reproduces its decision byte for byte.
* **Previous formats.** A manifest of the previous format version still replays; a version nobody wrote does not.
* **Exploratory.** With the seam off, an exploratory document is byte-identical and costs the same statements.
* **Stability.** Two identical requests make the same tokens and the same digest, whatever order the facts load in.
* **Equality.** The same real ref has one token, so an equality the engine reads survives the renaming.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass

import pytest
from sqlalchemy import event

from app.services.kinetics_selection import manifest as kinetics_manifest
from app.services.network_selection import manifest as network_manifest
from app.services.relative_conformer_energy import manifest as relative_manifest
from app.services.statmech_selection import manifest as statmech_manifest
from app.services.structure_selection import manifest as structure_manifest
from app.services.thermo_selection import manifest as thermo_manifest
from tests.api.scientific._curated_sweep_run import install_spy
from tests.api.scientific._curated_sweep_world import build_sweep_world

BASE = "/api/v1/scientific"
TOKEN = re.compile(r"withheld:[1-9][0-9]*")

#: Keys that are checksums or sizes of the document itself: they move with every byte, so the rename comparison sets
#: them aside (their own correctness is the replay's job).
CHECKSUM_KEYS = frozenset({"digest", "integrity", "content_sha256", "manifest_sha256", "canonical_bytes"})

#: Every service module that crosses the seam, by the name it imports it under.
SEAM_MODULES = (
    "app.services.thermo_selection.service",
    "app.services.kinetics_selection.service",
    "app.services.network_selection.service",
    "app.services.statmech_selection.service",
    "app.services.structure_selection.service",
    "app.services.relative_conformer_energy.service",
)


@dataclass(frozen=True)
class Route:
    """One selection document: where it is asked, with what, and the nested positions that must hold a token."""

    name: str
    path: str
    body: dict
    #: IDENTITY positions: the engine needs the ref to compare or partition, so a placeholder token stands there.
    positions: tuple[str, ...]
    #: The hidden world records whose real refs those positions would have held.
    hidden: tuple[str, ...]
    #: EVIDENCE positions: a hidden record reads as unavailable (hidden == unknown), so no token and no fact stands there.
    evidence: tuple[str, ...] = ()


@pytest.fixture
def world(db_session, login_as):
    w = build_sweep_world(db_session)
    login_as(w.extra["curator"].id)
    return w


def _routes(w):
    from tests.api.scientific import _curated_sweep_inputs as inputs

    sa = w.extra["species_entry"].public_ref
    rxe = w.extra["reaction_entry"].public_ref
    net = w.visible["net_sel"].public_ref

    def body(provider, key=None):
        (variant,) = provider(w)
        return variant["json"]

    thermo = body(inputs._THERMO_SELECT)
    kinetics = body(inputs._KINETICS_SELECT)
    network = body(inputs._NETWORK_SELECT)
    statmech = body(inputs._STATMECH_SELECT)
    structure = body(inputs._SELECT_STRUCTURE)
    relative = body(inputs._RELATIVE_ENERGIES)
    return [
        Route(
            "thermo",
            f"/species-entries/{sa}/thermo/select/manifest",
            thermo,
            ("candidates[*].target_group_ref",),
            ("cg_h", "calc_h"),
            ("candidates[*].protocol.supporting_calculations[*].calculation_ref",),
        ),
        Route(
            "kinetics",
            f"/reaction-entries/{rxe}/kinetics/select/manifest",
            kinetics,
            (
                "candidates[*].determination.transition_state_entry_ref",
                "candidates[*].determination.network_ref",
                "candidates[*].network_channel_ref",
            ),
            ("tse_h", "net_h", "nsolve_h", "calc_h", "calc_h_tssp"),
            (
                "candidates[*].network_solve_ref",
                "candidates[*].protocol.supporting_calculations[*].calculation_ref",
                "candidates[*].energy_levels[*].calculation_ref",
            ),
        ),
        Route(
            "network",
            f"/networks/{net}/kinetics/select/manifest",
            network,
            (),
            ("nsolve_hsel",),
            ("solves[*].validation.entries[*].reference_solve_ref",),
        ),
        Route(
            "structure",
            f"/species-entries/{sa}/conformers/select/manifest",
            structure,
            ("determinations[*].conformer_group_ref",),
            ("cg_h",),
        ),
        Route(
            "statmech",
            "/statmech/select/manifest",
            statmech,
            (
                "candidates[*].declaration.conformer_group_ref",
                "candidates[*].supersession.superseded_by",
                "candidates[*].supersession.current",
            ),
            ("cg_h", "sm_hsucc"),
            ("candidates[*].anchor_group_refs[*]", "candidates[*].declaration.members[*].conformer_group_ref"),
        ),
        Route(
            "relative_energy",
            f"/species-entries/{sa}/conformers/relative-energies/manifest",
            relative,
            (),  # the population filter keeps a hidden group out of the compared world: no position holds one
            (),
        ),
    ]


def _walk(value, path=""):
    """Every string leaf with its path (list indices collapsed to ``[*]``)."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, f"{path}[*]")
    elif isinstance(value, str):
        yield path, value


def _post(client, path, body, profile):
    response = client.post(f"{BASE}{path}", params={"profile": profile}, json=body)
    assert response.status_code == 200, (path, profile, response.text[:300])
    return response.json()


def _tokens_made(spy) -> dict[str, str]:
    """Real ref -> token over every document the request made; one ref never has two tokens across documents."""
    merged: dict[str, str] = {}
    for report in spy.context.document_reports:
        for ref, token in report.tokens.items():
            assert merged.setdefault(ref, token) == token, "one real ref made two different tokens"
    return merged


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in CHECKSUM_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _rename(value, mapping: dict[str, str]):
    """``value`` with every real ref (and every dict key that is one) renamed to its token, longest refs first."""
    text = json.dumps(value, sort_keys=True)
    for ref in sorted(mapping, key=len, reverse=True):
        text = text.replace(ref, mapping[ref])
    return json.loads(text)


@pytest.fixture
def seam_off(monkeypatch):
    """Turn the loader to engine seam into the identity in every service that crosses it (the document of real refs)."""
    import importlib

    def turn_off():
        for name in SEAM_MODULES:
            monkeypatch.setattr(importlib.import_module(name), "withhold_selection_facts", lambda session, facts: facts)

    return turn_off


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


def test_every_nested_position_holds_a_token_and_the_real_ref_is_nowhere(client, world, monkeypatch):
    """The sweep world has the shape in every position. At an identity position the curated document carries the token
    and not the ref; at an evidence position it carries neither (the evidence reads as unavailable).

    Kills: a world without a position (every route would answer 200 and name nothing); a seam not crossed; a position
    the walker does not reach (a string inside a tuple, a dict, a nested dataclass); a composite field left unreplaced.
    """
    spy = install_spy(monkeypatch)
    hidden_refs = world.hidden_refs()
    for route in _routes(world):
        exploratory = _post(client, route.path, route.body, "exploratory")
        curated = _post(client, route.path, route.body, "curated")
        text = json.dumps(curated)
        for key in route.hidden:
            assert hidden_refs[key] not in text, f"{route.name}: {key} is named in the curated document"
        found = {path for path, value in _walk(curated) if TOKEN.search(value)}
        sightings = {path for path, value in _walk(exploratory) if any(hidden_refs[k] in value for k in route.hidden)}
        for position in route.positions:
            assert position in sightings, f"{route.name}: the world never puts a hidden ref at {position}"
            assert position in found, f"{route.name}: no token at identity position {position}"
        for position in route.evidence:
            assert position in sightings, f"{route.name}: the world never puts a hidden ref at {position}"
            assert position not in found, f"{route.name}: a token stands at evidence position {position}"
        assert spy.context is not None and spy.context.documents >= 1, f"{route.name}: the seam was not crossed"


def test_a_composite_ref_field_keeps_its_key_after_the_ref_is_a_token(client, world):
    """``network_channel_ref`` is ``<network ref>/<channel key>``: the ref is replaced in place, the key stays."""
    route = next(r for r in _routes(world) if r.name == "kinetics")
    curated = _post(client, route.path, route.body, "curated")
    composites = [
        c["network_channel_ref"] for c in curated["candidates"] if c["network_channel_ref"] is not None
    ]
    assert any(re.fullmatch(r"withheld:[1-9][0-9]*/ch-.+", c) for c in composites), composites


# ---------------------------------------------------------------------------
# The plain routes and the manifests are one document
# ---------------------------------------------------------------------------


def _pairs(w):
    from tests.api.scientific import _curated_sweep_inputs as inputs

    sa = w.extra["species_entry"].public_ref
    tse = w.visible["tse_v2"].public_ref
    structure = {}
    return [
        (f"/species-entries/{sa}/calculations/select", structure),
        (f"/species-entries/{sa}/conformers/select", structure),
        (f"/transition-state-entries/{tse}/evidence/select", structure),
        (f"/species-entries/{sa}/conformers/relative-energies", inputs._RELATIVE_ENERGIES(w)[0]["json"]),
        ("/statmech/select", inputs._STATMECH_SELECT(w)[0]["json"]),
    ]


def test_each_plain_route_answers_what_its_manifest_records(client, world):
    """``calculations/select``, the TS ``evidence/select``, relative energies and the rest build their response from the
    manifest of the same request, so the outcome, the selection and the units agree with it, and neither names a hidden
    record (the sweep's ref ratchet) nor differs between the two.

    Hidden == absent for these is proved at the engine in ``test_selection_hidden_equals_absent.py`` (the calculation
    grain, the transition-state grain, relative energies, statmech); here the response is held to its manifest.
    """
    for path, body in _pairs(world):
        response = _post(client, path, body, "curated")
        manifest = _post(client, f"{path}/manifest", body, "curated")
        assert response["outcome"] == manifest["outcome"], path
        text = json.dumps(response)
        for ref in world.hidden_refs().values():
            assert ref not in text and ref not in json.dumps(manifest), path


def test_a_comparison_makes_one_document_per_run(client, world, monkeypatch):
    spy = install_spy(monkeypatch)
    path = f"/species-entries/{world.extra['species_entry'].public_ref}/conformers/relative-energies/compare/manifest"
    from tests.api.scientific import _curated_sweep_inputs as inputs

    (variant,) = inputs._RELATIVE_ENERGIES_COMPARE(world)
    _post(client, path, variant["json"], "curated")
    assert spy.context.documents == 2


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _decision_of(name, manifest):
    return {
        "thermo": lambda m: m["decision"],
        "kinetics": lambda m: m["decision"],
        "network": lambda m: m["decision"],
        "structure": lambda m: m["decision"],
        "statmech": lambda m: m["decision"],
        "relative_energy": lambda m: m["result"],
    }[name](manifest)


def _replay(name, manifest):
    return {
        "thermo": thermo_manifest.replay_decision,
        "kinetics": kinetics_manifest.replay_decision,
        "network": network_manifest.replay_network_decision,
        "structure": structure_manifest.replay_structure_decision,
        "statmech": lambda m: statmech_manifest.replay_statmech_decision(m)["decision"],
        "relative_energy": relative_manifest.replay_relative_energies,
    }[name](manifest)


@pytest.mark.parametrize("name", ["thermo", "kinetics", "network", "structure", "statmech", "relative_energy"])
def test_a_curated_manifest_replays_and_reproduces_its_decision_byte_for_byte(client, world, name):
    route = next(r for r in _routes(world) if r.name == name)
    manifest = _post(client, route.path, route.body, "curated")
    replayed = _replay(name, copy.deepcopy(manifest))
    recorded = _decision_of(name, manifest)
    assert json.dumps(replayed, sort_keys=True) == json.dumps(recorded, sort_keys=True)
    if route.positions:
        assert TOKEN.search(json.dumps(manifest)), f"{name}: the replayed manifest holds no token"


def test_a_curated_structure_manifest_replays_every_assessment_from_its_tokens(client, world):
    """The structure replay recomputes each assessment from the normalised inputs it holds, tokens included."""
    sa = world.extra["species_entry"].public_ref
    for suffix in ("calculations", "conformers"):
        manifest = _post(client, f"/species-entries/{sa}/{suffix}/select/manifest", {}, "curated")
        assert structure_manifest.replay_matches(copy.deepcopy(manifest))
        assert structure_manifest.replay_structure_assessment(copy.deepcopy(manifest))


def test_a_curated_comparison_manifest_replays_both_runs(client, world):
    from tests.api.scientific import _curated_sweep_inputs as inputs

    path = f"/species-entries/{world.extra['species_entry'].public_ref}/conformers/relative-energies/compare/manifest"
    (variant,) = inputs._RELATIVE_ENERGIES_COMPARE(world)
    manifest = _post(client, path, variant["json"], "curated")
    assert relative_manifest.replay_comparison_matches(copy.deepcopy(manifest))


def test_the_export_of_a_curated_selection_is_accepted_and_names_no_hidden_ref(client, world):
    """The saved curated manifest (tokens in it) is what the server holds now, so the export proceeds."""
    from tests.api.scientific import _curated_sweep_inputs as inputs

    net = world.visible["net_sel"].public_ref
    (variant,) = inputs._export_selected(world)
    body = variant["json_by_profile"]["curated"]
    assert world.hidden["nsolve_hsel"].public_ref not in json.dumps(body["manifest"])  # an unavailable reference
    out = _post(client, f"/networks/{net}/kinetics/export-selected", body, "curated")
    assert out["solve_ref"] == world.visible["nsolve_vsel"].public_ref
    assert world.hidden["nsolve_hsel"].public_ref not in json.dumps(out)


# ---------------------------------------------------------------------------
# Previous format versions
# ---------------------------------------------------------------------------


def _reseal_network(manifest):
    manifest["digest"] = {"algorithm": "sha256", "value": network_manifest.manifest_digest(manifest)}
    return manifest


def _reseal_integrity(manifest, digest):
    body = {k: v for k, v in manifest.items() if k != "integrity"}
    manifest["integrity"]["manifest_sha256"] = digest(body)
    return manifest


def test_the_current_format_version_of_every_engine_is_written(client, world):
    versions = {}
    for route in _routes(world):
        manifest = _post(client, route.path, route.body, "exploratory")
        versions[route.name] = (manifest.get("manifest_format_version"), (manifest.get("versions") or {}).get("relative_energy"))
    assert versions == {
        "thermo": (2, None),
        "kinetics": (2, None),
        "network": (3, None),
        "structure": (2, None),
        "statmech": (2, None),
        "relative_energy": (1, "3"),
    }


def test_a_manifest_of_the_previous_format_version_still_replays(client, world):
    """An exploratory manifest (real refs), rewritten as the previous release wrote it: version, and nothing else."""
    routes = {r.name: r for r in _routes(world)}

    thermo = _post(client, routes["thermo"].path, routes["thermo"].body, "exploratory")
    thermo["manifest_format_version"] = 1
    assert thermo_manifest.replay_matches(thermo)

    kinetics = _post(client, routes["kinetics"].path, routes["kinetics"].body, "exploratory")
    kinetics["manifest_format_version"] = 1
    assert kinetics_manifest.replay_matches(kinetics)

    network = _post(client, routes["network"].path, routes["network"].body, "exploratory")
    network["manifest_format_version"] = 2
    assert network_manifest.replay_matches(_reseal_network(network))

    structure = _post(client, routes["structure"].path, routes["structure"].body, "exploratory")
    structure["manifest_format_version"] = 1
    assert structure_manifest.replay_matches(_reseal_integrity(structure, structure_manifest.digest))

    statmech = _post(client, routes["statmech"].path, routes["statmech"].body, "exploratory")
    statmech["manifest_format_version"] = 1
    assert statmech_manifest.replay_matches(_reseal_integrity(statmech, statmech_manifest.digest))

    relative = _post(client, routes["relative_energy"].path, routes["relative_energy"].body, "exploratory")
    relative["versions"]["relative_energy"] = "2"
    relative["result"]["relative_energy_version"] = "2"
    assert relative_manifest.replay_matches(_reseal_integrity(relative, relative_manifest.digest))


def test_a_comparison_manifest_of_the_previous_relative_energy_version_still_replays(client, world):
    from tests.api.scientific import _curated_sweep_inputs as inputs

    path = f"/species-entries/{world.extra['species_entry'].public_ref}/conformers/relative-energies/compare/manifest"
    (variant,) = inputs._RELATIVE_ENERGIES_COMPARE(world)
    manifest = _post(client, path, variant["json"], "exploratory")
    manifest["versions"]["relative_energy"] = "2"
    for run in manifest["runs"].values():
        run["versions"]["relative_energy"] = "2"
        run["result"]["relative_energy_version"] = "2"
        _reseal_integrity(run, relative_manifest.digest)
    assert relative_manifest.replay_comparison_matches(_reseal_integrity(manifest, relative_manifest.digest))


@pytest.mark.parametrize("name", ["thermo", "kinetics", "network", "structure", "statmech"])
def test_a_format_version_nobody_wrote_is_refused(client, world, name):
    route = next(r for r in _routes(world) if r.name == name)
    manifest = _post(client, route.path, route.body, "exploratory")
    manifest["manifest_format_version"] = 99
    with pytest.raises(Exception, match="manifest format|unsupported_manifest_format"):
        _replay(name, manifest)


# ---------------------------------------------------------------------------
# Exploratory is untouched
# ---------------------------------------------------------------------------


def _statements(client, db_session, path, body, profile):
    seen: list[str] = []

    def hook(conn, cursor, statement, *args):
        seen.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", hook)
    try:
        out = _post(client, path, body, profile)
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    return out, len(seen)


@pytest.mark.parametrize("name", ["thermo", "kinetics", "network", "structure", "statmech", "relative_energy"])
def test_an_exploratory_document_is_byte_identical_and_costs_the_same_with_the_seam_off(
    client, world, db_session, seam_off, name
):
    """Under the exploratory profile the seam reads no statement and changes nothing.

    Kills: a seam that queries or rewrites without a review floor; a token anywhere in an exploratory document.
    """
    route = next(r for r in _routes(world) if r.name == name)
    with_seam, statements_with = _statements(client, db_session, route.path, route.body, "exploratory")
    seam_off()
    without, statements_without = _statements(client, db_session, route.path, route.body, "exploratory")
    assert json.dumps(with_seam, sort_keys=True) == json.dumps(without, sort_keys=True)
    assert statements_with == statements_without
    assert not TOKEN.search(json.dumps(with_seam))


@pytest.mark.parametrize("name", ["thermo", "kinetics", "network", "structure", "statmech"])
def test_a_curated_selection_costs_exactly_one_statement_more_for_the_seam(client, world, db_session, seam_off, name):
    route = next(r for r in _routes(world) if r.name == name)
    _, with_seam = _statements(client, db_session, route.path, route.body, "curated")
    seam_off()
    _, without = _statements(client, db_session, route.path, route.body, "curated")
    assert with_seam - without == 1


# ---------------------------------------------------------------------------
# Stability and equality
# ---------------------------------------------------------------------------


def test_two_identical_requests_make_the_same_tokens_and_the_same_digest(client, world, monkeypatch):
    spy = install_spy(monkeypatch)
    route = next(r for r in _routes(world) if r.name == "structure")
    first = _post(client, route.path, route.body, "curated")
    first_tokens = _tokens_made(spy)
    second = _post(client, route.path, route.body, "curated")
    assert _tokens_made(spy) == first_tokens and first_tokens
    assert first["integrity"]["manifest_sha256"] == second["integrity"]["manifest_sha256"]


def test_the_tokens_do_not_depend_on_the_order_the_facts_load_in(client, world, monkeypatch):
    """The population loads in a different order; the numbering follows the sorted real refs, so nothing changes.

    Kills: tokens numbered in the order the walker meets the refs.
    """
    from app.services.kinetics_selection import service as kinetics_service

    spy = install_spy(monkeypatch)
    route = next(r for r in _routes(world) if r.name == "kinetics")
    first = _post(client, route.path, route.body, "curated")
    first_tokens = _tokens_made(spy)
    real_load = kinetics_service.load_population

    def reversed_load(session, scan):
        loaded = real_load(session, scan)
        loaded.candidates = dict(reversed(list(loaded.candidates.items())))
        return loaded

    monkeypatch.setattr(kinetics_service, "load_population", reversed_load)
    second = _post(client, route.path, route.body, "curated")
    assert _tokens_made(spy) == first_tokens
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_an_equality_the_engine_reads_survives_the_tokens(client, world):
    """A fit of a hidden network's channel, on a determination of that channel: the network is the same ref twice.

    The assessment compares the record's ``<network>/<channel>`` with the determination's network and channel, and
    refuses the record when they differ. Under the tokens it must still find them equal: one real ref, one token.

    Kills: a ref given a new token at each position it appears in.
    """
    route = next(r for r in _routes(world) if r.name == "kinetics")
    kin_vn = world.visible["kin_vn"].public_ref
    for profile in ("exploratory", "curated"):
        manifest = _post(client, route.path, route.body, profile)
        candidate = next(c for c in manifest["candidates"] if c["kinetics_ref"] == kin_vn)
        codes = [r["code"] for r in candidate["assessment"]["reasons"]]
        assert "network_channel_mismatch" not in codes, (profile, codes)
        det = candidate["determination"]
        assert candidate["network_channel_ref"] == f"{det['network_ref']}/{det['channel_key']}"
    assert TOKEN.search(det["network_ref"])
