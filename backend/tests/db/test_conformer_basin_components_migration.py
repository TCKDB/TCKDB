"""Disposable-database contract for ``b7e4c9a2d518`` (conformer basins as connected components, scheme version 3).

The revision adds columns, a pending-merge table and a scheme row, then reassigns every conformer observation under
that scheme inside the migration's own transaction. An empty database proves nothing about that, so these tests build
the states the previous rule left on a real one, stand on the parent revision, and upgrade:

* ethanol trans and gauche filed under ONE group (a false merge), the gauche observation and a thermo target approved;
* n-butane structures 0, 18 and 36 degrees filed as ``{0, 18}`` and ``{36}`` (the order-dependent split), the 36
  structure and a thermo target on its group approved.

and check that:

* the default scheme flips to version 3 and its stored parameters are the documented ones;
* the false merge is split, the false split is merged onto the OLDER group and the emptied row is kept with
  ``merged_into_conformer_group_id``; a chain that is not a clique is stored as a wide basin, not split;
* each group's representative is its medoid and the observations carry their mapped coordinates;
* approved observations and thermo targets moved through the declared-repair path (a declaration naming the revision
  and the exact columns, each change recorded old -> new) and the accepted-science trigger is still standing;
* running the step again changes nothing;
* the downgrade makes version 2 the default again, drops what the revision added and does not pretend to un-merge.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.db._migration_chain import revision_under_test
from tests.db.test_thermo_model_kind_backfill_migration import _MigrationHarness
from tests.services.test_basin_matching import with_dihedral

_MIGRATION = revision_under_test("b7e4c9a2d518")

_ETHANOL = "CCO"
_BUTANE = "CCCC"


@pytest.fixture
def harness():
    created = _MigrationHarness("conformer_basin_components")
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


def _entry(conn, smiles: str, inchi_key: str) -> int:
    species = conn.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) VALUES "
            "(CAST('molecule' AS molecule_kind), :s, :k, 0, 1, CAST('achiral' AS stereo_kind)) RETURNING id"
        ),
        {"s": smiles, "k": inchi_key},
    )
    return conn.scalar(text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species})


def _group(conn, entry: int, label: str) -> int:
    group = conn.scalar(
        text("INSERT INTO conformer_group (species_entry_id, label) VALUES (:e, :l) RETURNING id"),
        {"e": entry, "l": label},
    )
    conn.execute(
        text("UPDATE conformer_group SET representative_fingerprint_json = CAST('{\"rotor_count\": 1}' AS jsonb) WHERE id = :g"),
        {"g": group},
    )
    return group


def _observation(conn, entry: int, group: int, scheme: int, atoms) -> tuple[int, int]:
    """An observation filed under ``group`` whose primary ``opt`` calculation carries ``atoms`` as its final geometry."""
    observation = conn.scalar(
        text("INSERT INTO conformer_observation (conformer_group_id, assignment_scheme_id) VALUES (:g, :s) RETURNING id"),
        {"g": group, "s": scheme},
    )
    calculation = conn.scalar(
        text(
            "INSERT INTO calculation (type, species_entry_id, conformer_observation_id) "
            "VALUES (CAST('opt' AS calc_type), :e, :o) RETURNING id"
        ),
        {"e": entry, "o": observation},
    )
    conn.execute(
        text(
            "INSERT INTO calculation_output_geometry (calculation_id, geometry_id, output_order, role) "
            "VALUES (:c, :g, 1, CAST('final' AS calculation_geometry_role))"
        ),
        {"c": calculation, "g": _geometry(conn, atoms)},
    )
    return observation, calculation


def _thermo(conn, entry: int, group: int, calculation: int) -> int:
    thermo = conn.scalar(
        text(
            "INSERT INTO thermo (species_entry_id, scientific_origin, thermodynamic_target_kind, target_conformer_group_id) "
            "VALUES (:e, CAST('computed' AS scientific_origin_kind), CAST('single_conformer' AS thermo_target_kind), :g) "
            "RETURNING id"
        ),
        {"e": entry, "g": group},
    )
    conn.execute(
        text(
            "INSERT INTO thermo_source_calculation (thermo_id, calculation_id, role) "
            "VALUES (:t, :c, CAST('sp' AS thermo_calc_role))"
        ),
        {"t": thermo, "c": calculation},
    )
    return thermo


def _seed(conn) -> dict[str, int]:
    """The state the previous rule left behind (see the module docstring)."""
    ids: dict[str, int] = {}
    reviewer = conn.scalar(
        text("INSERT INTO app_user (username, role) VALUES ('basin-curator', CAST('curator' AS app_user_role)) RETURNING id")
    )
    previous = conn.scalar(text("SELECT id FROM conformer_assignment_scheme WHERE name = 'torsion_basin' AND version = 2"))

    # ethanol: a false merge, trans and gauche under one group
    ethanol = _entry(conn, _ETHANOL, "LFQSCWFLJHTTHZ-UHFFFAOYSA-N")
    ids["eth_group"] = _group(conn, ethanol, "conformer_1")
    for name, degrees in (("trans", 180.0), ("gauche", 60.0)):
        ids[f"{name}_observation"], ids[f"{name}_calculation"] = _observation(
            conn, ethanol, ids["eth_group"], previous, with_dihedral(_ETHANOL, "[#1][OX2][CH2][CH3]", degrees, seed=5)
        )
    ids["eth_thermo"] = _thermo(conn, ethanol, ids["eth_group"], ids["gauche_calculation"])
    conn.execute(
        text(
            "INSERT INTO record_review (record_type, record_id, status) "
            "VALUES (CAST('conformer_group' AS submission_record_type), :g, CAST('under_review' AS record_review_status))"
        ),
        {"g": ids["eth_group"]},
    )
    _approve(conn, "conformer_observation", ids["gauche_observation"], reviewer)
    _approve(conn, "thermo", ids["eth_thermo"], reviewer)

    # n-butane: a false split, {0, 18} and {36}
    butane = _entry(conn, _BUTANE, "IJDNQMDRQITEOD-UHFFFAOYSA-N")
    ids["but_older"], ids["but_younger"] = _group(conn, butane, "conformer_1"), _group(conn, butane, "conformer_2")
    for name, degrees, group in (("a", 0.0, "but_older"), ("b", 18.0, "but_older"), ("c", 36.0, "but_younger")):
        ids[f"but_{name}"], ids[f"but_{name}_calculation"] = _observation(
            conn, butane, ids[group], previous, with_dihedral(_BUTANE, "[CH3][CH2][CH2][CH3]", degrees)
        )
    ids["but_thermo"] = _thermo(conn, butane, ids["but_younger"], ids["but_c_calculation"])
    _approve(conn, "conformer_observation", ids["but_c"], reviewer)
    _approve(conn, "thermo", ids["but_thermo"], reviewer)
    return ids


def _state(conn, ids: dict[str, int]) -> dict:
    def group_of(key: str) -> int:
        return conn.scalar(text("SELECT conformer_group_id FROM conformer_observation WHERE id = :i"), {"i": ids[key]})

    return {
        "gauche_group": group_of("gauche_observation"),
        "trans_group": group_of("trans_observation"),
        "eth_thermo_group": conn.scalar(
            text("SELECT target_conformer_group_id FROM thermo WHERE id = :i"), {"i": ids["eth_thermo"]}
        ),
        "butane_groups": [group_of(k) for k in ("but_a", "but_b", "but_c")],
        "but_thermo_group": conn.scalar(
            text("SELECT target_conformer_group_id FROM thermo WHERE id = :i"), {"i": ids["but_thermo"]}
        ),
        "groups": conn.scalar(text("SELECT count(*) FROM conformer_group")),
    }


def test_upgrade_reassigns_through_declared_repairs_and_the_guard_stays_standing(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        state = _state(conn, ids)
        v3 = conn.execute(
            text(
                "SELECT id, is_default, parameters_json FROM conformer_assignment_scheme "
                "WHERE name = 'torsion_basin' AND version = 3"
            )
        ).one()
        v2_default = conn.scalar(
            text("SELECT is_default FROM conformer_assignment_scheme WHERE name = 'torsion_basin' AND version = 2")
        )

        # The scheme: version 3 is the default and its parameters are stored as documented; version 2's are not touched.
        assert v3.is_default and not v2_default
        assert v3.parameters_json == {
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
            "linkage": "connected_components",
            "reference": "medoid",
            "defining_members": "converged_opt",
        }

        # Ethanol: the false merge is split, the original group keeps the trans representative, the thermo target
        # follows its own (gauche) calculation.
        assert state["trans_group"] == ids["eth_group"]
        assert state["gauche_group"] != ids["eth_group"]
        assert state["eth_thermo_group"] == state["gauche_group"]

        # n-butane: the false split is merged onto the OLDER group, whose representative is the middle structure; the
        # emptied younger row is kept and says where its members went; a chain that is not a clique is flagged, not split.
        assert state["butane_groups"] == [ids["but_older"]] * 3
        assert state["but_thermo_group"] == ids["but_older"]
        older = conn.execute(
            text(
                "SELECT representative_observation_id AS rep, basin_is_clique AS clique, basin_span_deg AS span, "
                "merged_into_conformer_group_id AS merged FROM conformer_group WHERE id = :g"
            ),
            {"g": ids["but_older"]},
        ).one()
        assert (older.rep, older.clique, older.merged) == (ids["but_b"], False, None)
        assert older.span == pytest.approx(36.0, abs=1.0)
        younger = conn.execute(
            text(
                "SELECT representative_observation_id AS rep, representative_fingerprint_json AS fp, "
                "merged_into_conformer_group_id AS merged FROM conformer_group WHERE id = :g"
            ),
            {"g": ids["but_younger"]},
        ).one()
        assert (younger.rep, younger.fp, younger.merged) == (None, None, ids["but_older"])

        # Every observation carries the structure in canonical order, and every stamp is the new scheme.
        assert conn.scalar(
            text("SELECT count(*) FROM conformer_observation WHERE mapped_coords_json IS NULL OR assignment_scheme_id <> :s"),
            {"s": v3.id},
        ) == 0

        # The review of every group whose membership changed is reset, with an event saying so.
        status = conn.scalar(
            text(
                "SELECT status::text FROM record_review "
                "WHERE record_type = CAST('conformer_group' AS submission_record_type) AND record_id = :g"
            ),
            {"g": ids["eth_group"]},
        )
        assert status == "not_reviewed"
        assert conn.scalar(
            text(
                "SELECT count(*) FROM record_review_event e JOIN record_review r ON r.id = e.record_review_id "
                "WHERE r.record_id = :g AND e.from_status = CAST('under_review' AS record_review_status) "
                "AND e.to_status = CAST('not_reviewed' AS record_review_status)"
            ),
            {"g": ids["eth_group"]},
        ) == 1

        # The frozen rows moved through declared repairs naming this revision and exactly the columns changed.
        declared = {
            row.target_table: sorted(row.declared_columns)
            for row in conn.execute(
                text(
                    "SELECT target_table, declared_columns FROM accepted_science_repair WHERE alembic_revision = :r"
                ),
                {"r": _MIGRATION.revision},
            )
        }
        assert declared == {
            "conformer_observation": [
                "assignment_scheme_id",
                "conformer_group_id",
                "mapped_coords_json",
                "torsion_fingerprint_json",
            ],
            "thermo": ["target_conformer_group_id"],
        }
        recorded = {
            (row.record_type, row.record_id): (row.before_json, row.after_json)
            for row in conn.execute(
                text(
                    "SELECT c.record_type::text AS record_type, c.record_id, c.before_json, c.after_json "
                    "FROM accepted_science_repair_change c JOIN accepted_science_repair r ON r.id = c.repair_id "
                    "WHERE r.alembic_revision = :r"
                ),
                {"r": _MIGRATION.revision},
            )
        }
        before, after = recorded[("conformer_observation", ids["gauche_observation"])]
        assert (before["conformer_group_id"], after["conformer_group_id"]) == (ids["eth_group"], state["gauche_group"])
        before, after = recorded[("conformer_observation", ids["but_c"])]
        assert (before["conformer_group_id"], after["conformer_group_id"]) == (ids["but_younger"], ids["but_older"])
        before, after = recorded[("thermo", ids["eth_thermo"])]
        assert (before["target_conformer_group_id"], after["target_conformer_group_id"]) == (
            ids["eth_group"], state["gauche_group"],
        )
        before, after = recorded[("thermo", ids["but_thermo"])]
        assert (before["target_conformer_group_id"], after["target_conformer_group_id"]) == (
            ids["but_younger"], ids["but_older"],
        )

        # Nothing is owed: the data step completed whatever it was asked to.
        assert conn.scalar(text("SELECT count(*) FROM conformer_group_merge_pending WHERE completed_at IS NULL")) == 0

    # The trigger was never disabled: an undeclared write to an approved observation is still refused.
    with harness.engine.connect() as conn:
        with pytest.raises(Exception, match="accepted|approved|immutable|frozen"):
            conn.execute(
                text("UPDATE conformer_observation SET note = 'edited' WHERE id = :i"), {"i": ids["gauche_observation"]}
            )
            conn.commit()


def test_running_the_step_again_changes_nothing(harness) -> None:
    from sqlalchemy.orm import Session

    from app.services.conformer_reassignment import reassign_conformer_groups

    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.connect() as conn:
        first = _state(conn, ids)
        repairs = conn.scalar(text("SELECT count(*) FROM accepted_science_repair"))

    with harness.engine.begin() as conn:
        report = reassign_conformer_groups(Session(bind=conn), repair_revision=_MIGRATION.revision)
    assert report.entries_examined == 2 and report.observations_examined == 5
    moved = {k: v for k, v in report.__dict__.items() if isinstance(v, int) and k not in ("entries_examined", "observations_examined")}
    assert not any(moved.values()), moved
    assert report.repairs_declared == []

    with harness.engine.connect() as conn:
        assert _state(conn, ids) == first
        # Nothing needed moving the second time, so no new repair was declared.
        assert conn.scalar(text("SELECT count(*) FROM accepted_science_repair")) == repairs


def test_downgrading_and_upgrading_again_reaches_the_same_grouping(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.connect() as conn:
        first = _state(conn, ids)

    harness.run("downgrade", _MIGRATION.parent)
    harness.run("upgrade", _MIGRATION.revision)

    with harness.engine.connect() as conn:
        assert _state(conn, ids) == first


def test_downgrade_restores_the_default_and_drops_what_the_revision_added_without_pretending_to_unmerge(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.connect() as conn:
        grouped = _state(conn, ids)
        assert conn.scalar(text("SELECT to_regclass('conformer_group_merge_pending')")) is not None

    harness.run("downgrade", _MIGRATION.parent)

    with harness.engine.connect() as conn:
        defaults = conn.execute(
            text("SELECT version FROM conformer_assignment_scheme WHERE name = 'torsion_basin' AND is_default")
        ).all()
        assert [row.version for row in defaults] == [2]
        # The scheme row version 3 stays because observations reference it, and so does the grouping.
        assert _state(conn, ids) == grouped
        assert conn.scalar(text("SELECT to_regclass('conformer_group_merge_pending')")) is None
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name IN ('conformer_group', 'conformer_observation')"
                )
            )
        }
        assert not columns & {
            "representative_observation_id", "merged_into_conformer_group_id", "basin_is_clique",
            "basin_span_deg", "mapped_coords_json",
        }
