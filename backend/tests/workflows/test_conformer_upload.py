from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationFreqResult,
    CalculationInputGeometry,
    CalculationOutputGeometry,
    CalculationSPResult,
)
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationType,
)
from app.db.models.energy_correction import AppliedEnergyCorrection
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
            "type": "freq",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "freq_result": {"n_imag": 0},
        },
        label=label,
        note="uploaded conformer",
    )


def test_persist_conformer_upload_creates_expected_rows(db_conn) -> None:
    with Session(db_conn) as session:
        with session.begin():
            outcome = persist_conformer_upload(
                session, _hydrogen_request(label="conf-a")
            )
            observation = outcome.observation
            assert outcome.primary_calculation.calculation_id == observation.calculations[0].id
            assert outcome.primary_calculation.role == "primary"

            stored_observation = session.scalar(
                select(ConformerObservation).where(
                    ConformerObservation.id == observation.id
                )
            )
            assert stored_observation is not None

            assert len(observation.calculations) >= 1
            calculation = observation.calculations[0]
            assert calculation.species_entry_id is not None

            # Primary calc here is type=sp; under the narrowed fallback,
            # only opt gets an auto-created calculation_output_geometry
            # row. Sp claims no output geometry unless declared.
            geometry_link = session.scalar(
                select(CalculationOutputGeometry).where(
                    CalculationOutputGeometry.calculation_id == calculation.id
                )
            )
            assert geometry_link is None

            # The conformer geometry is still resolved as a Geometry row
            # via the upload's top-level ``geometry`` field, even when no
            # per-calc output_geometry row is written. Scope the lookup to
            # THIS calculation's input geometry rather than the global
            # lowest-id row — the shared session-scoped test DB accumulates
            # committed geometries from other tests, so a global
            # ``order_by(Geometry.id)`` is not isolation-safe.
            input_geom_link = session.scalar(
                select(CalculationInputGeometry).where(
                    CalculationInputGeometry.calculation_id == calculation.id
                )
            )
            assert input_geom_link is not None
            geometry = session.get(Geometry, input_geom_link.geometry_id)
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
    db_conn,
) -> None:
    with Session(db_conn) as session:
        with session.begin():
            first_outcome = persist_conformer_upload(
                session, _hydrogen_request(label="conf-a")
            )
            second_outcome = persist_conformer_upload(
                session, _hydrogen_request(label="conf-a")
            )
            first = first_outcome.observation
            second = second_outcome.observation

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


def test_persist_conformer_upload_creates_linked_statmech_record(db_conn) -> None:
    request = ConformerUploadRequest(
        species_entry={
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        geometry={
            "xyz_text": "1\nH atom\nH 0.0 0.0 0.0",
        },
        # A freq job, because the statmech block below claims it as the
        # record's frequency basis. This said ``sp`` until statmech began
        # enforcing DR-0028 Requirement 1: the subject of the test is that
        # the statmech row is created and linked, not that a role may
        # contradict the type of the job it names.
        calculation={
            "type": "freq",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "freq_result": {"n_imag": 0},
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

    with Session(db_conn) as session:
        with session.begin():
            observation = persist_conformer_upload(session, request).observation

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


def test_conformer_upload_with_additional_calculations(db_conn) -> None:
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

    with Session(db_conn) as session, session.begin():
        observation = persist_conformer_upload(session, request).observation

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

        # Under the narrowed fallback, only opt auto-claims the conformer
        # geometry as a (role=final, output_order=1) output. Freq and sp
        # produce zero output_geometry rows unless the producer declares
        # them explicitly.
        geo_links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id.in_(
                    [c.id for c in calcs]
                )
            )
        ).all()
        assert len(geo_links) == 1
        assert geo_links[0].calculation_id == opt_calc.id
        assert geo_links[0].role == CalculationGeometryRole.final

        # opt has no input_geometry row (its true input is the pre-opt
        # xyz which the producer doesn't currently surface). freq and sp
        # each get exactly one row pointing at the conformer geometry.
        opt_inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == opt_calc.id
            )
        ).all()
        assert opt_inputs == []

        freq_inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == freq_calc.id
            )
        ).all()
        assert len(freq_inputs) == 1
        assert freq_inputs[0].input_order == 1

        sp_inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == sp_calc.id
            )
        ).all()
        assert len(sp_inputs) == 1
        assert sp_inputs[0].input_order == 1

        shared_geo_id = geo_links[0].geometry_id
        assert freq_inputs[0].geometry_id == shared_geo_id
        assert sp_inputs[0].geometry_id == shared_geo_id


def test_conformer_upload_opt_primary_has_no_input_geometry(db_conn) -> None:
    """An opt-primary upload yields one output_geometry row (final) and
    zero input_geometry rows — opt's true input is the pre-opt xyz, which
    the producer doesn't surface."""
    request = ConformerUploadRequest(
        species_entry={
            "smiles": "[H][H]",
            "charge": 0,
            "multiplicity": 1,
        },
        geometry={"xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"},
        calculation={
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "opt_result": {"converged": True},
        },
        label="opt-only",
    )
    with Session(db_conn) as session, session.begin():
        outcome = persist_conformer_upload(session, request)
        opt_id = outcome.primary_calculation.calculation_id

        outputs = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == opt_id
            )
        ).all()
        assert len(outputs) == 1

        inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == opt_id
            )
        ).all()
        assert inputs == []


def test_conformer_upload_statmech_resolves_literature_from_payload(
    db_conn, monkeypatch,
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
            "type": "freq",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "freq_result": {"n_imag": 0},
        },
        label="conf-lit-doi",
        statmech={
            "scientific_origin": "computed",
            "statmech_treatment": "rrho",
            "uploaded_calculation_role": "freq",
            "note": "statmech-with-literature-payload",
            "literature": {
                "doi": "10.1063/conformer-statmech",
                "title": "fallback if DOI lookup fails",
            },
        },
    )

    with Session(db_conn) as session, session.begin():
        observation = persist_conformer_upload(session, request).observation
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


def test_primitive_conformer_explicit_input_geometries_for_opt(
    db_conn,
) -> None:
    """Primitive ``/uploads/conformers``: a primary opt that declares
    ``input_geometries`` lands a row, distinct from opt's converged
    output geometry."""
    pre_opt_xyz = "2\npre-opt H2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.80"
    request = ConformerUploadRequest(
        species_entry={
            "smiles": "[H][H]",
            "charge": 0,
            "multiplicity": 1,
        },
        geometry={"xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"},
        calculation={
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "opt_result": {"converged": True},
            "input_geometries": [{"xyz_text": pre_opt_xyz}],
        },
        label="opt-explicit-input",
    )
    with Session(db_conn) as session, session.begin():
        outcome = persist_conformer_upload(session, request)
        opt_id = outcome.primary_calculation.calculation_id

        inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == opt_id
            )
        ).all()
        assert len(inputs) == 1
        assert inputs[0].input_order == 1

        outputs = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == opt_id
            )
        ).all()
        assert len(outputs) == 1
        assert inputs[0].geometry_id != outputs[0].geometry_id


def test_primitive_conformer_empty_input_geometries_uses_fallback(
    db_conn,
) -> None:
    """With no ``input_geometries`` declared, the prior PR's freq fallback
    still fires for an additional freq calc and skips the primary opt."""
    request = ConformerUploadRequest(
        species_entry={
            "smiles": "[H][H]",
            "charge": 0,
            "multiplicity": 1,
        },
        geometry={"xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"},
        calculation={
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "opt_result": {"converged": True},
        },
        additional_calculations=[
            {
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "freq_result": {"n_imag": 0},
            },
        ],
        label="primitive-fallback",
    )
    with Session(db_conn) as session, session.begin():
        outcome = persist_conformer_upload(session, request)
        opt_id = outcome.primary_calculation.calculation_id
        freq_id = outcome.additional_calculations[0].calculation_id

        opt_inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == opt_id
            )
        ).all()
        assert opt_inputs == []

        freq_inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == freq_id
            )
        ).all()
        assert len(freq_inputs) == 1


def test_primitive_conformer_explicit_output_geometries_for_opt(
    db_conn,
) -> None:
    """Primitive ``/uploads/conformers``: a primary opt that declares
    ``output_geometries`` lands a row with the producer-declared role,
    and the narrowed fallback does NOT also fire."""
    declared_xyz = "2\ndeclared-final\nH 0.0 0.0 0.0\nH 0.0 0.0 0.99"
    request = ConformerUploadRequest(
        species_entry={
            "smiles": "[H][H]",
            "charge": 0,
            "multiplicity": 1,
        },
        geometry={"xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"},
        calculation={
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "opt_result": {"converged": True},
            "output_geometries": [
                {
                    "geometry": {"xyz_text": declared_xyz},
                    "role": "final",
                },
            ],
        },
        label="opt-explicit-output",
    )
    with Session(db_conn) as session, session.begin():
        outcome = persist_conformer_upload(session, request)
        opt_id = outcome.primary_calculation.calculation_id

        rows = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == opt_id
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].role == CalculationGeometryRole.final
        assert rows[0].output_order == 1


def test_primitive_conformer_empty_output_geometries_freq_sp(db_conn) -> None:
    """Primitive ``/uploads/conformers``: with no ``output_geometries``
    declared on the additional freq calc, the narrowed fallback skips
    freq (not in the {opt} set), so freq gets ZERO rows."""
    request = ConformerUploadRequest(
        species_entry={
            "smiles": "[H][H]",
            "charge": 0,
            "multiplicity": 1,
        },
        geometry={"xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"},
        calculation={
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "opt_result": {"converged": True},
        },
        additional_calculations=[
            {
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "freq_result": {"n_imag": 0},
            },
            {
                "type": "sp",
                "software_release": {"name": "Orca", "version": "5.0"},
                "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVTZ"},
                "sp_result": {"electronic_energy_hartree": -1.195},
            },
        ],
        label="primitive-output-fallback",
    )
    with Session(db_conn) as session, session.begin():
        outcome = persist_conformer_upload(session, request)
        opt_id = outcome.primary_calculation.calculation_id
        freq_id = outcome.additional_calculations[0].calculation_id
        sp_id = outcome.additional_calculations[1].calculation_id

        opt_outputs = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == opt_id
            )
        ).all()
        # opt still gets the fallback row.
        assert len(opt_outputs) == 1
        assert opt_outputs[0].role == CalculationGeometryRole.final

        freq_outputs = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == freq_id
            )
        ).all()
        sp_outputs = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == sp_id
            )
        ).all()
        # Behavior change: freq and sp produce zero rows.
        assert freq_outputs == []
        assert sp_outputs == []


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


# ---------------------------------------------------------------------------
# Applied energy corrections: the source keys must name what they say
#
# Both keys used to be tested for ``is not None`` alone, so *any* string
# resolved to the primary observation/calculation. The test that matters is
# therefore not the rejection but the acceptance: a correction naming the
# additional freq calculation must land on that row, not on the primary.
#
# The conformer half then resolved against ``label`` for a while, because a
# label was the only name this request gave its single conformer. That was
# still a conflation -- a label is a human tag that also feeds
# conformer-group matching -- so the namespace is now the request's own
# ``conformer_key``, the counterpart to the ``key`` it already puts on its
# calculations.
# ---------------------------------------------------------------------------


def _correction_request(
    *,
    label: str,
    conformer_key: str | None = None,
    source_calculation_key: str | None = "primary_sp",
    source_conformer_key: str | None = None,
) -> ConformerUploadRequest:
    """A conformer upload carrying one frequency-scale-factor correction.

    ``source_calculation_key`` defaults to a declared key because the
    payload schema already requires a scale-factor correction to name the
    frequency calculation it was applied to; the tests below vary it to
    exercise resolution, not that requirement.
    """
    correction: dict = {
        "frequency_scale_factor": {
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "scale_kind": "zpe",
            "value": 0.977,
        },
        "application_role": "zpe",
        "value": 0.0215,
        "value_unit": "hartree",
    }
    if source_calculation_key is not None:
        correction["source_calculation_key"] = source_calculation_key
    if source_conformer_key is not None:
        correction["source_conformer_key"] = source_conformer_key
    return ConformerUploadRequest(
        species_entry={"smiles": "[H]", "charge": 0, "multiplicity": 2},
        geometry={"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        calculation={
            "key": "primary_sp",
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "sp_result": {"electronic_energy_hartree": -0.5},
        },
        additional_calculations=[
            {
                "key": "the_freq_job",
                "type": "freq",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "freq_result": {"n_imag": 0},
            },
        ],
        label=label,
        conformer_key=conformer_key,
        applied_energy_corrections=[correction],
    )


def test_correction_calculation_key_links_the_calculation_it_names(
    db_conn,
) -> None:
    """The named key wins over the primary calculation.

    This is the assertion the old code could not pass: it resolved every
    non-null key to ``calculation.id``, so a correction naming the freq
    job was silently attached to the primary sp job instead.
    """
    with Session(db_conn) as session, session.begin():
        outcome = persist_conformer_upload(
            session,
            _correction_request(
                label="aec-names-freq", source_calculation_key="the_freq_job"
            ),
        )
        freq_id = outcome.additional_calculations[0].calculation_id
        primary_id = outcome.primary_calculation.calculation_id

        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.source_conformer_observation_id.is_(None)
            )
        ).all()
        assert len(applied) == 1
        assert applied[0].source_calculation_id == freq_id
        assert applied[0].source_calculation_id != primary_id


def test_correction_conformer_key_links_the_observation_it_names(
    db_conn,
) -> None:
    with Session(db_conn) as session, session.begin():
        outcome = persist_conformer_upload(
            session,
            _correction_request(
                label="aec-names-key",
                conformer_key="the-conformer",
                source_conformer_key="the-conformer",
            ),
        )
        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.source_conformer_observation_id
                == outcome.observation.id
            )
        ).all()
        assert len(applied) == 1


def test_correction_conformer_key_is_not_the_label(db_conn) -> None:
    """``label`` was the namespace and is not one any more.

    A label is a human tag that also drives conformer-group matching, so
    a reference resolving through it broke whenever the label was changed
    for grouping reasons -- and a depositor with no label could not name
    their conformer at all. ``conformer_key`` answers only to references,
    which is the whole of its job.
    """
    with Session(db_conn) as session, session.begin():
        with pytest.raises(CodedValueError) as excinfo:
            persist_conformer_upload(
                session,
                _correction_request(
                    label="a-human-label",
                    conformer_key="a-machine-key",
                    source_conformer_key="a-human-label",
                ),
            )
    assert excinfo.value.context["declared_keys"] == ["a-machine-key"]


def test_correction_conformer_key_works_without_any_label(db_conn) -> None:
    """The deposit that could not previously be expressed at all."""
    with Session(db_conn) as session, session.begin():
        request = _correction_request(
            label="unused",
            conformer_key="unlabelled",
            source_conformer_key="unlabelled",
        )
        request.label = None
        outcome = persist_conformer_upload(session, request)
        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.source_conformer_observation_id
                == outcome.observation.id
            )
        ).all()
        assert len(applied) == 1


def test_correction_with_undeclared_calculation_key_is_refused(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        with pytest.raises(CodedValueError) as excinfo:
            persist_conformer_upload(
                session,
                _correction_request(
                    label="aec-ghost-calc",
                    source_calculation_key="the_freq_jbo",
                ),
            )
    assert (
        excinfo.value.code == "applied_energy_correction_source_key_undeclared"
    )
    assert excinfo.value.context["key"] == "the_freq_jbo"
    assert excinfo.value.context["declared_keys"] == [
        "primary_sp",
        "the_freq_job",
    ]


def test_correction_with_undeclared_conformer_key_is_refused(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        with pytest.raises(CodedValueError) as excinfo:
            persist_conformer_upload(
                session,
                _correction_request(
                    label="aec-ghost-conf",
                    conformer_key="aec-ghost-conf-key",
                    source_conformer_key="a-different-conformer",
                ),
            )
    assert (
        excinfo.value.code == "applied_energy_correction_source_key_undeclared"
    )
    assert excinfo.value.context["declared_keys"] == ["aec-ghost-conf-key"]


def test_correction_conformer_key_with_no_conformer_key_is_refused(
    db_conn,
) -> None:
    """No ``conformer_key`` means the request named no conformer at all."""
    with Session(db_conn) as session, session.begin():
        request = _correction_request(
            label="aec-no-key", source_conformer_key="anything"
        )
        with pytest.raises(CodedValueError) as excinfo:
            persist_conformer_upload(session, request)
    assert excinfo.value.context["declared_keys"] == []
