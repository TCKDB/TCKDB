"""The catalogue loader: the migrated database matches the seed, a re-run is a no-op, and drift is detected."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, update

from app.db.models.common import LiteratureKind, LiteratureTitleSource
from app.db.models.literature import Literature
from app.db.models.method_catalogue import (
    BasisSetCatalogue,
    BasisSetCatalogueKey,
    MethodCatalogue,
    MethodCatalogueKey,
    MethodCatalogueLiterature,
)
from app.services.method_catalogue_seed import load_seed
from app.services.method_catalogue_sync import (
    BASIS_SET_TABLES,
    METHOD_TABLES,
    catalogue_public_ref,
    sync_catalogue,
)
from app.services.public_refs import literature_doi_public_ref

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "load_method_catalogue.py"


@pytest.fixture(scope="module")
def seed():
    return load_seed()


@pytest.fixture(scope="module")
def loader():
    spec = importlib.util.spec_from_file_location("load_method_catalogue", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migrated_database_matches_the_seed(db_session, seed):
    assert sync_catalogue(db_session.connection(), seed, apply=False) == []
    assert db_session.scalar(select(func.count()).select_from(MethodCatalogue)) == len(seed.methods)
    assert db_session.scalar(select(func.count()).select_from(BasisSetCatalogue)) == len(seed.basis_sets)


def test_applying_the_seed_again_changes_nothing(db_session, seed):
    conn = db_session.connection()
    assert sync_catalogue(conn, seed, apply=True) == []
    assert sync_catalogue(conn, seed, apply=True) == []


def test_every_entry_carries_its_content_derived_ref_and_every_identity_key(db_session, seed):
    for m in seed.methods:
        row = db_session.scalars(select(MethodCatalogue).where(MethodCatalogue.key == m.key)).one()
        assert row.public_ref == catalogue_public_ref(METHOD_TABLES, m.key)
        assert sorted(k.key for k in row.keys) == sorted(m.keys)
        assert [(r.literature.doi, r.role, r.position) for r in row.references] == [
            (c.doi, c.role, i) for i, c in enumerate(m.references, start=1)
        ]
    for b in seed.basis_sets:
        row = db_session.scalars(select(BasisSetCatalogue).where(BasisSetCatalogue.key == b.key)).one()
        assert row.public_ref == catalogue_public_ref(BASIS_SET_TABLES, b.key)
        assert sorted(k.key for k in row.keys) == sorted(b.keys)


def test_seeded_literature_is_registry_resolved_and_keyed_by_its_doi(db_session, seed):
    for doi, ref in seed.references.items():
        row = db_session.scalars(select(Literature).where(Literature.doi == doi)).one()
        assert row.public_ref == literature_doi_public_ref(doi)
        assert row.title_source is LiteratureTitleSource.resolved_metadata
        assert (row.title, row.journal, row.year, row.volume, row.pages) == (
            ref.title,
            ref.journal,
            ref.year,
            ref.volume,
            ref.pages,
        )


def test_check_reports_drift_and_apply_removes_it(db_session, seed, loader, capsys):
    conn = db_session.connection()
    db_session.execute(update(MethodCatalogue).where(MethodCatalogue.key == "b3lyp").values(description="changed"))
    db_session.execute(delete(MethodCatalogueKey).where(MethodCatalogueKey.key == "gfn2"))
    db_session.execute(delete(BasisSetCatalogueKey).where(BasisSetCatalogueKey.key == "6-31g*"))
    entry_id = db_session.scalar(select(MethodCatalogue.id).where(MethodCatalogue.key == "hf"))
    db_session.execute(
        delete(MethodCatalogueLiterature).where(
            MethodCatalogueLiterature.method_catalogue_id == entry_id, MethodCatalogueLiterature.position == 3
        )
    )
    db_session.add(
        MethodCatalogue(
            key="not-in-seed", kind="method", short_name="X", full_name="X", description="X", seed_version=1
        )
    )
    db_session.flush()

    assert loader.run(conn, apply=False, check=True, seed=seed) == 1
    printed = capsys.readouterr().out
    for line in (
        "method_catalogue: update b3lyp (description)",
        "method_catalogue_key: add gfn2 -> gfn2-xtb",
        "basis_set_catalogue_key: add 6-31g* -> 6-31g(d)",
        "method_catalogue_literature: set hf (3 references)",
        "method_catalogue: delete not-in-seed",
    ):
        assert line in printed

    assert loader.run(conn, apply=True, check=False, seed=seed) == 0
    assert loader.run(conn, apply=False, check=True, seed=seed) == 0
    db_session.expire_all()
    assert db_session.scalar(select(MethodCatalogue.description).where(MethodCatalogue.key == "b3lyp")).startswith(
        "A global hybrid"
    )


def test_an_existing_citation_of_a_seeded_doi_is_adopted_not_rewritten(db_session, seed):
    doi = "10.1063/1.2436888"
    entry_id = db_session.scalar(select(MethodCatalogue.id).where(MethodCatalogue.key == "g4"))
    seeded_id = db_session.scalar(select(Literature.id).where(Literature.doi == doi))
    db_session.execute(delete(MethodCatalogueLiterature).where(MethodCatalogueLiterature.literature_id == seeded_id))
    db_session.execute(delete(Literature).where(Literature.id == seeded_id))
    deposited = Literature(
        kind=LiteratureKind.article,
        title="typed by a depositor",
        title_source=LiteratureTitleSource.depositor_supplied,
        doi="https://doi.org/10.1063/1.2436888",
    )
    db_session.add(deposited)
    db_session.flush()

    changes = sync_catalogue(db_session.connection(), seed, apply=True)
    assert changes == ["method_catalogue_literature: set g4 (1 references)"]
    db_session.expire_all()
    assert db_session.scalar(
        select(MethodCatalogueLiterature.literature_id).where(MethodCatalogueLiterature.method_catalogue_id == entry_id)
    ) == deposited.id
    assert db_session.get(Literature, deposited.id).title == "typed by a depositor"
