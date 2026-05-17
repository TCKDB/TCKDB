"""Tests for the new public introspection helpers on builder uploads.

These cover the four additions that lifted the Phase-7 demo out of
private state:

- ``upload.iter_calculations(*, with_artifacts_only=False)``
- ``upload.iter_calculation_entries(*, with_artifacts_only=False)``
- ``upload.iter_artifacts()``
- ``upload.artifact_plan_preview(*, starting_calculation_id=1000)``

Plus a leakage guard that fails if either demo file ever reaches
into ``_species_calc_pairs`` or synthesises its own
``calculation_keys`` mock again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tckdb_client.builders import (
    Calculation,
    CalculationEntry,
    ChemReaction,
    ComputedReactionUpload,
    ComputedSpeciesUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    PlannedArtifactUpload,
    Species,
    SoftwareRelease,
    TransitionState,
)


def _sr() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


# ---------------------------------------------------------------------
# ComputedSpeciesUpload fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def water_geom() -> Geometry:
    return Geometry.from_xyz(
        "3\nwater\n"
        "O 0.0 0.0 0.117\n"
        "H 0.0 0.757 -0.469\n"
        "H 0.0 -0.757 -0.469"
    )


@pytest.fixture
def water_upload(water_geom) -> ComputedSpeciesUpload:
    sr, lot = _sr(), _lot()
    opt = Calculation.opt(
        sr, lot, output_geometry=water_geom, converged=True,
        final_energy_hartree=-76.4, label="opt",
    )
    freq = Calculation.freq(
        sr, lot, input_geometry=water_geom, n_imag=0,
        depends_on=opt, label="freq",
    )
    sp = Calculation.sp(
        sr, lot, input_geometry=water_geom,
        electronic_energy_hartree=-76.45, depends_on=opt, label="sp",
    )
    freq.add_artifact("freq.log", kind="output_log")
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
        calculations=[opt, freq, sp],
        primary_calculation=opt,
    )


# ---------------------------------------------------------------------
# ComputedReactionUpload fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def reaction_upload() -> ComputedReactionUpload:
    sr, lot = _sr(), _lot()
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True, label="ch4 opt")
    ch4_sp = Calculation.sp(
        sr, lot, electronic_energy_hartree=-40.5,
        depends_on=ch4_opt, label="ch4 sp",
    )
    ch4_sp.add_artifact("ch4_sp.log", kind="output_log")
    ch3_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True, label="ch3 opt")
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom, converged=True, label="ts opt",
    )
    ts_opt.add_artifact("ts_opt.log", kind="output_log")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt, ch4_sp], ch3: [ch3_opt]},
    )


# ---------------------------------------------------------------------
# iter_calculations
# ---------------------------------------------------------------------


class TestIterCalculationsSpecies:
    def test_yields_all_calcs_in_payload_order(self, water_upload):
        labels = [c.label for c in water_upload.iter_calculations()]
        assert labels == ["opt", "freq", "sp"]

    def test_with_artifacts_only_filters(self, water_upload):
        labels = [
            c.label for c in water_upload.iter_calculations(with_artifacts_only=True)
        ]
        assert labels == ["freq"]

    def test_default_is_unfiltered(self, water_upload):
        assert len(list(water_upload.iter_calculations())) == 3


class TestIterCalculationsReaction:
    def test_ts_first_then_species_in_unique_order(self, reaction_upload):
        labels = [c.label for c in reaction_upload.iter_calculations()]
        # TS bucket, then species in reaction.unique_species() order
        # (reactants then products): ch3, ch4.
        assert labels == ["ts opt", "ch3 opt", "ch4 opt", "ch4 sp"]

    def test_with_artifacts_only_filters(self, reaction_upload):
        labels = [
            c.label for c in
            reaction_upload.iter_calculations(with_artifacts_only=True)
        ]
        # Only the TS opt and the CH4 sp carry artifacts in the fixture.
        assert labels == ["ts opt", "ch4 sp"]

    def test_deterministic_across_repeated_calls(self, reaction_upload):
        first = list(reaction_upload.iter_calculations())
        second = list(reaction_upload.iter_calculations())
        assert first == second


# ---------------------------------------------------------------------
# iter_calculation_entries
# ---------------------------------------------------------------------


class TestIterCalculationEntries:
    def test_species_upload_carries_species_in_every_entry(self, water_upload):
        entries = list(water_upload.iter_calculation_entries())
        assert len(entries) == 3
        # Bucket and species are identical for every entry on the
        # computed-species side — there's only one species.
        for e in entries:
            assert isinstance(e, CalculationEntry)
            assert e.bucket == "water"
            assert e.species is water_upload.species

    def test_reaction_upload_ts_entries_have_no_species(self, reaction_upload):
        ts_entries = [
            e for e in reaction_upload.iter_calculation_entries()
            if e.bucket == "TS"
        ]
        assert len(ts_entries) == 1
        assert ts_entries[0].species is None
        assert ts_entries[0].calculation.label == "ts opt"

    def test_reaction_upload_species_entries_have_bucket_and_species(
        self, reaction_upload,
    ):
        species_entries = [
            e for e in reaction_upload.iter_calculation_entries()
            if e.bucket != "TS"
        ]
        assert {e.bucket for e in species_entries} == {"CH3", "CH4"}
        for e in species_entries:
            assert e.species is not None
            assert e.species.label == e.bucket

    def test_with_artifacts_only_filters_entries(self, reaction_upload):
        entries = list(
            reaction_upload.iter_calculation_entries(with_artifacts_only=True)
        )
        assert [(e.bucket, e.calculation.label) for e in entries] == [
            ("TS", "ts opt"),
            ("CH4", "ch4 sp"),
        ]


# ---------------------------------------------------------------------
# iter_artifacts
# ---------------------------------------------------------------------


class TestIterArtifacts:
    def test_species_upload_yields_only_calcs_with_artifacts(self, water_upload):
        pairs = list(water_upload.iter_artifacts())
        assert len(pairs) == 1
        calc, art = pairs[0]
        assert calc.label == "freq"
        assert art.kind == "output_log"

    def test_reaction_upload_yields_in_walk_order(self, reaction_upload):
        pairs = list(reaction_upload.iter_artifacts())
        assert [(c.label, a.kind) for c, a in pairs] == [
            ("ts opt", "output_log"),
            ("ch4 sp", "output_log"),
        ]

    def test_no_artifacts_yields_empty(self, water_geom):
        sr, lot = _sr(), _lot()
        opt = Calculation.opt(
            sr, lot, output_geometry=water_geom, converged=True, label="opt",
        )
        upload = ComputedSpeciesUpload(
            species=Species(smiles="O", charge=0, multiplicity=1),
            calculations=[opt], primary_calculation=opt,
        )
        assert list(upload.iter_artifacts()) == []


# ---------------------------------------------------------------------
# artifact_plan_preview
# ---------------------------------------------------------------------


class TestArtifactPlanPreviewSpecies:
    def test_returns_planned_artifact_uploads(self, water_upload):
        plan = water_upload.artifact_plan_preview()
        assert all(isinstance(p, PlannedArtifactUpload) for p in plan)
        assert len(plan) == 1  # one artifact attached to freq
        assert plan[0].kind == "output_log"

    def test_deterministic_synthetic_ids(self, water_upload):
        p1 = water_upload.artifact_plan_preview()
        p2 = water_upload.artifact_plan_preview()
        assert [
            (e.calculation_key, e.calculation_id, e.kind) for e in p1
        ] == [
            (e.calculation_key, e.calculation_id, e.kind) for e in p2
        ]

    def test_starting_id_is_honored(self, water_upload):
        plan_default = water_upload.artifact_plan_preview()
        plan_custom = water_upload.artifact_plan_preview(
            starting_calculation_id=42,
        )
        # Default starts at 1000; custom offset shifts every entry by
        # the same delta — preview IDs are monotone over payload order.
        default_id = plan_default[0].calculation_id
        custom_id = plan_custom[0].calculation_id
        assert custom_id - 42 == default_id - 1000


class TestArtifactPlanPreviewReaction:
    def test_returns_planned_artifact_uploads(self, reaction_upload):
        plan = reaction_upload.artifact_plan_preview()
        assert all(isinstance(p, PlannedArtifactUpload) for p in plan)
        assert len(plan) == 2

    def test_synthetic_ids_unique_per_calc_key(self, reaction_upload):
        plan = reaction_upload.artifact_plan_preview()
        ids = [p.calculation_id for p in plan]
        assert len(set(ids)) == len(ids)

    def test_preview_matches_real_artifact_plan(self, reaction_upload):
        """Running ``artifact_plan_preview`` then feeding the same
        synthetic response into ``artifact_plan`` directly must produce
        the same plan."""
        plan_via_preview = reaction_upload.artifact_plan_preview()
        # Build the equivalent response by hand and round-trip through
        # artifact_plan.
        payload = reaction_upload.to_payload()
        keys: list[str] = []
        ts = payload.get("transition_state")
        if ts is not None:
            keys.append(ts["calculation"]["key"])
            for extra in ts.get("calculations", []):
                keys.append(extra["key"])
        for sp in payload.get("species", []):
            for conf in sp.get("conformers", []):
                keys.append(conf["calculation"]["key"])
            for extra in sp.get("calculations", []):
                keys.append(extra["key"])
        synthetic_response = {
            "type": "computed_reaction",
            "calculation_keys": {k: 1000 + i for i, k in enumerate(keys)},
        }
        plan_via_artifact_plan = reaction_upload.artifact_plan(
            synthetic_response,
        )
        assert [
            (p.calculation_key, p.calculation_id, p.kind)
            for p in plan_via_preview
        ] == [
            (p.calculation_key, p.calculation_id, p.kind)
            for p in plan_via_artifact_plan
        ]

    def test_preview_does_not_mutate_upload(self, reaction_upload):
        before = reaction_upload.to_payload()
        reaction_upload.artifact_plan_preview()
        after = reaction_upload.to_payload()
        assert before == after


# ---------------------------------------------------------------------
# Demo files no longer touch private state
# ---------------------------------------------------------------------


_DEMO_PY = (
    Path(__file__).resolve().parents[1]
    / "examples" / "builder_computed_reaction_demo.py"
)
_DEMO_NB = (
    Path(__file__).resolve().parents[1]
    / "examples" / "builder_computed_reaction_demo.ipynb"
)


def test_demo_files_do_not_reach_into_species_calc_pairs():
    """The whole point of Phase-8 was to remove this leak — guard it."""
    for path in (_DEMO_PY, _DEMO_NB):
        text = path.read_text(encoding="utf-8")
        assert "_species_calc_pairs" not in text, (
            f"{path.name} still reaches into private upload state; "
            "use ``upload.iter_calculation_entries(...)`` instead."
        )


def test_demo_files_do_not_synthesise_calculation_keys_inline():
    """The mock plan-preview implementation moved into the public
    ``artifact_plan_preview``. The demos should never re-implement
    it inline — that would mean the public method has gaps.

    We look for the exact mock pattern (a dict literal with
    ``"calculation_keys":`` as a key) instead of the bare substring,
    so legitimate doc-comment mentions like "the server's
    calculation_keys field" don't flag.
    """
    forbidden_markers = (
        '"calculation_keys":',
        "'calculation_keys':",
    )
    for path in (_DEMO_PY, _DEMO_NB):
        text = path.read_text(encoding="utf-8")
        offenders = [m for m in forbidden_markers if m in text]
        assert not offenders, (
            f"{path.name} mocks ``calculation_keys`` inline "
            f"(found marker {offenders[0]!r}); call "
            "``upload.artifact_plan_preview()`` instead."
        )


def test_demo_files_use_the_new_public_helpers():
    """Each demo file references at least one of the new helpers — a
    light sanity check that the cleanup actually wired the API in."""
    for path in (_DEMO_PY, _DEMO_NB):
        text = path.read_text(encoding="utf-8")
        assert (
            "iter_calculation_entries" in text
            or "iter_artifacts" in text
            or "iter_calculations" in text
        ), f"{path.name} does not use any of the new iter_* helpers"
        assert "artifact_plan_preview" in text, (
            f"{path.name} does not use artifact_plan_preview"
        )
