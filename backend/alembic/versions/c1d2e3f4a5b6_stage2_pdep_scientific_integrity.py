"""Add explicit PDep pathway identity and solve scientific inputs.

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


_ENERGY_ZERO_CONVENTION = (
    "lowest_state",
    "entrance_channel",
    "separated_reactants",
    "absolute",
    "other",
)
_ENERGY_CORRECTION_CONVENTION = (
    "electronic_only",
    "electronic_plus_zpe",
    "atom_and_bond_corrected",
    "thermal_enthalpy_298k",
    "other",
)
_KINETICS_ENSEMBLE_POLICY = (
    "single_structure",
    "lowest_energy_conformer",
    "boltzmann_weighted_conformers",
    "multi_structural_torsional",
    "other",
)
_KINETICS_STANDARD_STATE_CONVENTION = (
    "ideal_gas_1_bar",
    "ideal_gas_1_atm",
    "concentration_1_mol_cm3",
    "concentration_1_mol_l",
    "other",
)
_KINETICS_DEGENERACY_INTERPRETATION = (
    "external_symmetry_number",
    "reaction_path_degeneracy",
    "symmetry_number_and_path_degeneracy",
    "no_symmetry_treatment",
    "other",
)

_NEW_ENUMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("energy_zero_convention", _ENERGY_ZERO_CONVENTION),
    ("energy_correction_convention", _ENERGY_CORRECTION_CONVENTION),
    ("kinetics_ensemble_policy", _KINETICS_ENSEMBLE_POLICY),
    ("kinetics_standard_state_convention", _KINETICS_STANDARD_STATE_CONVENTION),
    ("kinetics_degeneracy_interpretation", _KINETICS_DEGENERACY_INTERPRETATION),
)


def _enum(name: str) -> postgresql.ENUM:
    """Reference an enum type this revision already created."""
    values = dict(_NEW_ENUMS)[name]
    return postgresql.ENUM(*values, name=name, create_type=False)


def _refuse_legacy_pdep_data(bind) -> None:
    """Stop the upgrade when v1 PDep rows exist that v2 cannot describe.

    Legacy ``network_solve_energy_transfer`` rows carry no ``(state, collider)``
    scope and legacy ``network_channel`` rows carry no producer-visible
    ``channel_key``. Neither can be derived from what was stored: an
    unscoped ⟨ΔE⟩down does not say which well it applied to, and a channel's
    mechanistic identity was never recorded. Per the v2 contract these are
    re-uploaded from their source calculations, never guessed at during
    ingestion — so refuse loudly instead of inventing values or dying on a
    ``NotNullViolation`` halfway through the DDL.
    """
    unscoped_transfer = bind.execute(
        sa.text("SELECT count(*) FROM network_solve_energy_transfer")
    ).scalar_one()
    keyless_channels = bind.execute(
        sa.text("SELECT count(*) FROM network_channel")
    ).scalar_one()
    if not unscoped_transfer and not keyless_channels:
        return
    raise RuntimeError(
        "Cannot upgrade to c1d2e3f4a5b6: this database holds pre-v2 "
        f"pressure-dependent network data ({unscoped_transfer} "
        f"network_solve_energy_transfer row(s), {keyless_channels} "
        "network_channel row(s)). The v2 contract scopes energy transfer to "
        "an explicit (state, collider) pair and gives every channel a "
        "producer-visible channel_key; neither can be reconstructed from the "
        "stored v1 rows.\n"
        "Operator steps (see backend/docs/deployment/migrations.md, "
        "'Stage 2 PDep re-upload'):\n"
        "  1. Back up the database.\n"
        "  2. Export the affected networks (GET /scientific/networks, "
        "GET /scientific/network-solves) and keep the source Arkane/ME run "
        "directories they were built from.\n"
        "  3. Delete the network_* rows (network_kinetics_* -> "
        "network_solve_* -> network_channel_* -> network_state_* -> "
        "network_reaction/network_species -> network).\n"
        "  4. Re-run 'alembic upgrade head'.\n"
        "  5. Re-upload each network through POST /uploads/network-pdep "
        "using the v2 payload."
    )


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_legacy_pdep_data(bind)

    for name, values in _NEW_ENUMS:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    # A statmech record describes exactly one physical subject.  Existing
    # rows remain species-bound; this makes TS partition functions explicit
    # for canonical TST rather than encoding a TS as a pseudo-species.
    op.alter_column("statmech", "species_entry_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("statmech", sa.Column("transition_state_entry_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_statmech_transition_state_entry", "statmech", "transition_state_entry",
        ["transition_state_entry_id"], ["id"], deferrable=True, initially="IMMEDIATE",
    )
    op.create_index("ix_statmech_transition_state_entry_id", "statmech", ["transition_state_entry_id"])
    op.create_check_constraint(
        "statmech_exactly_one_subject", "statmech",
        "(species_entry_id IS NULL) <> (transition_state_entry_id IS NULL)",
    )
    # NOT NULL is safe here: ``_refuse_legacy_pdep_data`` above has already
    # established that ``network_channel`` is empty, so there is no legacy row
    # to backfill. A nullable key would defeat ``uq_network_channel_key``,
    # since PostgreSQL treats NULLs as distinct from one another.
    op.add_column("network_channel", sa.Column("channel_key", sa.Text(), nullable=False))
    # The initial schema made the endpoint triple unique. Parallel elementary
    # paths may share it, so the producer-visible channel key now owns identity.
    op.drop_constraint("uq_network_channel_network_id", "network_channel", type_="unique")
    op.create_unique_constraint("uq_network_channel_key", "network_channel", ["network_id", "channel_key"])
    # ``transition_state_entry_id`` is nullable: barrierless/variational
    # association and dissociation paths have no saddle point. A surrogate id
    # therefore owns row identity, with a partial unique index standing in for
    # the uniqueness a NULL column cannot provide.
    op.create_table(
        "network_channel_microreaction",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("reaction_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("transition_state_entry_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["channel_id"], ["network_channel.id"], deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["reaction_entry_id"], ["reaction_entry.id"], name="fk_network_channel_microreaction_reaction_entry", deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["transition_state_entry_id"], ["transition_state_entry.id"], name="fk_network_channel_microreaction_ts_entry", deferrable=True, initially="IMMEDIATE"),
        sa.UniqueConstraint(
            "channel_id", "reaction_entry_id", "transition_state_entry_id",
            name="uq_network_channel_microreaction_path",
        ),
    )
    op.create_index(
        "uq_network_channel_microreaction_barrierless",
        "network_channel_microreaction",
        ["channel_id", "reaction_entry_id"],
        unique=True,
        postgresql_where=sa.text("transition_state_entry_id IS NULL"),
    )
    op.add_column("network_solve_energy_transfer", sa.Column("state_id", sa.BigInteger(), nullable=False))
    op.add_column("network_solve_energy_transfer", sa.Column("collider_species_entry_id", sa.BigInteger(), nullable=False))
    op.create_foreign_key("fk_network_solve_energy_transfer_state", "network_solve_energy_transfer", "network_state", ["state_id"], ["id"], deferrable=True, initially="IMMEDIATE")
    op.create_foreign_key("fk_network_solve_energy_transfer_collider", "network_solve_energy_transfer", "species_entry", ["collider_species_entry_id"], ["id"], deferrable=True, initially="IMMEDIATE")
    op.create_unique_constraint("uq_network_solve_energy_transfer_scope", "network_solve_energy_transfer", ["solve_id", "state_id", "collider_species_entry_id"])
    op.create_table(
        "network_solve_state_energy",
        sa.Column("solve_id", sa.BigInteger(), nullable=False),
        sa.Column("state_id", sa.BigInteger(), nullable=False),
        sa.Column("energy_kj_mol", sa.Double(), nullable=False),
        sa.Column("energy_zero_convention", _enum("energy_zero_convention"), nullable=False),
        sa.Column("correction_convention", _enum("energy_correction_convention"), nullable=False),
        sa.Column("convention_note", sa.Text(), nullable=True),
        sa.Column("source_calculation_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["solve_id"], ["network_solve.id"], deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["state_id"], ["network_state.id"], deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["source_calculation_id"], ["calculation.id"], deferrable=True, initially="IMMEDIATE"),
        sa.CheckConstraint(
            "(energy_zero_convention <> 'other' AND correction_convention <> 'other') "
            "OR convention_note IS NOT NULL",
            name="other_note",
        ),
        sa.PrimaryKeyConstraint("solve_id", "state_id"),
    )
    # Barriers are signed relative to the declared zero: a submerged entrance
    # barrier is negative under an entrance-channel zero, so only NaN is
    # rejected. A barrierless path carries no row here at all.
    op.create_table(
        "network_solve_channel_barrier",
        sa.Column("solve_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("reaction_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("transition_state_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("forward_barrier_kj_mol", sa.Double(), nullable=False),
        sa.Column("reverse_barrier_kj_mol", sa.Double(), nullable=False),
        sa.Column("energy_zero_convention", _enum("energy_zero_convention"), nullable=False),
        sa.Column("correction_convention", _enum("energy_correction_convention"), nullable=False),
        sa.Column("convention_note", sa.Text(), nullable=True),
        sa.Column("source_calculation_id", sa.BigInteger()),
        sa.ForeignKeyConstraint(["solve_id"], ["network_solve.id"], deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["channel_id"], ["network_channel.id"], deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["reaction_entry_id"], ["reaction_entry.id"], name="fk_network_solve_channel_barrier_reaction_entry", deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["transition_state_entry_id"], ["transition_state_entry.id"], name="fk_network_solve_channel_barrier_ts_entry", deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["source_calculation_id"], ["calculation.id"], name="fk_network_solve_channel_barrier_source_calc", deferrable=True, initially="IMMEDIATE"),
        sa.CheckConstraint(
            "forward_barrier_kj_mol <> 'NaN'::double precision "
            "AND reverse_barrier_kj_mol <> 'NaN'::double precision",
            name="barriers_finite",
        ),
        sa.CheckConstraint(
            "(energy_zero_convention <> 'other' AND correction_convention <> 'other') "
            "OR convention_note IS NOT NULL",
            name="other_note",
        ),
        sa.PrimaryKeyConstraint("solve_id", "channel_id", "reaction_entry_id", "transition_state_entry_id"),
    )
    op.create_table(
        "kinetics_interpretation_assignment",
        sa.Column("kinetics_id", sa.BigInteger(), nullable=False), sa.Column("subject_key", sa.Text(), nullable=False), sa.Column("role", sa.Text(), nullable=False),
        sa.Column("statmech_id", sa.BigInteger(), nullable=False), sa.Column("conformer_selection_id", sa.BigInteger()),
        sa.Column("transition_state_entry_id", sa.BigInteger()),
        sa.Column("ensemble_policy", _enum("kinetics_ensemble_policy"), nullable=False),
        sa.Column("standard_state_convention", _enum("kinetics_standard_state_convention"), nullable=False),
        sa.Column("degeneracy_interpretation", _enum("kinetics_degeneracy_interpretation"), nullable=False),
        sa.Column("convention_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["kinetics_id"], ["kinetics.id"], deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["statmech_id"], ["statmech.id"], deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["conformer_selection_id"], ["conformer_selection.id"], name="fk_kinetics_interp_conformer_selection", deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["transition_state_entry_id"], ["transition_state_entry.id"], name="fk_kinetics_interp_ts_entry", deferrable=True, initially="IMMEDIATE"),
        # A trailing-digit substring is NULL for a key with no digits, and a
        # NULL CHECK passes, so match the whole subject-key shape by regex.
        sa.CheckConstraint(
            "(role = 'transition_state' AND subject_key = 'transition_state' "
            "AND transition_state_entry_id IS NOT NULL AND conformer_selection_id IS NULL) "
            "OR (role IN ('reactant', 'product') "
            "AND subject_key ~ ('^' || role || ':[0-9]+$') "
            "AND transition_state_entry_id IS NULL)",
            name="subject_shape",
        ),
        sa.CheckConstraint(
            "(ensemble_policy <> 'other' AND standard_state_convention <> 'other' "
            "AND degeneracy_interpretation <> 'other') OR convention_note IS NOT NULL",
            name="other_note",
        ),
        sa.PrimaryKeyConstraint("kinetics_id", "subject_key"),
    )
    op.create_table(
        "kinetics_tunneling_application",
        # The parent ``kinetics.tunneling_model`` owns the PostgreSQL enum.
        # Keep this additive table textual with the same value set: Alembic's
        # table DDL otherwise attempts to recreate the pre-existing enum.
        sa.Column("kinetics_id", sa.BigInteger(), nullable=False), sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_identifier", sa.Text(), nullable=True),
        sa.Column("transition_state_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("source_calculation_id", sa.BigInteger(), nullable=True),
        sa.Column("imaginary_frequency_cm1", sa.Double()), sa.Column("frequency_sign_convention", sa.Text(), nullable=False, server_default="negative_imaginary_cm1"),
        sa.Column("reactant_energy_kj_mol", sa.Double()), sa.Column("product_energy_kj_mol", sa.Double()), sa.Column("forward_barrier_kj_mol", sa.Double()), sa.Column("reverse_barrier_kj_mol", sa.Double()),
        sa.Column("energy_zero_convention", _enum("energy_zero_convention")),
        sa.Column("energy_correction_convention", _enum("energy_correction_convention")),
        sa.Column("convention_note", sa.Text(), nullable=True),
        sa.Column("result_artifact_id", sa.BigInteger()), sa.Column("sct_path_integral_artifact_id", sa.BigInteger()),
        sa.ForeignKeyConstraint(["kinetics_id"], ["kinetics.id"], deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["transition_state_entry_id"], ["transition_state_entry.id"], name="fk_kinetics_tunneling_ts_entry", deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["source_calculation_id"], ["calculation.id"], name="fk_kinetics_tunneling_source_calculation", deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["result_artifact_id"], ["calculation_artifact.id"], name="fk_kinetics_tunneling_result_artifact", deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["sct_path_integral_artifact_id"], ["calculation_artifact.id"], name="fk_kinetics_tunneling_sct_artifact", deferrable=True, initially="IMMEDIATE"),
        sa.CheckConstraint("model IN ('none', 'wigner', 'eckart', 'sct', 'other')", name="model_enum"),
        sa.CheckConstraint("imaginary_frequency_cm1 IS NULL OR imaginary_frequency_cm1 < 0", name="imaginary_negative"),
        sa.CheckConstraint(
            "model <> 'other' OR (model_identifier IS NOT NULL AND result_artifact_id IS NOT NULL)",
            name="other_replayable",
        ),
        sa.CheckConstraint(
            "(energy_zero_convention IS DISTINCT FROM 'other' "
            "AND energy_correction_convention IS DISTINCT FROM 'other') "
            "OR convention_note IS NOT NULL",
            name="other_note",
        ),
        sa.PrimaryKeyConstraint("kinetics_id"),
    )
    # IRC only: normal-mode-displacement evidence is a producer-side heuristic
    # and is not stored. Every record therefore points at the IRC calculation
    # that reconstructed the path.
    op.create_table(
        "transition_state_validation_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True), sa.Column("transition_state_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False), sa.Column("passed", sa.Boolean(), nullable=False), sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("reconstruction_calculation_id", sa.BigInteger(), nullable=False),
        sa.Column("reactant_participant_mapping", sa.dialects.postgresql.JSONB()), sa.Column("product_participant_mapping", sa.dialects.postgresql.JSONB()), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")), sa.Column("created_by", sa.BigInteger()),
        sa.ForeignKeyConstraint(["transition_state_entry_id"], ["transition_state_entry.id"], name="fk_ts_validation_evidence_ts_entry", deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], deferrable=True, initially="IMMEDIATE"),
        sa.ForeignKeyConstraint(["reconstruction_calculation_id"], ["calculation.id"], name="fk_ts_validation_evidence_reconstruction_calc", deferrable=True, initially="IMMEDIATE"),
        sa.CheckConstraint("kind IN ('irc')", name="ts_validation_kind"),
        sa.UniqueConstraint("transition_state_entry_id", "kind", name="uq_ts_validation_evidence_kind"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    # The prior schema has no truthful TS subject for a partition function.
    # Refuse rollback before dropping its only subject identity.
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM statmech WHERE transition_state_entry_id IS NOT NULL)")
    ).scalar():
        raise RuntimeError(
            "Cannot downgrade c1d2e3f4a5b6 while TS-owned statmech rows exist; "
            "export or migrate those records first."
        )
    # Symmetrically: the prior schema made (network, source, sink) unique, so
    # parallel mechanistic channels sharing endpoints cannot be re-expressed.
    # Refuse rather than die on a UniqueViolation mid-downgrade.
    if bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM network_channel "
            "GROUP BY network_id, source_state_id, sink_state_id HAVING count(*) > 1)"
        )
    ).scalar():
        raise RuntimeError(
            "Cannot downgrade c1d2e3f4a5b6 while parallel network channels share "
            "the same (network, source_state, sink_state); the prior schema made "
            "that triple unique. Export or merge those channels first."
        )
    op.drop_table("network_solve_channel_barrier")
    op.drop_constraint("statmech_exactly_one_subject", "statmech", type_="check")
    op.drop_index("ix_statmech_transition_state_entry_id", table_name="statmech")
    op.drop_constraint("fk_statmech_transition_state_entry", "statmech", type_="foreignkey")
    op.drop_column("statmech", "transition_state_entry_id")
    op.alter_column("statmech", "species_entry_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_table("transition_state_validation_evidence")
    op.drop_table("kinetics_tunneling_application")
    op.drop_table("kinetics_interpretation_assignment")
    op.drop_table("network_solve_state_energy")
    op.drop_constraint("uq_network_solve_energy_transfer_scope", "network_solve_energy_transfer", type_="unique")
    op.drop_constraint("fk_network_solve_energy_transfer_collider", "network_solve_energy_transfer", type_="foreignkey")
    op.drop_constraint("fk_network_solve_energy_transfer_state", "network_solve_energy_transfer", type_="foreignkey")
    op.drop_column("network_solve_energy_transfer", "collider_species_entry_id")
    op.drop_column("network_solve_energy_transfer", "state_id")
    op.drop_index(
        "uq_network_channel_microreaction_barrierless",
        table_name="network_channel_microreaction",
    )
    op.drop_table("network_channel_microreaction")
    op.drop_constraint("uq_network_channel_key", "network_channel", type_="unique")
    op.drop_column("network_channel", "channel_key")
    op.create_unique_constraint(
        "uq_network_channel_network_id", "network_channel",
        ["network_id", "source_state_id", "sink_state_id"],
    )
    for name, values in reversed(_NEW_ENUMS):
        postgresql.ENUM(*values, name=name).drop(bind, checkfirst=True)
