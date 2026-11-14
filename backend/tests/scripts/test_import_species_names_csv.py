"""The offline species-name import: report, conservative identity, provenance, and no network."""

from __future__ import annotations

import importlib.util
import io
import itertools
import pathlib
import socket
import string
import sys

import pytest
from sqlalchemy import select

from app.db.models.common import SpeciesNameSourceKind
from app.db.models.species_name import SpeciesName
from app.services.species_merge import merge_species
from app.services.species_name_csv_import import propose_from_cccbdb
from tests.services.scientific_read._factories import (
    make_external_source,
    make_observation,
    make_species,
    make_species_entry,
)

_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "import_species_names.py"
SOURCE = "Reviewed Names List"
_COUNTER = itertools.count(1)


def next_inchi_key(tag: str) -> str:
    """A well-formed InChIKey (14 letters, 10 letters, one letter), unique per call."""
    n = next(_COUNTER)
    letters = "".join(string.ascii_uppercase[(n // 26**i) % 26] for i in range(4))
    return f"{tag.upper()}{letters}".ljust(14, "A")[:14] + "-ABCDEFGHIJ-N"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("import_species_names_csv_script", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(db_session):
    return make_external_source(db_session, source_name=SOURCE, source_release="r1")


def _write(tmp_path, text, name="reviewed.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _run(script, db_session, path, *extra, source=SOURCE):
    out = io.StringIO()
    code = script.main(["--file", str(path), "--source", source, *extra], session=db_session, out=out)
    return code, out.getvalue()


def _names(db_session, species):
    return db_session.scalars(select(SpeciesName).where(SpeciesName.species_id == species.id)).all()


def test_dry_run_reports_and_writes_nothing(script, db_session, source, tmp_path):
    a, b = make_species(db_session, inchi_key=next_inchi_key("IA")), make_species(db_session, inchi_key=next_inchi_key("IB"))
    path = _write(
        tmp_path,
        "inchikey,name,kind\n"
        f"{a.inchi_key},Alphaol,common\n"
        f"{b.inchi_key},Betaol,common\n"
        f"{b.inchi_key},betaol,abbreviation\n"
        f"{next_inchi_key('IC')},Nothing,common\n"
        f"{a.inchi_key},bad%name,common\n"
        f"{a.inchi_key},Fine name,nickname\n",
    )
    code, text = _run(script, db_session, path, "--dry-run")
    assert code == 0
    lines = text.splitlines()
    assert lines[0] == f"would add: {a.public_ref} 'Alphaol' common"
    assert lines[1] == f"would add: {b.public_ref} 'Betaol' common"
    assert lines[2] == f"skipped: {b.public_ref} 'betaol' abbreviation (duplicate)"
    assert "(species_not_found)" in lines[3]
    assert "(name_characters)" in lines[4]
    assert "(bad_kind)" in lines[5]
    assert lines[-1].startswith("dry run: ") and "would_insert=2" in lines[-1]
    assert _names(db_session, a) == [] and _names(db_session, b) == []


def test_import_writes_offline_rows_with_provenance(script, db_session, source, tmp_path):
    a = make_species(db_session, inchi_key=next_inchi_key("IW"))
    path = _write(tmp_path, f"inchikey,name,kind,retrieved_at\n{a.inchi_key},Alphaol,systematic,2026-01-02T03:04:05Z\n")
    code, text = _run(script, db_session, path)
    assert code == 0 and "added:" in text
    (row,) = _names(db_session, a)
    assert row.source_kind is SpeciesNameSourceKind.offline_import
    assert row.external_source_id == source.id
    assert row.external_record_key == "reviewed.csv#2"
    assert row.retrieved_at.year == 2026 and row.kind.value == "systematic" and row.created_by_user_id is None
    again = _run(script, db_session, path)[1]
    assert "(duplicate)" in again and len(_names(db_session, a)) == 1


def test_smiles_charge_multiplicity_identity(script, db_session, source, tmp_path):
    a = make_species(db_session, smiles="CCO", charge=0, multiplicity=1, inchi_key=next_inchi_key("IS"))
    path = _write(
        tmp_path,
        "smiles,charge,multiplicity,name,kind\n"
        "CCO,0,1,Spirit,common\n"
        "CCO,0,3,Wrong spin,common\n"
        "CCO,0,,Partial,common\n"
        "CCO,x,1,Bad charge,common\n",
    )
    _, text = _run(script, db_session, path)
    assert [r.name for r in _names(db_session, a)] == ["Spirit"]
    assert "(species_not_found)" in text and "(identity_incomplete)" in text and "(bad_identity)" in text


def test_an_ambiguous_inchikey_is_skipped_not_guessed(script, db_session, source, tmp_path):
    key = next_inchi_key("IM")
    a = make_species(db_session, smiles="CC(C)O", inchi_key=key)
    b = make_species(db_session, smiles="OC(C)C", inchi_key=key)
    path = _write(tmp_path, f"inchikey,name,kind\n{key},Propanol,common\n")
    _, text = _run(script, db_session, path)
    assert "(species_ambiguous)" in text
    assert _names(db_session, a) == [] and _names(db_session, b) == []


def test_a_merged_species_is_not_a_match(script, db_session, source, tmp_path):
    key = next_inchi_key("IG")
    loser = make_species(db_session, smiles="CC(C)O", inchi_key=key)
    survivor = make_species(db_session, smiles="OC(C)C", inchi_key=key)
    merge_species(db_session, loser=loser, survivor=survivor)
    path = _write(tmp_path, f"inchikey,name,kind\n{key},Propanol,common\n")
    _run(script, db_session, path)
    assert _names(db_session, loser) == [] and len(_names(db_session, survivor)) == 1


def test_a_retired_name_is_not_restored(script, db_session, source, tmp_path):
    a = make_species(db_session, inchi_key=next_inchi_key("IR"))
    path = _write(tmp_path, f"inchikey,name,kind\n{a.inchi_key},Alphaol,common\n")
    _run(script, db_session, path)
    (row,) = _names(db_session, a)
    from sqlalchemy import func

    row.retired_at = func.now()
    db_session.flush()
    _, text = _run(script, db_session, path)
    assert "(retired_name_not_restored)" in text and len(_names(db_session, a)) == 1


def test_a_missing_source_and_a_bad_file_exit_2(script, db_session, tmp_path, capsys):
    path = _write(tmp_path, "inchikey,name,kind\n")
    assert _run(script, db_session, path, source="No such source")[0] == 2
    bad = _write(tmp_path, "foo,bar\n1,2\n", "bad.csv")
    assert _run(script, db_session, bad)[0] == 2
    assert script.main(["--file", str(path)], session=db_session) == 2
    assert _run(script, db_session, tmp_path / "absent.csv")[0] == 2
    assert capsys.readouterr().err


def test_the_import_never_touches_the_network(script, db_session, source, tmp_path, monkeypatch):
    a = make_species(db_session, inchi_key=next_inchi_key("IN"))
    path = _write(tmp_path, f"inchikey,name,kind\n{a.inchi_key},Alphaol,common\n")
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("a network call was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert _run(script, db_session, path, "--dry-run")[0] == 0
    assert _run(script, db_session, path)[0] == 0
    assert script.main(["--propose-from-cccbdb"], session=db_session, out=io.StringIO()) == 0
    assert attempts == []


def test_the_script_imports_no_network_client():
    text = _SCRIPT.read_text() + (_SCRIPT.parents[1] / "app/services/species_name_csv_import.py").read_text()
    for word in ("requests", "urllib", "httpx", "http.client", "socket", "aiohttp"):
        assert f"import {word}" not in text and f"from {word}" not in text


def test_propose_from_cccbdb_lists_new_names_and_writes_nothing(script, db_session, tmp_path):
    a = make_species(db_session, inchi_key=next_inchi_key("PA"))
    b = make_species(db_session, inchi_key=next_inchi_key("PB"))
    for i, (sp, name) in enumerate(((a, "methanol"), (a, "methanol"), (b, "ethanol"), (b, "bad%"))):
        entry = make_species_entry(db_session, sp)
        make_observation(
            db_session, species_entry=entry, reference_label=f"row{i}", raw_payload_json={"identity_hint": {"name": name}}
        )
    db_session.add(
        SpeciesName(
            species_id=b.id, name="Ethanol", name_key="ethanol", kind="common", source_kind=SpeciesNameSourceKind.curated
        )
    )
    db_session.flush()
    proposals = propose_from_cccbdb(db_session)
    assert proposals == [{"species_ref": a.public_ref, "inchikey": a.inchi_key, "name": "methanol", "kind": ""}]
    out = io.StringIO()
    assert script.main(["--propose-from-cccbdb"], session=db_session, out=out) == 0
    assert out.getvalue().splitlines()[0] == "species_ref,inchikey,name,kind"
    assert len(_names(db_session, a)) == 0
