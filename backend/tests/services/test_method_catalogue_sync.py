"""The catalogue loader: the migrated database matches the seed, a re-run is a no-op, and drift is detected."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, update

from app.db.models.author import Author
from app.db.models.common import LiteratureKind, LiteratureTitleSource
from app.db.models.literature import Literature
from app.db.models.literature_author import LiteratureAuthor
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


G4_DOI = "10.1063/1.2436888"
G4_AUTHORS = ["Curtiss", "Redfern", "Raghavachari"]


def _authors_of(db_session, literature_id: int) -> list[str]:
    return list(
        db_session.scalars(
            select(Author.family_name)
            .join(LiteratureAuthor, LiteratureAuthor.author_id == Author.id)
            .where(LiteratureAuthor.literature_id == literature_id)
            .order_by(LiteratureAuthor.author_order)
        )
    )


def _replace_seeded_g4(db_session, *, authors: tuple[str, ...] = (), **fields) -> Literature:
    """Swap the seeded G4 citation for a row as a deposit could have left it."""
    seeded_id = db_session.scalar(select(Literature.id).where(Literature.doi == G4_DOI))
    db_session.execute(delete(MethodCatalogueLiterature).where(MethodCatalogueLiterature.literature_id == seeded_id))
    db_session.execute(delete(LiteratureAuthor).where(LiteratureAuthor.literature_id == seeded_id))
    db_session.execute(delete(Literature).where(Literature.id == seeded_id))
    row = Literature(kind=LiteratureKind.article, **fields)
    db_session.add(row)
    db_session.flush()
    for order, name in enumerate(authors, start=1):
        author = Author(family_name=name, full_name=name)
        db_session.add(author)
        db_session.flush()
        db_session.add(LiteratureAuthor(literature_id=row.id, author_id=author.id, author_order=order))
    db_session.flush()
    return row


def _g4_link(db_session) -> int:
    entry_id = db_session.scalar(select(MethodCatalogue.id).where(MethodCatalogue.key == "g4"))
    return db_session.scalar(
        select(MethodCatalogueLiterature.literature_id).where(MethodCatalogueLiterature.method_catalogue_id == entry_id)
    )


def test_seeded_literature_carries_its_authors_in_order(db_session, seed):
    for doi, ref in seed.references.items():
        literature_id = db_session.scalar(select(Literature.id).where(Literature.doi == doi))
        assert _authors_of(db_session, literature_id) == list(ref.authors), doi


def test_a_depositor_citation_of_a_seeded_doi_is_upgraded_to_the_registry_record(db_session, seed):
    deposited = _replace_seeded_g4(
        db_session,
        title="typed by a depositor",
        title_source=LiteratureTitleSource.depositor_supplied,
        year=1999,
        doi="https://doi.org/10.1063/1.2436888",
    )
    public_ref = deposited.public_ref

    changes = sync_catalogue(db_session.connection(), seed, apply=True)
    assert changes == [f"literature: upgrade {G4_DOI}", "method_catalogue_literature: set g4 (1 references)"]
    assert sync_catalogue(db_session.connection(), seed, apply=False) == []
    db_session.expire_all()
    row = db_session.get(Literature, deposited.id)
    ref = seed.references[G4_DOI]
    assert row.title_source is LiteratureTitleSource.resolved_metadata
    assert (row.title, row.journal, row.year, row.volume, row.pages) == (
        ref.title, ref.journal, ref.year, ref.volume, ref.pages
    )
    assert (row.doi, row.public_ref) == ("https://doi.org/10.1063/1.2436888", public_ref)
    assert _authors_of(db_session, row.id) == G4_AUTHORS
    assert _g4_link(db_session) == row.id


def test_an_upgraded_citation_keeps_the_authors_it_already_has(db_session, seed):
    deposited = _replace_seeded_g4(
        db_session, title="typed", title_source=None, doi=G4_DOI, authors=("Depositor",)
    )
    assert sync_catalogue(db_session.connection(), seed, apply=True)[0] == f"literature: upgrade {G4_DOI}"
    db_session.expire_all()
    assert db_session.get(Literature, deposited.id).title_source is LiteratureTitleSource.resolved_metadata
    assert _authors_of(db_session, deposited.id) == ["Depositor"]


def test_a_registry_resolved_citation_of_a_seeded_doi_is_left_as_it_stands(db_session, seed):
    resolved = _replace_seeded_g4(
        db_session,
        title="resolved elsewhere",
        title_source=LiteratureTitleSource.resolved_metadata,
        doi=G4_DOI,
        authors=("Someone",),
    )

    changes = sync_catalogue(db_session.connection(), seed, apply=True)
    assert changes == ["method_catalogue_literature: set g4 (1 references)"]
    db_session.expire_all()
    row = db_session.get(Literature, resolved.id)
    assert row.title == "resolved elsewhere"
    assert _authors_of(db_session, row.id) == ["Someone"]
    assert _g4_link(db_session) == row.id


def test_a_registry_resolved_citation_without_authors_gets_none_added(db_session, seed):
    resolved = _replace_seeded_g4(
        db_session, title="resolved elsewhere", title_source=LiteratureTitleSource.resolved_metadata, doi=G4_DOI
    )
    sync_catalogue(db_session.connection(), seed, apply=True)
    db_session.expire_all()
    assert _authors_of(db_session, resolved.id) == []
    assert sync_catalogue(db_session.connection(), seed, apply=False) == []


def test_a_doi_prefixed_citation_is_reused_not_duplicated(db_session, seed):
    stray = _replace_seeded_g4(
        db_session, title="typed", title_source=LiteratureTitleSource.depositor_supplied, doi="doi:10.1063/1.2436888"
    )
    assert stray.public_ref != literature_doi_public_ref(G4_DOI)

    changes = sync_catalogue(db_session.connection(), seed, apply=True)
    assert changes[0] == f"literature: upgrade {G4_DOI}"
    assert not any(line.startswith("literature: create") for line in changes)
    assert _g4_link(db_session) == stray.id
    db_session.expire_all()
    assert db_session.get(Literature, stray.id).doi == "doi:10.1063/1.2436888"
    assert sync_catalogue(db_session.connection(), seed, apply=False) == []


def test_check_reports_missing_authors_on_a_seeded_citation(db_session, seed, loader, capsys):
    literature_id = db_session.scalar(select(Literature.id).where(Literature.doi == G4_DOI))
    db_session.execute(delete(LiteratureAuthor).where(LiteratureAuthor.literature_id == literature_id))
    db_session.flush()
    conn = db_session.connection()
    assert loader.run(conn, apply=False, check=True, seed=seed) == 1
    assert f"literature_author: add {G4_DOI} (3 authors)" in capsys.readouterr().out
    assert loader.run(conn, apply=True, check=False, seed=seed) == 0
    assert _authors_of(db_session, literature_id) == G4_AUTHORS
    assert loader.run(conn, apply=False, check=True, seed=seed) == 0
