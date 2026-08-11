"""Evidence tables are frozen by the acceptance of the root that owns them.

``a1f6c3e9b527`` brings five tables under ``c6f2a9d4e7b1``'s accepted-science
regime: the IRC evidence backing a saddle point's claim, the barriers and state
energies a network solve ran with, and the interpretation and tunneling rows a
rate constant was computed under. Each carried an ownership foreign key to an
accepted-science root and no ``trg_as_*`` guard, so each could be rewritten in
place under an accepted record with no supersession edge and no review event.

Why this file exists beside ``test_accepted_science_trigger_registry.py``
------------------------------------------------------------------------
That test derives its expected trigger set from the same revision registries it
compares against ``pg_trigger``. It therefore catches a trigger present in the
database but undeclared, and one declared but not created -- but a guard
deleted from *both* the registry and the DDL leaves it green, because the
expectation shrinks with the reality. Only a test that writes a row and demands
a refusal can catch that, which is what every test below does.

The tests pin both halves of the rule for every table, because a guard that is
present but never fires is the failure mode that matters:

* under an **approved** root, the row cannot be updated, deleted, or inserted;
* under an **unapproved** root it is fully editable, so the freeze is
  acceptance-triggered rather than a blanket write ban on the table.

None of these five tables has children of its own, so unlike
``test_atom_map_immutability`` there is no cascade that could let a child's
guard absorb a refusal the parent's guard was supposed to make. Each refusal
below is attributable to the trigger the revision put on that table: removing
that one trigger, and only that one, turns the corresponding test red.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    EnergyCorrectionConvention,
    EnergyZeroConvention,
    KineticsDegeneracyInterpretation,
    KineticsEnsemblePolicy,
    KineticsStandardStateConvention,
    NetworkChannelKind,
    NetworkStateKind,
    RecordReviewStatus,
    SubmissionRecordType,
    TunnelingModel,
)
from app.db.models.kinetics import (
    KineticsInterpretationAssignment,
    KineticsTunnelingApplication,
)
from app.db.models.network_pdep import (
    NetworkSolveChannelBarrier,
    NetworkSolveStateEnergy,
)
from app.db.models.transition_state import TransitionStateValidationEvidence
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_network,
    make_network_channel,
    make_network_solve,
    make_network_state,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

#: Every table below is guarded against this record type via the column named
#: beside it in ``a1f6c3e9b527._DIRECT_CHILDREN``.
_ROOTS = (
    SubmissionRecordType.transition_state_entry,
    SubmissionRecordType.network_solve,
    SubmissionRecordType.kinetics,
)

_GUARDED_TABLES = (
    "transition_state_validation_evidence",
    "network_solve_channel_barrier",
    "network_solve_state_energy",
    "kinetics_interpretation_assignment",
    "kinetics_tunneling_application",
)


def _curator(session, username: str) -> AppUser:
    actor = AppUser(username=username, role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    return actor


def _approve(session, *, record_type: SubmissionRecordType, record_id: int, actor: AppUser) -> None:
    assert record_type in _ROOTS
    ensure_record_review(session, record_type=record_type, record_id=record_id)
    review = set_record_review_status(
        session,
        record_type=record_type,
        record_id=record_id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )
    assert review.first_approved_at is not None


def _reaction_entry(session, tag: str):
    reactant = make_species(session, inchi_key=next_inchi_key(f"{tag}R"))
    product = make_species(session, inchi_key=next_inchi_key(f"{tag}P"))
    reaction = make_chem_reaction(session, reactants=[reactant], products=[product])
    return (
        make_reaction_entry(
            session,
            reaction=reaction,
            reactant_entries=[make_species_entry(session, reactant)],
            product_entries=[make_species_entry(session, product)],
        ),
        reactant,
        product,
    )


# ---------------------------------------------------------------------------
# transition_state_validation_evidence -> transition_state_entry
# ---------------------------------------------------------------------------


def _ts_evidence(session, tag: str):
    reaction_entry, _, _ = _reaction_entry(session, tag)
    ts_entry = make_transition_state_entry(
        session,
        transition_state=make_transition_state(session, reaction_entry=reaction_entry),
    )
    calculation = make_calculation(session, transition_state_entry_id=ts_entry.id)
    evidence = TransitionStateValidationEvidence(
        transition_state_entry_id=ts_entry.id,
        kind="irc",
        passed=True,
        rationale="the reconstructed path reaches both declared endpoints",
        reconstruction_calculation_id=calculation.id,
    )
    session.add(evidence)
    session.flush()
    return ts_entry, evidence, calculation


def test_irc_evidence_of_approved_transition_state_entry_is_frozen(db_session) -> None:
    """The evidence backing the claim is now as immutable as the atom map.

    ``b6c1f4a8e703`` froze ``reaction_atom_map`` under this same root while
    leaving this table writable, so the agreement
    ``validate_atom_map_agrees_with_irc_evidence`` establishes at deposit could
    afterwards be falsified by rewriting whichever half was still mutable.
    """

    actor = _curator(db_session, "irc-evidence-curator")
    ts_entry, evidence, _ = _ts_evidence(db_session, "IRCFRZ")
    _approve(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=ts_entry.id,
        actor=actor,
    )

    with pytest.raises(DBAPIError), db_session.begin_nested():
        evidence.passed = False
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        evidence.rationale = "on reflection the path went somewhere else"
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(evidence)
        db_session.flush()


def test_irc_evidence_cannot_be_attached_to_an_approved_transition_state_entry(
    db_session,
) -> None:
    """Adding evidence to an accepted record changes what was accepted."""

    actor = _curator(db_session, "irc-late-curator")
    ts_entry, evidence, calculation = _ts_evidence(db_session, "IRCLATE")
    db_session.delete(evidence)
    db_session.flush()
    _approve(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=ts_entry.id,
        actor=actor,
    )

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            TransitionStateValidationEvidence(
                transition_state_entry_id=ts_entry.id,
                kind="irc",
                passed=True,
                rationale="evidence produced after review closed",
                reconstruction_calculation_id=calculation.id,
            )
        )
        db_session.flush()


def test_irc_evidence_of_unapproved_transition_state_entry_stays_editable(db_session) -> None:
    _, evidence, _ = _ts_evidence(db_session, "IRCOPEN")

    evidence.rationale = "corrected before review"
    evidence.passed = False
    db_session.flush()
    assert evidence.rationale == "corrected before review"

    db_session.delete(evidence)
    db_session.flush()


# ---------------------------------------------------------------------------
# network_solve_channel_barrier / network_solve_state_energy -> network_solve
# ---------------------------------------------------------------------------


class _SolveBundle:
    def __init__(self, *, solve, barrier, state_energy, channel, reaction_entry, ts_entry, state):
        self.solve = solve
        self.barrier = barrier
        self.state_energy = state_energy
        self.channel = channel
        self.reaction_entry = reaction_entry
        self.ts_entry = ts_entry
        self.state = state


def _solve_bundle(db_session, tag: str) -> _SolveBundle:
    reaction_entry, reactant, product = _reaction_entry(db_session, tag)
    ts_entry = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=reaction_entry),
    )
    network = make_network(db_session, name=f"network-{tag}")
    source = make_network_state(
        db_session,
        network=network,
        kind=NetworkStateKind.well,
        composition_hash=f"{tag}-source",
    )
    sink = make_network_state(
        db_session,
        network=network,
        kind=NetworkStateKind.bimolecular,
        composition_hash=f"{tag}-sink",
    )
    channel = make_network_channel(
        db_session,
        network=network,
        source_state=source,
        sink_state=sink,
        kind=NetworkChannelKind.dissociation,
    )
    solve = make_network_solve(db_session, network=network)

    barrier = NetworkSolveChannelBarrier(
        solve_id=solve.id,
        channel_id=channel.id,
        reaction_entry_id=reaction_entry.id,
        transition_state_entry_id=ts_entry.id,
        forward_barrier_kj_mol=112.5,
        reverse_barrier_kj_mol=64.25,
        energy_zero_convention=EnergyZeroConvention.entrance_channel,
        correction_convention=EnergyCorrectionConvention.electronic_plus_zpe,
    )
    state_energy = NetworkSolveStateEnergy(
        solve_id=solve.id,
        state_id=source.id,
        energy_kj_mol=-31.75,
        energy_zero_convention=EnergyZeroConvention.entrance_channel,
        correction_convention=EnergyCorrectionConvention.electronic_plus_zpe,
    )
    db_session.add_all([barrier, state_energy])
    db_session.flush()
    return _SolveBundle(
        solve=solve,
        barrier=barrier,
        state_energy=state_energy,
        channel=channel,
        reaction_entry=reaction_entry,
        ts_entry=ts_entry,
        state=sink,
    )


def test_solve_inputs_of_an_approved_solve_are_frozen(db_session) -> None:
    """A solve's own inputs are as immutable as the k(T,P) it produced.

    ``network_kinetics`` and its Chebyshev/PLOG children were already frozen
    under ``network_solve``. Leaving the barriers and state energies the solve
    ran with editable meant the stored rate could stop following from the
    inputs recorded beside it without either row admitting to a change.
    """

    actor = _curator(db_session, "solve-curator")
    bundle = _solve_bundle(db_session, "SLVFRZ")
    _approve(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=bundle.solve.id,
        actor=actor,
    )

    with pytest.raises(DBAPIError), db_session.begin_nested():
        bundle.barrier.forward_barrier_kj_mol = 98.0
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(bundle.barrier)
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        bundle.state_energy.energy_kj_mol = 0.0
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(bundle.state_energy)
        db_session.flush()


def test_solve_inputs_cannot_be_added_to_an_approved_solve(db_session) -> None:
    actor = _curator(db_session, "solve-late-curator")
    bundle = _solve_bundle(db_session, "SLVLATE")
    _approve(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=bundle.solve.id,
        actor=actor,
    )

    # A second state energy, for the well the bundle left without one. Every
    # key and constraint admits it; only the guard stands in the way.
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            NetworkSolveStateEnergy(
                solve_id=bundle.solve.id,
                state_id=bundle.state.id,
                energy_kj_mol=12.5,
                energy_zero_convention=EnergyZeroConvention.entrance_channel,
                correction_convention=EnergyCorrectionConvention.electronic_plus_zpe,
            )
        )
        db_session.flush()


def test_solve_inputs_of_an_unapproved_solve_stay_editable(db_session) -> None:
    bundle = _solve_bundle(db_session, "SLVOPEN")

    bundle.barrier.forward_barrier_kj_mol = 98.0
    bundle.state_energy.energy_kj_mol = 0.0
    db_session.flush()
    assert bundle.barrier.forward_barrier_kj_mol == 98.0

    db_session.delete(bundle.barrier)
    db_session.delete(bundle.state_energy)
    db_session.flush()


# ---------------------------------------------------------------------------
# kinetics_interpretation_assignment / kinetics_tunneling_application -> kinetics
# ---------------------------------------------------------------------------


class _KineticsBundle:
    def __init__(self, *, kinetics, assignment, tunneling, statmech, ts_entry):
        self.kinetics = kinetics
        self.assignment = assignment
        self.tunneling = tunneling
        self.statmech = statmech
        self.ts_entry = ts_entry


def _kinetics_bundle(db_session, tag: str) -> _KineticsBundle:
    reaction_entry, reactant, _ = _reaction_entry(db_session, tag)
    ts_entry = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=reaction_entry),
    )
    statmech = make_statmech(
        db_session,
        species_entry=make_species_entry(db_session, reactant),
    )
    kinetics = make_kinetics(db_session, reaction_entry=reaction_entry)

    assignment = KineticsInterpretationAssignment(
        kinetics_id=kinetics.id,
        subject_key="reactant:1",
        role="reactant",
        statmech_id=statmech.id,
        ensemble_policy=KineticsEnsemblePolicy.lowest_energy_conformer,
        standard_state_convention=KineticsStandardStateConvention.ideal_gas_1_bar,
        degeneracy_interpretation=(KineticsDegeneracyInterpretation.reaction_path_degeneracy),
    )
    tunneling = KineticsTunnelingApplication(
        kinetics_id=kinetics.id,
        model=TunnelingModel.eckart,
        transition_state_entry_id=ts_entry.id,
        imaginary_frequency_cm1=-1204.0,
        forward_barrier_kj_mol=45.5,
        reverse_barrier_kj_mol=88.25,
    )
    db_session.add_all([assignment, tunneling])
    db_session.flush()
    return _KineticsBundle(
        kinetics=kinetics,
        assignment=assignment,
        tunneling=tunneling,
        statmech=statmech,
        ts_entry=ts_entry,
    )


def test_rate_interpretation_of_approved_kinetics_is_frozen(db_session) -> None:
    """The conventions a rate was computed under move with the rate.

    ``kinetics_arrhenius_entry`` and ``kinetics_plog`` -- the coefficients --
    were already frozen under this root. The interpretation names the ensemble,
    standard state and degeneracy treatment those coefficients mean something
    only relative to, and the tunneling row names the correction applied to
    them; either rewritten silently makes the stored number stop being what
    the recorded inputs produce.
    """

    actor = _curator(db_session, "kinetics-curator")
    bundle = _kinetics_bundle(db_session, "KINFRZ")
    _approve(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=bundle.kinetics.id,
        actor=actor,
    )

    with pytest.raises(DBAPIError), db_session.begin_nested():
        bundle.assignment.ensemble_policy = KineticsEnsemblePolicy.single_structure
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(bundle.assignment)
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        bundle.tunneling.forward_barrier_kj_mol = 12.0
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(bundle.tunneling)
        db_session.flush()


def test_rate_interpretation_cannot_be_added_to_approved_kinetics(db_session) -> None:
    actor = _curator(db_session, "kinetics-late-curator")
    bundle = _kinetics_bundle(db_session, "KINLATE")
    _approve(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=bundle.kinetics.id,
        actor=actor,
    )

    # The transition-state slot the bundle left unassigned. The composite
    # primary key and ``ck_kinetics_interpretation_assignment_subject_shape``
    # both admit this row.
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            KineticsInterpretationAssignment(
                kinetics_id=bundle.kinetics.id,
                subject_key="transition_state",
                role="transition_state",
                statmech_id=bundle.statmech.id,
                transition_state_entry_id=bundle.ts_entry.id,
                ensemble_policy=KineticsEnsemblePolicy.single_structure,
                standard_state_convention=(KineticsStandardStateConvention.ideal_gas_1_bar),
                degeneracy_interpretation=(KineticsDegeneracyInterpretation.external_symmetry_number),
            )
        )
        db_session.flush()


def test_rate_interpretation_of_unapproved_kinetics_stays_editable(db_session) -> None:
    bundle = _kinetics_bundle(db_session, "KINOPEN")

    bundle.assignment.ensemble_policy = KineticsEnsemblePolicy.single_structure
    bundle.tunneling.forward_barrier_kj_mol = 12.0
    db_session.flush()
    assert bundle.tunneling.forward_barrier_kj_mol == 12.0

    db_session.delete(bundle.assignment)
    db_session.delete(bundle.tunneling)
    db_session.flush()


# ---------------------------------------------------------------------------
# Statement-level bypass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", _GUARDED_TABLES)
def test_truncate_cannot_bypass_the_evidence_guards(db_session, table: str) -> None:
    """TRUNCATE fires no row trigger, so each table carries its own refusal."""

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text(f"TRUNCATE {table}"))


def test_every_guarded_table_refuses_a_write_under_an_approved_root(db_session) -> None:
    """One assertion that names all five tables, so none can be dropped quietly.

    The registry parity test compares ``pg_trigger`` against the revision's own
    registry, so deleting a table from both leaves it green. This list is
    written out by hand for exactly that reason: it is the copy of the intent
    that does not shrink when the registry does.
    """

    guarded = {
        row.table_name
        for row in db_session.execute(
            text(
                """
                SELECT relation.relname AS table_name
                FROM pg_trigger AS trigger
                JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                WHERE NOT trigger.tgisinternal
                  AND trigger.tgname LIKE 'trg_as_child_%'
                """
            )
        )
    }
    assert set(_GUARDED_TABLES) <= guarded
