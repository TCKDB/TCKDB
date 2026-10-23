"""No export picks a record.

The many-record formats export every qualifying record; CHEMKIN, which holds one record per species or reaction, takes
the records the request names or refuses with a structured 422. Each test names what it kills.
"""

from __future__ import annotations

import io
import json
import zipfile

from app.db.models.common import PhaseKind, RecordReviewStatus, SubmissionRecordType
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)

EXPORT = "/api/v1/scientific/export"


def _approved_thermo(db_session, species_entry, h298=-12.3):
    thermo = make_thermo_scalar(db_session, species_entry=species_entry, h298_kj_mol=h298)
    thermo.phase = PhaseKind.gas
    thermo.reference_pressure_bar = 1.01325
    attach_thermo_nasa(db_session, thermo=thermo)
    set_review(
        db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id, status=RecordReviewStatus.approved
    )
    return thermo


def _reaction(db_session):
    """A reaction whose reactant has TWO approved thermo records; everything else has one."""
    entries = {}
    thermos = {}
    for key, smiles in (("a", "C"), ("c", "CO")):
        species = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key("NP"))
        entries[key] = make_species_entry(db_session, species)
        thermos[key] = _approved_thermo(db_session, entries[key])
    chem = make_chem_reaction(db_session, reactants=[entries["a"].species], products=[entries["c"].species])
    reaction_entry = make_reaction_entry(
        db_session, reaction=chem, reactant_entries=[entries["a"]], product_entries=[entries["c"]]
    )
    kinetics = make_kinetics(db_session, reaction_entry=reaction_entry)
    set_review(
        db_session, record_type=SubmissionRecordType.kinetics, record_id=kinetics.id, status=RecordReviewStatus.approved
    )
    extra = _approved_thermo(db_session, entries["a"], h298=-99.0)
    return reaction_entry, entries, thermos, extra


def test_ndjson_export_lists_every_thermo_record_and_says_so(client, db_session, login_as, _api_curator_user):
    """Kills: an export that still keeps one thermo per species (``collapse=first`` was its default)."""
    reaction_entry, entries, thermos, extra = _reaction(db_session)
    login_as(_api_curator_user)

    resp = client.get(f"{EXPORT}/ndjson", params={"reaction_ref": reaction_entry.public_ref})

    assert resp.status_code == 200, resp.text
    parsed = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    assert parsed[0]["format"] == {"name": "tckdb.export", "version": 2}
    assert parsed[0]["records"] == "all_matching"
    assert "collapse" not in parsed[0] and "selection_policy" not in parsed[0]
    species = {p["species_entry_ref"]: p for p in parsed if p["record_type"] == "species"}
    refs = [t["thermo_ref"] for t in species[entries["a"].public_ref]["thermos"]]
    assert sorted(refs) == sorted([thermos["a"].public_ref, extra.public_ref])


def test_every_export_refuses_collapse_and_selection_policy(client, db_session, login_as, _api_curator_user):
    """Kills: ``?collapse=first`` quietly accepted and ignored."""
    reaction_entry, *_ = _reaction(db_session)
    login_as(_api_curator_user)
    seed = {"reaction_ref": reaction_entry.public_ref}

    for params in ({"collapse": "first"}, {"selection_policy": "latest"}):
        resp = client.get(f"{EXPORT}/ndjson", params={**seed, **params})
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "browse_parameter_removed"
    for extra in ({"collapse": "first"}, {"selection_policy": "latest"}):
        resp = client.post(f"{EXPORT}/chemkin", json={"seed": {"reaction_refs": [reaction_entry.public_ref]}, **extra})
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "browse_parameter_removed"


def test_chemkin_refuses_to_pick_and_names_the_ambiguous_species_with_its_candidates(
    client, db_session, login_as, _api_curator_user
):
    """Kills: CHEMKIN writing the first of several thermo records as if it were the only one."""
    reaction_entry, entries, thermos, extra = _reaction(db_session)
    login_as(_api_curator_user)

    resp = client.post(f"{EXPORT}/chemkin", json={"seed": {"reaction_refs": [reaction_entry.public_ref]}})

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "export_selection_ambiguous"
    (ambiguous,) = body["context"]["ambiguous"]
    assert ambiguous["subject_ref"] == entries["a"].public_ref
    assert ambiguous["subject_kind"] == "species_entry"
    assert ambiguous["record_kind"] == "thermo"
    assert sorted(ambiguous["candidate_refs"]) == sorted([thermos["a"].public_ref, extra.public_ref])
    assert body["context"]["resolve_with"] == ["filters", "select_endpoint", "explicit_refs"]
    # No generated text, no zip: a refusal is the whole response.
    assert resp.headers["content-type"].startswith("application/json")


def test_chemkin_exports_the_named_record_and_the_manifest_says_it_was_named(
    client, db_session, login_as, _api_curator_user
):
    reaction_entry, entries, thermos, extra = _reaction(db_session)
    login_as(_api_curator_user)

    resp = client.post(
        f"{EXPORT}/chemkin",
        json={"seed": {"reaction_refs": [reaction_entry.public_ref]}, "thermo_refs": [extra.public_ref]},
    )

    assert resp.status_code == 200, resp.text
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    assert json.loads(archive.read("manifest.json"))["records"] == "named"
    therm = archive.read("therm.dat").decode()
    # One card for the species that had two candidates (a card names the species, not the thermo record).
    assert therm.count("SMILES=C ") == 1


def test_chemkin_refuses_a_named_ref_that_is_nobodys_candidate(client, db_session, login_as, _api_curator_user):
    """Kills: a typo in ``thermo_refs`` looking like a successful pick."""
    reaction_entry, entries, thermos, extra = _reaction(db_session)
    login_as(_api_curator_user)

    resp = client.post(
        f"{EXPORT}/chemkin",
        json={
            "seed": {"reaction_refs": [reaction_entry.public_ref]},
            "thermo_refs": [extra.public_ref, "thm_nosuchrecord"],
        },
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "export_ref_unmatched"
    assert body["context"]["unmatched"] == [{"record_kind": "thermo", "ref": "thm_nosuchrecord"}]
