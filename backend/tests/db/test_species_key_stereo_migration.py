"""Disposable-database contract for ``c7e2a9f4d138`` (species key is the constitution; entry stereo descriptor).

An empty database proves nothing about a data step, so this builds the state the old rule left on a real one and
upgrades from the parent revision:

* three 2-butene species (``C/C=C/C``, ``CC=CC``, ``C/C=C\\C``) that are one constitution;
* entries labelled ``E`` under two of them (one of which carries an approved thermo and conformer groups), one ``Z``;
* one entry whose SMILES marks (trans) and geometry (cis) contradict each other, deposited under a free-text label;
* reactions whose participants collide once the species are one.

It asserts the merged graph, the declared repairs and the change ledger, the survivors' public refs, that the
contradiction is listed and not relabelled, and that the approved record is still guarded.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text

from tests.db._migration_chain import revision_under_test
from tests.db.test_thermo_model_kind_backfill_migration import _MigrationHarness
from tests.services.test_stereo_descriptor import embed_xyz

_MIGRATION = revision_under_test("c7e2a9f4d138")


@pytest.fixture
def harness():
    created = _MigrationHarness("species_key_stereo")
    yield created
    created.close()


def _species(conn, smiles: str, inchi_key: str) -> int:
    return conn.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) VALUES "
            "(CAST('molecule' AS molecule_kind), :s, :k, 0, 1, CAST('ez_isomer' AS stereo_kind)) RETURNING id"
        ),
        {"s": smiles, "k": inchi_key},
    )


def _entry(conn, species: int, mol_smiles: str, label: str | None) -> int:
    return conn.scalar(
        text(
            "INSERT INTO species_entry (species_id, mol, stereo_label) "
            "VALUES (:sp, mol_from_smiles(CAST(:m AS cstring)), :l) RETURNING id"
        ),
        {"sp": species, "m": mol_smiles, "l": label},
    )


def _hash(reactants: dict[int, int], products: dict[int, int]) -> str:
    payload = {"reversible": True, "reactants": sorted(reactants.items()), "products": sorted(products.items())}
    return hashlib.sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def _reaction(conn, reactants: dict[int, int], products: dict[int, int]) -> tuple[int, int]:
    reaction = conn.scalar(
        text("INSERT INTO chem_reaction (reversible, stoichiometry_hash) VALUES (true, :h) RETURNING id"),
        {"h": _hash(reactants, products)},
    )
    for role, side in (("reactant", reactants), ("product", products)):
        for species, n in side.items():
            conn.execute(
                text(
                    "INSERT INTO reaction_participant (reaction_id, species_id, role, stoichiometry) "
                    "VALUES (:r, :s, CAST(:role AS reaction_role), :n)"
                ),
                {"r": reaction, "s": species, "role": role, "n": n},
            )
    entry = conn.scalar(text("INSERT INTO reaction_entry (reaction_id) VALUES (:r) RETURNING id"), {"r": reaction})
    return reaction, entry


def _seed(conn) -> dict[str, int]:
    ids: dict[str, int] = {}
    reviewer = conn.scalar(
        text("INSERT INTO app_user (username, role) VALUES ('stereo-curator', CAST('curator' AS app_user_role)) RETURNING id")
    )
    ids["s_trans"] = _species(conn, "C/C=C/C", "IAQRGUVFOMOMEM-ONEGZZNKSA-N")
    ids["s_flat"] = _species(conn, "CC=CC", "IAQRGUVFOMOMEM-UHFFFAOYSA-N")
    ids["s_cis"] = _species(conn, "C/C=C\\C", "IAQRGUVFOMOMEM-ARJAWSKDSA-N")
    ids["s_butane"] = _species(conn, "CCCC", "IJDNQMDRQITEOD-UHFFFAOYSA-N")
    ids["e_trans"] = _entry(conn, ids["s_trans"], "C/C=C/C", "E")
    ids["e_flat"] = _entry(conn, ids["s_flat"], "CC=CC", "E")
    ids["e_cis"] = _entry(conn, ids["s_cis"], "C/C=C\\C", "Z")
    ids["e_bad"] = _entry(conn, ids["s_trans"], "C/C=C/C", "trans")  # marks say trans; its geometry is cis
    ids["e_butane"] = _entry(conn, ids["s_butane"], "CCCC", None)

    # The contradictory entry's only geometry is the cis one.
    geometry = conn.scalar(
        text("INSERT INTO geometry (natoms, geom_hash, xyz_text) VALUES (14, :h, :x) RETURNING id"),
        {"h": "c" * 64, "x": embed_xyz("C/C=C\\C")},
    )
    ids["calc_bad"] = conn.scalar(
        text("INSERT INTO calculation (type, species_entry_id) VALUES (CAST('opt' AS calc_type), :e) RETURNING id"),
        {"e": ids["e_bad"]},
    )
    conn.execute(
        text(
            "INSERT INTO calculation_output_geometry (calculation_id, geometry_id, output_order, role) "
            "VALUES (:c, :g, 1, CAST('final' AS calculation_geometry_role))"
        ),
        {"c": ids["calc_bad"], "g": geometry},
    )

    # An approved thermo on the entry that will be merged away (the younger of the two E entries).
    ids["thermo"] = conn.scalar(
        text(
            "INSERT INTO thermo (species_entry_id, scientific_origin, thermodynamic_target_kind) "
            "VALUES (:e, CAST('computed' AS scientific_origin_kind), CAST('equilibrium_ensemble' AS thermo_target_kind)) "
            "RETURNING id"
        ),
        {"e": ids["e_flat"]},
    )
    conn.execute(
        text(
            "INSERT INTO record_review (record_type, record_id, status, reviewed_by, reviewed_at, first_approved_at) "
            "VALUES (CAST('thermo' AS submission_record_type), :id, CAST('approved' AS record_review_status), :u, now(), now())"
        ),
        {"id": ids["thermo"], "u": reviewer},
    )
    # The surviving entry was reviewed too; merging into it must reset that review.
    conn.execute(
        text(
            "INSERT INTO record_review (record_type, record_id, status, reviewed_by, reviewed_at) "
            "VALUES (CAST('species_entry' AS submission_record_type), :id, CAST('under_review' AS record_review_status), NULL, NULL)"
        ),
        {"id": ids["e_trans"]},
    )
    # Each of the two E entries has a conformer group with the same label: the merge cannot keep both.
    for key in ("e_trans", "e_flat"):
        ids[f"group_{key}"] = conn.scalar(
            text("INSERT INTO conformer_group (species_entry_id, label) VALUES (:e, 'conf_1') RETURNING id"),
            {"e": ids[key]},
        )

    # Reactions: A + B -> butane (one E species twice once merged), and two reactions that become identical.
    ids["r_pair"], ids["re_pair"] = _reaction(conn, {ids["s_trans"]: 1, ids["s_flat"]: 1}, {ids["s_butane"]: 1})
    ids["r_one"], ids["re_one"] = _reaction(conn, {ids["s_trans"]: 1}, {ids["s_butane"]: 1})
    ids["r_two"], ids["re_two"] = _reaction(conn, {ids["s_flat"]: 1}, {ids["s_butane"]: 1})
    return ids


def test_upgrade_merges_species_and_entries_through_declared_repairs(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        refs_before = {
            name: conn.scalar(text("SELECT public_ref FROM species_entry WHERE id = :i"), {"i": ids[name]})
            for name in ("e_trans", "e_cis", "e_bad")
        }
        species_ref_before = conn.scalar(text("SELECT public_ref FROM species WHERE id = :i"), {"i": ids["s_trans"]})
        loser_entry_ref = conn.scalar(text("SELECT public_ref FROM species_entry WHERE id = :i"), {"i": ids["e_flat"]})
    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode == 0, completed.stderr[-3000:]

    with harness.engine.connect() as conn:
        # One species for the three spellings; the oldest id survives and keeps its public ref.
        species = conn.execute(
            text("SELECT id, smiles, inchi_key, public_ref FROM species WHERE smiles = 'CC=CC'")
        ).all()
        assert [row.id for row in species] == [ids["s_trans"]]
        assert species[0].public_ref == species_ref_before
        assert species[0].inchi_key == "IAQRGUVFOMOMEM-UHFFFAOYSA-N"
        assert conn.scalar(text("SELECT count(*) FROM species WHERE smiles LIKE '%/%' OR smiles LIKE '%\\\\%'")) == 0
        assert conn.scalar(text("SELECT count(*) FROM species")) == 2  # 2-butene and butane

        # Entries: E (merged), Z, and the contradictory one, which is left unresolved rather than relabelled.
        entries = {
            row.id: row
            for row in conn.execute(
                text(
                    "SELECT id, species_id, stereo_descriptor, stereo_label, stereo_resolution::text AS res, "
                    "stereo_smiles, public_ref FROM species_entry WHERE species_id = :s"
                ),
                {"s": ids["s_trans"]},
            )
        }
        assert set(entries) == {ids["e_trans"], ids["e_cis"], ids["e_bad"]}
        assert entries[ids["e_trans"]].stereo_descriptor == "E"
        assert entries[ids["e_trans"]].stereo_smiles == "C/C=C/C"
        assert entries[ids["e_cis"]].stereo_descriptor == "Z"
        bad = entries[ids["e_bad"]]
        assert (bad.stereo_descriptor, bad.res, bad.stereo_label) == ("?", "unresolved", "trans")
        for name in ("e_trans", "e_cis", "e_bad"):
            assert entries[ids[name]].public_ref == refs_before[name]
        assert conn.scalar(text("SELECT count(*) FROM species_entry WHERE id = :i"), {"i": ids["e_flat"]}) == 0

        # The approved thermo followed its entry, through a declaration naming this revision and the one column.
        assert conn.scalar(text("SELECT species_entry_id FROM thermo WHERE id = :i"), {"i": ids["thermo"]}) == ids["e_trans"]
        declared = {
            row.target_table: sorted(row.declared_columns)
            for row in conn.execute(
                text("SELECT target_table, declared_columns FROM accepted_science_repair WHERE alembic_revision = 'c7e2a9f4d138'")
            )
        }
        assert declared["thermo"] == ["species_entry_id"]
        ledger = conn.execute(
            text(
                "SELECT c.before_json, c.after_json FROM accepted_science_repair_change c "
                "JOIN accepted_science_repair r ON r.id = c.repair_id "
                "WHERE r.alembic_revision = 'c7e2a9f4d138' AND c.record_id = :i AND c.record_type = CAST('thermo' AS submission_record_type)"
            ),
            {"i": ids["thermo"]},
        ).one()
        assert (ledger.before_json["species_entry_id"], ledger.after_json["species_entry_id"]) == (
            ids["e_flat"],
            ids["e_trans"],
        )

        # The surviving entry's review is reset, with an event; conformer groups of the merged entries are one set.
        assert conn.scalar(
            text("SELECT status::text FROM record_review WHERE record_type = CAST('species_entry' AS submission_record_type) AND record_id = :i"),
            {"i": ids["e_trans"]},
        ) == "not_reviewed"
        assert conn.scalar(
            text("SELECT count(*) FROM conformer_group WHERE species_entry_id = :i"), {"i": ids["e_trans"]}
        ) == 2
        assert conn.scalar(text("SELECT count(*) FROM conformer_group WHERE species_entry_id = :i"), {"i": ids["e_flat"]}) == 0

        # Reactions: the pair is one species twice; the two single-reactant reactions are one reaction now.
        assert conn.scalar(
            text("SELECT stoichiometry FROM reaction_participant WHERE reaction_id = :r AND role = CAST('reactant' AS reaction_role)"),
            {"r": ids["r_pair"]},
        ) == 2
        assert conn.scalar(text("SELECT count(*) FROM chem_reaction")) == 2
        assert conn.scalar(text("SELECT count(*) FROM chem_reaction WHERE id = :r"), {"r": ids["r_two"]}) == 0
        assert conn.scalar(text("SELECT reaction_id FROM reaction_entry WHERE id = :e"), {"e": ids["re_two"]}) == ids["r_one"]
        assert conn.scalar(text("SELECT stoichiometry_hash FROM chem_reaction WHERE id = :r"), {"r": ids["r_one"]}) == _hash(
            {ids["s_trans"]: 1}, {ids["s_butane"]: 1}
        )
        assert conn.scalar(text("SELECT stoichiometry_hash FROM chem_reaction WHERE id = :r"), {"r": ids["r_pair"]}) == _hash(
            {ids["s_trans"]: 2}, {ids["s_butane"]: 1}
        )

    # The summary lists the contradiction (by public ref) and the merged refs, and says what was repaired.
    assert "contradictions_left_for_curation" in completed.stdout
    assert refs_before["e_bad"] in completed.stdout
    assert loser_entry_ref in completed.stdout

    # The trigger was never disabled: an undeclared write to the approved thermo is still refused.
    with harness.engine.connect() as conn:
        with pytest.raises(Exception, match="accepted|approved|immutable|frozen"):
            conn.execute(text("UPDATE thermo SET note = 'edited' WHERE id = :i"), {"i": ids["thermo"]})
            conn.commit()


def test_a_collision_the_repair_path_cannot_express_stops_the_migration(harness) -> None:
    """Two third-body efficiencies for what becomes one collider cannot be merged by guessing which stands."""
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        reaction_entry = ids["re_one"]
        kinetics = conn.scalar(
            text(
                "INSERT INTO kinetics (reaction_entry_id, scientific_origin, model_kind) "
                "VALUES (:r, CAST('computed' AS scientific_origin_kind), CAST('arrhenius' AS kinetics_model_kind)) RETURNING id"
            ),
            {"r": reaction_entry},
        )
        for species in (ids["s_trans"], ids["s_flat"]):
            conn.execute(
                text(
                    "INSERT INTO kinetics_third_body_efficiency (kinetics_id, collider_species_id, efficiency) "
                    "VALUES (:k, :s, 2.0)"
                ),
                {"k": kinetics, "s": species},
            )
    failed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert failed.returncode != 0
    assert "kinetics_third_body_efficiency" in failed.stderr
    # Nothing was half-applied: the database is still at the parent revision with the old rows.
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM species WHERE smiles = 'C/C=C/C'")) == 1


def test_downgrade_restores_the_schema_and_does_not_pretend_to_unmerge(harness) -> None:
    harness.run("upgrade", _MIGRATION.revision)
    harness.run("downgrade", _MIGRATION.parent)
    with harness.engine.connect() as conn:
        columns = {
            row[0] for row in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'species_entry'"))
        }
        assert "stereo_label" in columns
        assert not columns & {"stereo_descriptor", "stereo_resolution", "stereo_smiles"}
