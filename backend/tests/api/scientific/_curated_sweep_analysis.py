"""Reading a crawl: where hidden refs appear, and whether curated is only a withholding of exploratory."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from app.services.scientific_read import ref_guard
from tests.api.scientific._curated_sweep_world import SweepWorld


def fmt(route: tuple[str, str], text: str) -> str:
    return f"{route[0]} {route[1]} :: {text}"


#: Top-level and nested keys that describe the request or the page, not the records. Compared by key name.
REQUEST_KEYS = frozenset({"request", "review_visibility", "pagination", "review_summary"})

#: Counts that legitimately differ between profiles because they answer "what would the list return" (owner decision
#: D5: population summaries follow the floor in the service). They are not facts about a visible record, so the
#: withholding-only comparison does not hold them to "equal or null". Grow this only with a reason.
POPULATION_KEYS = frozenset(
    {
        "total",
        "count",
        "returned",
        "total_count",
        "n_results",
        "has_more",
        "next_cursor",
        "next_offset",
        "calculation_count",
        "has_thermo",
        "has_statmech",
        "has_transport",
        "has_conformers",
        "has_kinetics",
        "has_calculations",
        "ids",
        # PR-2b (D5): the transition-state and reaction counts and flags follow the floor like the species-side ones.
        "kinetics_count",
        "has_transition_state",
        "has_path_search",
        "has_atom_map",
        "has_entries",
        "has_geometries",
        "has_validation_evidence",
        "has_opt",
        "has_freq",
        "has_sp",
        "has_irc",
        "has_geometry_validation",
        "has_scf_stability",
        "entry_count",
        "by_status",
        # PR-2c (D5): the network, level-of-theory, correction, literature and analytics counts and flags follow the
        # floor. Each is pinned against what the list endpoints serve by test_curated_ref_guard_network_and_provenance_
        # family.py, because this comparator cannot hold a count to "equal or null".
        "solve_count",
        "source_calculation_count",
        "has_solves",
        "has_source_calculations",
        "has_chebyshev",
        "has_plog",
        "has_point_kinetics",
        "solve_temperature_min_k",
        "solve_temperature_max_k",
        "solve_pressure_min_bar",
        "solve_pressure_max_bar",
        "calculation_usage_count",
        "applied_usage_count",
        "has_applied_usage",
        "statmech_usage_count",
        "has_statmech_usage",
        "has_used_by",
        "has_linked_records",
        "has_statmech_provenance",
        "has_transition_state_provenance",
        "has_software",
        "distinct_software_count",
        "partial_sources",
        "has_channel_barriers",
        # A candidate's ordinal among the population's own (guard PR-3): it stands in for the database id as the last
        # tie-break. The population is the profile's, so the same record has another ordinal under curated.
        "id_rank",
    }
)

#: Blocks that describe the read itself (when it was taken, which profile, the selection document's own disclosures and
#: checksum) rather than the records. They differ between profiles by design.
DESCRIBES_THE_READ_KEYS = frozenset(
    {
        "watermark",
        "disclosures",
        "read_profile",
        "integrity",
        "population",
    }
)
#: Exact paths for the same reason where the key name is too common to ignore everywhere (``review`` is also a badge).
DESCRIBES_THE_READ_PATHS = frozenset(
    {
        "review.effective_floor",
        # D5 (PR-2b): the validation tokens and the per-type entry coverage count only the evidence the floor shows.
        "validation.irc",
        "validation.energy_ordering",
        "validation.imaginary_mode",
        # D5 (PR-2b): the kinetics evidence checklist counts the calculation types the profile shows.
        "evidence_completeness",
        # D5 (PR-2c): a literature record's linked-record counts are of the records the profile shows.
        "record_counts",
        # The trust fragment and the public assessment are verdicts over the evidence the profile shows.
        "trust",
        "assessments",
        *(
            f"evidence_coverage.{name}"
            for name in ("opt", "freq", "sp", "irc", "path_search", "geometry_validation", "scf_stability")
        ),
    }
)

#: Fields whose value is a MAP of slots keyed by role (``/full``'s ``transition_states[*].calculations``), not a model
#: with fixed keys. A slot whose calculation is hidden is removed from the map; the key of a model is never removed.
REMOVABLE_MAP_FIELDS = frozenset({"calculations", "levels_of_theory"})

#: Key stem -> world kind, for the integer-id check.
ID_STEM_TO_KIND = {
    "calculation": "calc",
    "statmech": "sm",
    "transport": "trn",
    "conformer_group": "cg",
    "conformer_observation": "co",
    "transition_state": "ts",
    "transition_state_entry": "tse",
    "network": "net",
    "network_solve": "nsolve",
    "solve": "nsolve",
    "network_kinetics": "nkin",
    "thermo": "thm",
    "kinetics": "kin",
    "molecular_property_observation": "mpo",
}


def parse_body(text: str) -> tuple[str, Any]:
    """``("json", obj)``, ``("ndjson", [obj, ...])`` or ``("text", text)``."""
    try:
        return "json", json.loads(text)
    except ValueError:
        pass
    objs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            objs.append(json.loads(line))
        except ValueError:
            return "text", text
    return ("ndjson", objs) if objs else ("text", text)


def _walk(obj: Any, path: str = "", *, top: bool = True) -> Iterator[tuple[str, str, Any]]:
    """Yield ``(path, key, value)`` for every leaf, with list indices collapsed to ``[*]``."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if top and key == "request":
                continue
            sub = f"{path}.{key}" if path else key
            if isinstance(value, (dict, list)):
                yield from _walk(value, sub, top=False)
            else:
                yield sub, key, value
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                yield from _walk(item, f"{path}[*]", top=False)
            else:
                yield f"{path}[*]", path.rsplit(".", 1)[-1], item


def _root(kind: str, obj: Any):
    return ({"line": obj} if kind == "ndjson" else obj) if kind != "text" else obj


def ref_leaks(text: str, hidden_refs: dict[str, str], request_text: str) -> set[tuple[str, str]]:
    """``(hidden key, json path)`` for every hidden ref in the body that the caller did not supply."""
    present = {k: r for k, r in hidden_refs.items() if r in text and r not in request_text}
    if not present:
        return set()
    kind, obj = parse_body(text)
    found: set[tuple[str, str]] = set()
    if kind == "text":
        return {(k, "<text>") for k in present}
    for path, _key, value in _walk(_root(kind, obj)):
        if isinstance(value, str):
            for k, r in present.items():
                if r in value:
                    found.add((k, path))
    for k in present:
        if not any(fk == k for fk, _ in found):
            found.add((k, "<raw>"))  # in a dict key, or under `request`: still on the wire
    return found


def id_leaks(text: str, world: SweepWorld) -> set[tuple[str, str]]:
    """``("id:<hidden key>", path)`` where a hidden record's integer pk is the value of a key naming its type."""
    kind, obj = parse_body(text)
    if kind == "text":
        return set()
    hidden_ids: dict[str, dict[int, str]] = {}
    for key, row in world.hidden.items():
        hidden_ids.setdefault(world.kind_of(key), {})[row.id] = key
    out: set[tuple[str, str]] = set()
    for path, key, value in _walk(_root(kind, obj)):
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if key.endswith("_id"):
            stem = key[: -len("_id")]
        elif key.endswith("_ids"):
            stem = key[: -len("_ids")]
        else:
            continue
        for candidate in (stem, stem.rsplit("_", 1)[-1]) if stem not in ID_STEM_TO_KIND else (stem,):
            kind_name = ID_STEM_TO_KIND.get(candidate)
            if kind_name and value in hidden_ids.get(kind_name, {}):
                out.add((f"id:{hidden_ids[kind_name][value]}", path))
                break
    # An id whose type sits in a SIBLING key (``{"record_type": "kinetics", "record_id": 7}``): the stem of ``record_id``
    # names no type, so the stem check above cannot see it.
    for path, node in _dicts(_root(kind, obj)):
        record_id, record_type = node.get("record_id"), node.get("record_type")
        if isinstance(record_id, int) and not isinstance(record_id, bool) and isinstance(record_type, str):
            kind_name = ID_STEM_TO_KIND.get(record_type)
            if kind_name and record_id in hidden_ids.get(kind_name, {}):
                out.add((f"id:{hidden_ids[kind_name][record_id]}", f"{path}.record_id" if path else "record_id"))
    return out


def _dicts(obj: Any, path: str = "", *, top: bool = True) -> Iterator[tuple[str, dict]]:
    """Every dict in a document with its path (list indices collapsed to ``[*]``); the echoed ``request`` is skipped."""
    if isinstance(obj, dict):
        yield path, obj
        for key, value in obj.items():
            if top and key == "request":
                continue
            yield from _dicts(value, f"{path}.{key}" if path else key, top=False)
    elif isinstance(obj, list):
        for item in obj:
            yield from _dicts(item, f"{path}[*]", top=False)


def _is_withholding(cur: Any, exp: Any) -> bool:
    return not withholding_violations(exp, cur)


def withholding_violations(exp: Any, cur: Any, path: str = "") -> list[str]:
    """Paths where ``cur`` is not a withholding of ``exp``.

    Allowed: equal; a value turned ``None``; a list with elements removed (a subsequence under this same relation).
    Not allowed: a changed value, a key that appears or disappears, an element that is not in the exploratory list.
    The last is how a substituted record shows up: a slot that names a *different* visible record.
    """
    if cur is None:
        return []
    if isinstance(exp, dict) and isinstance(cur, dict):
        out: list[str] = []
        for key in sorted(set(exp) | set(cur)):
            sub = f"{path}.{key}" if path else key
            if (
                key in REQUEST_KEYS
                or key in POPULATION_KEYS
                or key in DESCRIBES_THE_READ_KEYS
                or any(sub == known or sub.endswith("." + known) for known in DESCRIBES_THE_READ_PATHS)
                or key.endswith("_sha256")  # a checksum over whatever population the profile left
            ):
                continue
            if key not in cur:
                if path.rsplit(".", 1)[-1] in REMOVABLE_MAP_FIELDS:
                    continue  # a slot of a map, withheld whole
                out.append(f"{sub}<key removed>")
            elif key not in exp:
                out.append(f"{sub}<key added>")
            else:
                out.extend(withholding_violations(exp[key], cur[key], sub))
        return out
    if isinstance(exp, list) and isinstance(cur, list):
        if len(cur) == len(exp):
            return [v for e, c in zip(exp, cur, strict=True) for v in withholding_violations(e, c, f"{path}[*]")]
        # Fewer elements: each curated element must match, in order, a distinct exploratory element.
        i = 0
        for c in cur:
            while i < len(exp) and not _is_withholding(c, exp[i]):
                i += 1
            if i == len(exp):
                # Name the field that differs, not just the element: the closest exploratory element (fewest
                # violations) is the one this curated element was taken from, so its violations are the substitution.
                closest = min((withholding_violations(e, c, f"{path}[*]") for e in exp), key=len, default=[])
                return closest or [f"{path}[*]<element not in exploratory list>"]
            i += 1
        return []
    return [] if _same_modulo_placeholders(exp, cur) else [f"{path}<value changed>"]


#: A hidden ref inside a selection document is a placeholder token (guard PR-3), so a curated string that differs from
#: the exploratory one only where a token stands in a ref's place is a withholding, not a different value.
_TOKEN = re.compile(r"withheld:[1-9][0-9]*")
_GATED_REF = re.compile("(?:" + "|".join(ref_guard.GATED_TYPES) + ")_[a-z0-9]{26}")


def _same_modulo_placeholders(exp: Any, cur: Any) -> bool:
    if exp == cur:
        return True
    if isinstance(exp, str) and isinstance(cur, str):
        if cur == "":
            return _GATED_REF.search(exp) is not None  # free text that named a hidden ref is blanked whole
        masked = _TOKEN.sub("<ref>", cur)
        return masked != cur and masked == _GATED_REF.sub("<ref>", exp)
    return False


#: Routes whose default order sorts on the availability flags (``has_kinetics``, ``has_transition_state``). Those flags
#: follow the floor (D5), so a curated page can order the same records differently: it is compared by record, not by
#: position.
FLAG_SORTED_ROUTES = frozenset({"/api/v1/scientific/reactions/browse", "/api/v1/scientific/reactions/search"})


def order_independent(route_path: str, body: Any) -> Any:
    """``body`` with the ``records`` of a flag-sorted route put in a canonical order, for the withholding comparison."""
    if route_path in FLAG_SORTED_ROUTES and isinstance(body, dict) and isinstance(body.get("records"), list):
        return {**body, "records": sorted(body["records"], key=lambda r: str(r.get("reaction_entry_ref")))}
    return body
