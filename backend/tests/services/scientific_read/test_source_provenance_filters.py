"""Thermo and kinetics reads take the full provenance filter set, matched through ONE source calculation.

The set: level of theory, software, software version, method, basis, workflow tool and its version, method family and
DFT rung (the last two classify the calculation's method; they are not a measure of accuracy), and the record's own
``scientific_origin``. One shown source calculation has to satisfy every supplied field at once: a record whose calculation
A ran at level X in software P and whose calculation B ran at level Y in software Q is NOT matched by level X with
software Q. Under ``curated`` a calculation the profile hides contributes nothing. Each test names what it kills.
"""

from __future__ import annotations

import pytest

from app.chemistry.method_families import MethodFamily as F
from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    ProfileRecommendation,
    ReadProfile,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.kinetics import KineticsSourceCalculation
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.schemas.reads.scientific_thermo import ThermoReadRequest
from app.schemas.reads.scientific_thermo_search import ThermoSearchRequest
from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.services.scientific_read.profile import (
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)
from app.services.scientific_read.thermo import get_species_thermo
from app.services.scientific_read.thermo_search import search_thermo
from tests.services.scientific_read._factories import (
    attach_thermo_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_software_release,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    make_workflow_tool_release,
    next_inchi_key,
    set_review,
    unique_smiles,
)

CURATED = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)

#: Calculation A: a hybrid functional (DFT rung 4) in gaussian 16, run by arc 1.0.
A = {"method": "b3lyp", "basis": "def2tzvp", "software": "gaussian", "version": "16", "tool": "arc", "tool_version": "1.0"}
#: Calculation B: a coupled-cluster method (wavefunction) in orca 6, run by rmg 2.0.
B = {"method": "ccsd(t)-f12", "basis": "cc-pvtz-f12", "software": "orca", "version": "6", "tool": "rmg", "tool_version": "2.0"}


def _calculation(session, spec, *, species_entry_id=None):
    lot = make_lot(session, method=spec["method"], basis=spec["basis"])
    software = make_software_release(session, name=spec["software"], version=spec["version"])
    tool = make_workflow_tool_release(session, name=spec["tool"], version=spec["tool_version"])
    calc = make_calculation(
        session,
        type=CalculationType.sp,
        species_entry_id=species_entry_id,
        lot_id=lot.id,
        software_release_id=software.id,
        workflow_tool_release_id=tool.id,
    )
    return calc, lot


class _Thermo:
    """A thermo record with one source calculation per spec."""

    name = "thermo"

    def build(self, session, specs, *, approved_calcs=(), origin=ScientificOriginKind.computed, approve=False):
        species = make_species(session, smiles=unique_smiles(), inchi_key=next_inchi_key("PV"))
        self.entry = make_species_entry(session, species)
        self.record = make_thermo_scalar(session, species_entry=self.entry, scientific_origin=origin)
        self.calcs, self.lots = [], []
        for i, spec in enumerate(specs):
            calc, lot = _calculation(session, spec, species_entry_id=self.entry.id)
            attach_thermo_source_calculation(
                session, thermo=self.record, calculation=calc, role=ThermoCalculationRole.sp if i == 0 else ThermoCalculationRole.freq
            )
            self.calcs.append(calc)
            self.lots.append(lot)
        for i in approved_calcs:
            set_review(session, record_type=SubmissionRecordType.calculation, record_id=self.calcs[i].id, status=RecordReviewStatus.approved)
        if approve:
            set_review(session, record_type=SubmissionRecordType.thermo, record_id=self.record.id, status=RecordReviewStatus.approved)
        return self

    def read(self, session, **filters):
        response = get_species_thermo(session, species_entry_id=self.entry.id, request=ThermoReadRequest(**filters))
        return [r.thermo_ref for r in response.records]

    def search(self, session, **filters):
        response = search_thermo(session, ThermoSearchRequest(species_entry_ref=self.entry.public_ref, **filters))
        return [r.thermo.thermo_ref for r in response.records]

    @property
    def ref(self):
        return self.record.public_ref


class _Kinetics:
    name = "kinetics"

    def build(self, session, specs, *, approved_calcs=(), origin=ScientificOriginKind.computed, approve=False):
        rs = make_species(session, smiles=unique_smiles(), inchi_key=next_inchi_key("KR"))
        ps = make_species(session, smiles=unique_smiles(), inchi_key=next_inchi_key("KP"))
        chem = make_chem_reaction(session, reactants=[rs], products=[ps])
        reactant_entry = make_species_entry(session, rs)
        self.entry = make_reaction_entry(
            session, reaction=chem, reactant_entries=[reactant_entry], product_entries=[make_species_entry(session, ps)]
        )
        self.record = make_kinetics(session, reaction_entry=self.entry, scientific_origin=origin)
        self.calcs, self.lots = [], []
        for i, spec in enumerate(specs):
            calc, lot = _calculation(session, spec, species_entry_id=reactant_entry.id)
            session.add(
                KineticsSourceCalculation(
                    kinetics_id=self.record.id,
                    calculation_id=calc.id,
                    role=KineticsCalculationRole.ts_energy if i == 0 else KineticsCalculationRole.freq,
                )
            )
            self.calcs.append(calc)
            self.lots.append(lot)
        session.flush()
        for i in approved_calcs:
            set_review(session, record_type=SubmissionRecordType.calculation, record_id=self.calcs[i].id, status=RecordReviewStatus.approved)
        if approve:
            set_review(session, record_type=SubmissionRecordType.kinetics, record_id=self.record.id, status=RecordReviewStatus.approved)
        return self

    def read(self, session, **filters):
        response = get_reaction_kinetics(session, reaction_entry_id=self.entry.id, request=KineticsReadRequest(**filters))
        return [r.kinetics_ref for r in response.records]

    @property
    def ref(self):
        return self.record.public_ref


KINDS = pytest.mark.parametrize("kind", [_Thermo, _Kinetics], ids=lambda k: k.name)

#: (filters, whether the record matches). The record has calculation A and calculation B, and nothing else.
CASES = [
    # --- each field alone matches through whichever calculation carries it
    ({"method": "b3lyp"}, True),
    ({"method": "CCSD(T)-F12"}, True),
    ({"method": "mp2"}, False),
    ({"basis": "def2tzvp"}, True),
    ({"basis": "cc-pvtz-f12"}, True),
    ({"basis": "6-31g"}, False),
    ({"software": "gaussian"}, True),
    ({"software": "orca"}, True),
    ({"software": "molpro"}, False),
    ({"software": "orca", "software_version": "6"}, True),
    ({"software_version": "16"}, True),
    ({"software_version": "99"}, False),
    ({"workflow_tool": "arc"}, True),
    ({"workflow_tool": "rmg", "workflow_tool_version": "2.0"}, True),
    ({"workflow_tool_version": "9.9"}, False),
    ({"method_family": [F.dft]}, True),
    ({"method_family": [F.wavefunction]}, True),
    ({"method_family": [F.composite]}, False),
    ({"method_family": [F.composite, F.dft]}, True),
    ({"method_family": F.dft}, True),
    ({"method_family": [F.unclassified]}, False),
    ({"dft_rung_min": 4}, True),
    ({"dft_rung_min": 5}, False),
    ({"dft_rung_max": 4}, True),
    ({"dft_rung_max": 3}, False),
    ({"dft_rung_min": 4, "dft_rung_max": 4}, True),
    ({"dft_rung_min": 1, "dft_rung_max": 3}, False),
    # --- ONE calculation must satisfy every field: the fields of A with the fields of B match nothing
    ({"method": "b3lyp", "software": "gaussian"}, True),
    ({"method": "b3lyp", "software": "orca"}, False),
    ({"basis": "def2tzvp", "software": "orca"}, False),
    ({"basis": "cc-pvtz-f12", "software": "orca", "workflow_tool": "rmg"}, True),
    ({"software": "gaussian", "software_version": "6"}, False),
    ({"software": "orca", "workflow_tool": "arc"}, False),
    ({"method": "ccsd(t)-f12", "workflow_tool": "arc"}, False),
    ({"method": "ccsd(t)-f12", "workflow_tool": "rmg", "workflow_tool_version": "2.0"}, True),
    ({"method_family": [F.dft], "software": "orca"}, False),
    ({"method_family": [F.dft], "software": "gaussian"}, True),
    ({"method_family": [F.wavefunction], "software": "gaussian"}, False),
    ({"method_family": [F.wavefunction], "workflow_tool": "rmg", "basis": "cc-pvtz-f12"}, True),
    ({"dft_rung_min": 4, "software": "orca"}, False),
    ({"dft_rung_min": 4, "dft_rung_max": 4, "software": "gaussian"}, True),
    ({"dft_rung_min": 4, "workflow_tool": "rmg"}, False),
    ({"method_family": [F.dft, F.wavefunction], "method": "b3lyp", "software": "orca"}, False),
    # --- a rung bound implies dft: a wavefunction-only family list with a bound matches nothing
    ({"method_family": [F.wavefunction], "dft_rung_min": 1}, False),
    ({"method_family": [F.wavefunction, F.dft], "dft_rung_min": 1}, True),
    # --- a lower bound above the upper matches nothing
    ({"dft_rung_min": 5, "dft_rung_max": 4}, False),
]


@KINDS
@pytest.mark.parametrize(("filters", "matches"), CASES, ids=[f"{i}:{sorted(c[0])}" for i, c in enumerate(CASES)])
def test_one_source_calculation_has_to_match_every_supplied_provenance_field(db_session, kind, filters, matches):
    """Kills: matching level and software (or any two fields) through two different calculations."""
    world = kind().build(db_session, [A, B])
    assert world.read(db_session, **filters) == ([world.ref] if matches else [])


@KINDS
def test_a_level_of_theory_and_a_software_have_to_be_one_calculation(db_session, kind):
    """Kills: the previous reading, where level X (calculation A) and software Q (calculation B) both held."""
    world = kind().build(db_session, [A, B])
    lot_a, lot_b = world.lots
    assert world.read(db_session, level_of_theory_id=lot_a.id, software="orca") == []
    assert world.read(db_session, level_of_theory_ref=lot_a.public_ref, software="orca") == []
    assert world.read(db_session, level_of_theory_ref=lot_a.public_ref, software="gaussian") == [world.ref]
    assert world.read(db_session, level_of_theory_id=lot_b.id, software="orca") == [world.ref]
    assert world.read(db_session, level_of_theory_id=lot_b.id, method_family=[F.dft]) == []
    assert world.read(db_session, level_of_theory_id=lot_a.id, method_family=[F.dft], dft_rung_min=4) == [world.ref]


@KINDS
def test_a_record_matches_only_through_calculations_it_links_to(db_session, kind):
    """Kills: borrowing a sibling record's calculation (never borrow provenance)."""
    mine = kind().build(db_session, [A])
    other = kind().build(db_session, [B])
    assert mine.read(db_session, software="orca") == []
    assert other.read(db_session, software="gaussian") == []
    assert mine.read(db_session, software="gaussian") == [mine.ref]


@KINDS
def test_a_family_filter_on_a_record_whose_calculation_is_unclassified(db_session, kind):
    """Kills: an unclassified method guessed into a family, or dropped by an `unclassified` filter."""
    unknown = {**A, "method": "xyg3"}
    world = kind().build(db_session, [unknown])
    assert world.read(db_session, method_family=[F.unclassified]) == [world.ref]
    assert world.read(db_session, method_family=[F.dft, F.wavefunction, F.composite]) == []
    assert world.read(db_session, dft_rung_min=1) == []
    assert world.read(db_session, method_family=[F.unclassified, F.dft]) == [world.ref]


@KINDS
def test_a_record_with_no_linked_calculation_matches_no_provenance_filter(db_session, kind):
    """Kills: an empty source set matching by luck, including an `unclassified` family filter."""
    world = kind().build(db_session, [])
    for filters in ({"method": "b3lyp"}, {"method_family": [F.unclassified]}, {"dft_rung_min": 1}, {"software": "gaussian"}):
        assert world.read(db_session, **filters) == [], filters
    assert world.read(db_session) == [world.ref]


@KINDS
@pytest.mark.parametrize(
    ("origin", "filter_value", "matches"),
    [
        (ScientificOriginKind.computed, ScientificOriginKind.computed, True),
        (ScientificOriginKind.computed, ScientificOriginKind.experimental, False),
        (ScientificOriginKind.experimental, ScientificOriginKind.experimental, True),
        (ScientificOriginKind.experimental, ScientificOriginKind.computed, False),
        (ScientificOriginKind.estimated, ScientificOriginKind.estimated, True),
        (ScientificOriginKind.estimated, ScientificOriginKind.computed, False),
    ],
)
def test_scientific_origin_is_matched_on_the_record_itself(db_session, kind, origin, filter_value, matches):
    """Kills: the origin filter ignored, or read from a source calculation."""
    world = kind().build(db_session, [A], origin=origin)
    assert world.read(db_session, scientific_origin=filter_value) == ([world.ref] if matches else [])
    assert world.read(db_session) == [world.ref]


@KINDS
@pytest.mark.parametrize(
    "filters",
    [
        {"method": "b3lyp"},
        {"basis": "def2tzvp"},
        {"software": "gaussian"},
        {"software_version": "16"},
        {"workflow_tool": "arc"},
        {"workflow_tool_version": "1.0"},
        {"method_family": [F.dft]},
        {"dft_rung_min": 4},
        {"dft_rung_max": 4},
        {"method_family": [F.dft], "software": "gaussian"},
    ],
    ids=lambda f: "+".join(f),
)
def test_a_hidden_calculation_cannot_make_a_record_match_under_curated(db_session, kind, filters):
    """Hidden equals unknown equals absent. Calculation A (the hybrid functional, gaussian, arc) is hidden; B is shown.

    Kills: matching through the hidden calculation, so a record appears for a fact the profile says does not exist.
    """
    world = kind().build(db_session, [A, B], approved_calcs=[1], approve=True)
    assert world.read(db_session, **filters) == [world.ref], "exploratory sees the record through calculation A"
    token = set_current_read_profile(CURATED)
    try:
        assert world.read(db_session, **filters) == []
        # and what B carries still matches, so the empty answers above are not a world with nothing visible
        assert world.read(db_session, software="orca", method_family=[F.wavefunction]) == [world.ref]
    finally:
        reset_current_read_profile(token)


def test_the_thermo_search_takes_the_same_filters_and_echoes_them(db_session):
    """Kills: a search that drops the new filters on the way to the per-entry read, or leaves them out of the echo."""
    world = _Thermo().build(db_session, [A, B])
    assert world.search(db_session, method_family=[F.dft], software="gaussian") == [world.ref]
    assert world.search(db_session, method_family=[F.dft], software="orca") == []
    assert world.search(db_session, dft_rung_min=4, dft_rung_max=4, workflow_tool="arc") == [world.ref]
    assert world.search(db_session, scientific_origin=ScientificOriginKind.experimental) == []
    response = search_thermo(
        db_session,
        ThermoSearchRequest(
            species_entry_ref=world.entry.public_ref,
            method="b3lyp",
            basis="def2tzvp",
            software="gaussian",
            software_version="16",
            workflow_tool="arc",
            workflow_tool_version="1.0",
            method_family=[F.dft, F.composite],
            dft_rung_min=3,
            dft_rung_max=5,
            scientific_origin=ScientificOriginKind.computed,
        ),
    )
    assert response.request.filter["method_family"] == ["composite", "dft"]
    assert response.request.filter["dft_rung_min"] == 3 and response.request.filter["dft_rung_max"] == 5
    assert response.request.filter["scientific_origin"] == "computed"
    for name, value in (("method", "b3lyp"), ("basis", "def2tzvp"), ("software_version", "16"), ("workflow_tool", "arc")):
        assert response.request.filter[name] == value
