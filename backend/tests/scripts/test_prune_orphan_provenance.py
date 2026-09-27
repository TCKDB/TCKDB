"""``prune_orphan_provenance.py``: discovery, plan, dry run, commit, races.

Runs against the pytest transaction (the ``_SessionProxy`` device from
``test_backfill_assumed_tau.py``), so every delete rolls back with the test.

The seed mirrors the deployed playground on 2026-09-27 with one deliberate
twist the brief did not know about: the Arkane release has zero
calculations but *is* cited by a thermo row -- the analysis-software shape
the ARC adapter deposits. A tool that only looked at
``calculation.software_release_id`` would delete it; this one must not.
"""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import sys

import pytest
from sqlalchemy import select

from app.db.base import Base
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from tests.services.scientific_read._factories import (
    make_calculation,
    make_software,
    make_software_release,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    make_workflow_tool_release,
    next_inchi_key,
    unique_smiles,
)

_SCRIPT = (
    pathlib.Path(__file__).parents[2] / "scripts" / "ops" / "prune_orphan_provenance.py"
)
_TABLES = ("software_release", "software", "workflow_tool_release", "workflow_tool")


@pytest.fixture(scope="module")
def prune():
    spec = importlib.util.spec_from_file_location("prune_orphan_provenance", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SessionProxy:
    def __init__(self, session) -> None:
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def begin(self):
        return contextlib.nullcontext()


def _run_main(prune, monkeypatch, db_session, argv, *, db_name="tckdb_test_prune"):
    import app.api.deps as deps
    from app.api.config import settings

    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    monkeypatch.setattr(settings, "db_name", db_name)
    return prune.main(argv)


def _entry(db_session):
    return make_species_entry(
        db_session,
        make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("PRUNE")),
    )


@pytest.fixture
def seeded(db_session):
    """Playground shape. Returns the public_refs of every seeded row by role."""
    g16 = make_software_release(db_session, name="Gaussian", version="16", revision="C.02")
    g_null = make_software_release(db_session, name="Gaussian", version=None)
    orca_null = make_software_release(db_session, name="ORCA", version=None)
    arkane = make_software_release(
        db_session, name="Arkane", version=None, revision="b8586246" + "0" * 32
    )
    lonely_release = make_software_release(db_session, name="LonelyPkg", version="1.0")
    never_released = make_software(db_session, name="NeverReleased")

    arc = make_workflow_tool_release(db_session, name="ARC", version="1.1.0")
    arkane_wt = make_workflow_tool_release(db_session, name="Arkane", version="3.2.0")

    entry = _entry(db_session)
    make_calculation(
        db_session,
        species_entry_id=entry.id,
        software_release_id=g16.id,
        workflow_tool_release_id=arc.id,
    )
    make_calculation(
        db_session, species_entry_id=entry.id, software_release_id=orca_null.id
    )
    # Zero calculations, but cited as the thermo's analysis software.
    make_thermo_scalar(
        db_session, species_entry=entry, software_release_id=arkane.id
    )
    db_session.flush()

    lonely = db_session.get(Software, lonely_release.software_id)
    return {
        "g16": g16.public_ref,
        "g_null": g_null.public_ref,
        "orca_null": orca_null.public_ref,
        "arkane": arkane.public_ref,
        "lonely_release": lonely_release.public_ref,
        "lonely": lonely.public_ref,
        "never_released": never_released.public_ref,
        "arc": arc.public_ref,
        "arkane_wt": arkane_wt.public_ref,
        "arkane_wt_parent": db_session.get(
            WorkflowTool, arkane_wt.workflow_tool_id
        ).public_ref,
    }


def _refs(db_session) -> dict[str, set[str]]:
    return {
        "software_release": set(db_session.scalars(select(SoftwareRelease.public_ref))),
        "software": set(db_session.scalars(select(Software.public_ref))),
        "workflow_tool_release": set(
            db_session.scalars(select(WorkflowToolRelease.public_ref))
        ),
        "workflow_tool": set(db_session.scalars(select(WorkflowTool.public_ref))),
    }


def _metadata_references() -> dict[str, set[tuple[str, str]]]:
    """The same question answered from the ORM, an independent source."""
    out: dict[str, set[tuple[str, str]]] = {t: set() for t in _TABLES}
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name in out:
                out[fk.column.table.name].add((table.name, fk.parent.name))
    return out


def test_discovery_matches_the_orm_and_is_not_just_calculation(prune, db_session):
    discovered = prune.discover_references(db_session)
    as_pairs = {
        table: {(r.source_table, r.source_column) for r in refs}
        for table, refs in discovered.items()
    }

    assert as_pairs == _metadata_references()
    # The brief's premise was "calculation is the only FK into
    # software_release". It is one of many; the thermo one is what keeps
    # the Arkane analysis release alive.
    assert ("calculation", "software_release_id") in as_pairs["software_release"]
    assert ("thermo", "software_release_id") in as_pairs["software_release"]
    assert len(as_pairs["software_release"]) >= 10
    assert as_pairs["software"] == {("software_release", "software_id")}


def test_plan_is_exactly_the_orphans(prune, db_session, seeded):
    plan = prune.build_plan(db_session)
    got = {(c.table, c.public_ref, c.after_releases) for c in plan.all()}

    assert got == {
        ("software_release", seeded["g_null"], False),
        ("software_release", seeded["lonely_release"], False),
        ("software", seeded["never_released"], False),
        ("software", seeded["lonely"], True),
        ("workflow_tool_release", seeded["arkane_wt"], False),
        ("workflow_tool", seeded["arkane_wt_parent"], True),
    }
    labels = {c.public_ref: c.label for c in plan.all()}
    assert labels[seeded["g_null"]] == "Gaussian version=None revision=None build=None"


def test_dry_run_deletes_nothing(prune, monkeypatch, db_session, seeded, capsys):
    before = _refs(db_session)

    assert _run_main(prune, monkeypatch, db_session, []) == 0

    assert _refs(db_session) == before
    out = capsys.readouterr().out
    assert "Unreferenced rows: 6" in out
    assert "thermo.software_release_id -> software_release.id" in out
    assert "Dry run -- nothing was deleted." in out


def test_commit_removes_exactly_the_orphans(prune, monkeypatch, db_session, seeded, capsys):
    before = _refs(db_session)

    assert _run_main(prune, monkeypatch, db_session, ["--commit"]) == 0

    after = _refs(db_session)
    assert before["software_release"] - after["software_release"] == {
        seeded["g_null"],
        seeded["lonely_release"],
    }
    assert before["software"] - after["software"] == {
        seeded["never_released"],
        seeded["lonely"],
    }
    assert before["workflow_tool_release"] - after["workflow_tool_release"] == {
        seeded["arkane_wt"]
    }
    assert before["workflow_tool"] - after["workflow_tool"] == {
        seeded["arkane_wt_parent"]
    }
    # Kept: the cited ones, including the zero-calculation Arkane release.
    assert {seeded["g16"], seeded["orca_null"], seeded["arkane"]} <= after[
        "software_release"
    ]
    assert seeded["arc"] in after["workflow_tool_release"]
    assert "Removed 6 row(s):" in capsys.readouterr().out


def test_a_row_cited_after_planning_is_kept(prune, db_session, seeded):
    """Plan, then a deposit cites one planned release, then commit."""
    plan = prune.build_plan(db_session)
    late = db_session.scalar(
        select(SoftwareRelease).where(SoftwareRelease.public_ref == seeded["lonely_release"])
    )
    make_calculation(
        db_session, species_entry_id=_entry(db_session).id, software_release_id=late.id
    )
    db_session.flush()

    result = prune.commit_plan(db_session, plan)

    assert {c.public_ref for c in result.removed} == {
        seeded["g_null"],
        seeded["never_released"],
        seeded["arkane_wt"],
        seeded["arkane_wt_parent"],
    }
    assert {(c.public_ref, why) for c, why in result.kept} == {
        (seeded["lonely_release"], "referenced at commit time"),
        # Its parent was planned "after releases"; the release stayed, so
        # the parent is still referenced and stays too.
        (seeded["lonely"], "referenced at commit time"),
    }
    assert db_session.get(SoftwareRelease, late.id) is not None


def test_help_tells_the_operator_to_run_commit_with_no_deposits_in_flight(prune, capsys):
    """Review finding F5: ``--commit`` holds its row locks to the end."""
    with pytest.raises(SystemExit):
        prune.main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "Run it when no deposits are in flight" in help_text
    assert "when no deposits are in flight" in " ".join(prune.__doc__.split())


def test_commit_on_a_non_test_database_needs_the_explicit_flag(
    prune, monkeypatch, db_session, seeded, capsys
):
    before = _refs(db_session)

    rc = _run_main(prune, monkeypatch, db_session, ["--commit"], db_name="tckdb")

    assert rc == 2
    assert _refs(db_session) == before
    assert "Refusing --commit" in capsys.readouterr().err
