"""Reading a reaction's atom map back (ADR 0011).

Deposits ``CH3 + H -> CH4`` through the real upload endpoint and reads it back
through ``/scientific/reaction-entries/{id}/full``, because the property under
test is a round trip: a consumer must be able to tell a mapped reaction from an
unmapped one, and a declared map from an inferred one, without knowing how
either was written.

The distinction is deliberately *not* behind an ``include`` token. A record
that reads identically whether or not anyone knows which atom is which is the
failure ADR 0011 exists to remove, and gating the answer behind a second
request would reintroduce it for every consumer that does not think to ask.
"""

from __future__ import annotations

import pytest

_XYZ_H = "1\nH\nH 0.0 0.0 0.0"
_XYZ_CH3 = (
    "4\nmethyl\n"
    "C  0.000  0.000  0.000\n"
    "H  1.080  0.000  0.000\n"
    "H -0.540  0.935  0.000\n"
    "H -0.540 -0.935  0.000"
)
_XYZ_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
_XYZ_TS = (
    "5\nTS for CH3 + H -> CH4\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.400"
)

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}


def _species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {
            "smiles": smiles,
            "charge": 0,
            "multiplicity": multiplicity,
        },
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [],
    }


def _bundle(atom_map: dict | None) -> dict:
    bundle: dict = {
        "species": [
            _species("ch3", "[CH3]", 2, _XYZ_CH3),
            _species("h", "[H]", 2, _XYZ_H),
            _species("ch4", "C", 1, _XYZ_CH4),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
        "transition_state": {
            "charge": 0,
            "multiplicity": 2,
            "geometry": {"key": "ts-geom", "xyz_text": _XYZ_TS},
            "calculation": {
                "key": "ts-opt",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "opt_converged": True,
            },
            "calculations": [
                {
                    "key": "ts-freq",
                    "type": "freq",
                    "geometry_key": "ts-geom",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "freq_n_imag": 1,
                    "freq_imag_freq_cm1": -1500.0,
                }
            ],
        },
    }
    if atom_map is not None:
        bundle["atom_map"] = atom_map
    return bundle


def _map(source: str = "declared", **overrides) -> dict:
    atom_map: dict = {
        "source": source,
        "ts_geometry_key": "ts-geom",
        "participants": [
            {
                "side": "reactant",
                "species_key": "ch3",
                "participant_index": 1,
                "geometry_key": "ch3-geom",
                "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4},
            },
            {
                "side": "reactant",
                "species_key": "h",
                "participant_index": 2,
                "geometry_key": "h-geom",
                "atom_to_ts": {1: 5},
            },
            {
                "side": "product",
                "species_key": "ch4",
                "participant_index": 1,
                "geometry_key": "ch4-geom",
                "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
            },
        ],
    }
    atom_map.update(overrides)
    return atom_map


def _upload(client, atom_map: dict | None) -> dict:
    resp = client.post("/api/v1/uploads/computed-reaction", json=_bundle(atom_map))
    assert resp.status_code == 201, resp.text[:800]
    return resp.json()


def _full(client, reaction_entry_id: int, *includes: str) -> dict:
    query = f"?include={','.join(includes)}" if includes else ""
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{reaction_entry_id}/full{query}"
    )
    assert resp.status_code == 200, resp.text[:800]
    return resp.json()


# ---------------------------------------------------------------------------


def test_a_mapped_reaction_is_distinguishable_without_a_second_request(client):
    result = _upload(client, _map(equivalent_map_count=6))
    body = _full(client, result["reaction_entry_id"])

    # No ``include`` token was passed, so the per-atom section is absent.
    # The badge below is the point of the test and is unconditional: whether
    # a reaction is mapped at all must never be behind a second request.
    assert "atom_map" not in body
    badges = body["reaction_entry"]["atom_maps"]
    assert len(badges) == 1
    assert badges[0]["source"] == "declared"
    assert badges[0]["equivalent_map_count"] == 6
    assert badges[0]["reactant_atoms_mapped"] == 5
    assert badges[0]["product_atoms_mapped"] == 5


def test_an_unmapped_reaction_is_distinguishable_without_a_second_request(client):
    result = _upload(client, None)
    body = _full(client, result["reaction_entry_id"])
    assert body["reaction_entry"]["atom_maps"] == []


def test_upload_response_warns_that_no_map_was_supplied(client):
    result = _upload(client, None)
    codes = {warning["code"] for warning in result["warnings"]}
    assert "reaction_atom_map_absent" in codes


def test_include_atom_map_returns_both_legs_atom_by_atom(client):
    result = _upload(client, _map())
    body = _full(client, result["reaction_entry_id"], "atom_map")

    assert len(body["atom_map"]) == 1
    detail = body["atom_map"][0]
    assert detail["source"] == "declared"
    assert len(detail["pairs"]) == 10

    reactant_leg = {
        (pair["participant_index"], pair["atom_index"], pair["ts_atom_index"])
        for pair in detail["pairs"]
        if pair["side"] == "reactant"
    }
    product_leg = {
        (pair["atom_index"], pair["ts_atom_index"])
        for pair in detail["pairs"]
        if pair["side"] == "product"
    }
    # The methyl's four atoms and the lone hydrogen are told apart by which
    # participant molecule they belong to, not by their index.
    assert reactant_leg == {
        (1, 1, 1),
        (1, 2, 2),
        (1, 3, 3),
        (1, 4, 4),
        (2, 1, 5),
    }
    assert product_leg == {(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)}

    # Every index is stated against the geometry it counts into.
    assert all(pair["geometry_ref"].startswith("geom_") for pair in detail["pairs"])
    assert detail["transition_state_geometry_ref"].startswith("geom_")
    assert {pair["element"] for pair in detail["pairs"]} == {"C", "H"}


def test_reactants_to_products_is_not_a_stored_leg(client):
    """Both legs run toward the saddle point; the composition is derived."""
    result = _upload(client, _map())
    detail = _full(client, result["reaction_entry_id"], "atom_map")["atom_map"][0]
    assert {pair["side"] for pair in detail["pairs"]} == {"reactant", "product"}
    # Composing the legs on the client's side recovers reactants -> products.
    by_ts_reactant = {
        pair["ts_atom_index"]: (pair["participant_index"], pair["atom_index"])
        for pair in detail["pairs"]
        if pair["side"] == "reactant"
    }
    by_ts_product = {
        pair["ts_atom_index"]: pair["atom_index"]
        for pair in detail["pairs"]
        if pair["side"] == "product"
    }
    composed = {
        by_ts_reactant[ts]: by_ts_product[ts] for ts in by_ts_reactant
    }
    assert composed == {(1, 1): 1, (1, 2): 2, (1, 3): 3, (1, 4): 4, (2, 1): 5}


@pytest.mark.parametrize(
    "source,note",
    [
        ("declared", None),
        ("inferred", "mapped by a maximum-common-substructure search"),
    ],
)
def test_provenance_survives_the_round_trip(client, source, note):
    """An inferred map never reads back as a declared one."""
    overrides = {} if note is None else {"note": note}
    result = _upload(client, _map(source=source, **overrides))
    body = _full(client, result["reaction_entry_id"], "atom_map")
    assert body["reaction_entry"]["atom_maps"][0]["source"] == source
    assert body["atom_map"][0]["source"] == source
    assert body["atom_map"][0]["note"] == note


def test_equivalent_map_count_reads_back_as_null_when_unclaimed(client):
    """Null is "no claim", not 1: symmetry makes a valid map non-unique."""
    result = _upload(client, _map())
    badge = _full(client, result["reaction_entry_id"])["reaction_entry"][
        "atom_maps"
    ][0]
    assert badge["equivalent_map_count"] is None


def test_internal_ids_are_hidden_by_default_and_refs_are_not(client):
    result = _upload(client, _map())
    detail = _full(client, result["reaction_entry_id"], "atom_map")["atom_map"][0]
    assert "transition_state_entry_id" not in detail
    assert "transition_state_geometry_id" not in detail
    assert detail["transition_state_entry_ref"].startswith("tse_")
    for pair in detail["pairs"]:
        assert "species_entry_id" not in pair
        assert "geometry_id" not in pair
        # Atom indices are not identifiers and must survive the id filter.
        assert isinstance(pair["atom_index"], int)
        assert isinstance(pair["ts_atom_index"], int)


def test_reaction_search_advertises_whether_a_reaction_is_mapped(client):
    """A search result already says whether the reaction can answer the question.

    Without this a consumer filtering for atom-mapped reactions has to fetch
    every candidate to find out that most of them cannot help.
    """
    mapped = _upload(client, _map())
    unmapped = _upload(client, None)

    for result, expected in ((mapped, True), (unmapped, False)):
        ref = _full(client, result["reaction_entry_id"])["reaction_entry"][
            "reaction_entry_ref"
        ]
        resp = client.get(
            f"/api/v1/scientific/reactions/search?reaction_entry_ref={ref}"
        )
        assert resp.status_code == 200, resp.text[:500]
        records = resp.json()["records"]
        assert len(records) == 1
        assert records[0]["availability"]["has_atom_map"] is expected


def test_atom_map_expansion_is_capped_like_every_other_section(
    client, monkeypatch
):
    """The cap applies regardless of how the section was requested.

    ``atom_map`` was the one ``/full`` expansion with no ceiling, which made it
    the way past a policy the other four sections all obey. The cap counts
    *pairs*, not maps: a reaction carries one map per saddle-point candidate
    and each holds a row per atom per leg, so the pairs are the leaf rows that
    can actually run away on a large transition state.
    """
    from app.api.config import settings

    result = _upload(client, _map())
    reaction_entry_id = result["reaction_entry_id"]

    # Ten pairs for CH3 + H -> CH4; a ceiling of nine is one too few.
    monkeypatch.setattr(settings, "max_full_atom_map_pairs_public", 9)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{reaction_entry_id}/full"
        "?include=atom_map"
    )
    assert resp.status_code == 422
    assert "query_too_expensive" in resp.json()["detail"]
    assert "atom_map_pairs" in resp.json()["detail"]

    # ``include=all`` is not a way around it either.
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{reaction_entry_id}/full"
        "?include=all"
    )
    assert resp.status_code == 422
    assert "query_too_expensive" in resp.json()["detail"]

    # And the section is served when it fits.
    monkeypatch.setattr(settings, "max_full_atom_map_pairs_public", 10)
    body = _full(client, reaction_entry_id, "atom_map")
    assert len(body["atom_map"][0]["pairs"]) == 10


def test_the_badge_counts_pairs_without_reading_them(client):
    """The unconditional badge must not cost a row per atom per leg.

    ``atom_maps`` is on every ``/full`` response whether or not anyone asked
    for the map, and the two counts it carries were being produced by loading
    every pair and calling ``len``. That made a field nobody requested scale
    with the size of the saddle point -- a large map is thousands of rows read,
    turned into Pydantic models and thrown away -- on the one section that
    cannot be turned off. Counts are what the badge needs, so counts are what
    the database is asked for.

    Asserted against the emitted SQL rather than a timing, because the
    property is "no pair row is read", not "it is fast".
    """
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    result = _upload(client, _map())
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def _pair_reads() -> list[str]:
        return [
            statement
            for statement in statements
            if "reaction_atom_map_pair" in statement
            and "count(" not in statement.lower()
        ]

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        body = _full(client, result["reaction_entry_id"])
        badge_statements = _pair_reads()

        statements.clear()
        _full(client, result["reaction_entry_id"], "atom_map")
        expanded_statements = _pair_reads()
    finally:
        event.remove(Engine, "before_cursor_execute", _record)

    # The badge still says everything it said before.
    badges = body["reaction_entry"]["atom_maps"]
    assert len(badges) == 1
    assert badges[0]["reactant_atoms_mapped"] == 5
    assert badges[0]["product_atoms_mapped"] == 5

    assert badge_statements == [], badge_statements
    # Guard the guard: ``include=atom_map`` does read the pairs, so the
    # assertion above is detecting something rather than passing vacuously.
    assert expanded_statements != []


def test_the_atom_map_badge_is_not_subject_to_the_pair_cap(client, monkeypatch):
    """The header badge carries no pairs, so it cannot be capped away.

    Whether a reaction is mapped at all is the one thing ADR 0011 refuses to
    put behind a second request; a cap that hid it would reintroduce exactly
    the indistinguishability the decision removes.
    """
    from app.api.config import settings

    result = _upload(client, _map())
    monkeypatch.setattr(settings, "max_full_atom_map_pairs_public", 1)

    body = _full(client, result["reaction_entry_id"])
    assert "atom_map" not in body
    assert len(body["reaction_entry"]["atom_maps"]) == 1
