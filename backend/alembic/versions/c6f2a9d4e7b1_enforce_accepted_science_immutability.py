"""enforce accepted-science immutability and supersession

Revision ID: c6f2a9d4e7b1
Revises: b4e8c1f6a2d9
Create Date: 2026-07-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c6f2a9d4e7b1"
down_revision: Union[str, Sequence[str], None] = "b4e8c1f6a2d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ROOT_TYPES = {
    "calculation": "calculation",
    "thermo": "thermo",
    "statmech": "statmech",
    "kinetics": "kinetics",
    "transport": "transport",
    "network": "network",
    "network_solve": "network_solve",
    "applied_energy_correction": "applied_energy_correction",
    "transition_state_entry": "transition_state_entry",
    "conformer_observation": "conformer_observation",
}

# Each child is protected by the accepted root that owns its scientific
# meaning. Cross-domain provenance FKs are intentionally not treated as
# ownership (for example thermo_source_calculation.calculation_id).
_DIRECT_CHILDREN = (
    # calculation
    ("calculation_input_geometry", "calculation", "calculation_id"),
    ("calculation_output_geometry", "calculation", "calculation_id"),
    ("calculation_dependency", "calculation", "parent_calculation_id"),
    ("calculation_dependency", "calculation", "child_calculation_id"),
    ("calc_sp_result", "calculation", "calculation_id"),
    ("calc_opt_result", "calculation", "calculation_id"),
    ("calc_freq_result", "calculation", "calculation_id"),
    ("calc_freq_mode", "calculation", "calculation_id"),
    ("calc_scan_result", "calculation", "calculation_id"),
    ("calc_scan_coordinate", "calculation", "calculation_id"),
    ("calculation_constraint", "calculation", "calculation_id"),
    ("calc_scan_point", "calculation", "calculation_id"),
    ("calc_scan_point_coordinate_value", "calculation", "calculation_id"),
    ("calc_irc_result", "calculation", "calculation_id"),
    ("calc_irc_point", "calculation", "calculation_id"),
    ("calc_path_search_result", "calculation", "calculation_id"),
    ("calc_path_search_point", "calculation", "calculation_id"),
    ("calculation_artifact", "calculation", "calculation_id"),
    ("calculation_parameter", "calculation", "calculation_id"),
    ("calc_geometry_validation", "calculation", "calculation_id"),
    ("calc_scf_stability", "calculation", "calculation_id"),
    ("calc_scf_stability", "calculation", "source_calculation_id"),
    ("calc_hessian", "calculation", "calculation_id"),
    ("calc_wavefunction_diagnostic", "calculation", "calculation_id"),
    ("calc_spin_diagnostic", "calculation", "calculation_id"),
    # thermo
    ("thermo_point", "thermo", "thermo_id"),
    ("thermo_nasa", "thermo", "thermo_id"),
    ("thermo_nasa9_interval", "thermo", "thermo_id"),
    ("thermo_wilhoit", "thermo", "thermo_id"),
    ("thermo_source_calculation", "thermo", "thermo_id"),
    ("applied_group_additivity", "thermo", "thermo_id"),
    # statmech
    ("statmech_electronic_level", "statmech", "statmech_id"),
    ("statmech_source_calculation", "statmech", "statmech_id"),
    ("statmech_torsion", "statmech", "statmech_id"),
    # kinetics
    ("kinetics_source_calculation", "kinetics", "kinetics_id"),
    ("kinetics_falloff", "kinetics", "kinetics_id"),
    ("kinetics_third_body_efficiency", "kinetics", "kinetics_id"),
    ("kinetics_plog", "kinetics", "kinetics_id"),
    ("kinetics_arrhenius_entry", "kinetics", "kinetics_id"),
    ("kinetics_chebyshev", "kinetics", "kinetics_id"),
    # transport, networks, and corrections
    ("transport_source_calculation", "transport", "transport_id"),
    ("network_reaction", "network", "network_id"),
    ("network_species", "network", "network_id"),
    ("network_state", "network", "network_id"),
    ("network_channel", "network", "network_id"),
    ("network_solve_bath_gas", "network_solve", "solve_id"),
    ("network_solve_energy_transfer", "network_solve", "solve_id"),
    ("network_solve_source_calculation", "network_solve", "solve_id"),
    ("network_kinetics", "network_solve", "solve_id"),
    (
        "applied_energy_correction_component",
        "applied_energy_correction",
        "applied_correction_id",
    ),
)

_VIA_CHILDREN = (
    (
        "applied_group_additivity_component",
        "thermo",
        "applied_group_additivity_id",
        "applied_group_additivity",
        "id",
        "thermo_id",
    ),
    (
        "statmech_torsion_definition",
        "statmech",
        "torsion_id",
        "statmech_torsion",
        "id",
        "statmech_id",
    ),
    (
        "network_state_participant",
        "network",
        "state_id",
        "network_state",
        "id",
        "network_id",
    ),
    (
        "network_kinetics_chebyshev",
        "network_solve",
        "network_kinetics_id",
        "network_kinetics",
        "id",
        "solve_id",
    ),
    (
        "network_kinetics_plog",
        "network_solve",
        "network_kinetics_id",
        "network_kinetics",
        "id",
        "solve_id",
    ),
    (
        "network_kinetics_point",
        "network_solve",
        "network_kinetics_id",
        "network_kinetics",
        "id",
        "solve_id",
    ),
)


def _trigger_name(prefix: str, table: str, suffix: str = "") -> str:
    raw = f"trg_{prefix}_{table}_{suffix}".rstrip("_")
    return raw[:63]


def _direct_child_groups() -> list[tuple[str, str, tuple[str, ...]]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for table, record_type, column in _DIRECT_CHILDREN:
        grouped.setdefault((table, record_type), []).append(column)
    return [(table, record_type, tuple(columns)) for (table, record_type), columns in grouped.items()]


def _create_schema() -> None:
    op.add_column(
        "record_review",
        sa.Column("first_approved_at", sa.DateTime(timezone=False), nullable=True),
        schema="public",
    )
    op.execute(
        """
        UPDATE public.record_review rr
        SET first_approved_at = event_times.first_approved_at
        FROM (
            SELECT record_review_id, min(created_at) AS first_approved_at
            FROM public.record_review_event
            WHERE from_status = 'approved' OR to_status = 'approved'
            GROUP BY record_review_id
        ) AS event_times
        WHERE rr.id = event_times.record_review_id
        """
    )
    op.execute(
        """
        UPDATE public.record_review
        SET first_approved_at = coalesce(reviewed_at, created_at)
        WHERE first_approved_at IS NULL
          AND status IN ('approved', 'deprecated')
        """
    )
    op.create_check_constraint(
        op.f("ck_record_review_record_review_approved_has_first_approval"),
        "record_review",
        "status <> 'approved' OR first_approved_at IS NOT NULL",
        schema="public",
    )

    op.create_table(
        "scientific_record_supersession",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "record_type",
            postgresql.ENUM(name="submission_record_type", create_type=False),
            nullable=False,
        ),
        sa.Column("superseded_record_id", sa.BigInteger(), nullable=False),
        sa.Column("superseding_record_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scientific_record_supersession")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["public.app_user.id"],
            name=op.f("fk_scientific_record_supersession_created_by_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.UniqueConstraint(
            "record_type",
            "superseded_record_id",
            name="uq_scientific_supersession_old_record",
        ),
        sa.UniqueConstraint(
            "record_type",
            "superseding_record_id",
            name="uq_scientific_supersession_new_record",
        ),
        sa.CheckConstraint(
            "record_type IN ('calculation', 'thermo', 'statmech', 'kinetics', "
            "'transport', 'network', 'network_solve', 'applied_energy_correction', "
            "'transition_state_entry', 'conformer_observation')",
            name=op.f("ck_scientific_record_supersession_supported_type"),
        ),
        sa.CheckConstraint(
            "superseded_record_id <> superseding_record_id",
            name=op.f("ck_scientific_record_supersession_distinct_records"),
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0",
            name=op.f("ck_scientific_record_supersession_reason_nonblank"),
        ),
        schema="public",
    )


def _create_functions() -> None:
    supported = ", ".join(f"'{value}'" for value in _ROOT_TYPES)
    op.execute(
        f"""
        CREATE FUNCTION public.tckdb_is_accepted_science_type(
            p_type public.submission_record_type
        ) RETURNS boolean
        LANGUAGE sql IMMUTABLE
        SET search_path = pg_catalog, public
        AS $$
            SELECT p_type::text IN ({supported})
        $$
        """
    )
    cases = "\n".join(
        f"""
            WHEN '{record_type}' THEN
                PERFORM 1 FROM public.{table} WHERE id = p_record_id FOR UPDATE;
        """
        for record_type, table in _ROOT_TYPES.items()
    )
    op.execute(
        f"""
        CREATE FUNCTION public.tckdb_lock_scientific_record(
            p_type public.submission_record_type, p_record_id bigint
        ) RETURNS void
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            CASE p_type::text
                {cases}
                ELSE
                    RAISE EXCEPTION USING ERRCODE = '22023',
                        MESSAGE = 'unsupported accepted-science record type: ' || p_type::text;
            END CASE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '23503',
                    MESSAGE = format('%s record %s does not exist', p_type, p_record_id);
            END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_raise_if_accepted(
            p_type public.submission_record_type, p_record_id bigint
        ) RETURNS void
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.record_review
                WHERE record_type = p_type AND record_id = p_record_id
                  AND first_approved_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = format(
                        'accepted %s record %s is immutable', p_type, p_record_id
                    ),
                    HINT = 'Create and approve a replacement, then record a supersession.';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_guard_record_review()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'record_review rows cannot be deleted';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.record_type IS DISTINCT FROM OLD.record_type
                   OR NEW.record_id IS DISTINCT FROM OLD.record_id THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'record_review natural keys are immutable';
                END IF;
                IF OLD.first_approved_at IS NOT NULL
                   AND NEW.first_approved_at IS DISTINCT FROM OLD.first_approved_at THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'first_approved_at is immutable once set';
                END IF;
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.first_approved_at IS NULL
               AND NEW.first_approved_at IS NOT NULL
               AND NEW.status <> 'approved' THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'first_approved_at may only be established by approval';
            END IF;
            IF public.tckdb_is_accepted_science_type(NEW.record_type)
               AND (NEW.status = 'approved' OR NEW.first_approved_at IS NOT NULL) THEN
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.record_id
                );
            END IF;
            IF NEW.status = 'approved' THEN
                IF NEW.first_approved_at IS NULL THEN
                    NEW.first_approved_at := coalesce(NEW.reviewed_at, clock_timestamp());
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_guard_accepted_root()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE record_id bigint;
        BEGIN
            IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
            record_id := OLD.id;
            PERFORM public.tckdb_lock_scientific_record(
                TG_ARGV[0]::public.submission_record_type, record_id
            );
            PERFORM public.tckdb_raise_if_accepted(
                TG_ARGV[0]::public.submission_record_type, record_id
            );
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_guard_calculation_geometry()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE geometry_ids bigint[] := ARRAY[]::bigint[]; calculation_id bigint;
        BEGIN
            IF TG_TABLE_NAME = 'geometry' THEN
                IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
                IF TG_OP = 'DELETE' THEN
                    geometry_ids := ARRAY[OLD.id];
                ELSE
                    geometry_ids := ARRAY[OLD.id, NEW.id];
                END IF;
            ELSE
                IF TG_OP <> 'INSERT' THEN
                    geometry_ids := array_append(geometry_ids, OLD.geometry_id);
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    geometry_ids := array_append(geometry_ids, NEW.geometry_id);
                END IF;
            END IF;
            FOR calculation_id IN
                SELECT DISTINCT linked.calculation_id
                FROM (
                    SELECT cig.calculation_id
                    FROM public.calculation_input_geometry cig
                    WHERE cig.geometry_id = ANY(geometry_ids)
                    UNION
                    SELECT cog.calculation_id
                    FROM public.calculation_output_geometry cog
                    WHERE cog.geometry_id = ANY(geometry_ids)
                ) AS linked
                ORDER BY linked.calculation_id
            LOOP
                PERFORM public.tckdb_lock_scientific_record(
                    'calculation', calculation_id
                );
                PERFORM public.tckdb_raise_if_accepted(
                    'calculation', calculation_id
                );
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_guard_accepted_child()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE record_ids bigint[] := ARRAY[]::bigint[];
                argument_index integer; record_id bigint;
        BEGIN
            FOR argument_index IN 1..TG_NARGS - 1 LOOP
                IF TG_OP <> 'INSERT' THEN
                    record_ids := array_append(
                        record_ids,
                        (to_jsonb(OLD)->>TG_ARGV[argument_index])::bigint
                    );
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    record_ids := array_append(
                        record_ids,
                        (to_jsonb(NEW)->>TG_ARGV[argument_index])::bigint
                    );
                END IF;
            END LOOP;
            FOR record_id IN
                SELECT DISTINCT value
                FROM unnest(record_ids) AS value
                WHERE value IS NOT NULL
                ORDER BY value
            LOOP
                PERFORM public.tckdb_lock_scientific_record(
                    TG_ARGV[0]::public.submission_record_type, record_id
                );
                PERFORM public.tckdb_raise_if_accepted(
                    TG_ARGV[0]::public.submission_record_type, record_id
                );
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_guard_accepted_via_child()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE parent_id bigint; record_id bigint;
        BEGIN
            FOR parent_id IN
                SELECT DISTINCT value::bigint
                FROM unnest(ARRAY[
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD)->>TG_ARGV[1] END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW)->>TG_ARGV[1] END
                ]) AS value
                WHERE value IS NOT NULL
                ORDER BY value::bigint
            LOOP
                EXECUTE format(
                    'SELECT %I FROM public.%I WHERE %I = $1',
                    TG_ARGV[4], TG_ARGV[2], TG_ARGV[3]
                ) INTO record_id USING parent_id;
                IF record_id IS NOT NULL THEN
                    PERFORM public.tckdb_lock_scientific_record(
                        TG_ARGV[0]::public.submission_record_type, record_id
                    );
                    PERFORM public.tckdb_raise_if_accepted(
                        TG_ARGV[0]::public.submission_record_type, record_id
                    );
                END IF;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = TG_TABLE_NAME || ' is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_reject_truncate()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = TG_TABLE_NAME || ' cannot be truncated';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_validate_scientific_supersession()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE same_subject boolean := false;
        BEGIN
            IF NOT public.tckdb_is_accepted_science_type(NEW.record_type) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'unsupported scientific supersession type';
            END IF;
            IF NEW.superseded_record_id = NEW.superseding_record_id THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a scientific record cannot supersede itself';
            END IF;

            IF NEW.superseded_record_id < NEW.superseding_record_id THEN
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.superseded_record_id
                );
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.superseding_record_id
                );
            ELSE
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.superseding_record_id
                );
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.superseded_record_id
                );
            END IF;

            PERFORM 1
            FROM public.record_review
            WHERE record_type = NEW.record_type
              AND record_id IN (
                  NEW.superseded_record_id, NEW.superseding_record_id
              )
            ORDER BY record_id
            FOR UPDATE;

            IF NOT EXISTS (
                SELECT 1 FROM public.record_review
                WHERE record_type = NEW.record_type
                  AND record_id = NEW.superseded_record_id
                  AND first_approved_at IS NOT NULL
                  AND status = 'deprecated'
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'superseded record must be deprecated with approval history';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.record_review
                WHERE record_type = NEW.record_type
                  AND record_id = NEW.superseding_record_id
                  AND status = 'approved'
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'superseding record must currently be approved';
            END IF;

            CASE NEW.record_type::text
                WHEN 'thermo' THEN
                    SELECT o.species_entry_id IS NOT DISTINCT FROM n.species_entry_id
                    INTO same_subject
                    FROM public.thermo o CROSS JOIN public.thermo n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'statmech' THEN
                    SELECT o.species_entry_id IS NOT DISTINCT FROM n.species_entry_id
                    INTO same_subject
                    FROM public.statmech o CROSS JOIN public.statmech n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'transport' THEN
                    SELECT o.species_entry_id IS NOT DISTINCT FROM n.species_entry_id
                    INTO same_subject
                    FROM public.transport o CROSS JOIN public.transport n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'kinetics' THEN
                    SELECT o.reaction_entry_id IS NOT DISTINCT FROM n.reaction_entry_id
                       AND o.direction IS NOT DISTINCT FROM n.direction
                    INTO same_subject
                    FROM public.kinetics o CROSS JOIN public.kinetics n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'calculation' THEN
                    SELECT o.species_entry_id IS NOT DISTINCT FROM n.species_entry_id
                       AND o.transition_state_entry_id IS NOT DISTINCT FROM
                           n.transition_state_entry_id
                       AND o.type IS NOT DISTINCT FROM n.type
                    INTO same_subject
                    FROM public.calculation o CROSS JOIN public.calculation n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'network' THEN
                    same_subject := true;
                WHEN 'network_solve' THEN
                    SELECT o.network_id IS NOT DISTINCT FROM n.network_id
                    INTO same_subject
                    FROM public.network_solve o CROSS JOIN public.network_solve n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'applied_energy_correction' THEN
                    SELECT o.target_species_entry_id IS NOT DISTINCT FROM
                               n.target_species_entry_id
                       AND o.target_reaction_entry_id IS NOT DISTINCT FROM
                               n.target_reaction_entry_id
                       AND o.target_transition_state_entry_id IS NOT DISTINCT FROM
                               n.target_transition_state_entry_id
                       AND o.application_role IS NOT DISTINCT FROM n.application_role
                    INTO same_subject
                    FROM public.applied_energy_correction o
                    CROSS JOIN public.applied_energy_correction n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'transition_state_entry' THEN
                    SELECT o.transition_state_id IS NOT DISTINCT FROM
                               n.transition_state_id
                    INTO same_subject
                    FROM public.transition_state_entry o
                    CROSS JOIN public.transition_state_entry n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'conformer_observation' THEN
                    SELECT o.conformer_group_id IS NOT DISTINCT FROM
                               n.conformer_group_id
                    INTO same_subject
                    FROM public.conformer_observation o
                    CROSS JOIN public.conformer_observation n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
            END CASE;
            IF NOT coalesce(same_subject, false) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'supersession records must describe the same subject';
            END IF;

            IF EXISTS (
                WITH RECURSIVE chain(record_id) AS (
                    SELECT NEW.superseding_record_id
                    UNION
                    SELECT edge.superseding_record_id
                    FROM public.scientific_record_supersession edge
                    JOIN chain ON edge.superseded_record_id = chain.record_id
                    WHERE edge.record_type = NEW.record_type
                )
                SELECT 1 FROM chain WHERE record_id = NEW.superseded_record_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'scientific supersession cannot create a cycle';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def _create_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_guard_record_review
        BEFORE INSERT OR UPDATE OR DELETE ON public.record_review
        FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_record_review()
        """
    )
    for record_type, table in _ROOT_TYPES.items():
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_root", table)}
            BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.tckdb_guard_accepted_root('{record_type}')
            """
        )
    for index, (table, record_type, columns) in enumerate(_direct_child_groups()):
        arguments = ", ".join(f"'{column}'" for column in columns)
        op.execute(
            f"""
            CREATE TRIGGER trg_as_child_{index:02d}
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.tckdb_guard_accepted_child(
                '{record_type}', {arguments}
            )
            """
        )
    for index, (
        table,
        record_type,
        child_column,
        parent_table,
        parent_pk,
        root_column,
    ) in enumerate(_VIA_CHILDREN):
        op.execute(
            f"""
            CREATE TRIGGER trg_as_via_{index:02d}
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child(
                '{record_type}', '{child_column}', '{parent_table}',
                '{parent_pk}', '{root_column}'
            )
            """
        )
    op.execute(
        """
        CREATE TRIGGER trg_as_geometry
        BEFORE UPDATE OR DELETE ON public.geometry
        FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_calculation_geometry()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_as_geometry_atom
        BEFORE INSERT OR UPDATE OR DELETE ON public.geometry_atom
        FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_calculation_geometry()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_scientific_supersession_validate
        BEFORE INSERT ON public.scientific_record_supersession
        FOR EACH ROW
        EXECUTE FUNCTION public.tckdb_validate_scientific_supersession()
        """
    )
    for table in ("record_review_event", "scientific_record_supersession"):
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("append_only", table)}
            BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.tckdb_reject_mutation()
            """
        )

    truncate_tables = sorted(
        set(_ROOT_TYPES.values())
        | {table for table, _, _ in _DIRECT_CHILDREN}
        | {item[0] for item in _VIA_CHILDREN}
        | {
            "geometry",
            "geometry_atom",
            "record_review",
            "record_review_event",
            "record_reproducibility_assessment",
            "scientific_record_supersession",
        }
    )
    for index, table in enumerate(truncate_tables):
        op.execute(
            f"""
            CREATE TRIGGER trg_as_truncate_{index:02d}
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate()
            """
        )


def _drop_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_guard_record_review ON public.record_review")
    for _, table in _ROOT_TYPES.items():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_root', table)} ON public.{table}")
    for index, (table, _, _) in enumerate(_direct_child_groups()):
        op.execute(f"DROP TRIGGER IF EXISTS trg_as_child_{index:02d} ON public.{table}")
    for index, item in enumerate(_VIA_CHILDREN):
        op.execute(f"DROP TRIGGER IF EXISTS trg_as_via_{index:02d} ON public.{item[0]}")
    op.execute("DROP TRIGGER IF EXISTS trg_as_geometry ON public.geometry")
    op.execute("DROP TRIGGER IF EXISTS trg_as_geometry_atom ON public.geometry_atom")
    op.execute("DROP TRIGGER IF EXISTS trg_scientific_supersession_validate ON public.scientific_record_supersession")
    for table in ("record_review_event", "scientific_record_supersession"):
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('append_only', table)} ON public.{table}")
    truncate_tables = sorted(
        set(_ROOT_TYPES.values())
        | {table for table, _, _ in _DIRECT_CHILDREN}
        | {item[0] for item in _VIA_CHILDREN}
        | {
            "geometry",
            "geometry_atom",
            "record_review",
            "record_review_event",
            "record_reproducibility_assessment",
            "scientific_record_supersession",
        }
    )
    for index, table in enumerate(truncate_tables):
        op.execute(f"DROP TRIGGER IF EXISTS trg_as_truncate_{index:02d} ON public.{table}")


def upgrade() -> None:
    """Add permanent approval history and database-enforced immutability."""

    _create_schema()
    _create_functions()
    _create_triggers()


def downgrade() -> None:
    """Remove accepted-science guards and replacement history."""

    _drop_triggers()
    for function in (
        "tckdb_validate_scientific_supersession",
        "tckdb_reject_truncate",
        "tckdb_reject_mutation",
        "tckdb_guard_accepted_via_child",
        "tckdb_guard_calculation_geometry",
        "tckdb_guard_accepted_child",
        "tckdb_guard_accepted_root",
        "tckdb_guard_record_review",
        "tckdb_raise_if_accepted",
        "tckdb_lock_scientific_record",
        "tckdb_is_accepted_science_type",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS public.{function}() CASCADE")
    # Parameterized functions need explicit signatures on PostgreSQL.
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_raise_if_accepted(public.submission_record_type, bigint) CASCADE")
    op.execute(
        "DROP FUNCTION IF EXISTS public.tckdb_lock_scientific_record(public.submission_record_type, bigint) CASCADE"
    )
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_is_accepted_science_type(public.submission_record_type) CASCADE")
    op.drop_table("scientific_record_supersession", schema="public")
    op.drop_constraint(
        op.f("ck_record_review_record_review_approved_has_first_approval"),
        "record_review",
        type_="check",
        schema="public",
    )
    op.drop_column("record_review", "first_approved_at", schema="public")
