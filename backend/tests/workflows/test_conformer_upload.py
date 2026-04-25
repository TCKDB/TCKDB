from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationFreqResult,
    CalculationOutputGeometry,
    CalculationSPResult,
)
from app.db.models.common import CalculationDependencyRole, CalculationType
from app.db.models.geometry import Geometry
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.schemas.workflows.conformer_upload import ConformerUploadRequest
from app.workflows.conformer import persist_conformer_upload


def _hydrogen_request(*, label: str | None = None) -> ConformerUploadRequest:
    return ConformerUploadRequest(
        species_entry={
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        geometry={
            "xyz_text": "1\nH atom\nH 0.0 0.0 0.0",
        },
        calculation={
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        label=label,
        note="uploaded conformer",
    )


def test_persist_conformer_upload_creates_expected_rows(db_engine) -> None:
    with Session(db_engine) as session:
        with session.begin():
            observation = persist_conformer_upload(
                session, _hydrogen_request(label="conf-a")
            )

            stored_observation = session.scalar(
                select(ConformerObservation).where(
                    ConformerObservation.id == observation.id
                )
            )
            assert stored_observation is not None

            assert len(observation.calculations) >= 1
            calculation = observation.calculations[0]
            assert calculation.species_entry_id is not None

            geometry_link = session.scalar(
                select(CalculationOutputGeometry).where(
                    CalculationOutputGeometry.calculation_id == calculation.id
                )
            )
            assert geometry_link is not None

            geometry = session.scalar(
                select(Geometry).where(Geometry.id == geometry_link.geometry_id)
            )
            assert geometry is not None
            assert geometry.natoms == 1

            conformer_group = session.scalar(
                select(ConformerGroup).where(
                    ConformerGroup.id == observation.conformer_group_id
                )
            )
            assert conformer_group is not None
            assert conformer_group.label == "conformer_1"

            assert session.scalar(select(Species)) is not None
            assert session.scalar(select(SpeciesEntry)) is not None


def test_persist_conformer_upload_reuses_species_entry_and_labeled_group(
    db_engine,
) -> None:
    with Session(db_engine) as session:
        with session.begin():
            first = persist_conformer_upload(session, _hydrogen_request(label="conf-a"))
            second = persist_conformer_upload(
                session, _hydrogen_request(label="conf-a")
            )

            first_group = session.scalar(
                select(ConformerGroup).where(
                    ConformerGroup.id == first.conformer_group_id
                )
            )
            second_group = session.scalar(
                select(ConformerGroup).where(
                    ConformerGroup.id == second.conformer_group_id
                )
            )
            first_calc = first.calculations[0]
            second_calc = second.calculations[0]

            assert first_group is not None
            assert second_group is not None
            assert first.id != second.id
            assert first_group.id == second_group.id

            assert first_calc is not None
            assert second_calc is not None
            assert first_calc.id != second_calc.id
            assert first_calc.species_entry_id == second_calc.species_entry_id

            grouped_observations = session.scalars(
                select(ConformerObservation).where(
                    ConformerObservation.conformer_group_id == first_group.id
                )
            ).all()
            grouped_ids = {obs.id for obs in grouped_observations}
            assert {first.id, second.id}.issubset(grouped_ids)


def test_persist_conformer_upload_creates_linked_statmech_record(db_engine) -> None:
    request = ConformerUploadRequest(
        species_entry={
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        geometry={
            "xyz_text": "1\nH atom\nH 0.0 0.0 0.0",
        },
        calculation={
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        label="conf-stat",
        statmech={
            "scientific_origin": "computed",
            "software_release": {"name": "Gaussian", "version": "16"},
            "uploaded_calculation_role": "freq",
            "statmech_treatment": "rrho_1d",
            "torsions": [
                {
                    "torsion_index": 1,
                    "dimension": 1,
                    "coordinates": [
                        {
                            "coordinate_index": 1,
                            "atom1_index": 1,
                            "atom2_index": 2,
                            "atom3_index": 3,
                            "atom4_index": 4,
                        }
                    ],
                }
            ],
        },
    )

    with Session(db_engine) as session:
        with session.begin():
            observation = persist_conformer_upload(session, request)

            assert len(observation.calculations) >= 1
            calculation = observation.calculations[0]
            assert calculation.species_entry_id is not None

            statmech = session.scalar(
                select(Statmech).where(
                    Statmech.species_entry_id == calculation.species_entry_id,
                    Statmech.statmech_treatment == "rrho_1d",
                )
            )
            assert statmech is not None
            assert statmech.software_release_id == calculation.software_release_id
            assert len(statmech.source_calculations) == 1
            assert statmech.source_calculations[0].calculation_id == calculation.id
            assert statmech.source_calculations[0].role.value == "freq"
            assert len(statmech.torsions) == 1
            assert len(statmech.torsions[0].coordinates) == 1


def test_conformer_upload_with_additional_calculations(db_engine) -> None:
    """Upload with primary opt + freq and sp additional calculations."""
    request = ConformerUploadRequest(
        species_entry={
            "smiles": "[H][H]",
            "charge": 0,
            "multiplicity": 1,
        },
        geometry={
            "xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74",
        },
        calculation={
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "opt_result": {
                "converged": True,
                "final_energy_hartree": -1.172,
            },
        },
        additional_calculations=[
            {
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "freq_result": {
                    "n_imag": 0,
                    "zpe_hartree": 0.010,
                },
            },
            {
                "type": "sp",
                "software_release": {"name": "Orca", "version": "5.0"},
                "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVTZ"},
                "sp_result": {
                    "electronic_energy_hartree": -1.195,
                },
            },
        ],
        label="h2-full",
    )

    with Session(db_engine) as session, session.begin():
        observation = persist_conformer_upload(session, request)

        primary_calc = observation.calculations[0]
        species_entry_id = primary_calc.species_entry_id

        # 3 calculations total attached to the species entry
        calcs = session.scalars(
            select(Calculation).where(
                Calculation.species_entry_id == species_entry_id
            )
        ).all()
        assert len(calcs) == 3

        opt_calc = next(c for c in calcs if c.type == CalculationType.opt)
        freq_calc = next(c for c in calcs if c.type == CalculationType.freq)
        sp_calc = next(c for c in calcs if c.type == CalculationType.sp)

        # Primary calc is the opt (linked to the observation)
        assert opt_calc.conformer_observation_id == observation.id
        assert freq_calc.conformer_observation_id == observation.id
        assert sp_calc.conformer_observation_id == observation.id

        # Freq result
        freq_result = session.get(CalculationFreqResult, freq_calc.id)
        assert freq_result is not None
        assert freq_result.n_imag == 0
        assert freq_result.zpe_hartree == pytest.approx(0.010)

        # SP result
        sp_result = session.get(CalculationSPResult, sp_calc.id)
        assert sp_result is not None
        assert sp_result.electronic_energy_hartree == pytest.approx(-1.195)

        # Dependency edges: freq→opt and sp→opt
        deps = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.parent_calculation_id == opt_calc.id
            )
        ).all()
        assert len(deps) == 2
        dep_roles = {d.dependency_role for d in deps}
        assert CalculationDependencyRole.freq_on in dep_roles
        assert CalculationDependencyRole.single_point_on in dep_roles

        # All 3 calcs share the same geometry
        geo_links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id.in_(
                    [c.id for c in calcs]
                )
            )
        ).all()
        assert len(geo_links) == 3
        assert len({g.geometry_id for g in geo_links}) == 1


def test_conformer_upload_statmech_resolves_literature_from_payload(
    db_engine, monkeypatch,
) -> None:
    """Nested literature payload on statmech must resolve into a Literature row,
    without the upload ever exposing a raw ``literature_id`` FK.
    """
    from app.db.models.literature import Literature

    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Statmech study on hydrogen",
            "container-title": ["J. Chem. Phys."],
            "issued": 2010,
            "URL": f"https://doi.org/{doi}",
        },
    )

    request = ConformerUploadRequest(
        species_entry={"smiles": "[H]", "charge": 0, "multiplicity": 2},
        geometry={"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        calculation={
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        label="conf-lit-doi",
        statmech={
            "scientific_origin": "computed",
            "statmech_treatment": "rrho",
            "note": "statmech-with-literature-payload",
            "literature": {
                "doi": "10.1063/conformer-statmech",
                "title": "fallback if DOI lookup fails",
            },
        },
    )

    with Session(db_engine) as session, session.begin():
        observation = persist_conformer_upload(session, request)
        calculation = observation.calculations[0]

        statmech = session.scalar(
            select(Statmech).where(
                Statmech.species_entry_id == calculation.species_entry_id,
                Statmech.note == "statmech-with-literature-payload",
            )
        )
        assert statmech is not None
        assert statmech.literature_id is not None

        lit = session.get(Literature, statmech.literature_id)
        assert lit is not None
        assert lit.title == "Statmech study on hydrogen"
        assert lit.doi == "10.1063/conformer-statmech"


def test_conformer_upload_rejects_irc_additional() -> None:
    """Conformer upload should reject IRC as an additional calculation type."""
    with pytest.raises(ValueError, match="not allowed"):
        ConformerUploadRequest(
            species_entry={
                "smiles": "[H]",
                "charge": 0,
                "multiplicity": 2,
            },
            geometry={"xyz_text": "1\n\nH 0 0 0"},
            calculation={
                "type": "opt",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            },
            additional_calculations=[
                {
                    "type": "irc",
                    "software_release": {"name": "Gaussian", "version": "16"},
                    "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                },
            ],
        )
