"""Merged-away species and entries are kept and forward (DR-0038), seen from the API.

A row that was merged into another keeps its public ref and its history; a ref to it resolves one hop to the survivor
and the response says so; listings and searches show the live row only; an upload resolves to the survivor; the database
refuses a new write onto the kept row. Every conformer an entry has is a stereo source, and every upload route that
resolves a species entry warns when a stereo element stays unknown.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models.common import StereoResolution
from app.db.models.species import Species, SpeciesEntry
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.services.public_refs import PREFIXES, make_content_ref
from app.services.species_resolution import resolve_species_entry
from app.services.upload_reconciliation import reconcile_species_entry, stereo_unresolved_warnings
from tests.api._merged_world import MARKED_INCHI
from tests.services.scientific_read._factories import make_thermo_scalar
from tests.services.test_stereo_descriptor import embed_xyz

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_TRANS = embed_xyz("C/C=C/C")
_CIS = embed_xyz("C/C=C\\C")


def _resolve(db_session, smiles: str, **kwargs):
    entry = resolve_species_entry(db_session, SpeciesEntryIdentityPayload(smiles=smiles, charge=0, multiplicity=1, **kwargs))
    db_session.flush()
    return entry


def _merged_pair(db_session, *, holds_key: bool = False):
    """A live 2-butene species and entry, and the kept rows of a marked spelling merged into them.

    By default the kept rows are what the migration leaves: the OLD marked SMILES, InChIKey and label, not the survivor's
    values. ``holds_key=True`` is the other shape the migration can leave (the loser was the row that already held the
    key's SMILES): the kept rows then carry the SAME key as their survivors, which the partial unique indexes allow, with
    ids that sort before and heap positions that sit before the survivors', so a lookup that forgot to skip merged-away
    rows would find the wrong one.
    """
    survivor = _resolve(db_session, "C/C=C/C")
    species = survivor.species
    loser_species = Species(
        **({"id": -species.id} if holds_key else {}),
        kind=species.kind,
        smiles=species.smiles if holds_key else "C/C=C/C",
        inchi_key=species.inchi_key if holds_key else MARKED_INCHI,
        charge=0,
        multiplicity=1,
        stereo_kind=species.stereo_kind,
        merged_into_species_id=species.id,
        # The ref the row had under the old key, which the kept row still answers to.
        public_ref=make_content_ref(PREFIXES["Species"], "species:smiles=C/C=C/C;charge=0;multiplicity=1;stereo_kind=ez_isomer"),
    )
    db_session.add(loser_species)
    db_session.flush()
    loser = SpeciesEntry(
        **({"id": -survivor.id} if holds_key else {}),
        species_id=species.id,
        mol="C/C=C/C",
        stereo_label="E",
        stereo_descriptor=survivor.stereo_descriptor,
        stereo_resolution=StereoResolution.resolved,
        merged_into_species_entry_id=survivor.id,
    )
    db_session.add(loser)
    db_session.flush()
    # Rewrite the survivors so their current row versions sit AFTER the kept rows in the heap: a lookup that forgot to
    # skip merged-away rows would otherwise be rescued by returning the physically first match.
    db_session.execute(text("UPDATE species SET inchi_key = inchi_key WHERE id = :i"), {"i": species.id})
    db_session.execute(text("UPDATE species_entry SET kind = kind WHERE id = :i"), {"i": survivor.id})
    db_session.expire_all()
    return survivor, loser, loser_species


def test_a_ref_to_a_merged_entry_resolves_to_the_survivor_and_the_response_says_so(client: TestClient, db_session) -> None:
    survivor, loser, _ = _merged_pair(db_session)
    make_thermo_scalar(db_session, species_entry=survivor)
    db_session.flush()

    via_survivor = client.get(f"/api/v1/scientific/species-entries/{survivor.public_ref}/thermo")
    via_merged = client.get(f"/api/v1/scientific/species-entries/{loser.public_ref}/thermo")
    assert via_survivor.status_code == via_merged.status_code == 200, via_merged.text
    assert via_survivor.json()["request"]["merged_refs"] == {}
    assert via_merged.json()["request"]["merged_refs"] == {loser.public_ref: survivor.public_ref}
    assert [r["thermo_ref"] for r in via_merged.json()["records"]] == [r["thermo_ref"] for r in via_survivor.json()["records"]]
    assert via_merged.json()["records"], "the survivor holds the results, so the read has something to show"


def test_a_merged_species_and_entry_are_not_listed_beside_their_survivors(client: TestClient, db_session) -> None:
    survivor, loser, loser_species = _merged_pair(db_session)
    response = client.get("/api/v1/scientific/species/search", params={"smiles": "C/C=C/C", "include_rejected": True})
    assert response.status_code == 200, response.text
    records = response.json()["records"]
    assert [r["species_ref"] for r in records] == [survivor.species.public_ref]
    assert {e["species_entry_ref"] for r in records for e in r["entries"]} == {survivor.public_ref}
    assert loser.public_ref not in response.text and loser_species.public_ref not in response.text


def test_exact_structure_search_returns_the_survivor_only(client: TestClient, db_session) -> None:
    survivor, loser, _ = _merged_pair(db_session)
    response = client.get(
        "/api/v1/scientific/species/structure-search",
        params={"mode": "exact", "query_smiles": "CC=CC", "include_rejected": True},
    )
    assert response.status_code == 200, response.text
    refs = {r["species_entry_ref"] for r in response.json()["records"]}
    assert survivor.public_ref in refs and loser.public_ref not in refs


@pytest.mark.parametrize("holds_key", [False, True], ids=["old_values", "holds_the_key"])
def test_an_upload_resolves_to_the_survivor_not_the_kept_row(db_session, holds_key: bool) -> None:
    survivor, loser, loser_species = _merged_pair(db_session, holds_key=holds_key)
    again = _resolve(db_session, "C/C=C/C")
    assert again.id == survivor.id and again.species_id == survivor.species_id
    # By id, a merged entry stands for its survivor.
    from app.services.species_resolution import resolve_species_entry_reference

    assert resolve_species_entry_reference(db_session, species_entry_id=loser.id).id == survivor.id


def test_the_database_refuses_a_new_write_onto_a_merged_row(db_session) -> None:
    _survivor, loser, loser_species = _merged_pair(db_session)
    for sql, params in (
        ("INSERT INTO conformer_group (species_entry_id, label) VALUES (:e, 'late')", {"e": loser.id}),
        (
            "INSERT INTO species_entry (species_id, stereo_descriptor, stereo_resolution) "
            "VALUES (:s, 'Z', CAST('resolved' AS stereo_resolution))",
            {"s": loser_species.id},
        ),
    ):
        with pytest.raises(DBAPIError, match="merged-away"):
            with db_session.begin_nested():
                db_session.execute(text(sql), params)


def test_a_merge_is_one_hop_never_a_chain(db_session) -> None:
    survivor, loser, _ = _merged_pair(db_session)
    third = _resolve(db_session, "C/C=C\\C")
    with pytest.raises(DBAPIError, match="itself merged|merged into"):
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE species_entry SET merged_into_species_entry_id = :l WHERE id = :t"), {"l": loser.id, "t": third.id}
            )
    with pytest.raises(DBAPIError, match="cannot itself be merged|merged into"):
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE species_entry SET merged_into_species_entry_id = :t WHERE id = :s"),
                {"t": third.id, "s": survivor.id},
            )


def test_every_foreign_key_into_the_identity_tables_has_its_trigger(db_session) -> None:
    """Enumerated from the database: a table added later that names a species, entry or reaction needs the guard too."""
    rows = db_session.execute(
        text(
            "SELECT c.conrelid::regclass::text AS tbl, a.attname AS col FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
            "WHERE c.contype = 'f' AND array_length(c.conkey, 1) = 1 "
            "AND c.confrelid = ANY(ARRAY['species'::regclass, 'species_entry'::regclass, 'chem_reaction'::regclass]) "
            "AND a.attname NOT LIKE 'merged_into_%'"
        )
    ).all()
    guarded = {
        (r.tbl, r.arg)
        for r in db_session.execute(
            text(
                "SELECT c.relname AS tbl, (regexp_match(pg_get_triggerdef(t.oid), $$tckdb_refuse_merged_target\\('[a-z_]+', '([a-z_]+)'$$))[1] AS arg "
                "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal AND t.tgname LIKE 'trg_mt_%'"
            )
        )
    }
    missing = {(r.tbl, r.col) for r in rows} - guarded
    assert not missing, f"no merged-target trigger on {sorted(missing)}"
    assert len(rows) > 15


# ---------------------------------------------------------------------------
# B5: every conformer is a stereo source
# ---------------------------------------------------------------------------


def _conformer(key: str, xyz: str) -> dict:
    return {
        "key": key,
        "geometry": {"xyz_text": xyz},
        "primary_calculation": {
            "key": f"opt_{key}",
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "opt_result": {"converged": True},
        },
    }


def _bundle(smiles: str, *geometries: str, label: str | None = None) -> dict:
    identity = {"smiles": smiles, "charge": 0, "multiplicity": 1}
    if label is not None:
        identity["stereo_label"] = label
    return {"species_entry": identity, "conformers": [_conformer(f"c{i}", xyz) for i, xyz in enumerate(geometries)]}


def test_a_second_conformer_deposited_as_the_other_isomer_is_refused(client: TestClient, db_session) -> None:
    response = client.post("/api/v1/uploads/computed-species", json=_bundle("CC=CC", _TRANS, _CIS))
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "species_stereo_contradiction"
    assert body["context"]["sources"] == ["conformer geometry 1", "conformer geometry 2"]
    assert db_session.scalar(select(Species.id).where(Species.smiles == "CC=CC")) is None, "nothing was stored"


def test_rotamers_of_one_configuration_are_one_entry(client: TestClient, db_session) -> None:
    rotamers = [embed_xyz("CC/C=C/C", seed=seed) for seed in (3, 11, 29)]
    response = client.post("/api/v1/uploads/computed-species", json=_bundle("CCC=CC", *rotamers))
    assert response.status_code == 201, response.text
    entry = db_session.get(SpeciesEntry, response.json()["species_entry_id"])
    assert entry.stereo_descriptor == "E"


# ---------------------------------------------------------------------------
# N5: every route that resolves a species entry warns about an unresolved element
# ---------------------------------------------------------------------------

_UNRESOLVED = "species_entry_stereo_unresolved"


def _codes(response) -> list[str]:
    assert response.status_code in (200, 201), response.text
    return [w["code"] for w in response.json()["warnings"]]


def test_thermo_upload_warns(client: TestClient) -> None:
    body = {
        "species_entry": {"smiles": "CC=CC", "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "h298_kj_mol": -11.0,
        "enthalpy_reference_kind": "formation_298k",
    }
    response = client.post("/api/v1/uploads/thermo", json=body)
    assert _UNRESOLVED in _codes(response)
    body["species_entry"]["smiles"] = "C/C=C/C"
    assert _UNRESOLVED not in _codes(client.post("/api/v1/uploads/thermo", json=body))


def test_computed_species_upload_warns_only_when_no_geometry_resolves_it(client: TestClient) -> None:
    # The geometry resolves the double bond, so there is nothing to warn about.
    assert _UNRESOLVED not in _codes(client.post("/api/v1/uploads/computed-species", json=_bundle("CC=CC", _TRANS)))


@pytest.mark.parametrize("smiles", ["CC=CC", "CC(O)CC", "C/C=C/C=CC"])
def test_the_shared_reconciliation_warns_for_every_unresolved_entry(smiles: str) -> None:
    payload = SpeciesEntryIdentityPayload(smiles=smiles, charge=0, multiplicity=1)
    assert [w.code for w in reconcile_species_entry(payload) if w.code == _UNRESOLVED] == [_UNRESOLVED]
    assert stereo_unresolved_warnings(payload)[0].field == "species_entry"


@pytest.mark.parametrize("smiles", ["C/C=C/C", "CCO", "C[C@H](O)CC"])
def test_resolved_and_achiral_entries_do_not_warn(smiles: str) -> None:
    payload = SpeciesEntryIdentityPayload(smiles=smiles, charge=0, multiplicity=1)
    assert stereo_unresolved_warnings(payload) == []


def test_a_request_submit_would_refuse_earns_no_warning() -> None:
    payload = SpeciesEntryIdentityPayload(smiles="C/C=C/C", charge=0, multiplicity=1, stereo_label="Z")
    assert stereo_unresolved_warnings(payload) == []


def test_every_request_warning_function_reaches_the_stereo_check() -> None:
    """The job-kind table is the list of routes that persist a species entry; each must produce the warning.

    Built from minimal duck-typed requests so a function that stops calling the shared check fails here.
    """
    from types import SimpleNamespace

    from app.services import upload_request_warnings as w

    identity = SpeciesEntryIdentityPayload(smiles="CC=CC", charge=0, multiplicity=1)
    network = SimpleNamespace(species_links=[SimpleNamespace(species_entry=identity)])
    assert [(x.code, x.field) for x in w._network_species_stereo_warnings(network)] == [
        (_UNRESOLVED, "species_links[0].species_entry")
    ]
    bundle = [SimpleNamespace(species_entry=identity, conformers=[])]
    assert [(x.code, x.field) for x in w._species_with_conformers_stereo_warnings(bundle, prefix="species")] == [
        (_UNRESOLVED, "species[0].species_entry")
    ]


# ---------------------------------------------------------------------------
# N6: the bundle export and the dry run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("smiles", ["C/C=C/C", "CC=CC", "C/C=C/C=CC", "C[C@H](O)/C=C/C"])
def test_the_export_emits_the_descriptor_and_a_reimport_lands_on_the_same_entry(db_session, smiles: str) -> None:
    from app.services.contribution_bundle_export import _species_entry_payload

    entry = _resolve(db_session, smiles)
    payload = _species_entry_payload(entry)
    assert payload["stereo_label"] == entry.stereo_descriptor
    again = resolve_species_entry(db_session, SpeciesEntryIdentityPayload(**payload))
    assert again.id == entry.id


def test_the_dry_run_predicts_what_submit_does(db_session) -> None:
    """The preview uses the lookup and the stereo resolution submit uses, so it names the entry submit would reuse."""
    from app.services.contribution_bundle_dry_run import _preview_species_entry_identity

    entry = _resolve(db_session, "C/C=C/C")
    for smiles, expected in (("CC=CC", "would_create"), ("C/C=C/C", "would_reuse"), ("C/C=C\\C", "would_create")):
        items, _species = _preview_species_entry_identity(
            db_session,
            payload=SpeciesEntryIdentityPayload(smiles=smiles, charge=0, multiplicity=1),
            local_ref="x",
        )
        assert items[1].action.value == expected, (smiles, items[1])
    # A spin state that is not stored is not the stored species.
    items, species = _preview_species_entry_identity(
        db_session, payload=SpeciesEntryIdentityPayload(smiles="C/C=C/C", charge=0, multiplicity=3), local_ref="x"
    )
    assert species is None and items[0].action.value == "would_create"
    # A contradiction is an error item, as it is a 422 on submit.
    items, _species = _preview_species_entry_identity(
        db_session,
        payload=SpeciesEntryIdentityPayload(smiles="C/C=C/C", charge=0, multiplicity=1, stereo_label="Z"),
        local_ref="x",
    )
    assert items[-1].action.value == "error"
    assert entry.stereo_descriptor == "E"


def test_the_legacy_detail_reads_answer_a_merged_row_as_its_survivor_and_say_so(client: TestClient, db_session) -> None:
    survivor, loser, loser_species = _merged_pair(db_session)
    make_thermo_scalar(db_session, species_entry=survivor)
    db_session.flush()
    header = "X-TCKDB-Merged-From"

    species = client.get(f"/api/v1/species/{loser_species.id}")
    assert species.status_code == 200, species.text
    assert species.json()["id"] == survivor.species_id and species.headers[header] == loser_species.public_ref

    entry = client.get(f"/api/v1/species-entries/{loser.id}")
    assert entry.status_code == 200, entry.text
    assert entry.json()["id"] == survivor.id and entry.headers[header] == loser.public_ref

    thermo = client.get(f"/api/v1/species-entries/{loser.id}/thermo")
    assert thermo.status_code == 200 and thermo.json(), "the results moved to the survivor, so the kept id shows them"
    assert thermo.headers[header] == loser.public_ref

    live = client.get(f"/api/v1/species-entries/{survivor.id}")
    assert header not in live.headers


def test_a_merged_row_cannot_be_edited(db_session) -> None:
    _survivor, loser, loser_species = _merged_pair(db_session)
    for sql, params in (
        ("UPDATE species SET inchi_key = 'X' WHERE id = :i", {"i": loser_species.id}),
        ("UPDATE species_entry SET stereo_smiles = 'C' WHERE id = :i", {"i": loser.id}),
    ):
        with pytest.raises(DBAPIError, match="cannot be edited"):
            with db_session.begin_nested():
                db_session.execute(text(sql), params)


_NOT_REPRESENTED = "species_entry_stereo_not_represented"


def _warnings(response) -> dict[str, str]:
    assert response.status_code in (200, 201), response.text
    return {w["code"]: w["message"] for w in response.json()["warnings"]}


def _thermo_body(smiles: str) -> dict:
    return {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "h298_kj_mol": -11.0,
        "enthalpy_reference_kind": "formation_298k",
    }


def test_an_allene_deposit_is_accepted_and_the_warning_names_the_lumping(client: TestClient) -> None:
    warnings = _warnings(client.post("/api/v1/uploads/thermo", json=_thermo_body("CC=C=CC")))
    assert _UNRESOLVED not in warnings, "no further data would settle an axis, so this is not a request for more"
    assert (
        "axial stereo (Ra/Sa) is not yet represented: both mirror-image forms of this allene are stored in one entry"
        in warnings[_NOT_REPRESENTED]
    )


def test_a_butatriene_deposit_is_accepted_and_the_warning_says_cis_trans_is_not_represented(client: TestClient) -> None:
    warnings = _warnings(client.post("/api/v1/uploads/thermo", json=_thermo_body("CC=C=C=CC")))
    assert "cis/trans stereo across this cumulene is not yet represented" in warnings[_NOT_REPRESENTED]
    assert _UNRESOLVED not in warnings


def test_an_allene_with_an_unknown_centre_says_both(client: TestClient) -> None:
    warnings = _warnings(client.post("/api/v1/uploads/thermo", json=_thermo_body("CC=C=CC(C)O")))
    assert "1 other stereo element(s) are also unknown" in warnings[_NOT_REPRESENTED]


def test_a_letter_on_an_allene_is_refused_before_anything_is_stored(client: TestClient) -> None:
    body = _thermo_body("CC=C=CC")
    body["species_entry"]["stereo_label"] = "R"
    response = client.post("/api/v1/uploads/thermo", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "species_stereo_label_invalid"
    assert "axial stereo (Ra/Sa)" in response.json()["detail"] and "not supported yet" in response.json()["detail"]
