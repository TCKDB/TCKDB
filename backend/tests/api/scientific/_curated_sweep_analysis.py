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
REQUEST_KEYS = frozenset({"request", "review_visibility", "pagination", "review_summary", "review_status_by_ref"})

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
        # ADR 0023 (step 3): the counts and flags of a returned record's linked evidence follow the evidence rule, so a
        # rejected or deprecated calculation is not counted. Each is pinned against the withheld calculation in
        # test_curated_evidence_counts_and_levels.py, because this comparator cannot hold a count to "equal or null".
        "geometry_count",
        "optimization_chain_count",
        "has_rotor_scans",
        "has_frequencies",
        "has_opt_calculation",
        "has_freq_calculation",
        "has_sp_calculation",
        # A candidate's ordinal among the population's own (guard PR-3): it stands in for the database id as the last
        # tie-break. The population is the profile's, so the same record has another ordinal under curated.
        "id_rank",
    }
)

#: Counts and flags of a returned record's linked EVIDENCE (ADR 0023, step 3). They are the evidence rule's, not the record
#: floor's, so the sweep holds them to it when it is given a ``mode``: in the unlisted state (linked evidence that is merely
#: unreviewed) a count or flag EQUALS the exploratory one; in the withheld state (rejected / deprecated) it is never higher
#: (a number is <=, a flag may only go True -> False). Without a ``mode`` they are skipped like the population keys.
EVIDENCE_COUNT_KEYS = frozenset(
    {
        "calculation_count",
        "has_calculations",
        "has_geometries",
        "has_validation_evidence",
        "has_opt",
        "has_freq",
        "has_sp",
        "has_irc",
        "has_path_search",
        "has_geometry_validation",
        "has_scf_stability",
        "source_calculation_count",
        "has_source_calculations",
        "geometry_count",
        "optimization_chain_count",
        "has_rotor_scans",
        "has_frequencies",
        "has_opt_calculation",
        "has_freq_calculation",
        "has_sp_calculation",
        "has_statmech_provenance",
        "has_transition_state_provenance",
    }
)

#: Evidence count keys that legitimately differ in the UNLISTED state on a route because they also rest on a RECORD the floor
#: removes (so they are "<=", not "==" there). Per route path, with the reason. Grow this only with a reason.
RELAXED_UNLISTED: dict[str, dict[str, str]] = {
    **{
        path: {"source_calculation_count": "a network's count adds up the source calculations of the solves that clear the floor"}
        for path in (
            "/api/v1/scientific/networks/browse",
            "/api/v1/scientific/networks/search",
            "/api/v1/scientific/networks/{network_ref_or_id}",
        )
    },
    **{
        path: {"has_path_search": "a path search counts through the transition state and entry, which are records (floored)"}
        for path in ("/api/v1/scientific/reactions/browse", "/api/v1/scientific/reactions/search")
    },
    **{
        path: {"calculation_count": "a species entry's availability previews the calculation record list (record floor)"}
        for path in ("/api/v1/scientific/species/browse", "/api/v1/scientific/species/search")
    },
    "/api/v1/scientific/transition-states/{transition_state_ref_or_id}": {
        "calculation_count": "the concept pools the entries that clear the floor (entries are records)",
        "has_geometries": "the concept pools the entries that clear the floor (entries are records)",
    },
}

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
#: Maps keyed by calculation type that are only a map at this exact path (a bare ``software`` field elsewhere is a model).
#: The type of a hidden calculation is removed from them, as from ``levels_of_theory`` (owner decision D5).
REMOVABLE_MAP_PATHS = ("evidence_summary.software",)

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


#: Direct identity keys of a selection candidate (the node under ``candidates[*]``): the ref that says which record it is.
CANDIDATE_IDENTITY_KEYS = frozenset({"thermo_ref", "kinetics_ref", "candidate_ref", "unit_ref", "solve_ref"})
CANDIDATE_PATHS = frozenset({"candidates[*]", "result.candidates[*]"})


def route_subject_identities(app) -> dict[tuple[str, str], tuple[str, ...] | None]:
    """``(method, path) -> identity path`` of the subject model of every scientific route that returns ``record`` / ``records``.

    Read from the app's own response models and the guard's registry (:data:`ref_guard.SUBJECT_IDENTITY`), so the sweep
    and the guard's subject net agree on what a subject's identity is. ``None`` marks an ungated subject (no check).
    """
    import typing

    from pydantic import BaseModel

    def models(annotation):
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield annotation
        for arg in typing.get_args(annotation):
            yield from models(arg)

    out: dict[tuple[str, str], tuple[str, ...] | None] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/scientific"):
            continue
        for envelope in models(getattr(route, "response_model", None)):
            for field_name in ("record", "records"):
                field = envelope.model_fields.get(field_name)
                if field is None:
                    continue
                for model in models(field.annotation):
                    identity = ref_guard.SUBJECT_IDENTITY.get(model.__name__)
                    for method in getattr(route, "methods", ()) or ():
                        out[(method, path)] = identity
    return out


def _at(node: Any, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def subject_leaks(
    text: str, hidden_refs: dict[str, str], identity: tuple[str, ...] | None = None
) -> set[tuple[str, str]]:
    """``(hidden key, json path)`` where an UNLISTED record is the subject of the answer (ADR 0023).

    A record below the approved floor may be named nested, badged; it must never BE a LISTED answer: not a row of ``records``,
    not a candidate. The ``record`` of a detail read is not checked: since step 7 it serves anything but the withheld set. The subject's identity is the path the guard's registry declares for its model
    (``identity``, from :func:`route_subject_identities`); a ref anywhere else on the row is linked evidence and is allowed.
    A candidate of a selection is checked by :data:`CANDIDATE_IDENTITY_KEYS`.
    """
    present = {k: r for k, r in hidden_refs.items() if r in text}
    if not present:
        return set()
    kind, obj = parse_body(text)
    if kind != "json":
        return set()
    by_ref = {r: k for k, r in present.items()}
    found: set[tuple[str, str]] = set()
    for path, node in _dicts(obj):
        if identity is not None and path == "records[*]":
            value = _at(node, identity)
            if isinstance(value, str) and value in by_ref:
                found.add((by_ref[value], f"{path}.{'.'.join(identity)}"))
        elif path in CANDIDATE_PATHS:
            for key in CANDIDATE_IDENTITY_KEYS & set(node):
                if isinstance(node[key], str) and node[key] in by_ref:
                    found.add((by_ref[node[key]], f"{path}.{key}"))
    return found


def subject_rows(text: str, identity: tuple[str, ...] | None) -> list[tuple[str | None, Any]]:
    """``(identity ref, row)`` for every LISTED subject row (``records[*]``) of a JSON body.

    The ``record`` of a detail read is not a row the floor can drop (step 7), so it is not returned.
    """
    kind, obj = parse_body(text)
    return _rows_of(kind, obj, identity)


def _rows_of(kind: str, obj: Any, identity: tuple[str, ...] | None) -> list[tuple[str | None, Any]]:
    if kind != "json" or not isinstance(obj, dict):
        return []
    rows: list[Any] = []
    if isinstance(obj.get("records"), list):
        rows.extend(r for r in obj["records"] if isinstance(r, dict))
    out = []
    for row in rows:
        ref = _at(row, identity) if identity is not None else None
        out.append((ref if isinstance(ref, str) else None, row))
    return out


def refs_in(node: Any, found: set[str] | None = None) -> set[str]:
    """Every string anywhere in ``node`` (the caller matches them against the refs it cares about)."""
    found = set() if found is None else found
    if isinstance(node, dict):
        for value in node.values():
            refs_in(value, found)
    elif isinstance(node, list):
        for item in node:
            refs_in(item, found)
    elif isinstance(node, str):
        found.add(node)
    return found


def refs_outside_floored_rows(text: str, identity: tuple[str, ...] | None, floored: set[str]) -> set[str]:
    """Strings of a body that sit outside every listed row whose own identity ref is in ``floored``.

    A row below the floor is the floor's to drop (its whole subtree goes with it), so what is nested inside it is not
    evidence a curated read owes. Everything else in the body (other rows, the envelope) is kept.
    """
    kind, obj = parse_body(text)
    if kind != "json" or not isinstance(obj, dict):
        return refs_in(obj) if kind == "json" else set()
    dropped = {id(row) for ref, row in _rows_of(kind, obj, identity) if ref in floored}
    out: set[str] = set()
    for key, value in obj.items():
        if key == "records" and isinstance(value, list):
            for row in value:
                if id(row) not in dropped:
                    refs_in(row, out)
            continue
        refs_in(value, out)
    return out


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


def _is_withholding(cur: Any, exp: Any, mode: str | None = None, relaxed: frozenset[str] = frozenset()) -> bool:
    return not withholding_violations(exp, cur, mode=mode, relaxed=relaxed)


def _evidence_count_violation(exp: Any, cur: Any, mode: str) -> bool:
    if isinstance(exp, bool) or isinstance(cur, bool):
        return exp != cur if mode == "equal" else bool(cur) and not bool(exp)
    if isinstance(exp, (int, float)) and isinstance(cur, (int, float)):
        return exp != cur if mode == "equal" else cur > exp
    return exp != cur


def withholding_violations(
    exp: Any, cur: Any, path: str = "", *, mode: str | None = None, relaxed: frozenset[str] = frozenset()
) -> list[str]:
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
            if mode is not None and key in EVIDENCE_COUNT_KEYS and key in exp and key in cur:
                key_mode = "le" if key in relaxed else mode
                if cur[key] is not None and _evidence_count_violation(exp[key], cur[key], key_mode):
                    out.append(f"{sub}<evidence count {key_mode}: {exp[key]!r} -> {cur[key]!r}>")
                continue
            if (
                key in REQUEST_KEYS
                or key in POPULATION_KEYS
                or key in DESCRIBES_THE_READ_KEYS
                or any(sub == known or sub.endswith("." + known) for known in DESCRIBES_THE_READ_PATHS)
                or key.endswith("_sha256")  # a checksum over whatever population the profile left
            ):
                continue
            if key not in cur:
                if path.rsplit(".", 1)[-1] in REMOVABLE_MAP_FIELDS or path.endswith(REMOVABLE_MAP_PATHS):
                    continue  # a slot of a map, withheld whole
                out.append(f"{sub}<key removed>")
            elif key not in exp:
                out.append(f"{sub}<key added>")
            else:
                out.extend(withholding_violations(exp[key], cur[key], sub, mode=mode, relaxed=relaxed))
        return out
    if isinstance(exp, list) and isinstance(cur, list):
        if len(cur) == len(exp):
            return [
                v for e, c in zip(exp, cur, strict=True) for v in withholding_violations(e, c, f"{path}[*]", mode=mode, relaxed=relaxed)
            ]
        # Fewer elements: each curated element must match, in order, a distinct exploratory element.
        i = 0
        for c in cur:
            while i < len(exp) and not _is_withholding(c, exp[i], mode, relaxed):
                i += 1
            if i == len(exp):
                # Name the field that differs, not just the element: the closest exploratory element (fewest
                # violations) is the one this curated element was taken from, so its violations are the substitution.
                closest = min(
                    (withholding_violations(e, c, f"{path}[*]", mode=mode, relaxed=relaxed) for e in exp), key=len, default=[]
                )
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
