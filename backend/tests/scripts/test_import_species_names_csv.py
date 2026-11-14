"""The species-name import script on a reviewed CSV: report, conservative identity, provenance, and no network."""

from __future__ import annotations

import importlib.util
import io
import itertools
import pathlib
import socket
import string
import sys

import pytest
from sqlalchemy import func, select

from app.db.models.common import SpeciesNameSourceKind
from app.db.models.external_source import ExternalSource
from app.db.models.species_name import SpeciesName
from app.services.species_name_csv_import import propose_from_cccbdb
from tests.services.scientific_read._factories import (
    make_observation,
    make_species,
    make_species_entry,
)

_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "names" / "import_species_names.py"
SOURCE = "Reviewed Names List"
STAMP = "2026-01-02T03:04:05Z"
_COUNTER = itertools.count(1)


def next_inchi_key(tag: str) -> str:
    """A well-formed InChIKey (14 letters, 10 letters, one letter), unique per call."""
    n = next(_COUNTER)
    letters = "".join(string.ascii_uppercase[(n // 26**i) % 26] for i in range(4))
    return f"{tag.upper()}{letters}".ljust(14, "A")[:14] + "-ABCDEFGHIJ-N"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("import_species_names_script", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(tmp_path, text, name="reviewed.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _run(script, db_session, path, *extra, release="r1"):
    out = io.StringIO()
    argv = [str(path), "--source", SOURCE, "--source-release", release, *extra]
    code = script.main(argv, session=db_session, out=out)
    return code, out.getvalue()


def _names(db_session, species):
    return db_session.scalars(select(SpeciesName).where(SpeciesName.species_id == species.id)).all()


def _sources(db_session):
    return db_session.scalars(select(ExternalSource).where(ExternalSource.source_name == SOURCE)).all()


def test_report_only_lists_what_would_happen_and_writes_nothing(script, db_session, tmp_path):
    a = make_species(db_session, inchi_key=next_inchi_key("IA"))
    b = make_species(db_session, inchi_key=next_inchi_key("IB"))
    path = _write(
        tmp_path,
        "inchikey,name,kind,record_key,retrieved_at\n"
        f"{a.inchi_key},Alphaol,common,k1,{STAMP}\n"
        f"{b.inchi_key},Betaol,common,k2,{STAMP}\n"
        f"{b.inchi_key},betaol,abbreviation,k3,{STAMP}\n"
        f"{next_inchi_key('IC')},Nothing,common,k4,{STAMP}\n"
        f"{a.inchi_key},bad%name,common,k5,{STAMP}\n"
        f"{a.inchi_key},Fine name,nickname,k6,{STAMP}\n"
        f"{a.inchi_key},No provenance,common,,\n",
    )
    code, text = _run(script, db_session, path)
    assert code == 0
    lines = text.splitlines()
    assert lines[0] == f"would add: {a.public_ref} 'Alphaol' common"
    assert lines[1] == f"would add: {b.public_ref} 'Betaol' common"
    assert lines[2] == f"skipped: {b.public_ref} 'betaol' abbreviation (already_present)"
    assert "(species_not_found)" in lines[3]
    assert "(name_characters)" in lines[4]
    assert "(bad_kind)" in lines[5]
    assert "(provenance_missing)" in lines[6]
    assert lines[-1].startswith("report only: ") and "would_insert=2" in lines[-1]
    assert _names(db_session, a) == [] and _names(db_session, b) == []
    assert _sources(db_session) == []


def test_commit_writes_offline_rows_with_provenance(script, db_session, tmp_path):
    a = make_species(db_session, inchi_key=next_inchi_key("IW"))
    path = _write(
        tmp_path, f"inchikey,name,kind,record_key,retrieved_at\n{a.inchi_key}, Alphaol ,systematic,rec-9,{STAMP}\n"
    )
    code, text = _run(script, db_session, path, "--commit", "--terms-url", "https://example.org/terms")
    assert code == 0 and "added:" in text
    (row,) = _names(db_session, a)
    (source,) = _sources(db_session)
    assert row.source_kind is SpeciesNameSourceKind.offline_import and row.external_source_id == source.id
    assert source.source_release == "r1" and source.terms_url == "https://example.org/terms"
    assert row.external_record_key == "rec-9" and row.retrieved_at.year == 2026
    assert row.kind.value == "systematic" and row.created_by_user_id is None
    assert row.name == "Alphaol"
    again = _run(script, db_session, path, "--commit")[1]
    assert "(already_present)" in again and len(_names(db_session, a)) == 1 and len(_sources(db_session)) == 1


def test_smiles_resolves_through_the_upload_path(script, db_session, tmp_path):
    propene = make_species(db_session, smiles="C=CC", charge=0, multiplicity=1, inchi_key=next_inchi_key("IP"))
    ethanol = make_species(db_session, smiles="CCO", charge=0, multiplicity=1, inchi_key=next_inchi_key("IE"))
    path = _write(
        tmp_path,
        "smiles,charge,multiplicity,name,kind,record_key,retrieved_at\n"
        f"CC=C,0,1,Propylene,common,k1,{STAMP}\n"
        f"OCC,0,1,Spirit,common,k2,{STAMP}\n"
        f"CCO,0,3,Wrong spin,common,k3,{STAMP}\n"
        f"CCO,0,,Partial,common,k4,{STAMP}\n"
        f"CCO,x,1,Bad charge,common,k5,{STAMP}\n"
        f"C(C,0,1,Bad smiles,common,k6,{STAMP}\n",
    )
    _, text = _run(script, db_session, path, "--commit")
    assert [r.name for r in _names(db_session, propene)] == ["Propylene"]
    assert [r.name for r in _names(db_session, ethanol)] == ["Spirit"]
    assert "(species_not_found)" in text and "(identity_incomplete)" in text
    assert text.count("(bad_identity)") == 2


def test_inchikey_and_smiles_that_name_different_species_conflict(script, db_session, tmp_path):
    a = make_species(db_session, smiles="CCO", inchi_key=next_inchi_key("IX"))
    b = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("IY"))
    path = _write(
        tmp_path,
        "inchikey,smiles,charge,multiplicity,name,kind,record_key,retrieved_at\n"
        f"{a.inchi_key},CCC,0,1,Mixed up,common,k1,{STAMP}\n"
        f"{a.inchi_key},CCO,0,1,Agreeing,common,k2,{STAMP}\n",
    )
    _, text = _run(script, db_session, path, "--commit")
    assert "(identity_conflict)" in text
    assert _names(db_session, b) == [] and [r.name for r in _names(db_session, a)] == ["Agreeing"]


def test_an_ambiguous_inchikey_is_skipped_not_guessed(script, db_session, tmp_path):
    key = next_inchi_key("IM")
    a = make_species(db_session, smiles="CC(C)O", inchi_key=key)
    b = make_species(db_session, smiles="OC(C)C", inchi_key=key)
    path = _write(tmp_path, f"inchikey,name,kind,record_key,retrieved_at\n{key},Propanol,common,k1,{STAMP}\n")
    _, text = _run(script, db_session, path, "--commit")
    assert "(species_ambiguous)" in text
    assert _names(db_session, a) == [] and _names(db_session, b) == []


def test_a_merged_species_is_not_a_match(script, db_session, tmp_path):
    key = next_inchi_key("IG")
    loser = make_species(db_session, smiles="CC(C)O", inchi_key=key)
    survivor = make_species(db_session, smiles="OC(C)C", inchi_key=key)
    loser.merged_into_species_id = survivor.id
    db_session.flush()
    path = _write(tmp_path, f"inchikey,name,kind,record_key,retrieved_at\n{key},Propanol,common,k1,{STAMP}\n")
    _run(script, db_session, path, "--commit")
    assert _names(db_session, loser) == [] and len(_names(db_session, survivor)) == 1


def test_a_retired_name_is_not_restored(script, db_session, tmp_path):
    a = make_species(db_session, inchi_key=next_inchi_key("IR"))
    path = _write(tmp_path, f"inchikey,name,kind,record_key,retrieved_at\n{a.inchi_key},Alphaol,common,k1,{STAMP}\n")
    _run(script, db_session, path, "--commit")
    (row,) = _names(db_session, a)
    row.retired_at = func.now()
    db_session.flush()
    _, text = _run(script, db_session, path, "--commit")
    assert "(retired_name_not_restored)" in text and len(_names(db_session, a)) == 1


def test_bad_arguments_and_files_exit_2(script, db_session, tmp_path, capsys):
    path = _write(tmp_path, "inchikey,name,kind\n")
    assert script.main([str(path)], session=db_session) == 2  # no --source
    bad = _write(tmp_path, "foo,bar\n1,2\n", "bad.csv")
    assert _run(script, db_session, bad)[0] == 2
    assert _run(script, db_session, tmp_path / "absent.csv")[0] == 2
    assert script.main([], session=db_session) == 2
    assert capsys.readouterr().err


def test_the_import_never_touches_the_network(script, db_session, tmp_path, monkeypatch):
    a = make_species(db_session, inchi_key=next_inchi_key("IN"))
    path = _write(tmp_path, f"inchikey,name,kind,record_key,retrieved_at\n{a.inchi_key},Alphaol,common,k1,{STAMP}\n")
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("a network call was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert _run(script, db_session, path)[0] == 0
    assert _run(script, db_session, path, "--commit")[0] == 0
    assert script.main(["--propose-from-cccbdb"], session=db_session, out=io.StringIO()) == 0
    assert attempts == []


def test_the_script_and_readers_import_no_network_client():
    root = _SCRIPT.parents[2]
    text = "".join(
        p.read_text()
        for p in (
            _SCRIPT,
            root / "app/services/species_name_csv_import.py",
            root / "app/services/species_name_import.py",
        )
    )
    for word in ("requests", "urllib", "httpx", "http.client", "socket", "aiohttp"):
        assert f"import {word}" not in text and f"from {word}" not in text


def test_a_json_review_file_is_still_read(script, db_session, tmp_path):
    out = io.StringIO()
    path = _write(tmp_path, '{"format": "other", "rows": []}', "review.json")
    assert script.main([str(path)], session=db_session, out=out) == 2


def test_propose_from_cccbdb_lists_new_names_and_writes_nothing(script, db_session):
    a = make_species(db_session, inchi_key=next_inchi_key("PA"))
    b = make_species(db_session, inchi_key=next_inchi_key("PB"))
    c = make_species(db_session, inchi_key=next_inchi_key("PC"))
    rows = ((a, "methanol", "CCCBDB"), (a, "methanol", "CCCBDB"), (b, "ethanol", "CCCBDB"), (b, "bad%", "CCCBDB"))
    for i, (sp, name, source) in enumerate([*rows, (c, "thermoml name", "NIST ThermoML Archive")]):
        entry = make_species_entry(db_session, sp)
        make_observation(
            db_session,
            species_entry=entry,
            reference_label=f"row{i}",
            external_source_name=source if len(source) else None,
            raw_payload_json={"identity_hint": {"name": name}},
        )
    db_session.add(
        SpeciesName(
            species_id=b.id,
            name="Ethanol",
            name_key="ethanol",
            kind="common",
            source_kind=SpeciesNameSourceKind.curated,
        )
    )
    db_session.flush()
    expected = {
        "species_ref": a.public_ref,
        "inchikey": a.inchi_key,
        "name": "methanol",
        "kind": "",
        "record_key": "",
        "retrieved_at": "",
    }
    assert propose_from_cccbdb(db_session) == [expected]
    out = io.StringIO()
    assert script.main(["--propose-from-cccbdb"], session=db_session, out=out) == 0
    assert out.getvalue().splitlines()[0] == "species_ref,inchikey,name,kind,record_key,retrieved_at"
    assert _names(db_session, a) == []
