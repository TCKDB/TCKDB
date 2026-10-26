"""Disposable-database contract for ``c7e2a9f4d138`` (species key is the constitution; entry stereo descriptor).

An empty database proves nothing about a data step, so this builds the state the old rule left on a real one and upgrades
from the parent revision:

* three 2-butene species (``C/C=C/C``, ``CC=CC``, ``C/C=C\\C``) that are one constitution;
* entries labelled ``E`` under two of them (the younger carries everything a merged-away row can carry), one ``Z``;
* one entry whose SMILES marks (trans) and geometry (cis) contradict each other, deposited under a free-text label;
* reactions whose participants collide once the species are one.

Nothing is deleted: the rows that lose are kept with their public ref and every record **about** them, and forward to the
row that holds the results. The tests assert that, the declared repairs, the ledger, the survivors' public refs, the
translation of old labels, what stops the migration, and that the downgrade does not fail on what the upgrade produced.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text

from app.services.public_refs import PREFIXES, make_content_ref
from tests.db._migration_chain import revision_under_test
from tests.db.test_thermo_model_kind_backfill_migration import _MigrationHarness
from tests.services.test_stereo_descriptor import embed_xyz

_MIGRATION = revision_under_test("c7e2a9f4d138")


@pytest.fixture
def harness():
    created = _MigrationHarness("species_key_stereo")
    yield created
    created.close()


def _species(conn, smiles: str, inchi_key: str, *, ref: str | None = None) -> int:
    return conn.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind"
            + (", public_ref" if ref else "")
            + ") VALUES (CAST('molecule' AS molecule_kind), :s, :k, 0, 1, CAST('ez_isomer' AS stereo_kind)"
            + (", :ref" if ref else "")
            + ") RETURNING id"
        ),
        {"s": smiles, "k": inchi_key, **({"ref": ref} if ref else {})},
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


def _review(conn, record_type: str, record_id: int, status: str, reviewer: int | None = None) -> int:
    approved = status in ("approved", "deprecated")
    return conn.scalar(
        text(
            "INSERT INTO record_review (record_type, record_id, status, reviewed_by, reviewed_at, first_approved_at) "
            "VALUES (CAST(:t AS submission_record_type), :id, CAST(:s AS record_review_status), :u, "
            + ("now(), now()" if approved else "NULL, NULL")
            + ") RETURNING id"
        ),
        {"t": record_type, "id": record_id, "s": status, "u": reviewer if approved else None},
    )


def _seed(conn) -> dict[str, int]:
    ids: dict[str, int] = {}
    ids["curator"] = reviewer = conn.scalar(
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
    xyz = embed_xyz("C/C=C\\C")
    geometry = conn.scalar(
        text("INSERT INTO geometry (natoms, geom_hash, xyz_text) VALUES (12, :h, :x) RETURNING id"),
        {"h": "c" * 64, "x": xyz},
    )
    for index, line in enumerate(xyz.strip().splitlines()[2:], start=1):
        element, x, y, z = line.split()
        conn.execute(
            text(
                "INSERT INTO geometry_atom (geometry_id, atom_index, element, x, y, z) VALUES (:g, :i, :e, :x, :y, :z)"
            ),
            {"g": geometry, "i": index, "e": element, "x": float(x), "y": float(y), "z": float(z)},
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

    # An approved thermo, and a second one that supersedes it, both on the entry that will be merged away.
    thermo_sql = (
        "INSERT INTO thermo (species_entry_id, scientific_origin, thermodynamic_target_kind) "
        "VALUES (:e, CAST('computed' AS scientific_origin_kind), CAST('equilibrium_ensemble' AS thermo_target_kind)) "
        "RETURNING id"
    )
    ids["thermo"] = conn.scalar(text(thermo_sql), {"e": ids["e_flat"]})
    ids["thermo_new"] = conn.scalar(text(thermo_sql), {"e": ids["e_flat"]})
    _review(conn, "thermo", ids["thermo_new"], "approved", reviewer)
    _review(conn, "thermo", ids["thermo"], "approved", reviewer)
    conn.execute(
        text("UPDATE record_review SET status = CAST('deprecated' AS record_review_status) "
             "WHERE record_type = CAST('thermo' AS submission_record_type) AND record_id = :i"),
        {"i": ids["thermo"]},
    )
    ids["supersession"] = conn.scalar(
        text(
            "INSERT INTO scientific_record_supersession (record_type, superseded_record_id, superseding_record_id, reason, created_by) "
            "VALUES (CAST('thermo' AS submission_record_type), :old, :new, 'restated', :u) RETURNING id"
        ),
        {"old": ids["thermo"], "new": ids["thermo_new"], "u": reviewer},
    )

    # Everything recorded ABOUT the entry that will be merged away, which must stay on it.
    ids["review_flat"] = _review(conn, "species_entry", ids["e_flat"], "approved", reviewer)
    conn.execute(
        text(
            "INSERT INTO record_review_event (record_review_id, event_kind, from_status, to_status, reason) "
            "VALUES (:r, CAST('status_change' AS record_review_event_kind), CAST('not_reviewed' AS record_review_status), "
            "CAST('approved' AS record_review_status), 'checked')"
        ),
        {"r": ids["review_flat"]},
    )
    # The survivor's own review: merging results into it withdraws it.
    _review(conn, "species_entry", ids["e_trans"], "approved", reviewer)
    ids["machine_review"] = conn.scalar(
        text(
            "INSERT INTO record_machine_review (record_type, record_id, status, context_hash, context_schema_version, "
            "prompt_name, prompt_version, rubric_name, rubric_version, reviewed_at) VALUES "
            "(CAST('species_entry' AS submission_record_type), :e, CAST('machine_screened_pass' AS machine_review_status), "
            "repeat('a', 64), 1, 'p', 1, 'r', 1, now()) RETURNING id"
        ),
        {"e": ids["e_flat"]},
    )
    ids["assessment"] = conn.scalar(
        text(
            "INSERT INTO record_reproducibility_assessment (record_type, record_id, grade, rubric_name, rubric_version, "
            "context_hash, context_json, assessor_kind, public_ref) VALUES "
            "(CAST('species_entry' AS submission_record_type), :e, CAST('described' AS reproducibility_grade), 'r', 1, repeat('b', 64), "
            "CAST('{}' AS jsonb), CAST('system' AS reproducibility_assessor_kind), 'rra_stereo_test_1') RETURNING id"
        ),
        {"e": ids["e_flat"]},
    )
    ids["idempotency"] = conn.scalar(
        text(
            "INSERT INTO idempotency_record (user_id, request_method, endpoint, idempotency_key, payload_hash, status_code, "
            "response_body_json, expires_at) VALUES (:u, 'POST', '/x', 'k1', :h, 201, CAST(:b AS jsonb), now() + interval '1 day') "
            "RETURNING id"
        ),
        {"u": reviewer, "h": "d" * 64, "b": json.dumps({"species_entry_id": ids["e_flat"], "species_entry_ref": "see public_ref"})},
    )
    submission = conn.scalar(
        text(
            "INSERT INTO submission (created_by, submission_kind, public_ref) "
            "VALUES (:u, CAST('computed_species' AS submission_kind), 'sub_stereo_test_1') RETURNING id"
        ),
        {"u": reviewer},
    )
    ids["submission_link"] = conn.scalar(
        text(
            "INSERT INTO submission_record_link (submission_id, record_type, record_id) "
            "VALUES (:s, CAST('species_entry' AS submission_record_type), :e) RETURNING id"
        ),
        {"s": submission, "e": ids["e_flat"]},
    )
    ids["curator_task"] = conn.scalar(
        text(
            "INSERT INTO machine_review_curator_task (submission_id, record_type, record_id, finding_fingerprint, "
            "machine_review_status, highest_severity) VALUES (:s, CAST('species_entry' AS submission_record_type), :e, 'f', "
            "CAST('machine_screened_warning' AS machine_review_status), CAST('warning' AS machine_review_severity)) RETURNING id"
        ),
        {"s": submission, "e": ids["e_flat"]},
    )
    policy = conn.scalar(
        text("INSERT INTO curation_policy (name, version, description, public_ref) VALUES ('p', '1', 'd', 'cpol_stereo_1') RETURNING id")
    )
    release = conn.scalar(
        text(
            "INSERT INTO dataset_release (tag, title, curation_policy_id, data_license, code_license, citation_text, contact, "
            "public_ref) VALUES ('2026.10.0', 't', :p, 'CC0', 'MIT', 'c', 'x', 'rel_stereo_1') RETURNING id"
        ),
        {"p": policy},
    )
    ids["release_selection"] = conn.scalar(
        text(
            "INSERT INTO release_selection (dataset_release_id, curation_policy_id, record_type, record_id, subject_type, "
            "subject_id, rationale, selected_by, public_ref) VALUES (:r, :p, CAST('thermo' AS submission_record_type), :t, "
            "CAST('species_entry' AS submission_record_type), :e, 'chosen', :u, 'rsel_stereo_1') RETURNING id"
        ),
        {"r": release, "p": policy, "t": ids["thermo_new"], "e": ids["e_flat"], "u": reviewer},
    )
    ids["entry_review"] = conn.scalar(
        text(
            "INSERT INTO species_entry_review (species_entry_id, user_id, role) "
            "VALUES (:e, :u, CAST('reviewer' AS species_entry_review_role)) RETURNING id"
        ),
        {"e": ids["e_flat"], "u": reviewer},
    )
    # Each of the two E entries has a conformer group with the same label: the merge cannot keep both labels.
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


def _value(conn, sql: str, **params):
    return conn.scalar(text(sql), params)


def test_upgrade_keeps_merged_rows_and_moves_results_through_declared_repairs(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        refs_before = {
            name: _value(conn, "SELECT public_ref FROM species_entry WHERE id = :i", i=ids[name])
            for name in ("e_trans", "e_flat", "e_cis", "e_bad")
        }
        species_refs = {
            name: _value(conn, "SELECT public_ref FROM species WHERE id = :i", i=ids[name])
            for name in ("s_trans", "s_flat", "s_cis")
        }
        reaction_refs = {
            name: _value(conn, "SELECT public_ref FROM chem_reaction WHERE id = :i", i=ids[name]) for name in ("r_one", "r_two")
        }
        about_before = {
            "record_review_events": _value(
                conn, "SELECT count(*) FROM record_review_event e JOIN record_review r ON r.id = e.record_review_id WHERE r.id = :r",
                r=ids["review_flat"],
            ),
        }
    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode == 0, completed.stderr[-3000:]

    with harness.engine.connect() as conn:
        # Species: nothing deleted. One live row holds the key -- the row that already held the key's SMILES, as no row
        # held the canonical ref -- and the others are kept, marked merged, with their own public ref and SMILES.
        assert _value(conn, "SELECT count(*) FROM species") == 4
        live = conn.execute(text("SELECT id, smiles, inchi_key FROM species WHERE smiles = 'CC=CC' AND merged_into_species_id IS NULL")).all()
        assert [row.id for row in live] == [ids["s_flat"]]
        assert live[0].inchi_key == "IAQRGUVFOMOMEM-UHFFFAOYSA-N"
        for name in ("s_trans", "s_cis"):
            row = conn.execute(
                text("SELECT merged_into_species_id, public_ref, smiles FROM species WHERE id = :i"), {"i": ids[name]}
            ).one()
            assert (row.merged_into_species_id, row.public_ref) == (ids["s_flat"], species_refs[name])
            assert "/" in row.smiles or "\\" in row.smiles, "a kept row keeps the SMILES it had"
        assert _value(conn, "SELECT public_ref FROM species WHERE id = :i", i=ids["s_flat"]) == species_refs["s_flat"]
        assert _value(conn, "SELECT count(*) FROM species WHERE merged_into_species_id IS NULL") == 2  # 2-butene, butane

        # Entries: nothing deleted. E (merged: the younger is kept and forwards), Z, and the contradictory entry, left
        # unresolved rather than relabelled. Every entry now lives under the surviving species.
        assert _value(conn, "SELECT count(*) FROM species_entry") == 5
        live_entries = {
            row.id: row
            for row in conn.execute(
                text(
                    "SELECT id, species_id, stereo_descriptor, stereo_label, stereo_resolution::text AS res, stereo_smiles, public_ref "
                    "FROM species_entry WHERE merged_into_species_entry_id IS NULL AND species_id = :s"
                ),
                {"s": ids["s_flat"]},
            )
        }
        assert set(live_entries) == {ids["e_trans"], ids["e_cis"], ids["e_bad"]}
        assert live_entries[ids["e_trans"]].stereo_descriptor == "E"
        assert live_entries[ids["e_trans"]].stereo_smiles == "C/C=C/C"
        assert live_entries[ids["e_cis"]].stereo_descriptor == "Z"
        bad = live_entries[ids["e_bad"]]
        assert (bad.stereo_descriptor, bad.res, bad.stereo_label) == ("?", "unresolved", "trans")
        for name in ("e_trans", "e_cis", "e_bad", "e_flat"):
            assert _value(conn, "SELECT public_ref FROM species_entry WHERE id = :i", i=ids[name]) == refs_before[name]
        flat = conn.execute(
            text("SELECT merged_into_species_entry_id, stereo_descriptor FROM species_entry WHERE id = :i"), {"i": ids["e_flat"]}
        ).one()
        assert (flat.merged_into_species_entry_id, flat.stereo_descriptor) == (ids["e_trans"], "E")

        # Results moved to the survivor through declared repairs naming this revision and the one column.
        assert _value(conn, "SELECT species_entry_id FROM thermo WHERE id = :i", i=ids["thermo"]) == ids["e_trans"]
        assert _value(conn, "SELECT species_entry_id FROM thermo WHERE id = :i", i=ids["thermo_new"]) == ids["e_trans"]
        declared = {
            row.target_table: sorted(row.declared_columns)
            for row in conn.execute(
                text("SELECT target_table, declared_columns FROM accepted_science_repair WHERE alembic_revision = 'c7e2a9f4d138'")
            )
        }
        assert declared["thermo"] == ["species_entry_id", "target_conformer_group_id"]
        ledger = conn.execute(
            text(
                "SELECT c.before_json, c.after_json FROM accepted_science_repair_change c "
                "JOIN accepted_science_repair r ON r.id = c.repair_id "
                "WHERE r.alembic_revision = 'c7e2a9f4d138' AND c.record_id = :i AND c.record_type = CAST('thermo' AS submission_record_type)"
            ),
            {"i": ids["thermo"]},
        ).one()
        assert (ledger.before_json["species_entry_id"], ledger.after_json["species_entry_id"]) == (ids["e_flat"], ids["e_trans"])

        # What was recorded ABOUT the merged-away entry stays on it, untouched.
        assert _value(conn, "SELECT status::text FROM record_review WHERE id = :r", r=ids["review_flat"]) == "approved"
        assert _value(
            conn, "SELECT count(*) FROM record_review_event WHERE record_review_id = :r", r=ids["review_flat"]
        ) == about_before["record_review_events"]
        for sql, key in (
            ("SELECT record_id FROM record_machine_review WHERE id = :i", "machine_review"),
            ("SELECT record_id FROM record_reproducibility_assessment WHERE id = :i", "assessment"),
            ("SELECT record_id FROM submission_record_link WHERE id = :i", "submission_link"),
            ("SELECT record_id FROM machine_review_curator_task WHERE id = :i", "curator_task"),
            ("SELECT subject_id FROM release_selection WHERE id = :i", "release_selection"),
        ):
            assert _value(conn, sql, i=ids[key]) == ids["e_flat"], key
        assert _value(conn, "SELECT species_entry_id FROM species_entry_review WHERE id = :i", i=ids["entry_review"]) == ids["e_flat"]
        assert str(ids["e_flat"]) in str(_value(conn, "SELECT response_body_json FROM idempotency_record WHERE id = :i", i=ids["idempotency"]))
        edge = conn.execute(
            text("SELECT superseded_record_id, superseding_record_id FROM scientific_record_supersession WHERE id = :i"),
            {"i": ids["supersession"]},
        ).one()
        assert (edge.superseded_record_id, edge.superseding_record_id) == (ids["thermo"], ids["thermo_new"])
        # ... and the survivor's own review is withdrawn, with an event, because it now holds results it never saw.
        assert _value(
            conn,
            "SELECT status::text FROM record_review WHERE record_type = CAST('species_entry' AS submission_record_type) AND record_id = :i",
            i=ids["e_trans"],
        ) == "not_reviewed"

        # Conformer groups of the merged entries moved to the survivor (the clashing label was cleared first).
        assert _value(conn, "SELECT count(*) FROM conformer_group WHERE species_entry_id = :i", i=ids["e_trans"]) == 2
        assert _value(conn, "SELECT count(*) FROM conformer_group WHERE species_entry_id = :i", i=ids["e_flat"]) == 0

        # Reactions: nothing deleted. The pair is one species twice; the two single-reactant reactions are one reaction
        # now. ``r_two`` already held the hash (its species is the surviving one), so it survives; ``r_one`` is kept
        # with its ref and forwards, and hands its reaction entry over.
        assert _value(
            conn,
            "SELECT stoichiometry FROM reaction_participant WHERE reaction_id = :r AND role = CAST('reactant' AS reaction_role)",
            r=ids["r_pair"],
        ) == 2
        assert _value(conn, "SELECT count(*) FROM chem_reaction") == 3
        one = conn.execute(
            text("SELECT merged_into_chem_reaction_id, public_ref FROM chem_reaction WHERE id = :r"), {"r": ids["r_one"]}
        ).one()
        assert (one.merged_into_chem_reaction_id, one.public_ref) == (ids["r_two"], reaction_refs["r_one"])
        assert _value(conn, "SELECT merged_into_chem_reaction_id FROM chem_reaction WHERE id = :r", r=ids["r_two"]) is None
        assert _value(conn, "SELECT reaction_id FROM reaction_entry WHERE id = :e", e=ids["re_one"]) == ids["r_two"]
        assert _value(conn, "SELECT reaction_id FROM reaction_entry WHERE id = :e", e=ids["re_two"]) == ids["r_two"]
        assert _value(conn, "SELECT stoichiometry_hash FROM chem_reaction WHERE id = :r", r=ids["r_two"]) == _hash(
            {ids["s_flat"]: 1}, {ids["s_butane"]: 1}
        )
        assert _value(conn, "SELECT stoichiometry_hash FROM chem_reaction WHERE id = :r", r=ids["r_pair"]) == _hash(
            {ids["s_flat"]: 2}, {ids["s_butane"]: 1}
        )

    # The summary lists the contradiction (by public ref) and the merged refs, and says what was repaired.
    assert "contradictions_left_for_curation" in completed.stdout
    assert refs_before["e_bad"] in completed.stdout
    assert refs_before["e_flat"] in completed.stdout

    # The trigger was never disabled: an undeclared write to the approved thermo is still refused.
    with harness.engine.connect() as conn:
        with pytest.raises(Exception, match="accepted|approved|immutable|frozen"):
            conn.execute(text("UPDATE thermo SET note = 'edited' WHERE id = :i"), {"i": ids["thermo"]})
            conn.commit()

    # Writes onto a merged-away row are refused by the database; one hop, never a chain.
    with harness.engine.connect() as conn:
        with pytest.raises(Exception, match="merged-away"):
            conn.execute(
                text("INSERT INTO conformer_group (species_entry_id, label) VALUES (:e, 'late')"), {"e": ids["e_flat"]}
            )
            conn.commit()
    with harness.engine.connect() as conn:
        with pytest.raises(Exception, match="itself merged|merged into"):
            conn.execute(
                text("UPDATE species_entry SET merged_into_species_entry_id = :f WHERE id = :c"),
                {"f": ids["e_flat"], "c": ids["e_cis"]},
            )
            conn.commit()


def test_the_survivor_is_the_row_that_already_holds_the_canonical_ref(harness) -> None:
    """A fresh database mints ``spc_...`` from the constitution; a row that already holds it survives, even if younger."""
    canonical = make_content_ref(
        PREFIXES["Species"], "species:smiles=CC=CC;charge=0;multiplicity=1;stereo_kind=ez_isomer"
    )
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        older = _species(conn, "C/C=C/C", "IAQRGUVFOMOMEM-ONEGZZNKSA-N", ref="spc_older_marked_row_000000000000")
        younger = _species(conn, "CC=CC", "IAQRGUVFOMOMEM-UHFFFAOYSA-N", ref=canonical)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.connect() as conn:
        assert _value(conn, "SELECT merged_into_species_id FROM species WHERE id = :i", i=older) == younger
        assert _value(conn, "SELECT merged_into_species_id FROM species WHERE id = :i", i=younger) is None
        assert _value(conn, "SELECT public_ref FROM species WHERE id = :i", i=younger) == canonical
        assert _value(conn, "SELECT public_ref FROM species WHERE id = :i", i=older) == "spc_older_marked_row_000000000000"


def test_old_labels_are_translated_and_what_cannot_be_is_listed(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        species = _species(conn, "CC=CC", "IAQRGUVFOMOMEM-UHFFFAOYSA-N")
        cis = _entry(conn, species, "CC=CC", "cis")
        trans = _entry(conn, species, "CC=CC", "trans")
        vocabulary = _entry(conn, species, "CC=CC", "E")  # the same configuration as "trans"
        diazene = _species(conn, "N=N", "RAABOESOVLLHRU-UHFFFAOYSA-N")
        free_text = _entry(conn, diazene, "N=N", "twisted")
        ids = {"cis": cis, "trans": trans, "vocabulary": vocabulary, "free_text": free_text}
    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode == 0, completed.stderr[-3000:]
    with harness.engine.connect() as conn:
        got = {
            name: conn.execute(
                text("SELECT stereo_descriptor, merged_into_species_entry_id FROM species_entry WHERE id = :i"), {"i": i}
            ).one()
            for name, i in ids.items()
        }
    # cis -> Z, trans -> E, computed by CIP; the entry already labelled E is the same configuration as "trans".
    assert got["cis"].stereo_descriptor == "Z" and got["cis"].merged_into_species_entry_id is None
    assert got["trans"].stereo_descriptor == "E" and got["trans"].merged_into_species_entry_id is None
    assert got["vocabulary"].stereo_descriptor == "E" and got["vocabulary"].merged_into_species_entry_id == ids["trans"]
    # "twisted" means nothing: the entry is unresolved, and the label is reported as not carried.
    assert got["free_text"].stereo_descriptor == "?"
    assert "'twisted': 1" in completed.stdout
    assert "labels_translated=2" in completed.stdout


def test_entries_with_different_old_labels_and_nothing_to_separate_them_stop_the_migration(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        species = _species(conn, "N=N", "RAABOESOVLLHRU-UHFFFAOYSA-N")
        first = _entry(conn, species, "N=N", "foo")
        second = conn.scalar(
            text("INSERT INTO species_entry (species_id, mol, stereo_label, electronic_state_label) "
                 "VALUES (:sp, mol_from_smiles(CAST('N=N' AS cstring)), 'bar', NULL) RETURNING id"),
            {"sp": species},
        )
        refs = [_value(conn, "SELECT public_ref FROM species_entry WHERE id = :i", i=i) for i in (first, second)]
    failed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert failed.returncode != 0
    for ref in refs:
        assert ref in failed.stderr
    assert "different stereo labels" in failed.stderr
    # Nothing was half-applied: the database is still at the parent revision with the old rows.
    with harness.engine.connect() as conn:
        assert _value(conn, "SELECT count(*) FROM species_entry") == 2
        assert "merged_into_species_entry_id" not in {
            r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'species_entry'"))
        }


def test_rows_that_cannot_move_stay_on_the_kept_row_and_are_listed(harness) -> None:
    """Two third-body efficiencies for what becomes one collider: the survivor keeps its own, the other stays put."""
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        kinetics = conn.scalar(
            text(
                "INSERT INTO kinetics (reaction_entry_id, scientific_origin, model_kind) "
                "VALUES (:r, CAST('computed' AS scientific_origin_kind), CAST('arrhenius' AS kinetics_model_kind)) RETURNING id"
            ),
            {"r": ids["re_one"]},
        )
        for species in (ids["s_trans"], ids["s_flat"]):
            conn.execute(
                text("INSERT INTO kinetics_third_body_efficiency (kinetics_id, collider_species_id, efficiency) VALUES (:k, :s, 2.0)"),
                {"k": kinetics, "s": species},
            )
    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert "kinetics_third_body_efficiency" in completed.stdout
    with harness.engine.connect() as conn:
        rows = sorted(
            r.collider_species_id
            for r in conn.execute(
                text("SELECT collider_species_id FROM kinetics_third_body_efficiency WHERE kinetics_id = :k"), {"k": kinetics}
            )
        )
        assert rows == sorted([ids["s_trans"], ids["s_flat"]])


def test_downgrade_does_not_fail_on_what_the_upgrade_produced(harness) -> None:
    canonical = make_content_ref(
        PREFIXES["Species"], "species:smiles=N=N;charge=0;multiplicity=1;stereo_kind=ez_isomer"
    )
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
        # The OLDER row holds the canonical ref, so it survives and takes the key's SMILES; the younger row, which already
        # had that SMILES, is kept and would collide with it under the old unique key.
        ids["older"] = _species(conn, "[H]/N=N/[H]", "RAABOESOVLLHRU-OWOJBTEDSA-N", ref=canonical)
        ids["younger"] = _species(conn, "N=N", "RAABOESOVLLHRU-UHFFFAOYSA-N")
    harness.run("upgrade", _MIGRATION.revision)
    down = harness.run("downgrade", _MIGRATION.parent, check=False)
    assert down.returncode == 0, down.stderr[-3000:]
    with harness.engine.connect() as conn:
        columns = {
            row[0] for row in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'species_entry'"))
        }
        assert "stereo_label" in columns
        assert not columns & {"stereo_descriptor", "stereo_resolution", "stereo_smiles", "merged_into_species_entry_id"}
        # Nothing was deleted and nothing was un-merged: the kept rows and their refs are still there, renamed where
        # they would otherwise collide with their survivor under the old unique keys.
        assert _value(conn, "SELECT count(*) FROM species") == 6
        assert _value(conn, "SELECT count(*) FROM species_entry") == 5
        assert _value(conn, "SELECT count(*) FROM chem_reaction") == 3
        assert _value(conn, "SELECT smiles FROM species WHERE id = :i", i=ids["older"]) == "N=N"
        assert _value(conn, "SELECT smiles FROM species WHERE id = :i", i=ids["younger"]).startswith("merged:")
        assert _value(conn, "SELECT stereo_label FROM species_entry WHERE id = :i", i=ids["e_flat"]).startswith("merged:")
        assert _value(conn, "SELECT stereo_label FROM species_entry WHERE id = :i", i=ids["e_trans"]) == "E"
        assert _value(conn, "SELECT status::text FROM record_review WHERE id = :r", r=ids["review_flat"]) == "approved"
    # Re-applying after a downgrade is not supported: the downgrade forgets which rows were merged away.


def test_the_catalogue_covers_every_foreign_key_and_a_trigger_guards_each() -> None:
    """Every foreign key into the three identity tables is either re-pointed by the step or deliberately stays, and each
    carries the trigger that refuses a merged-away target. Enumerated from the models, so a table added later is caught."""
    import app.db.models  # noqa: F401
    from app.db.base import Base
    from app.services.stereo_identity_merge import _STAYS_ON_MERGED_ROW

    into = {"species", "species_entry", "chem_reaction"}
    markers = {"merged_into_species_id", "merged_into_species_entry_id", "merged_into_chem_reaction_id"}
    found = {
        (table.name, fk.parent.name)
        for table in Base.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.column.table.name in into and fk.parent.name not in markers
    }
    assert ("thermo", "species_entry_id") in found and ("reaction_participant", "species_id") in found
    assert _STAYS_ON_MERGED_ROW <= found
    for composite in (c for t in Base.metadata.sorted_tables for c in t.foreign_key_constraints if len(c.columns) > 1):
        assert composite.referred_table.name not in into, composite
