"""Disposable-database contract for ``d3f8a1c6e92b`` (``torsion_basin`` version 2 and the reassignment).

The revision adds a scheme row and then reassigns every conformer observation under it, inside the
migration's own transaction. An empty database proves nothing about that, so these tests build the
state the previous rule left on a real one -- two ethanol rotamers filed under one group, one of them
approved, a thermo target on that group that is approved too -- stand on the parent revision, and
upgrade:

* the default scheme flips to version 2 and its stored parameters are the ones documented;
* the observations are reassigned, the thermo target follows its own calculation, the review of the
  changed group is reset with an event;
* the approved observation and the approved thermo target moved through the declared-repair path
  (a declaration naming the revision and the exact columns, each change recorded old -> new) and the
  accepted-science trigger is still standing afterwards;
* running the step again changes nothing;
* the downgrade makes version 1 the default again and does not pretend to undo the grouping.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.db._migration_chain import revision_under_test
from tests.db.test_thermo_model_kind_backfill_migration import _MigrationHarness
from tests.services.test_basin_matching import with_dihedral

_MIGRATION = revision_under_test("d3f8a1c6e92b")

_ETHANOL = "CCO"


@pytest.fixture
def harness():
    created = _MigrationHarness("torsion_basin_reassign")
    yield created
    created.close()


def _geometry(conn, atoms) -> int:
    geometry_id = conn.scalar(
        text("INSERT INTO geometry (natoms, geom_hash) VALUES (:n, :h) RETURNING id"),
        {"n": len(atoms), "h": f"{abs(hash(atoms)) % 10**16:0>64}"},
    )
    for index, (symbol, x, y, z) in enumerate(atoms, start=1):
        conn.execute(
            text(
                "INSERT INTO geometry_atom (geometry_id, atom_index, element, x, y, z) "
                "VALUES (:g, :i, :e, :x, :y, :z)"
            ),
            {"g": geometry_id, "i": index, "e": symbol, "x": x, "y": y, "z": z},
        )
    return geometry_id


def _approve(conn, record_type: str, record_id: int, reviewer: int) -> None:
    conn.execute(
        text(
            "INSERT INTO record_review (record_type, record_id, status, reviewed_by, reviewed_at, first_approved_at) "
            "VALUES (CAST(:t AS submission_record_type), :id, CAST('approved' AS record_review_status), :u, now(), now())"
        ),
        {"t": record_type, "id": record_id, "u": reviewer},
    )


def _seed(conn) -> dict[str, int]:
    """Version 1's state: both ethanol rotamers filed under ONE group (the false merge)."""
    ids: dict[str, int] = {}
    reviewer = conn.scalar(
        text("INSERT INTO app_user (username, role) VALUES ('reassign-curator', CAST('curator' AS app_user_role)) RETURNING id")
    )
    species = conn.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) VALUES "
            "(CAST('molecule' AS molecule_kind), :s, 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N', 0, 1, CAST('achiral' AS stereo_kind)) "
            "RETURNING id"
        ),
        {"s": _ETHANOL},
    )
    entry = conn.scalar(text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species})
    ids["v1_scheme"] = conn.scalar(
        text("SELECT id FROM conformer_assignment_scheme WHERE name = 'torsion_basin' AND version = 1")
    )
    ids["group"] = conn.scalar(
        text("INSERT INTO conformer_group (species_entry_id, label) VALUES (:e, 'conformer_1') RETURNING id"),
        {"e": entry},
    )
    conn.execute(
        text("UPDATE conformer_group SET representative_fingerprint_json = CAST('{\"rotor_count\": 1}' AS jsonb) WHERE id = :g"),
        {"g": ids["group"]},
    )
    for name, degrees in (("trans", 180.0), ("gauche", 60.0)):
        observation = conn.scalar(
            text(
                "INSERT INTO conformer_observation (conformer_group_id, assignment_scheme_id) VALUES (:g, :s) RETURNING id"
            ),
            {"g": ids["group"], "s": ids["v1_scheme"]},
        )
        calculation = conn.scalar(
            text(
                "INSERT INTO calculation (type, species_entry_id, conformer_observation_id) "
                "VALUES (CAST('opt' AS calc_type), :e, :o) RETURNING id"
            ),
            {"e": entry, "o": observation},
        )
        geometry = _geometry(conn, with_dihedral(_ETHANOL, "[#1][OX2][CH2][CH3]", degrees, seed=5))
        conn.execute(
            text(
                "INSERT INTO calculation_output_geometry (calculation_id, geometry_id, output_order, role) "
                "VALUES (:c, :g, 1, CAST('final' AS calculation_geometry_role))"
            ),
            {"c": calculation, "g": geometry},
        )
        ids[f"{name}_observation"], ids[f"{name}_calculation"] = observation, calculation
    ids["thermo"] = conn.scalar(
        text(
            "INSERT INTO thermo (species_entry_id, scientific_origin, thermodynamic_target_kind, target_conformer_group_id) "
            "VALUES (:e, CAST('computed' AS scientific_origin_kind), CAST('single_conformer' AS thermo_target_kind), :g) "
            "RETURNING id"
        ),
        {"e": entry, "g": ids["group"]},
    )
    conn.execute(
        text(
            "INSERT INTO thermo_source_calculation (thermo_id, calculation_id, role) "
            "VALUES (:t, :c, CAST('sp' AS thermo_calc_role))"
        ),
        {"t": ids["thermo"], "c": ids["gauche_calculation"]},
    )
    # The group was reviewed, and the gauche observation and the thermo are approved: the frozen rows.
    conn.execute(
        text(
            "INSERT INTO record_review (record_type, record_id, status, reviewed_by, reviewed_at) "
            "VALUES (CAST('conformer_group' AS submission_record_type), :g, CAST('under_review' AS record_review_status), NULL, NULL)"
        ),
        {"g": ids["group"]},
    )
    _approve(conn, "conformer_observation", ids["gauche_observation"], reviewer)
    _approve(conn, "thermo", ids["thermo"], reviewer)
    return ids


def _state(conn, ids: dict[str, int]) -> dict:
    return {
        "gauche_group": conn.scalar(
            text("SELECT conformer_group_id FROM conformer_observation WHERE id = :i"), {"i": ids["gauche_observation"]}
        ),
        "trans_group": conn.scalar(
            text("SELECT conformer_group_id FROM conformer_observation WHERE id = :i"), {"i": ids["trans_observation"]}
        ),
        "thermo_group": conn.scalar(text("SELECT target_conformer_group_id FROM thermo WHERE id = :i"), {"i": ids["thermo"]}),
        "groups": conn.scalar(text("SELECT count(*) FROM conformer_group")),
        "scheme_of_gauche": conn.scalar(
            text("SELECT assignment_scheme_id FROM conformer_observation WHERE id = :i"), {"i": ids["gauche_observation"]}
        ),
    }


def test_upgrade_reassigns_through_declared_repairs_and_the_guard_stays_standing(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        state = _state(conn, ids)
        v2 = conn.execute(
            text("SELECT id, is_default, parameters_json FROM conformer_assignment_scheme WHERE name = 'torsion_basin' AND version = 2")
        ).one()
        v1_default = conn.scalar(
            text("SELECT is_default FROM conformer_assignment_scheme WHERE id = :i"), {"i": ids["v1_scheme"]}
        )

        # The scheme: version 2 is the default and its parameters are stored as documented.
        assert v2.is_default and not v1_default
        assert v2.parameters_json == {
            "matching_rule": "symmetry_existence",
            "require_all_comparable_torsions_within_threshold": True,
            "use_circular_difference": True,
            "torsion_tolerance_heavy_degrees": 20,
            "torsion_tolerance_terminal_h_degrees": 30,
            "exclude_methyl_rotors": False,
            "exclude_terminal_noisy_rotors": False,
            "methyl_symmetry_fold": 3,
            "max_symmetry_mappings": 4096,
            "quantization_bin_degrees": 15,
            "rigid_fallback_use_rmsd": True,
            "rmsd_threshold_angstrom": 0.5,
            "tie_break": "closest_torsional_distance",
        }

        # The data step: the false merge is split, the original group keeps the trans representative, the
        # thermo target follows its own (gauche) calculation, and everything is stamped with version 2.
        assert state["groups"] == 2
        assert state["trans_group"] == ids["group"]
        assert state["gauche_group"] != ids["group"]
        assert state["thermo_group"] == state["gauche_group"]
        assert state["scheme_of_gauche"] == v2.id

        # The review of the group whose membership changed is reset, with an event saying so.
        status = conn.scalar(
            text(
                "SELECT status::text FROM record_review "
                "WHERE record_type = CAST('conformer_group' AS submission_record_type) AND record_id = :g"
            ),
            {"g": ids["group"]},
        )
        assert status == "not_reviewed"
        assert conn.scalar(
            text(
                "SELECT count(*) FROM record_review_event e JOIN record_review r ON r.id = e.record_review_id "
                "WHERE r.record_id = :g AND e.from_status = CAST('under_review' AS record_review_status) "
                "AND e.to_status = CAST('not_reviewed' AS record_review_status)"
            ),
            {"g": ids["group"]},
        ) == 1

        # The frozen rows moved through declared repairs naming this revision and exactly the columns changed.
        declared = {
            row.target_table: sorted(row.declared_columns)
            for row in conn.execute(
                text("SELECT target_table, declared_columns FROM accepted_science_repair WHERE alembic_revision = 'd3f8a1c6e92b'")
            )
        }
        assert declared == {
            "conformer_observation": ["assignment_scheme_id", "conformer_group_id", "torsion_fingerprint_json"],
            "thermo": ["target_conformer_group_id"],
        }
        recorded = {
            (row.record_type, row.record_id): (row.before_json, row.after_json)
            for row in conn.execute(
                text(
                    "SELECT c.record_type::text AS record_type, c.record_id, c.before_json, c.after_json "
                    "FROM accepted_science_repair_change c JOIN accepted_science_repair r ON r.id = c.repair_id "
                    "WHERE r.alembic_revision = 'd3f8a1c6e92b'"
                )
            )
        }
        before, after = recorded[("conformer_observation", ids["gauche_observation"])]
        assert (before["conformer_group_id"], after["conformer_group_id"]) == (ids["group"], state["gauche_group"])
        before, after = recorded[("thermo", ids["thermo"])]
        assert (before["target_conformer_group_id"], after["target_conformer_group_id"]) == (ids["group"], state["gauche_group"])

    # The trigger was never disabled: an undeclared write to the approved observation is still refused.
    with harness.engine.connect() as conn:
        with pytest.raises(Exception, match="accepted|approved|immutable|frozen"):
            conn.execute(
                text("UPDATE conformer_observation SET note = 'edited' WHERE id = :i"), {"i": ids["gauche_observation"]}
            )
            conn.commit()


def test_running_the_step_again_changes_nothing(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.connect() as conn:
        first = _state(conn, ids)
        repairs = conn.scalar(text("SELECT count(*) FROM accepted_science_repair"))

    harness.run("downgrade", _MIGRATION.parent)
    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        assert _state(conn, ids) == first
        # Nothing needed moving the second time, so no new repair was declared.
        assert conn.scalar(text("SELECT count(*) FROM accepted_science_repair")) == repairs


def test_downgrade_restores_the_default_without_pretending_to_undo_the_grouping(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.connect() as conn:
        grouped = _state(conn, ids)

    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        defaults = conn.execute(
            text("SELECT version FROM conformer_assignment_scheme WHERE name = 'torsion_basin' AND is_default")
        ).all()
        assert [row.version for row in defaults] == [1]
        # The scheme row version 2 stays because observations reference it, and so does the grouping.
        assert _state(conn, ids) == grouped
