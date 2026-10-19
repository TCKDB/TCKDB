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
    CalculationCompositeResult,
    CalculationCompositeTerm,
    CalculationDependency,
    CalculationFreqMode,
    CalculationFreqResult,
    CalculationInputGeometry,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationParameter,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanResult,
    CalculationSPResult,
)
from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
    ArtifactKind,
    CalculationDependencyRole,
    CalculationType,
    CompositeAssembly,
    CoordinateUnit,
    ParameterSource,
    ReproducibilityGrade,
    ScanCoordinateKind,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechModelScopeKind,
    StatmechModelSourceRole,
    SubmissionRecordType,
)
from app.db.models.geometry import GeometryAtom
from app.db.models.statmech import Statmech
from app.db.models.statmech_declaration import (
    StatmechModelDeclaration,
    StatmechModelMember,
    StatmechModelSource,
)
from app.services.artifact_integrity import record_integrity_observation
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility, evaluate_reproducibility
from tests.services.scientific_read._factories import (
    attach_statmech_electronic_level,
    attach_statmech_source_calculation,
    attach_statmech_torsion,
    make_calculation,
    make_chem_reaction,
    make_geometry,
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
_VALUE_KEYS = {
    "public_ref",
    "reference",
    "sha256",
    "inchi_key",
    "doi",
    "uri",
    "lot_hash",
    "geom_hash",
    "content_hash",
    "integrity_event_ref",
}
_AT_KEY = re.compile(r"_at$")
#: Foreign-key names that point at a table under a different name from the one the snapshot nests it under.
_KIND_ALIAS = {
    "lot": "level_of_theory",
    "record": "statmech",
    "member_statmech": "statmech",
    "parent_calculation": "calculation",
    "child_calculation": "calculation",
    "created_by": "user",
}


class _Ordinals:
    """First-appearance ordinals, numbered separately for each table an id points at.

    Two tables hand out the same integer in one transaction (a statmech and its species entry are both row 1 in an
    empty database and rows 40 and 7 in a busy one), so one shared numbering would make the digest depend on that
    coincidence. Instead the *table* is the kind, and every way the snapshot names a row of that table shares it:
    a foreign key ``calculation_id``, the key of its ``calculation_evidence`` entry, and the ``id`` inside that
    entry's ``columns``. A snapshot in which two rows have swapped places, or in which a foreign key points at a
    different row, therefore numbers differently and digests differently.
    """

    def __init__(self) -> None:
        self._seen: dict[tuple[str, object], str] = {}

    def of(self, kind: str, value: object) -> str:
        key = (kind, value)
        if key not in self._seen:
            self._seen[key] = f"<{kind}#{sum(1 for k, _ in self._seen if k == kind) + 1}>"
        return self._seen[key]


def _id_kind(key: str, path: tuple[str, ...]) -> str:
    """The table an id-valued field points at."""
    if key == "id":
        # The primary key of whatever the enclosing object is.
        owner = [part for part in path if part != "columns"]
        if path[:2] == ("target", "columns"):
            return "statmech"
        name = owner[-1] if owner else ""
        return "calculation" if name.startswith("<calculation#") else _KIND_ALIAS.get(name, name)
    stem = key[:-3] if key.endswith("_id") else key[:-4] if key.endswith("_ids") else key
    return _KIND_ALIAS.get(stem, stem)


def _normalise(node, ordinals: _Ordinals, key: str | None = None, path: tuple[str, ...] = ()):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            # ``calculation_evidence`` is a map keyed by calculation id. The key is text (``"5"``) while every
            # other mention of that calculation is the integer 5, so convert before numbering or the two never
            # meet.
            nk = ordinals.of("calculation", int(k)) if key == "calculation_evidence" else k
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


def _estimated(db_session, user, *, literature: bool):
    return make_statmech(
        db_session,
        species_entry=_entry(db_session),
        scientific_origin=ScientificOriginKind.estimated,
        literature_id=make_literature(db_session).id if literature else None,
    )


def _estimated_with_literature(db_session, user):
    return _estimated(db_session, user, literature=True)


def _estimated_without_literature(db_session, user):
    return _estimated(db_session, user, literature=False)


def _geometry(db_session, shift: float):
    geometry = make_geometry(db_session, natoms=2, xyz_text=f"2\n\nH 0 0 0\nH 0 0 {0.74 + shift}\n")
    db_session.add_all(
        GeometryAtom(geometry_id=geometry.id, atom_index=i, element="H", x=0.0, y=0.0, z=z)
        for i, z in ((1, 0.0), (2, 0.74 + shift))
    )
    db_session.flush()
    return geometry


def _edge(parent, child, role):
    return CalculationDependency(
        parent_calculation_id=parent.id, child_calculation_id=child.id, dependency_role=role
    )


def _dependency_edges(db_session, user):
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)
    calcs = _opt_freq_sp(db_session, entry, statmech, user=user)
    opt, freq, sp = (calcs[CalculationType.opt], calcs[CalculationType.freq], calcs[CalculationType.sp])
    db_session.add_all(
        [
            _edge(opt, freq, CalculationDependencyRole.freq_on),
            _edge(opt, sp, CalculationDependencyRole.single_point_on),
        ]
    )
    db_session.flush()
    for calculation in calcs.values():
        db_session.refresh(calculation)
    return statmech


def _dependency_cycle(db_session, user):
    """Two source calculations that are each other's parent.

    Nothing in the schema forbids the pair (the primary key is the ordered pair, and self-edges are the only edge
    refused), so the closure walk's cycle branch is reachable from stored data.
    """
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)
    calcs = _opt_freq_sp(db_session, entry, statmech, user=user)
    opt, freq = calcs[CalculationType.opt], calcs[CalculationType.freq]
    db_session.add_all(
        [
            _edge(opt, freq, CalculationDependencyRole.freq_on),
            _edge(freq, opt, CalculationDependencyRole.optimized_from),
        ]
    )
    db_session.flush()
    for calculation in calcs.values():
        db_session.refresh(calculation)
    return statmech


def _scan_with_result(db_session, user):
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)
    scan = _calc(db_session, entry, CalculationType.scan, user=user)
    attach_statmech_source_calculation(
        db_session, statmech=statmech, calculation=scan, role=StatmechCalculationRole.scan
    )
    db_session.add_all(
        [
            CalculationScanResult(
                calculation_id=scan.id, dimension=1, is_relaxed=True, zero_energy_reference_hartree=-40.7
            ),
            CalculationScanCoordinate(
                calculation_id=scan.id,
                coordinate_index=1,
                coordinate_kind=ScanCoordinateKind.dihedral,
                atom1_index=1,
                atom2_index=2,
                atom3_index=3,
                atom4_index=4,
                step_count=3,
                step_size=10.0,
                start_value=0.0,
                end_value=20.0,
                value_unit=CoordinateUnit.degree,
            ),
        ]
    )
    db_session.flush()
    for index in (1, 2):
        db_session.add(
            CalculationScanPoint(
                calculation_id=scan.id,
                point_index=index,
                electronic_energy_hartree=-40.7 + 0.001 * index,
                relative_energy_kj_mol=2.6 * index,
                geometry_id=_geometry(db_session, 0.01 * index).id,
            )
        )
    db_session.flush()
    db_session.refresh(scan)
    return statmech


def _composite_source(db_session, user):
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)
    composite = _calc(db_session, entry, CalculationType.composite, user=user)
    attach_statmech_source_calculation(
        db_session, statmech=statmech, calculation=composite, role=StatmechCalculationRole.composite
    )
    db_session.add(
        CalculationCompositeResult(
            calculation_id=composite.id,
            assembly=CompositeAssembly.program_run,
            electronic_energy_hartree=-40.9,
            e0_hartree=-40.85,
            recipe_zpe_hartree=0.05,
        )
    )
    db_session.add_all(
        CalculationCompositeTerm(calculation_id=composite.id, term_position=i, value_hartree=v)
        for i, v in enumerate((-40.8, -0.1))
    )
    db_session.flush()
    db_session.refresh(composite)
    return statmech


def _object_missing_break(db_session, user):
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)
    calcs = _opt_freq_sp(db_session, entry, statmech, user=user)
    artifact = calcs[CalculationType.sp].artifacts[0]
    record_integrity_observation(
        sha256=artifact.sha256,
        finding=ArtifactIntegrityFinding.object_missing,
        detected_during=ArtifactIntegrityDetectionContext.download,
        expected_bytes=artifact.bytes,
        artifact_id=artifact.id,
        artifact_recorded_at=artifact.created_at,
        session_factory=_SessionProxy(db_session),
        storage_client=_MuteStoreProbe(),
    )
    return statmech


def _declaration_with_members_and_sources(db_session, user):
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)
    member = make_statmech(db_session, species_entry=entry)
    source = _calc(db_session, entry, CalculationType.freq, user=user)
    attach_statmech_source_calculation(db_session, statmech=statmech, calculation=source)
    declaration = StatmechModelDeclaration(
        statmech_id=statmech.id,
        schema_version=1,
        scope_kind=StatmechModelScopeKind.explicit_equilibrium_ensemble,
        literature_id=make_literature(db_session).id,
        recipe={},
        content_hash=hashlib.sha256(b"frozen-declaration-with-members").hexdigest(),
    )
    db_session.add(declaration)
    db_session.flush()
    db_session.add_all(
        [
            StatmechModelMember(
                declaration_id=declaration.id,
                member_key="m1",
                member_statmech_id=member.id,
                counting_multiplicity=1,
                relative_energy_kj_mol=0.0,
            ),
            StatmechModelSource(
                declaration_id=declaration.id,
                source_key="s1",
                role=StatmechModelSourceRole.geometry,
                calculation_id=source.id,
            ),
        ]
    )
    db_session.flush()
    db_session.refresh(statmech)
    return statmech


def _source_geometries_and_parameters(db_session, user):
    entry = _entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)
    calcs = _opt_freq_sp(db_session, entry, statmech, user=user)
    opt = calcs[CalculationType.opt]
    opt.parameters_json = {"route": "opt=tight", "nproc": 4}
    db_session.add_all(
        [
            CalculationInputGeometry(calculation_id=opt.id, geometry_id=_geometry(db_session, 0.0).id, input_order=1),
            CalculationOutputGeometry(
                calculation_id=opt.id, geometry_id=_geometry(db_session, 0.02).id, output_order=1
            ),
            CalculationParameter(
                calculation_id=opt.id, raw_key="scf_convergence", raw_value="tight", source=ParameterSource.upload
            ),
        ]
    )
    db_session.flush()
    db_session.refresh(opt)
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
    "estimated_with_literature": _estimated_with_literature,
    "estimated_without_literature": _estimated_without_literature,
    "dependency_edges": _dependency_edges,
    "dependency_cycle": _dependency_cycle,
    "scan_source_with_result": _scan_with_result,
    "composite_source": _composite_source,
    "object_missing_break_on_source_artifact": _object_missing_break,
    "declaration_with_members_and_sources": _declaration_with_members_and_sources,
    "source_geometries_and_parameters": _source_geometries_and_parameters,
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
    "composite_source": {
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
        "digest": "00bce39a870f3cf7b3c5e4828d12ce98d50c88b601e2d6938a363222031d1f8f",
    },
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
        "digest": "5765646980c71112fd73c3d574bddd138ec7ce94549e06a4b63cdcb1e819d762",
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
        "digest": "5eb7e7413ce14480bb6bc9474946f189214f8739d24b9421a055237fc094b487",
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
        "digest": "a4aef64a765bd9b134d045312ab2914b9d3bcb7c54c426dcee27d3d0bf48d3b6",
    },
    "declaration_with_members_and_sources": {
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
        "digest": "2910ac8b8e293709b0dac8d3947a0967c65338adac41c16d307a2bdcae8cb0cb",
    },
    "dependency_cycle": {
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
        "digest": "7be4e3e5653de0fc147db99e84819b7628c72b01a8098212eeb231df55decf6c",
    },
    "dependency_edges": {
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
        "digest": "e2b0dccd8ca9616e88cd5b3387f167b2b232fd4f525c7a860a197f288cd78097",
    },
    "estimated_with_literature": {
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
        "digest": "0e52333acfc6753332a64f108a5285d1935ccd528e4e95321bae29aca83cb9e4",
    },
    "estimated_without_literature": {
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
        "digest": "e24adae0602dd6023d0470d881bc2b4ef28a963bf4ccc2fe0155aded400b12fa",
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
        "digest": "a3954a6ee43099bf42f82f70841519536af4dbc6d0bd72d8e350f29d354667ed",
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
        "digest": "9355fe4a776f8c5f642ca325c5c84a52189c8e750787eef8af17b83472115e3d",
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
        "digest": "25df6173c7041a475c064fe29ea42f0eb207d158a70a82c8c3560c457455c9f7",
    },
    "object_missing_break_on_source_artifact": {
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
        "digest": "e0664d3eacd2ed4f18ce63f12118975cd68d1b9e28ede43cd034960a5224e3a2",
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
        "digest": "d3b93c771bda3af0b682861168989ea81cfef4977b6ee757f13c9404c301e300",
    },
    "scan_source_with_result": {
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
        "digest": "33531e65d9c669562af8c2ac40008bab7961dd9b08a89e86002f123707ffc4d8",
    },
    "source_geometries_and_parameters": {
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
        "digest": "662035b591e5320f63d14f1b01f1fab851830f3391c19c5da15d08cedd0de0a2",
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
        "digest": "89d3de9083ff07acd381357de99a0c04d8ff2df100ae2a48567976d738d5e456",
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
        "digest": "a0b517570b27f17747adc0147aa1220995f5805b2b38d5b5c0432926962431bd",
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
        "digest": "092b3bcc413c22d47952986bbad8b3559649340b268c134ce5d6a45876135324",
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


def _context(db_session, name: str, user: int) -> dict:
    statmech = SCENARIOS[name](db_session, user)
    return evaluate_reproducibility(
        db_session, record_type=SubmissionRecordType.statmech, record_id=statmech.id, **_integrity_seam(db_session)
    ).context_json


def test_the_new_shapes_reach_the_branches_they_exist_for(db_session, _api_test_user):
    """A shape that quietly stops exercising its branch would freeze nothing. Say what each one reaches."""
    user = _api_test_user

    def evidence(name):
        context = _context(db_session, name, user)
        return context, list(context["calculation_evidence"].values())

    context, _ = evidence("dependency_cycle")
    check = next(c for c in context["checks"] if c["name"] == "upstream_dependency_snapshot")
    assert check["evidence"]["cycle_detected"] is True

    _, calcs = evidence("dependency_edges")
    assert sum(len(c["upstream_dependencies"]) for c in calcs) == 2

    _, calcs = evidence("scan_source_with_result")
    scan = next(c for c in calcs if c["typed_output"]["calculation_type"] == "scan")
    assert scan["typed_output_present"] is True
    assert len(scan["typed_output"]["points"]) == 2 and scan["typed_output"]["coordinates"]

    _, calcs = evidence("composite_source")
    composite = next(c for c in calcs if c["typed_output"]["calculation_type"] == "composite")
    assert composite["typed_output_present"] is True and len(composite["typed_output"]["terms"]) == 2

    _, calcs = evidence("object_missing_break_on_source_artifact")
    assert {a["verification"] for c in calcs for a in c["artifacts"]} >= {"evidence_missing"}

    context, _ = evidence("declaration_with_members_and_sources")
    declaration = context["target"]["relationships"]["model_declaration"]
    assert len(declaration["members"]) == 1 and len(declaration["sources"]) == 1

    _, calcs = evidence("source_geometries_and_parameters")
    opt = next(c for c in calcs if c["typed_output"]["calculation_type"] == "opt")
    assert opt["input_geometries"] and opt["output_geometries"] and opt["parameters"] and opt["parameters_json"]

    for name in ("estimated_with_literature", "estimated_without_literature"):
        context, _ = evidence(name)
        assert context["target"]["columns"]["scientific_origin"] == "estimated"


# ---------------------------------------------------------------------------
# The digest must notice a row being swapped for another row
# ---------------------------------------------------------------------------


def _two_calculation_context(db_session, user) -> dict:
    context = _context(db_session, "computed_opt_freq_sp", user)
    assert len(context["calculation_evidence"]) == 3
    return json.loads(json.dumps(context, default=str))


def _minimal_context() -> dict:
    """Two calculations that differ in nothing but their ids, so no other field can give a swap away."""
    return {
        "source_roles": [{"calculation_id": 11, "role": "opt"}, {"calculation_id": 12, "role": "opt"}],
        "calculation_evidence": {
            "11": {"columns": {"id": 11, "type": "opt", "lot_id": 21}, "level_of_theory": {"id": 21}},
            "12": {"columns": {"id": 12, "type": "opt", "lot_id": 21}, "level_of_theory": {"id": 21}},
        },
    }


def test_probe_exchanging_the_keys_of_two_calculations_evidence_entries_moves_the_digest(db_session, _api_test_user):
    """Entry 11 filed under 12 and entry 12 under 11: each entry's own ``id`` now disagrees with its key."""
    minimal = _minimal_context()
    baseline = normalised_digest(minimal)
    evidence = minimal["calculation_evidence"]
    minimal["calculation_evidence"] = {"12": evidence["11"], "11": evidence["12"]}
    assert normalised_digest(minimal) != baseline

    context = _two_calculation_context(db_session, _api_test_user)
    baseline = normalised_digest(context)
    assert normalised_digest(json.loads(json.dumps(context))) == baseline
    evidence = context["calculation_evidence"]
    first, second = list(evidence)[:2]
    context["calculation_evidence"] = {second: evidence[first], first: evidence[second], **{
        k: v for k, v in evidence.items() if k not in (first, second)
    }}
    assert normalised_digest(context) != baseline


def test_probe_exchanging_the_ids_inside_two_entries_moves_the_digest(db_session, _api_test_user):
    """Each entry stays under its key but now carries the other's ``id``."""
    minimal = _minimal_context()
    baseline = normalised_digest(minimal)
    columns = [entry["columns"] for entry in minimal["calculation_evidence"].values()]
    columns[0]["id"], columns[1]["id"] = columns[1]["id"], columns[0]["id"]
    assert normalised_digest(minimal) != baseline

    context = _two_calculation_context(db_session, _api_test_user)
    baseline = normalised_digest(context)
    evidence = context["calculation_evidence"]
    first, second = list(evidence)[:2]
    evidence[first]["columns"]["id"], evidence[second]["columns"]["id"] = int(second), int(first)
    assert normalised_digest(context) != baseline


def test_probe_a_key_that_names_no_listed_calculation_moves_the_digest():
    minimal = _minimal_context()
    baseline = normalised_digest(minimal)
    evidence = minimal["calculation_evidence"]
    minimal["calculation_evidence"] = {"11": evidence["11"], "13": evidence["12"]}
    assert normalised_digest(minimal) != baseline


def test_probe_pointing_a_foreign_key_at_another_row_moves_the_digest():
    """The same hole one table over: ``lot_id`` must meet the ``id`` of the level of theory it names."""
    minimal = _minimal_context()
    baseline = normalised_digest(minimal)
    minimal["calculation_evidence"]["12"]["columns"]["lot_id"] = 22
    assert normalised_digest(minimal) != baseline



def test_every_scenario_is_frozen():
    """No scenario escapes the freeze, and no literal outlives its scenario."""
    assert SCENARIOS
    assert set(FROZEN) == set(SCENARIOS)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_species_owned_statmech_reproducibility_is_frozen(db_session, _api_test_user, name):
    observed = _observe(db_session, name, _api_test_user)
    assert observed == FROZEN[name]


def test_smoke_a_species_row_appended_now_is_current_and_carries_the_original_rubric_name(client, db_session):
    """Smoke test, and only that: it pins the rubric name and version written on a freshly appended row.

    A row appended by this code reads ``current`` through ``include=assessments`` and says it was graded by
    ``tckdb_reproducibility`` ``v1``. It is not evidence about rows stored earlier: whether those still read
    ``current`` after the owner-scoped rubric lands is PR-2's to prove (its currency tests append under the old
    identity and read back).
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
    # Only a failed assertion about the outcome may excuse this test. Everything else, including a broken
    # precondition (which raises RuntimeError below), is reported as a failure, never as the expected xfail.
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
    if statmech.species_entry_id is not None or statmech.transition_state_entry_id is None:
        raise RuntimeError("precondition broken: the record under test is not TS-owned")

    result = evaluate_reproducibility(
        db_session, record_type=SubmissionRecordType.statmech, record_id=statmech.id, **_integrity_seam(db_session)
    )

    outcomes = {check["name"]: check["outcome"] for check in result.context_json["checks"]}
    assert outcomes["scientific_context"] == "passed"
    assert outcomes["source_attribution"] == "passed"
    assert result.grade is ReproducibilityGrade.described
