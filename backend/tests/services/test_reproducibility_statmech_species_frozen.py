"""Species-owned statmech reproducibility, frozen as literals (#760, PR-1).

Why this file exists
--------------------
A statmech record belongs to a species entry or to a transition-state entry, never both. Today the
reproducibility rubric asks of *every* statmech record whether it is attached to a species entry, so a
TS-owned record is graded ``insufficient`` whatever its evidence (``scientific_context``). The fix scopes the
rubric by owner and must leave species-owned records exactly as they are: same grade, same checks, same stored
snapshot, so their stored assessments keep reading ``current``.

What is frozen
--------------
For each species-owned shape below, ``FROZEN`` holds, as literals captured from the code as it stood before the
fix:

- the grade;
- ``context_json["rubric"]``;
- the ordered ``(name, level, outcome)`` of every check;
- a SHA-256 over the whole stored snapshot, after ids, refs, hashes and timestamps are replaced by ordinals
  (first appearance) so that two runs on two databases give the same digest. Anything else in the snapshot
  (a key added or removed, a value changed, a check's evidence edited) moves the digest.

These literals are expected to stay byte-identical when the owner-scoped rubric lands. If one of them changes,
species grading changed: that is the failure this file exists to catch, not a literal to refresh.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from app.db.models.calculation import (
    CalculationArtifact,
    CalculationFreqMode,
    CalculationFreqResult,
    CalculationOptResult,
    CalculationSPResult,
)
from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
    ArtifactKind,
    CalculationType,
    ReproducibilityGrade,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechModelScopeKind,
    SubmissionRecordType,
)
from app.db.models.statmech import Statmech
from app.db.models.statmech_declaration import StatmechModelDeclaration
from app.services.artifact_integrity import record_integrity_observation
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility, evaluate_reproducibility
from tests.services.scientific_read._factories import (
    attach_statmech_electronic_level,
    attach_statmech_source_calculation,
    attach_statmech_torsion,
    make_calculation,
    make_chem_reaction,
    make_literature,
    make_lot,
    make_reaction_entry,
    make_software_release,
    make_species,
    make_species_entry,
    make_statmech,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)
from tests.services.test_artifact_integrity import _SessionProxy
from tests.services.test_reproducibility_rubric import _integrity_seam, _MuteStoreProbe

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_ID_KEY = re.compile(r"(^id$|_id$|^created_by$)")
_ID_LIST_KEY = re.compile(r"_ids$")
_VALUE_KEYS = {"public_ref", "reference", "sha256", "inchi_key", "doi", "uri", "lot_hash", "content_hash", "integrity_event_ref"}
_AT_KEY = re.compile(r"_at$")


class _Ordinals:
    """First-appearance ordinals, kept per kind of id.

    Two different tables hand out the same integer in the same transaction (a statmech and its species entry are
    both row 1 in an empty database and rows 40 and 7 in a busy one). One shared namespace would make the digest
    depend on that coincidence, so each kind of id is numbered on its own. Ids that name the same table share a
    kind: ``calculation_id``, the keys of ``calculation_evidence`` and the ``id`` inside each of its entries.
    """

    def __init__(self) -> None:
        self._seen: dict[tuple[str, object], str] = {}

    def of(self, kind: str, value: object) -> str:
        key = (kind, value)
        if key not in self._seen:
            self._seen[key] = f"<{kind}#{sum(1 for k, _ in self._seen if k == kind) + 1}>"
        return self._seen[key]


def _id_kind(key: str, path: tuple[str, ...]) -> str:
    if key.endswith("_ids"):
        return key[:-1]
    if key != "id":
        return key
    if path and path[-1].startswith("<calculation_id#"):
        return "calculation_id"
    if path[:2] == ("target", "columns"):
        return "record_id"
    owner = [part for part in path if part != "columns"]
    return f"id@{owner[-1] if owner else ''}"


def _normalise(node, ordinals: _Ordinals, key: str | None = None, path: tuple[str, ...] = ()):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            # ``calculation_evidence`` is a map keyed by calculation id.
            nk = ordinals.of("calculation_id", k) if key == "calculation_evidence" and str(k).isdigit() else k
            out[nk] = _normalise(v, ordinals, nk, path + ((key,) if key else ()))
        return out
    if isinstance(node, list):
        items = [_normalise(v, ordinals, key, path) for v in node]
        # A list of ids is a set of rows, and the snapshot orders it by the ids themselves (as text in places), so
        # its order is a property of how big the numbers are. Sort it on the ordinals instead.
        return sorted(items) if key is not None and _ID_LIST_KEY.search(key) else items
    if node is None or key is None:
        return node
    if _ID_KEY.search(key) or _ID_LIST_KEY.search(key):
        return ordinals.of(_id_kind(key, path), node)
    if key in _VALUE_KEYS:
        return ordinals.of(key, node)
    if _AT_KEY.search(key):
        return "<timestamp>"
    return node


def normalised_digest(context_json: dict) -> str:
    """SHA-256 of the stored snapshot with every database-assigned value replaced by an ordinal."""
    normalised = _normalise(json.loads(json.dumps(context_json, default=str)), _Ordinals())
    return hashlib.sha256(json.dumps(normalised, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Scenarios: every one of these is species-owned
# ---------------------------------------------------------------------------


def _entry(db_session):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("FROZEN")))


def _calc(db_session, entry, kind: CalculationType, *, user: int):
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    release = make_software_release(db_session, name="gaussian", version="16")
    calculation = make_calculation(
        db_session,
        type=kind,
        species_entry_id=entry.id,
        lot_id=lot.id,
        software_release_id=release.id,
    )
    payload = f"frozen {kind.value} output {calculation.id:012d}".encode()
    db_session.add(
        CalculationArtifact(
            calculation_id=calculation.id,
            kind=ArtifactKind.output_log,
            uri=f"s3://frozen/{kind.value}-{calculation.id}.log",
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            filename=f"{kind.value}.log",
            created_by=user,
        )
    )
    if kind is CalculationType.opt:
        db_session.add(
            CalculationOptResult(calculation_id=calculation.id, converged=True, n_steps=7, final_energy_hartree=-40.5)
        )
    elif kind is CalculationType.sp:
        db_session.add(CalculationSPResult(calculation_id=calculation.id, electronic_energy_hartree=-40.6))
    elif kind is CalculationType.freq:
        db_session.add(CalculationFreqResult(calculation_id=calculation.id, n_imag=0, zpe_hartree=0.044))
        db_session.add_all(
            CalculationFreqMode(calculation_id=calculation.id, mode_index=i, frequency_cm1=f, is_imaginary=False)
            for i, f in enumerate((310.0, 1250.5, 3010.2), start=1)
        )
    db_session.flush()
    db_session.refresh(calculation)
    return calculation


def _opt_freq_sp(db_session, entry, statmech, *, user: int) -> dict:
    out = {}
    for kind, role in (
        (CalculationType.opt, StatmechCalculationRole.opt),
        (CalculationType.freq, StatmechCalculationRole.freq),
        (CalculationType.sp, StatmechCalculationRole.sp),
    ):
        out[kind] = _calc(db_session, entry, kind, user=user)
        attach_statmech_source_calculation(db_session, statmech=statmech, calculation=out[kind], role=role)
    return out


def _sparse(db_session, user):
    return make_statmech(
        db_session,
        species_entry=_entry(db_session),
        external_symmetry=None,
        point_group=None,
        is_linear=None,
        statmech_treatment=None,
    )


def _point_group_only(db_session, user):
    """Symmetry carried by the point group alone: one of the two ways ``scientific_context`` can be satisfied."""
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry, external_symmetry=None, point_group="Cs")
    attach_statmech_source_calculation(
        db_session,
        statmech=statmech,
        calculation=_calc(db_session, entry, CalculationType.freq, user=user),
        role=StatmechCalculationRole.freq,
    )
    return statmech


def _external_symmetry_only(db_session, user):
    """Symmetry carried by the external symmetry number alone: the other way."""
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry, external_symmetry=2, point_group=None)
    attach_statmech_source_calculation(
        db_session,
        statmech=statmech,
        calculation=_calc(db_session, entry, CalculationType.freq, user=user),
        role=StatmechCalculationRole.freq,
    )
    return statmech


def _computed_sources(db_session, user):
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry, external_symmetry=3, point_group="C3v", is_linear=False)
    _opt_freq_sp(db_session, entry, statmech, user=user)
    attach_statmech_electronic_level(db_session, statmech=statmech, level_index=1, energy_cm1=0.0, degeneracy=2)
    return statmech


def _torsion_scan(db_session, user):
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry, external_symmetry=1, point_group="C1", is_linear=False)
    _opt_freq_sp(db_session, entry, statmech, user=user)
    scan = _calc(db_session, entry, CalculationType.scan, user=user)
    attach_statmech_source_calculation(
        db_session, statmech=statmech, calculation=scan, role=StatmechCalculationRole.scan
    )
    attach_statmech_torsion(db_session, statmech=statmech, torsion_index=1, source_scan_calculation=scan)
    return statmech


def _experimental_with_literature(db_session, user):
    return make_statmech(
        db_session,
        species_entry=_entry(db_session),
        scientific_origin=ScientificOriginKind.experimental,
        literature_id=make_literature(db_session).id,
    )


def _experimental_without_literature(db_session, user):
    return make_statmech(
        db_session, species_entry=_entry(db_session), scientific_origin=ScientificOriginKind.experimental
    )


def _computed_no_sources(db_session, user):
    return make_statmech(db_session, species_entry=_entry(db_session))


def _with_model_declaration(db_session, user):
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)
    attach_statmech_source_calculation(
        db_session,
        statmech=statmech,
        calculation=_calc(db_session, entry, CalculationType.opt, user=user),
        role=StatmechCalculationRole.opt,
    )
    db_session.add(
        StatmechModelDeclaration(
            statmech_id=statmech.id,
            schema_version=1,
            scope_kind=StatmechModelScopeKind.atom,
            literature_id=make_literature(db_session).id,
            recipe={},
            content_hash=hashlib.sha256(b"frozen-declaration").hexdigest(),
        )
    )
    db_session.flush()
    db_session.refresh(statmech)
    return statmech


def _custody_break_on_source(db_session, user):
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)
    calcs = _opt_freq_sp(db_session, entry, statmech, user=user)
    artifact = calcs[CalculationType.freq].artifacts[0]
    record_integrity_observation(
        sha256=artifact.sha256,
        finding=ArtifactIntegrityFinding.digest_mismatch,
        detected_during=ArtifactIntegrityDetectionContext.download,
        observed_sha256="0" * 64,
        artifact_id=artifact.id,
        artifact_recorded_at=artifact.created_at,
        session_factory=_SessionProxy(db_session),
        storage_client=_MuteStoreProbe(),
    )
    return statmech


SCENARIOS = {
    "sparse": _sparse,
    "point_group_only": _point_group_only,
    "external_symmetry_only": _external_symmetry_only,
    "computed_opt_freq_sp": _computed_sources,
    "torsion_with_scan": _torsion_scan,
    "experimental_with_literature": _experimental_with_literature,
    "experimental_without_literature": _experimental_without_literature,
    "computed_no_sources": _computed_no_sources,
    "with_model_declaration": _with_model_declaration,
    "custody_break_on_source_artifact": _custody_break_on_source,
}


def _observe(db_session, name: str, user: int) -> dict:
    statmech = SCENARIOS[name](db_session, user)
    assert statmech.species_entry_id is not None and statmech.transition_state_entry_id is None
    result = evaluate_reproducibility(
        db_session, record_type=SubmissionRecordType.statmech, record_id=statmech.id, **_integrity_seam(db_session)
    )
    return {
        "grade": result.grade.value,
        "rubric": result.context_json["rubric"],
        "checks": [[c["name"], c["level"], c["outcome"]] for c in result.context_json["checks"]],
        "digest": normalised_digest(result.context_json),
    }


# ---------------------------------------------------------------------------
# The literals. Captured from the unchanged rubric; PR-2 must leave them byte-identical.
# ---------------------------------------------------------------------------

FROZEN: dict[str, dict] = {
    "computed_no_sources": {
        "grade": "insufficient",
        "rubric": {'name': 'tckdb_reproducibility', 'version': 'v1'},
        "checks": [
            ['target_identity', 'described', 'passed'],
            ['scientific_context', 'described', 'passed'],
            ['source_attribution', 'described', 'missing'],
            ['record_type_audit_policy', 'auditable', 'missing'],
            ['calculation_metadata', 'auditable', 'missing'],
            ['typed_output_evidence', 'auditable', 'missing'],
            ['verified_output_artifact_bytes', 'auditable', 'missing'],
            ['source_role_preservation', 'auditable', 'missing'],
            ['preserved_input_artifacts', 'rerunnable', 'missing'],
            ['execution_parameter_snapshot', 'rerunnable', 'missing'],
            ['upstream_dependency_snapshot', 'rerunnable', 'missing'],
            ['evidence_warnings_clear', 'rerunnable', 'passed'],
        ],
        "digest": "7fa7693195f420d66fb9ea431308ab542c3ff6217c2a67a6be7909ff1a2c450d",
    },
    "computed_opt_freq_sp": {
        "grade": "described",
        "rubric": {'name': 'tckdb_reproducibility', 'version': 'v1'},
        "checks": [
            ['target_identity', 'described', 'passed'],
            ['scientific_context', 'described', 'passed'],
            ['source_attribution', 'described', 'passed'],
            ['record_type_audit_policy', 'auditable', 'missing'],
            ['calculation_metadata', 'auditable', 'missing'],
            ['typed_output_evidence', 'auditable', 'missing'],
            ['verified_output_artifact_bytes', 'auditable', 'missing'],
            ['source_role_preservation', 'auditable', 'missing'],
            ['preserved_input_artifacts', 'rerunnable', 'missing'],
            ['execution_parameter_snapshot', 'rerunnable', 'missing'],
            ['upstream_dependency_snapshot', 'rerunnable', 'missing'],
            ['evidence_warnings_clear', 'rerunnable', 'passed'],
        ],
        "digest": "26c42bf824c88a2cf23d3a032219d20b5a34f803cefbee2257ee048902f819f0",
    },
    "custody_break_on_source_artifact": {
        "grade": "described",
        "rubric": {'name': 'tckdb_reproducibility', 'version': 'v1'},
        "checks": [
            ['target_identity', 'described', 'passed'],
            ['scientific_context', 'described', 'passed'],
            ['source_attribution', 'described', 'passed'],
            ['record_type_audit_policy', 'auditable', 'missing'],
            ['calculation_metadata', 'auditable', 'missing'],
            ['typed_output_evidence', 'auditable', 'missing'],
            ['verified_output_artifact_bytes', 'auditable', 'missing'],
            ['source_role_preservation', 'auditable', 'missing'],
            ['preserved_input_artifacts', 'rerunnable', 'missing'],
            ['execution_parameter_snapshot', 'rerunnable', 'missing'],
            ['upstream_dependency_snapshot', 'rerunnable', 'missing'],
            ['evidence_warnings_clear', 'rerunnable', 'missing'],
        ],
        "digest": "dd7b4d8095ed2e95fe32a9e3ba5a53ace902f4d6242cdc5ecf396a9a08e29e73",
    },
    "experimental_with_literature": {
        "grade": "described",
        "rubric": {'name': 'tckdb_reproducibility', 'version': 'v1'},
        "checks": [
            ['target_identity', 'described', 'passed'],
            ['scientific_context', 'described', 'passed'],
            ['source_attribution', 'described', 'passed'],
            ['record_type_audit_policy', 'auditable', 'missing'],
            ['calculation_metadata', 'auditable', 'missing'],
            ['typed_output_evidence', 'auditable', 'missing'],
            ['verified_output_artifact_bytes', 'auditable', 'missing'],
            ['source_role_preservation', 'auditable', 'missing'],
            ['preserved_input_artifacts', 'rerunnable', 'missing'],
            ['execution_parameter_snapshot', 'rerunnable', 'missing'],
            ['upstream_dependency_snapshot', 'rerunnable', 'missing'],
            ['evidence_warnings_clear', 'rerunnable', 'passed'],
        ],
        "digest": "0b117736de65c4f6c8bf31f7619d794cb2c32e3a3bb5fd0b4b9dcc97e412f224",
    },
    "experimental_without_literature": {
        "grade": "insufficient",
        "rubric": {'name': 'tckdb_reproducibility', 'version': 'v1'},
        "checks": [
            ['target_identity', 'described', 'passed'],
            ['scientific_context', 'described', 'passed'],
            ['source_attribution', 'described', 'missing'],
            ['record_type_audit_policy', 'auditable', 'missing'],
            ['calculation_metadata', 'auditable', 'missing'],
            ['typed_output_evidence', 'auditable', 'missing'],
            ['verified_output_artifact_bytes', 'auditable', 'missing'],
            ['source_role_preservation', 'auditable', 'missing'],
            ['preserved_input_artifacts', 'rerunnable', 'missing'],
            ['execution_parameter_snapshot', 'rerunnable', 'missing'],
            ['upstream_dependency_snapshot', 'rerunnable', 'missing'],
            ['evidence_warnings_clear', 'rerunnable', 'passed'],
        ],
        "digest": "7ecf57a6157b6bb3fcd24b5220ba2756ea145b8e8638c050beae73c1129bbb18",
    },
    "external_symmetry_only": {
        "grade": "described",
        "rubric": {'name': 'tckdb_reproducibility', 'version': 'v1'},
        "checks": [
            ['target_identity', 'described', 'passed'],
            ['scientific_context', 'described', 'passed'],
            ['source_attribution', 'described', 'passed'],
            ['record_type_audit_policy', 'auditable', 'missing'],
            ['calculation_metadata', 'auditable', 'missing'],
            ['typed_output_evidence', 'auditable', 'missing'],
            ['verified_output_artifact_bytes', 'auditable', 'missing'],
            ['source_role_preservation', 'auditable', 'missing'],
            ['preserved_input_artifacts', 'rerunnable', 'missing'],
            ['execution_parameter_snapshot', 'rerunnable', 'missing'],
            ['upstream_dependency_snapshot', 'rerunnable', 'missing'],
            ['evidence_warnings_clear', 'rerunnable', 'passed'],
        ],
        "digest": "f50e4aa2e7dfc3c55bda0102cc414970d34fa4ef10578674d8569c32f16f5058",
    },
    "point_group_only": {
        "grade": "described",
        "rubric": {'name': 'tckdb_reproducibility', 'version': 'v1'},
        "checks": [
            ['target_identity', 'described', 'passed'],
            ['scientific_context', 'described', 'passed'],
            ['source_attribution', 'described', 'passed'],
            ['record_type_audit_policy', 'auditable', 'missing'],
            ['calculation_metadata', 'auditable', 'missing'],
            ['typed_output_evidence', 'auditable', 'missing'],
            ['verified_output_artifact_bytes', 'auditable', 'missing'],
            ['source_role_preservation', 'auditable', 'missing'],
            ['preserved_input_artifacts', 'rerunnable', 'missing'],
            ['execution_parameter_snapshot', 'rerunnable', 'missing'],
            ['upstream_dependency_snapshot', 'rerunnable', 'missing'],
            ['evidence_warnings_clear', 'rerunnable', 'passed'],
        ],
        "digest": "4635e153a0fa66b209e05cb278f7ce4ad219d358a52e097c9904c22fa72bb964",
    },
    "sparse": {
        "grade": "insufficient",
        "rubric": {'name': 'tckdb_reproducibility', 'version': 'v1'},
        "checks": [
            ['target_identity', 'described', 'passed'],
            ['scientific_context', 'described', 'missing'],
            ['source_attribution', 'described', 'missing'],
            ['record_type_audit_policy', 'auditable', 'missing'],
            ['calculation_metadata', 'auditable', 'missing'],
            ['typed_output_evidence', 'auditable', 'missing'],
            ['verified_output_artifact_bytes', 'auditable', 'missing'],
            ['source_role_preservation', 'auditable', 'missing'],
            ['preserved_input_artifacts', 'rerunnable', 'missing'],
            ['execution_parameter_snapshot', 'rerunnable', 'missing'],
            ['upstream_dependency_snapshot', 'rerunnable', 'missing'],
            ['evidence_warnings_clear', 'rerunnable', 'passed'],
        ],
        "digest": "9452291a91ec3793e2be351229e88e3f8ccee5b81626fb06fe75e4517e3510b1",
    },
    "torsion_with_scan": {
        "grade": "described",
        "rubric": {'name': 'tckdb_reproducibility', 'version': 'v1'},
        "checks": [
            ['target_identity', 'described', 'passed'],
            ['scientific_context', 'described', 'passed'],
            ['source_attribution', 'described', 'passed'],
            ['record_type_audit_policy', 'auditable', 'missing'],
            ['calculation_metadata', 'auditable', 'missing'],
            ['typed_output_evidence', 'auditable', 'missing'],
            ['verified_output_artifact_bytes', 'auditable', 'missing'],
            ['source_role_preservation', 'auditable', 'missing'],
            ['preserved_input_artifacts', 'rerunnable', 'missing'],
            ['execution_parameter_snapshot', 'rerunnable', 'missing'],
            ['upstream_dependency_snapshot', 'rerunnable', 'missing'],
            ['evidence_warnings_clear', 'rerunnable', 'passed'],
        ],
        "digest": "c3ab62268c95ac817e8d749b363b760a76a2478e4f7f81d77748a483339eda80",
    },
    "with_model_declaration": {
        "grade": "described",
        "rubric": {'name': 'tckdb_reproducibility', 'version': 'v1'},
        "checks": [
            ['target_identity', 'described', 'passed'],
            ['scientific_context', 'described', 'passed'],
            ['source_attribution', 'described', 'passed'],
            ['record_type_audit_policy', 'auditable', 'missing'],
            ['calculation_metadata', 'auditable', 'missing'],
            ['typed_output_evidence', 'auditable', 'missing'],
            ['verified_output_artifact_bytes', 'auditable', 'missing'],
            ['source_role_preservation', 'auditable', 'missing'],
            ['preserved_input_artifacts', 'rerunnable', 'missing'],
            ['execution_parameter_snapshot', 'rerunnable', 'missing'],
            ['upstream_dependency_snapshot', 'rerunnable', 'missing'],
            ['evidence_warnings_clear', 'rerunnable', 'passed'],
        ],
        "digest": "8b401d7c87397d515ca1cbf7e98ed3d6efcb7ff9131e7fcd0bc2683f44d93874",
    },
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_digest_does_not_depend_on_row_ids(db_session, _api_test_user, name):
    """The same shape built twice, on different row ids, digests the same.

    This is what makes the literals below portable: they must not move with the order tests run in or with how
    far a sequence has advanced.
    """
    first = _observe(db_session, name, _api_test_user)
    second = _observe(db_session, name, _api_test_user)
    assert first == second


def test_every_scenario_is_frozen():
    """No scenario escapes the freeze, and no literal outlives its scenario."""
    assert SCENARIOS
    assert set(FROZEN) == set(SCENARIOS)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_species_owned_statmech_reproducibility_is_frozen(db_session, _api_test_user, name):
    observed = _observe(db_session, name, _api_test_user)
    assert observed == FROZEN[name]


def test_a_stored_species_assessment_reads_current_under_its_original_rubric_identity(client, db_session):
    """Append, then read over the API: the row says what graded it, and it is ``current``.

    This is the observable half of "species rows are untouched": a row appended by this code reads ``current``
    through ``include=assessments``, and carries the rubric identity species records have always carried.
    """
    statmech = _computed_no_sources(db_session, None)
    row = evaluate_and_append_reproducibility(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=statmech.id,
        **_integrity_seam(db_session),
    )
    assert (row.rubric_name, row.rubric_version) == ("tckdb_reproducibility", "v1")
    assert row.context_json["rubric"] == {"name": "tckdb_reproducibility", "version": "v1"}

    response = client.get(f"/api/v1/scientific/statmech/{statmech.public_ref}", params={"include": "assessments"})
    assert response.status_code == 200, response.text
    reproducibility = response.json()["record"]["assessments"]["reproducibility"]
    assert reproducibility["state"] == "current"
    assert reproducibility["assessment_ref"] == row.public_ref
    assert reproducibility["rubric"] == "tckdb_reproducibility"
    assert reproducibility["rubric_version"] == "v1"
    assert reproducibility["grade"] == row.grade.value


# ---------------------------------------------------------------------------
# The defect (#760): a TS-owned statmech is graded ``insufficient`` whatever its evidence
# ---------------------------------------------------------------------------


def _ts_entry(db_session):
    reactant = make_species(db_session, inchi_key=next_inchi_key("FROZENTS"))
    product = make_species(db_session, inchi_key=next_inchi_key("FROZENTS"))
    reaction = make_chem_reaction(db_session, reactants=[reactant], products=[product])
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    return make_transition_state_entry(
        db_session, transition_state=make_transition_state(db_session, reaction_entry=reaction_entry)
    )


@pytest.mark.xfail(
    strict=True,
    # Only an assertion about the grade may excuse this test. A setup error must fail it, not hide behind xfail.
    raises=AssertionError,
    reason="#760: scientific_context asks every statmech record for species_entry_id, so a TS-owned record is "
    "graded insufficient. PR-2 grades it on its own evidence and flips this to a pass.",
)
def test_a_ts_owned_statmech_is_graded_on_its_evidence(db_session, _api_test_user):
    """Intended behaviour: complete described-level evidence, graded ``described`` (the ceiling for a product)."""
    ts_entry = _ts_entry(db_session)
    statmech = Statmech(
        transition_state_entry_id=ts_entry.id,
        scientific_origin=ScientificOriginKind.computed,
        external_symmetry=1,
        point_group="C1",
    )
    db_session.add(statmech)
    db_session.flush()
    calculation = make_calculation(
        db_session,
        type=CalculationType.freq,
        transition_state_entry_id=ts_entry.id,
        lot_id=make_lot(db_session).id,
        software_release_id=make_software_release(db_session).id,
    )
    attach_statmech_source_calculation(
        db_session, statmech=statmech, calculation=calculation, role=StatmechCalculationRole.freq
    )
    assert statmech.species_entry_id is None and statmech.transition_state_entry_id is not None

    result = evaluate_reproducibility(
        db_session, record_type=SubmissionRecordType.statmech, record_id=statmech.id, **_integrity_seam(db_session)
    )

    outcomes = {check["name"]: check["outcome"] for check in result.context_json["checks"]}
    assert outcomes["scientific_context"] == "passed"
    assert outcomes["source_attribution"] == "passed"
    assert result.grade is ReproducibilityGrade.described
