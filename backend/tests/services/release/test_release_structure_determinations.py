"""A released transition-state entry ships its structure determinations, with the provenance of what they pin.

Held here: the determination, its sources and its findings ship under the entry with public refs only; the two digests
over this database's row ids stay out; the calculations a determination pins are cited provenance with the declared
actual protocol; and a calculation that declared nothing keeps the provenance block it always had.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    StructureDeterminationTargetKind,
    StructureFindingKind,
    StructureFindingVerdict,
    StructureSourceRole,
    SubmissionRecordType,
)
from app.db.models.structure_determination import (
    StructureDetermination,
    StructureDeterminationSource,
    StructureEvidenceFinding,
)
from app.services.record_review import set_record_review_status
from app.services.release.records import calculation_provenance, cited_calculation_ids, serialize_records
from tests.services.scientific_read._factories import (
    attach_input_geometry,
    attach_output_geometry,
    make_calculation,
    make_chem_reaction,
    make_geometry,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    make_workflow_tool_release,
    next_inchi_key,
)

DECLARATION = {"version": 1, "source": {"origin": "producer_declared", "producer": "arc"}, "dispersion": {"state": "unknown"}}


@pytest.fixture
def released(db_session, curator):
    reactant, product = make_species(db_session, inchi_key=next_inchi_key()), make_species(db_session, inchi_key=next_inchi_key())
    reaction = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(db_session, reactants=[reactant], products=[product]),
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    entry = make_transition_state_entry(db_session, transition_state=make_transition_state(db_session, reaction_entry=reaction))
    geometry = make_geometry(db_session)
    lot = make_lot(db_session)
    opt = make_calculation(db_session, type=CalculationType.opt, transition_state_entry_id=entry.id, lot_id=lot.id)
    attach_output_geometry(db_session, calculation=opt, geometry=geometry)
    opt.actual_protocol_declaration = DECLARATION
    freq = make_calculation(db_session, type=CalculationType.freq, transition_state_entry_id=entry.id, lot_id=lot.id)
    attach_input_geometry(db_session, calculation=freq, geometry=geometry)
    determination = StructureDetermination(
        transition_state_entry_id=entry.id,
        target_kind=StructureDeterminationTargetKind.saddle_point,
        evaluated_geometry_id=geometry.id,
        workflow_tool_release_id=make_workflow_tool_release(db_session).id,
        determination_key="saddle-1",
        identity_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
        content_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
    )
    db_session.add(determination)
    db_session.flush()
    db_session.execute(text("SELECT set_config('tckdb.structure_determination_writing', :i, true)"), {"i": str(determination.id)})
    for role, calc in ((StructureSourceRole.geometry_optimization, opt), (StructureSourceRole.curvature, freq)):
        db_session.add(
            StructureDeterminationSource(
                determination_id=determination.id,
                role=role,
                calculation_id=calc.id,
                transition_state_entry_id=entry.id,
                geometry_id=geometry.id,
            )
        )
    finding = StructureEvidenceFinding(
        kind=StructureFindingKind.path_incompatibility,
        scope="determination",
        subject_determination_id=determination.id,
        verdict=StructureFindingVerdict.unresolved,
        authority="producer_assertion",
        rationale="the stated path is not confirmed",
        semantic_version=1,
    )
    db_session.add(finding)
    db_session.flush()
    db_session.execute(text("SELECT set_config('tckdb.structure_determination_writing', '', true)"))
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entry.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )
    return {"entry": entry, "determination": determination, "opt": opt, "freq": freq, "geometry": geometry, "finding": finding}


def test_a_released_entry_ships_its_determination_sources_and_findings_by_public_ref(db_session, released):
    entry, determination = released["entry"], released["determination"]
    payload = serialize_records(
        db_session, record_type=SubmissionRecordType.transition_state_entry, record_ids=[entry.id]
    )[entry.id]
    (shipped,) = payload["structure_determination"]
    assert shipped["public_ref"] == determination.public_ref
    assert shipped["transition_state_entry_ref"] == entry.public_ref
    assert shipped["evaluated_geometry_ref"] == released["geometry"].public_ref
    assert shipped["determination_key"] == "saddle-1" and shipped["target_kind"] == "saddle_point"
    sources = {s["role"]: s for s in shipped["structure_determination_source"]}
    assert set(sources) == {"geometry_optimization", "curvature"}
    assert sources["curvature"]["calculation_ref"] == released["freq"].public_ref
    assert sources["curvature"]["geometry_ref"] == released["geometry"].public_ref
    (finding,) = shipped["structure_evidence_finding"]
    assert finding["public_ref"] == released["finding"].public_ref and finding["verdict"] == "unresolved"
    assert finding["subject_determination_ref"] == determination.public_ref


def test_the_digests_over_this_databases_row_ids_never_ship_and_no_row_id_does(db_session, released):
    entry = released["entry"]
    payload = serialize_records(
        db_session, record_type=SubmissionRecordType.transition_state_entry, record_ids=[entry.id]
    )[entry.id]
    (shipped,) = payload["structure_determination"]
    assert "identity_hash" not in shipped and "content_hash" not in shipped
    text_ = json.dumps(shipped)
    assert released["determination"].identity_hash not in text_ and released["determination"].content_hash not in text_
    for node in (shipped, *shipped["structure_determination_source"], *shipped["structure_evidence_finding"]):
        assert not [k for k in node if k.endswith("_id")], node


def test_the_pinned_calculations_are_cited_provenance_with_their_declared_protocol(db_session, released):
    entry = released["entry"]
    cited = cited_calculation_ids(
        db_session, record_type=SubmissionRecordType.transition_state_entry, record_ids=[entry.id]
    )
    assert cited[entry.id] == sorted([released["opt"].id, released["freq"].id])
    provenance = calculation_provenance(db_session, cited[entry.id])
    declared = provenance[released["opt"].id]
    assert declared["actual_protocol_declaration"]["version"] == 1
    assert declared["actual_protocol_declaration"]["dispersion"] == {"state": "unknown"}
    # A calculation that declared nothing keeps the block it always had: no new key, so no release digest moves.
    assert "actual_protocol_declaration" not in provenance[released["freq"].id]
    assert set(provenance[released["freq"].id]) == {
        "calculation_ref",
        "calculation_type",
        "level_of_theory",
        "software",
    }
