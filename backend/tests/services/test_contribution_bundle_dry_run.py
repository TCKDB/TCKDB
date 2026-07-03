"""Tests for the hosted contribution-bundle dry-run service.

Covers identity + provenance preview classification, no-mutation guarantees,
and conservative behavior when participant species are missing for kinetics.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.kinetics import Kinetics
from app.db.models.literature import Literature
from app.db.models.reaction import ChemReaction
from app.db.models.software import SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.thermo import Thermo
from app.db.models.workflow import WorkflowToolRelease
from app.schemas.contribution_bundle_dry_run import (
    DryRunAction,
    DryRunRecordType,
)
from app.schemas.workflows.contribution_bundle import (
    BundleKind,
    ContributionBundleV0,
)
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.contribution_bundle_dry_run import dry_run_contribution_bundle
from app.workflows.kinetics import persist_kinetics_upload
from app.workflows.thermo import persist_thermo_upload

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_bundle(filename: str) -> ContributionBundleV0:
    raw = (EXAMPLES_DIR / filename).read_text()
    return ContributionBundleV0.model_validate_json(raw)


@contextmanager
def _isolated_session(db_engine) -> Iterator[Session]:
    """Open a session on a connection-bound transaction that always rolls back.

    The dry-run reuse tests seed real rows via persist_*_upload(...) — without
    an outer rollback those rows commit to the shared session-scoped test DB
    and pollute later tests (notably the hydrogen species inchi_key collides
    with later species_entry_review tests).
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _table_counts(session: Session) -> dict[str, int]:
    """Snapshot row counts for the tables dry-run must never touch."""
    return {
        "species": session.scalar(select(func.count()).select_from(Species)) or 0,
        "species_entry": session.scalar(select(func.count()).select_from(SpeciesEntry))
        or 0,
        "chem_reaction": session.scalar(select(func.count()).select_from(ChemReaction))
        or 0,
        "thermo": session.scalar(select(func.count()).select_from(Thermo)) or 0,
        "kinetics": session.scalar(select(func.count()).select_from(Kinetics)) or 0,
        "literature": session.scalar(select(func.count()).select_from(Literature))
        or 0,
        "software_release": session.scalar(
            select(func.count()).select_from(SoftwareRelease)
        )
        or 0,
        "workflow_tool_release": session.scalar(
            select(func.count()).select_from(WorkflowToolRelease)
        )
        or 0,
    }


def _items_by_type(items, record_type: DryRunRecordType):
    return [it for it in items if it.record_type is record_type]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_thermo_clean_db_classifies_dependencies_as_create(db_engine) -> None:
    """A clean DB should classify the species/species_entry as ``would_create``
    and the thermo row as ``would_append``."""
    bundle = _load_bundle("thermo-bundle-v0.json")

    with _isolated_session(db_engine) as session:
        before = _table_counts(session)
        result = dry_run_contribution_bundle(session, bundle)
        after = _table_counts(session)
        # No flushes, no commits — table counts must match exactly.
        assert before == after

    assert result.bundle_valid is True
    assert result.bundle_kind is BundleKind.thermo

    species_items = _items_by_type(result.items, DryRunRecordType.species)
    species_entry_items = _items_by_type(result.items, DryRunRecordType.species_entry)
    thermo_items = _items_by_type(result.items, DryRunRecordType.thermo)

    assert len(species_items) == 1
    assert species_items[0].action is DryRunAction.would_create
    assert species_items[0].hosted_identity is not None
    assert "inchi_key" in species_items[0].hosted_identity

    assert len(species_entry_items) == 1
    assert species_entry_items[0].action is DryRunAction.would_create

    assert len(thermo_items) == 1
    assert thermo_items[0].action is DryRunAction.would_append

    summary = result.summary
    assert summary.would_append == 1
    assert summary.would_reuse == 0
    assert summary.errors == 0


def test_dry_run_thermo_existing_species_classifies_as_reuse(db_engine) -> None:
    """If the same species already exists on hosted, dry-run must say reuse."""
    bundle = _load_bundle("thermo-bundle-v0.json")

    with _isolated_session(db_engine) as session:
        # Seed the same water species via a real upload — this creates the
        # species, species_entry, and one thermo row.
        seed = ThermoUploadRequest(
            species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
            scientific_origin="computed",
            h298_kj_mol=-241.8,
            s298_j_mol_k=188.8,
            note="seeded for dry-run reuse test",
        )
        persist_thermo_upload(session, seed)
        session.flush()

        before = _table_counts(session)
        result = dry_run_contribution_bundle(session, bundle)
        after = _table_counts(session)
        assert before == after

    species_items = _items_by_type(result.items, DryRunRecordType.species)
    species_entry_items = _items_by_type(result.items, DryRunRecordType.species_entry)
    thermo_items = _items_by_type(result.items, DryRunRecordType.thermo)

    assert species_items[0].action is DryRunAction.would_reuse
    assert species_entry_items[0].action is DryRunAction.would_reuse
    # Append-only product is still "would_append" — never collapsed to reuse.
    assert thermo_items[0].action is DryRunAction.would_append


def test_dry_run_kinetics_clean_db(db_engine) -> None:
    bundle = _load_bundle("kinetics-bundle-v0.json")

    with _isolated_session(db_engine) as session:
        before = _table_counts(session)
        result = dry_run_contribution_bundle(session, bundle)
        after = _table_counts(session)
        assert before == after

    assert result.bundle_valid is True
    assert result.bundle_kind is BundleKind.kinetics

    # Two reactant participants (both H atoms) + one product participant ⇒
    # 3 species items + 3 species_entry items.
    assert len(_items_by_type(result.items, DryRunRecordType.species)) == 3
    assert len(_items_by_type(result.items, DryRunRecordType.species_entry)) == 3

    rxn_items = _items_by_type(result.items, DryRunRecordType.chem_reaction)
    assert len(rxn_items) == 1
    # Clean DB: participants are missing, so reaction must be would_create —
    # the service must NOT issue a misleading reuse here.
    assert rxn_items[0].action is DryRunAction.would_create

    kinetics_items = _items_by_type(result.items, DryRunRecordType.kinetics)
    assert len(kinetics_items) == 1
    assert kinetics_items[0].action is DryRunAction.would_append

    # Provenance items present and classified as would_create on a clean DB.
    sr_items = _items_by_type(result.items, DryRunRecordType.software_release)
    wt_items = _items_by_type(result.items, DryRunRecordType.workflow_tool_release)
    assert len(sr_items) == 1 and sr_items[0].action is DryRunAction.would_create
    assert len(wt_items) == 1 and wt_items[0].action is DryRunAction.would_create


def test_dry_run_kinetics_existing_reaction_and_provenance_classifies_as_reuse(
    db_engine,
) -> None:
    """Seed a matching kinetics record, then dry-run the example bundle and
    confirm the reaction graph + provenance both light up as would_reuse."""
    bundle = _load_bundle("kinetics-bundle-v0.json")

    with _isolated_session(db_engine) as session:
        seed = KineticsUploadRequest(
            reaction={
                "reversible": False,
                "reactants": [
                    {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                    {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                ],
                "products": [
                    {
                        "species_entry": {
                            "smiles": "[H][H]",
                            "charge": 0,
                            "multiplicity": 1,
                        }
                    }
                ],
            },
            scientific_origin="computed",
            model_kind="modified_arrhenius",
            software_release={"name": "gaussian", "version": "09", "revision": "D.01"},
            workflow_tool_release={"name": "ARC", "version": "1.0.0"},
            a=1.23e12,
            a_units="cm3_mol_s",
            n=0.5,
            reported_ea=12.3,
            reported_ea_units="kj_mol",
            tmin_k=300.0,
            tmax_k=2000.0,
            degeneracy=2.0,
            tunneling_model="eckart",
            note="seeded for dry-run reuse test",
        )
        persist_kinetics_upload(session, seed)
        session.flush()

        before = _table_counts(session)
        result = dry_run_contribution_bundle(session, bundle)
        after = _table_counts(session)
        assert before == after

    species_items = _items_by_type(result.items, DryRunRecordType.species)
    rxn_items = _items_by_type(result.items, DryRunRecordType.chem_reaction)
    sr_items = _items_by_type(result.items, DryRunRecordType.software_release)
    wt_items = _items_by_type(result.items, DryRunRecordType.workflow_tool_release)
    kinetics_items = _items_by_type(result.items, DryRunRecordType.kinetics)

    # All three participant species exist now — every species item is reuse.
    assert all(it.action is DryRunAction.would_reuse for it in species_items)
    assert rxn_items[0].action is DryRunAction.would_reuse
    assert rxn_items[0].hosted_identity is not None
    assert "stoichiometry_hash" in rxn_items[0].hosted_identity
    assert sr_items[0].action is DryRunAction.would_reuse
    assert wt_items[0].action is DryRunAction.would_reuse
    # Append-only product never collapses.
    assert kinetics_items[0].action is DryRunAction.would_append


def test_dry_run_thermo_with_literature_provenance_creates_item(db_engine) -> None:
    """A thermo upload carrying a DOI must produce a literature preview item."""
    bundle_dict = json.loads((EXAMPLES_DIR / "thermo-bundle-v0.json").read_text())
    bundle_dict["records"]["thermo_uploads"][0]["literature"] = {
        "kind": "article",
        "title": "Synthetic example",
        "year": 2024,
        "doi": "10.1234/example.doi",
    }
    bundle = ContributionBundleV0.model_validate(bundle_dict)

    with _isolated_session(db_engine) as session:
        before = _table_counts(session)
        result = dry_run_contribution_bundle(session, bundle)
        after = _table_counts(session)
        assert before == after

    lit_items = _items_by_type(result.items, DryRunRecordType.literature)
    assert len(lit_items) == 1
    assert lit_items[0].action is DryRunAction.would_create
    assert lit_items[0].hosted_identity == {"doi": "10.1234/example.doi"}


def test_summary_counts_match_items(db_engine) -> None:
    bundle = _load_bundle("kinetics-bundle-v0.json")

    with _isolated_session(db_engine) as session:
        result = dry_run_contribution_bundle(session, bundle)

    actions = [it.action for it in result.items]
    assert result.summary.records_seen == len(result.items)
    assert result.summary.would_create == sum(
        1 for a in actions if a is DryRunAction.would_create
    )
    assert result.summary.would_reuse == sum(
        1 for a in actions if a is DryRunAction.would_reuse
    )
    assert result.summary.would_append == sum(
        1 for a in actions if a is DryRunAction.would_append
    )
