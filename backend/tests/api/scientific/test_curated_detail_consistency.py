"""A served curated detail must count what it lists (ADR 0023 step 7).

Since step 7 a detail page lists its embedded records by the evidence rule (everything but ``rejected`` and
``deprecated``), badged. Its counts, summaries and ``has_*`` flags must be computed over the same population, or the page
contradicts itself: one badged entry listed, ``total=0``, ``has_entries=False``.

The walker below is generic: for every dict in a response, a ``has_<x>`` flag, an ``<x>_count`` and an ``entries_summary``
total are compared with the collection ``<x>`` (or ``<x>s``) listed beside it, in the same record or in the record that holds
the ``available_sections`` / ``evidence_summary`` block. A pair is only compared when the collection is present in the
response (``include=all`` supplies them).
"""

from __future__ import annotations

import pytest

from tests.api.scientific._curated_sweep_world import build_sweep_world
from tests.api.scientific.test_curated_detail_by_ref import BASE, DETAIL_ROUTES

#: Records whose collection of the same name is NOT the population the flag counts, each with the reason.
#: A TS-entry record's ``entries`` is "this one included"; its ``has_entries`` asks for OTHER entries.
SIBLING_FLAGS = {"has_entries", "has_observations"}
#: Flags whose collection is a wrapper per entry (the transition-state concept's ``validation_evidence`` has one block
#: per entry, empty or not), so its length says nothing about whether any evidence exists.
WRAPPER_COLLECTIONS = {"has_validation_evidence"}


def _plural(name: str) -> tuple[str, ...]:
    irregular = (name[:-1] + "ies",) if name.endswith("y") else ()  # entry -> entries
    return (name, name + "s", *irregular, name[:-1] if name.endswith("s") else name)


def _collection(holders: list[dict], name: str):
    for holder in holders:
        for candidate in _plural(name):
            value = holder.get(candidate)
            if isinstance(value, list):
                return candidate, value
    return None, None


def _is_ts_concept(holders, node) -> bool:
    """The wrapper exemption is for the transition-state concept record only (its availability block's holder)."""
    record = holders[0] if holders else node
    return "transition_state" in record and "transition_state_entry" not in record


def inconsistencies(node, holders=None, path="record", is_sibling_record=False) -> list[str]:
    """``path: message`` for every flag, count or total that disagrees with the collection listed beside it."""
    out: list[str] = []
    holders = holders or []
    if isinstance(node, list):
        for i, item in enumerate(node):
            out += inconsistencies(item, holders, f"{path}[{i}]", is_sibling_record)
        return out
    if not isinstance(node, dict):
        return out
    # The collection sits in this dict, or (for a summary / availability block) in the record that holds the block.
    block = path.rsplit(".", 1)[-1] in {"available_sections", "evidence_summary", "entries_summary"}
    here = [node, *holders[:1]] if block else [node]
    sibling_record = is_sibling_record or (
        ("transition_state_entry" in node or "conformer_observation" in node) and "evidence_summary" in node
    )
    for key, value in node.items():
        if isinstance(value, bool) and key.startswith("has_") and not (key in WRAPPER_COLLECTIONS and _is_ts_concept(holders, node)):
            name, items = _collection(here, key[4:])
            if items is None:
                continue
            expected = bool(items)
            if key in SIBLING_FLAGS and sibling_record:
                expected = len(items) > 1
            if value != expected:
                out.append(f"{path}.{key}={value} but {name} lists {len(items)}")
        elif isinstance(value, int) and not isinstance(value, bool) and key.endswith("_count"):
            name, items = _collection(here, key[: -len("_count")])
            if key == "observation_count" and sibling_record:
                continue  # an observation's own summary counts itself; its ``observations`` are its siblings
            if items is not None and value != len(items):
                out.append(f"{path}.{key}={value} but {name} lists {len(items)}")
        elif key == "entries_summary" and isinstance(value, dict) and isinstance(node.get("entries"), list):
            if value.get("total") != len(node["entries"]):
                out.append(f"{path}.entries_summary.total={value.get('total')} but entries lists {len(node['entries'])}")
    for key, value in node.items():
        if isinstance(value, (dict, list)):
            out += inconsistencies(value, [node, *holders], f"{path}.{key}", sibling_record)
    return out


def test_the_walker_finds_the_defect_it_exists_for():
    """Kills: a walker that compares nothing (it would pass for any response)."""
    bad = {"entries": [{"x": 1}], "entries_summary": {"total": 0}, "available_sections": {"has_entries": False}}
    found = inconsistencies(bad)
    assert len(found) == 2 and any("entries_summary.total=0" in f for f in found)
    assert inconsistencies({"calculations": [], "available_sections": {"has_calculations": False}}) == []


@pytest.mark.parametrize("withheld", [False, True], ids=["unlisted", "withheld"])
def test_every_curated_detail_counts_what_it_lists(client, db_session, login_as, withheld):
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    if withheld:
        world.withhold_hidden(db_session)
    refs = {key: world.hidden[key].public_ref for key in DETAIL_ROUTES if not withheld}
    refs |= {key: row.public_ref for key, row in world.visible.items() if key.split("_")[0] in {k.split("_")[0] for k in DETAIL_ROUTES}}
    prefix_route = {k.split("_")[0]: t for k, t in DETAIL_ROUTES.items()}
    compared = 0
    failures: list[str] = []
    for key, ref in sorted(refs.items()):
        template = prefix_route.get(key.split("_")[0])
        if template is None:
            continue
        response = client.get(
            f"{BASE}{template.format(ref=ref)}", params={"profile": "curated", "include": ["all"]}
        )
        if response.status_code != 200:
            continue
        body = response.json()
        compared += 1
        failures += [f"{key} {template}: {m}" for m in inconsistencies(body["record"])]
    rxe = world.extra["reaction_entry"].public_ref
    full = client.get(f"{BASE}/reaction-entries/{rxe}/full", params={"profile": "curated", "include": ["all"]})
    assert full.status_code == 200
    failures += [f"/full: {m}" for m in inconsistencies(full.json())]
    assert compared >= 10, "the walk compared too few responses to mean anything"
    assert not failures, "\n".join(failures)
