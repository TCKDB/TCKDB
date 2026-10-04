"""The kinetics select routes, unstubbed: the real app, its real dependencies, and committed rows.

Every other route test goes through the harness (one outer transaction, the snapshot dependency overridden), and the
snapshot-dependency test stubs the three service calls. This one stubs nothing: a reaction entry and a record are
committed to a scratch database, the application's session factory is pointed at it with no pool, and both routes are
called as a client would. Each must answer 200, and the manifest must say it was read in a REPEATABLE READ snapshot.

Committed rows cannot be cleaned up row by row (the science tables carry append-only triggers), so this runs in a
database that is dropped whole, as ``test_write_commit_before_response.py`` does.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api import deps as api_deps
from app.api.app import create_app
from app.db.models.common import RecordReviewStatus as S
from tests import conftest
from tests.conftest import scratch_database_name
from tests.services.kinetics_selection.test_service import declared, determination
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_literature,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
QUESTION = {
    "direction": "forward",
    "target": {"kind": "whole_reaction"},
    "coefficient_basis": "elementary_coefficient",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure": {"kind": "independent"},
}


@pytest.fixture(scope="module")
def scratch_engine() -> Iterator:
    """A database made by ``alembic upgrade head`` alone, dropped afterwards, with no connection pool."""
    db_name = scratch_database_name("kinetics_select_real")
    conftest._recreate_test_database(db_name)
    engine = None
    try:
        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT, env=conftest._db_env(db_name), check=True, capture_output=True, text=True,
        )
        engine = create_engine(conftest._database_url(db_name), poolclass=NullPool)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        conftest._drop_test_database(db_name)


@pytest.fixture(scope="module")
def committed_entry(scratch_engine):
    """One reaction entry with one approved, fully declared record, committed."""
    with Session(scratch_engine) as session:
        h = make_species_entry(session, make_species(session, smiles="[H]", multiplicity=2, inchi_key=next_inchi_key("RDA")))
        ch4 = make_species_entry(session, make_species(session, smiles="C", multiplicity=1, inchi_key=next_inchi_key("RDB")))
        h2 = make_species_entry(session, make_species(session, smiles="[H][H]", multiplicity=1, inchi_key=next_inchi_key("RDC")))
        ch3 = make_species_entry(session, make_species(session, smiles="[CH3]", multiplicity=2, inchi_key=next_inchi_key("RDD")))
        reaction = make_chem_reaction(session, reactants=[h.species, ch4.species], products=[h2.species, ch3.species])
        entry = make_reaction_entry(session, reaction=reaction, reactant_entries=[h, ch4], product_entries=[h2, ch3])
        world = SimpleNamespace(entry=entry, literature=make_literature(session))
        det = determination(session, world, "committed-determination")
        record = declared(session, world, det, status=S.approved)
        refs = SimpleNamespace(entry=entry.public_ref, record=record.public_ref, determination=det.public_ref)
        session.commit()
    return refs


@pytest.fixture
def real_app_client(scratch_engine):
    """The real application, no dependency overridden, its session factory on the scratch database."""
    factory = api_deps.SessionLocal
    previous = factory.kw.get("bind")
    factory.configure(bind=scratch_engine)
    try:
        app = create_app()
        assert not app.dependency_overrides
        with TestClient(app) as client:
            yield client
    finally:
        factory.configure(bind=previous)


@pytest.mark.parametrize("suffix", ["", "/manifest"])
def test_both_routes_answer_200_over_committed_rows_and_the_manifest_says_it_read_a_snapshot(
    real_app_client, committed_entry, suffix
):
    url = f"/api/v1/scientific/reaction-entries/{committed_entry.entry}/kinetics/select{suffix}"
    response = real_app_client.post(url, json=QUESTION)
    assert response.status_code == 200, response.text
    body = response.json()
    if suffix:
        assert body["snapshot_isolation"] == "repeatable read"
        assert body["outcome"] == "sole_eligible_candidate"
        assert [c["kinetics_ref"] for c in body["candidates"]] == [committed_entry.record]
    else:
        assert body["outcome"] == "sole_eligible_candidate"
        assert body["selection"]["determination_ref"] == committed_entry.determination
        assert body["selection"]["kinetics_refs"] == [committed_entry.record]
        assert body["request"]["reaction_entry_ref"] == committed_entry.entry


def test_the_two_routes_agree_over_the_committed_rows(real_app_client, committed_entry):
    base = f"/api/v1/scientific/reaction-entries/{committed_entry.entry}/kinetics/select"
    answer = real_app_client.post(base, json=QUESTION).json()
    manifest = real_app_client.post(base + "/manifest", json=QUESTION).json()
    assert answer["outcome"] == manifest["outcome"]
    assert answer["administrative_order"] == manifest["decision"]["administrative_order"]
    assert answer["disclosures"]["visible_candidates"] == manifest["population"]["visible_candidates"] == 1


def test_an_unknown_reaction_entry_is_a_404_through_the_real_dependency_too(real_app_client, committed_entry):
    response = real_app_client.post(
        "/api/v1/scientific/reaction-entries/rxe_doesnotexistdoesnotexist/kinetics/select", json=QUESTION
    )
    assert response.status_code == 404
