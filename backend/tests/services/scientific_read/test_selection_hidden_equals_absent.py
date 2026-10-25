"""Hidden == absent: a curated selection over a world with hidden records equals the exploratory selection over the same
world with those records never created.

Owner decision (2026-10-18): selection evidence that points at a record the reader cannot open is treated exactly like a
reference to nothing, so the curated decision may differ from the exploratory one and that is intended. The proof is an
equivalence, per engine:

* ``scene(session, hidden=True)`` builds a world with a hidden record in every place a visible record can lean on it
  (supporting calculations, source links, a solve, an anchoring group, a member's group, a hidden record of the population);
* ``scene(session, hidden=False)`` builds the same world in which those records were never created;
* the curated selection over the first equals the exploratory selection over the second, once the few positions that are
  IDENTITY (the engine needs the ref to compare or partition: a determination's target, a candidate's own target group)
  are compared by name. Those hold a placeholder token under curated and a real ref under exploratory, and both are
  renamed to the same label.

Each test names the loader line it kills when it is removed.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.models.common import (
    CalculationDependencyRole,
    CalculationType,
    KineticsCalculationRole,
    KineticsDeterminationTargetKind,
    KineticsDirection,
    KineticsRepresentationRole,
    ProfileRecommendation,
    ReadProfile,
    RecordReviewStatus,
    StatmechCalculationRole,
    StatmechModelScopeKind,
    StatmechModelSourceRole,
    StructureDeterminationTargetKind,
    SubmissionRecordType,
    ThermoCalculationRole,
    ThermoTargetKind,
)
from app.db.models.kinetics import KineticsDetermination, KineticsSourceCalculation
from app.db.models.statmech_declaration import StatmechModelDeclaration, StatmechModelMember, StatmechModelSource
from app.schemas.reads.scientific_kinetics_selection import KineticsSelectionRequest
from app.schemas.reads.scientific_statmech_selection import StatmechSelectionRequest
from app.schemas.reads.scientific_thermo_selection import ThermoSelectionRequest
from app.services.kinetics_selection import public as kinetics_public
from app.services.kinetics_selection.selection import select_reaction_entry_kinetics
from app.services.scientific_read import ref_guard
from app.services.scientific_read.profile import (
    EXPLORATORY,
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)
from app.services.statmech_selection.selection import select_statmech
from app.services.thermo_selection import public as thermo_public
from app.services.thermo_selection.service import select_h298
from tests.services.scientific_read._factories import (
    attach_dependency,
    attach_statmech_source_calculation,
    attach_thermo_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_kinetics,
    make_literature,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)
from tests.services.statmech_selection._support import BASE_RECIPE, THERMAL, make

R = SubmissionRecordType
APPROVED = RecordReviewStatus.approved
CURATED = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
TIME = re.compile(r"\d{4}-\d\d-\d\dT[\d:.+\-]+Z?")
#: Keys of a document that describe the read (profile echoes, checksums, sizes), not the decision.
DESCRIBES_THE_READ = frozenset(
    {
        "read_profile",
        "visibility",
        "integrity",
        "digest",
        "content_sha256",
        "manifest_sha256",
        "canonical_bytes",
        "snapshot_isolation",
        "effective_review_statuses",
        "candidate_review_statuses",
        "excluded_by_review_withheld",
    }
)


class Scene(SimpleNamespace):
    """What a scene hands the comparison: a runner and the labels of every ref it created."""

    collapse: frozenset = frozenset()  # ref prefixes this scene names by kind (see the scene that sets it)
    #: Paths of the lists the ENGINE sorts by a ref (a random string), so their order says nothing: compared as sets.
    #: Every other list is compared in order. Each is read from the sort key the engine uses (named beside it).
    ref_sorted: frozenset = frozenset()


def approve(session, record_type, row):
    set_review(session, record_type=record_type, record_id=row.id, status=APPROVED)


def world_labels(world) -> dict[str, str]:
    """Labels for what a structure ``World`` made: its calculations by name, its geometry, its entry and level."""
    out = {c.public_ref: f"calc:{name}" for name, c in world.calcs.items()}
    out[world.geometry.public_ref] = "geometry"
    out[world.entry.public_ref] = "entry"
    out[world.lot.public_ref] = "lot"
    return out


def shared_source_with_hidden_determination(session, entry, *, lot, hidden: bool):
    """A visible calculation C that a visible record uses as its energy source, and (``hidden``) a structure
    determination D of a hidden observation that also pins C, carrying a live ``invalidates`` finding.

    In the world without D the record is not blocked; under curated D is hidden, so a finding on it must not block the
    record that merely shares C. Returns ``(C, D or None, hidden observation or None)``.
    """
    from app.db.models.common import (
        StructureFindingAuthority,
        StructureFindingKind,
        StructureFindingScope,
        StructureFindingVerdict,
    )
    from app.db.models.structure_determination import StructureEvidenceFinding
    from tests.api.scientific._curated_sweep_selection_world import basin_determination
    from tests.services.scientific_read._factories import attach_input_geometry, attach_sp_result, make_geometry

    observation = (
        make_conformer_observation(session, conformer_group=make_conformer_group(session, entry)) if hidden else None
    )
    calc = make_calculation(
        session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
        conformer_observation_id=observation.id if observation is not None else None,  # the pin needs the anchor
    )
    geometry = make_geometry(session)
    attach_sp_result(session, calculation=calc, electronic_energy_hartree=-76.4)
    attach_input_geometry(session, calculation=calc, geometry=geometry)
    if not hidden:
        return calc, None, None
    determination = basin_determination(
        session,
        entry=entry,
        observation=observation,
        sources=[("energy", calc)],
        quantity="electronic_energy",
        geometry=geometry,
    )
    session.add(
        StructureEvidenceFinding(
            kind=StructureFindingKind.identity_incompatibility,
            scope=StructureFindingScope.determination,
            subject_determination_id=determination.id,
            verdict=StructureFindingVerdict.invalidates,
            authority=StructureFindingAuthority.authorized_adjudication,
            rationale="a stated reason",
            semantic_version=1,
        )
    )
    session.flush()
    return calc, determination, observation


def label_all(**named):
    return {row.public_ref: name for name, row in named.items() if row is not None}


@pytest.fixture
def captured(monkeypatch):
    """Every token the seam made in a run: real ref -> token."""
    made: dict[str, str] = {}
    real = ref_guard.withhold_in_document

    def spy(session, facts, **kw):
        report = real(session, facts, **kw)
        made.update(report.tokens)
        return report

    monkeypatch.setattr(ref_guard, "withhold_in_document", spy)
    return made


def run_under(profile, fn):
    token = set_current_read_profile(profile)
    try:
        return fn()
    finally:
        reset_current_read_profile(token)


_REF_OR_TOKEN = re.compile(r"\b[a-z]{2,6}_[a-z0-9]{26}\b|withheld:[1-9][0-9]*")
_LEFTOVER = re.compile(r"\b[a-z]{2,6}_[a-z0-9]{26}\b|withheld:[1-9][0-9]*")
#: Refs of vocabulary rows no scene needs to tell apart (a level of theory, a literature record, a workflow release,
#: a software release): named by kind. Every other ref a compared document holds must have a label from its role.
VOCABULARY_PREFIXES = frozenset({"lot", "lit", "wtr", "swr", "sw", "wt"})
#: Profile echoes: they differ by profile on purpose.
#: A scene's species are synthetic, and a second scene gets other identity strings from the factory counter: the subject's
#: InChIKey, formula and SMILES say nothing about the selection.
SYNTHETIC_IDENTITY = frozenset({"inchi_key", "molecular_formula", "smiles"})
PROFILE_ECHO_KEYS = frozenset({"profile", "recommendation", "release_ref", "review_floor", "release"})


def list_at(doc, path: str):
    """The (first) list at a declared path such as ``.result.members`` or ``.candidates[].x``; ``None`` when there is none."""
    nodes = [doc]
    steps = [t for t in re.split(r"(?=\.)", path) if t]
    for step in steps:
        key, deeper = step[1:].split("[]", 1)[0], step.endswith("[]")
        nxt = []
        for node in nodes:
            if isinstance(node, dict) and key in node:
                value = node[key]
                nxt.extend(value if deeper and isinstance(value, list) else [value])
        nodes = nxt
    return next((n for n in nodes if isinstance(n, list)), None)


def normal(doc, labels, tokens, collapse=frozenset(), ordered=frozenset()):
    """The document with every ref and token renamed to its role label.

    What is still normalised, and why: checksums and sizes (they move with the bytes), the profile echo and the effective
    review statuses (they differ by profile on purpose), timestamps, random determination keys,
    ``excluded_by_review_withheld`` (the flag that says a curated read withheld a listing), and the ORDER of a list the
    engine sorts by ref (the refs are random strings). Everything else is compared as it stands: population counts,
    requests, versions, policies, the order the engine defines, and every unlabelled ref is an error.
    """
    token_labels = sorted(((t, labels[r]) for r, t in tokens.items()), key=lambda tl: -len(tl[0]))
    by_len = sorted(labels.items(), key=lambda kv: -len(kv[0]))
    leftovers: set[str] = set()

    def rename(text: str) -> str:
        for ref, label in by_len:
            text = text.replace(ref, f"<{label}>")
        for token, label in token_labels:
            text = text.replace(token, f"<{label}>")
        text = TIME.sub("<time>", text)
        for found in set(_LEFTOVER.findall(text)):
            if found.split("_", 1)[0] in VOCABULARY_PREFIXES | collapse:
                text = text.replace(found, f"<{found.split('_', 1)[0]}>")
            else:
                leftovers.add(found)
        return text

    drop = DESCRIBES_THE_READ | {"key"} | SYNTHETIC_IDENTITY

    def walk(v, in_request=False, path=""):
        if isinstance(v, dict):
            return {
                rename(k): walk(x, in_request or k == "request", f"{path}.{k}")
                for k, x in v.items()
                if k not in drop and not (in_request and (k in PROFILE_ECHO_KEYS or k.startswith('profile')))
            }
        if isinstance(v, list):
            items = [walk(x, in_request, f"{path}[]") for x in v]
            if path in ordered:
                items.sort(key=lambda x: json.dumps(x, sort_keys=True))
            return items
        return rename(v) if isinstance(v, str) else v

    out = walk(doc)
    assert not leftovers, f"refs without a label (name them in the scene): {sorted(leftovers)}"
    return out


def differences(a, b, path=""):
    """Where two documents differ, as ``path: left != right`` (the first few are enough to read)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'left' if k in a else 'right'}")
            else:
                out += differences(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}: {a} != {b}"]
        return [d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in differences(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def withhold_hidden_refs(session, refs) -> None:
    """Turn every record of the scene that is not approved from merely-unreviewed into rejected, so the evidence rule
    withholds it (``refs`` is every ref the scene labelled, the identity-position groups and entries included).

    ADR 0023: under curated a record that is only below the floor is linked evidence, shown; "hidden == absent" is the
    property of evidence the profile WITHHOLDS (rejected, deprecated). The scenes build their hidden records unreviewed, so
    this pins them to the withheld state before the curated run. A record reviewed through a parent (a network-kinetics fit,
    through its solve) has no review row of its own and is left to the parent. The loaders' own populations still apply the
    floor (step 5 moves them), so the unlisted state is not claimed here.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.db.models.record_review import RecordReview

    for ref in sorted(refs):
        gated = ref_guard.GATED_TYPES.get(ref.split("_", 1)[0])
        if gated is None or gated.parent_fk is not None:
            continue
        row = session.scalar(select(gated.model).where(gated.model.public_ref == ref))
        existing = session.scalar(
            select(RecordReview).where(RecordReview.record_type == gated.record_type, RecordReview.record_id == row.id)
        )
        if existing is not None and existing.status is APPROVED:
            continue  # the scene's visible records stay as they are
        if existing is None:
            set_review(session, record_type=gated.record_type, record_id=row.id, status=RecordReviewStatus.rejected)
        else:
            existing.status = RecordReviewStatus.rejected
            existing.reviewed_at = existing.reviewed_at or datetime.now(timezone.utc)
            if existing.reviewed_by is None:
                existing.reviewed_by = _reviewer(session)
    session.flush()


def _reviewer(session) -> int:
    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole

    user = AppUser(username=f"hea_reviewer_{uuid4().hex[:8]}", role=AppUserRole.curator)
    session.add(user)
    session.flush()
    return user.id


def assert_hidden_equals_absent(session, scene, captured, keep, *, tokens_expected: bool):
    """Curated over the full world == exploratory over the world without the hidden records."""
    captured.clear()
    full = scene(session, hidden=True)
    withhold_hidden_refs(session, set(full.labels) | set(full.hidden_refs))
    curated_doc = run_under(CURATED, full.run)
    tokens = dict(captured)
    assert bool(tokens) == tokens_expected, tokens
    copy = scene(session, hidden=False)
    exploratory_doc = run_under(EXPLORATORY, copy.run)
    # The full world's real hidden refs are nowhere in the curated document.
    text = json.dumps(curated_doc, default=str)
    for ref in full.hidden_refs:
        assert ref not in text, f"{full.labels[ref]} is named in the curated document"
    ordered = full.ref_sorted
    for declared in ordered:
        assert list_at(curated_doc, declared) is not None and list_at(exploratory_doc, declared) is not None, (
            f"{declared} is declared ref-sorted but is no list in the documents: the declaration is stale"
        )
    left = normal(curated_doc, full.labels, tokens, full.collapse, ordered)
    right = normal(exploratory_doc, copy.labels, {}, copy.collapse, ordered)
    # Identity positions: the exploratory copy names the same record by its real ref, which the labels rename too.
    found = differences(left, right)
    if found:
        pytest.fail("\n".join(d[:300] for d in found[:25]))


def counterpart_labels(full: Scene, copy: Scene) -> None:  # pragma: no cover - documentation of the convention
    """Labels are role names, identical in both scenes: that is what makes the two documents comparable."""


#: Lists the relative-energy engine sorts by a ref (``compute_relative_energies``): the compared groups (``sorted(group_refs)``),
#: the members (ordered by conformer ref, ``NOTE_ORDER``), each member's determination, energy-calculation and witness refs
#: (``tuple(sorted(...))``), each cohort's member and unit refs, the population's determinations and the structure
#: request's member refs (``sorted(determinations)``), and the structure assessment's witness refs.
RELATIVE_REF_SORTED = frozenset(
    {
        ".population.groups",
        ".population.determinations",
        ".structure_request.member_refs",
        ".result.members",
        ".result.members[].determination_refs",
        ".result.members[].energy_calculation_refs",
        ".result.members[].certificate.witness_refs",
        ".result.cohorts[].member_refs",
        ".result.cohorts[].unit_refs",
        ".assessments[].claim.witness_refs",
    }
)
#: Lists the statmech engine sorts by a ref: each candidate's evidence by kind (``tuple(sorted(ev.geometry_sources))``,
#: ``sorted(ev.energy)``, ``sorted(ev.parameters)`` in ``assessment.py``). Only the kinds this scene's documents hold are
#: declared: a declared path that is no list in the documents fails the comparison (a stale declaration).
STATMECH_REF_SORTED = frozenset(
    {
        ".assessments[].evidence.geometry",
    }
)



# ---------------------------------------------------------------------------
# Thermo
# ---------------------------------------------------------------------------


def thermo_scene(session, hidden: bool) -> Scene:
    lot = make_lot(session)
    entry = make_species_entry(session, make_species(session, inchi_key=next_inchi_key("HAT")))
    calc_visible = make_calculation(session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id)
    group = make_conformer_group(session, entry)  # never reviewed: hidden under curated, shown under exploratory
    calc_hidden = (
        make_calculation(
            session,
            type=CalculationType.freq,
            species_entry_id=entry.id,
            lot_id=make_lot(session, method="CBS-QB3", basis=None).id,  # a named composite: it would add a recipe key
        )
        if hidden
        else None
    )
    cited = [{"calculation_ref": calc_visible.public_ref}]
    if calc_hidden is not None:
        cited.append({"calculation_ref": calc_hidden.public_ref})
    if calc_hidden is not None:
        # A live finding about the hidden calculation: it would block the record that rests on it.
        from app.db.models.common import (
            StructureFindingAuthority,
            StructureFindingKind,
            StructureFindingScope,
            StructureFindingVerdict,
        )
        from app.db.models.structure_determination import StructureEvidenceFinding

        session.add(
            StructureEvidenceFinding(
                kind=StructureFindingKind.identity_incompatibility,
                scope=StructureFindingScope.calculation,
                subject_calculation_id=calc_hidden.id,
                verdict=StructureFindingVerdict.invalidates,
                authority=StructureFindingAuthority.authorized_adjudication,
                rationale="a stated reason",
                semantic_version=1,
            )
        )
        session.flush()
    shared, shared_det, shared_obs = shared_source_with_hidden_determination(session, entry, lot=lot, hidden=hidden)
    cites = make_thermo_scalar(session, species_entry=entry, h298_kj_mol=-10.0)
    cites.protocol_declaration = {"version": 1, "supporting_calculations": cited}
    session.flush()
    attach_thermo_source_calculation(session, thermo=cites, calculation=calc_visible, role=ThermoCalculationRole.sp)
    attach_thermo_source_calculation(session, thermo=cites, calculation=shared, role=ThermoCalculationRole.sp)
    if calc_hidden is not None:
        attach_thermo_source_calculation(session, thermo=cites, calculation=calc_hidden, role=ThermoCalculationRole.freq)
    targets_group = make_thermo_scalar(session, species_entry=entry, h298_kj_mol=-11.0)
    targets_group.thermodynamic_target_kind = ThermoTargetKind.single_conformer
    targets_group.target_conformer_group_id = group.id
    only_hidden = make_thermo_scalar(session, species_entry=entry, h298_kj_mol=-12.0) if hidden else None
    session.flush()
    approve(session, R.calculation, calc_visible)
    approve(session, R.calculation, shared)
    approve(session, R.thermo, cites)
    approve(session, R.thermo, targets_group)
    labels = label_all(
        cv=calc_visible, ch=calc_hidden, cites=cites, targets=targets_group, group=group, entry=entry, shared=shared
    )
    labels.update(label_all(shared_det=shared_det, shared_obs=shared_obs))

    def run():
        body = ThermoSelectionRequest.model_validate({"target": {"kind": "equilibrium_ensemble"}})
        from app.services.thermo_selection.models import H298Request

        selection = select_h298(
            session,
            species_entry_id=entry.id,
            request=H298Request(target_kind=body.target.kind),
            require_snapshot=False,
        )
        return thermo_public.redact_manifest(selection.manifest, withhold_excluded=thermo_public.profile_has_floor())

    hidden_refs = [r.public_ref for r in (calc_hidden, only_hidden, shared_obs) if r is not None]
    labels.update({only_hidden.public_ref: "only_hidden"} if only_hidden is not None else {})
    return Scene(run=run, labels=labels, hidden_refs=hidden_refs)


def test_thermo_hidden_equals_absent(db_session, captured):
    """Kills: ``unavailable_evidence`` removed from the thermo loader (a hidden supporting calculation counts again);
    the hidden calculation left in the source-link recipe keys; the hidden population record counted."""
    assert_hidden_equals_absent(
        db_session, thermo_scene, captured, {"candidates", "decision", "outcome", "disclosures"}, tokens_expected=True
    )


# ---------------------------------------------------------------------------
# Kinetics
# ---------------------------------------------------------------------------

KINETICS_BODY = {
    "direction": "forward",
    "target": {"kind": "whole_reaction"},
    "coefficient_basis": "elementary_coefficient",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure": {"kind": "independent"},
}


def kinetics_scene(session, hidden: bool) -> Scene:
    from app.db.models.common import ArrheniusAUnits, NetworkChannelKind, NetworkKineticsModelKind, NetworkStateKind
    from tests.services.scientific_read._factories import (
        attach_network_kinetics_chebyshev,
        make_network,
        make_network_channel,
        make_network_kinetics,
        make_network_solve,
        make_network_state,
    )

    lot = make_lot(session)
    lit = make_literature(session)
    r1 = make_species_entry(session, make_species(session, inchi_key=next_inchi_key("HAKR")))
    p1 = make_species_entry(session, make_species(session, inchi_key=next_inchi_key("HAKP")))
    reaction = make_chem_reaction(session, reactants=[r1.species], products=[p1.species], reversible=False)
    rxe = make_reaction_entry(session, reaction=reaction, reactant_entries=[r1], product_entries=[p1])
    tse = make_transition_state_entry(session, transition_state=make_transition_state(session, reaction_entry=rxe))
    calc_visible = make_calculation(session, type=CalculationType.sp, transition_state_entry_id=tse.id, lot_id=lot.id)
    calc_hidden = (
        make_calculation(session, type=CalculationType.sp, transition_state_entry_id=tse.id, lot_id=lot.id) if hidden else None
    )
    solve = fit = None
    if hidden:
        net = make_network(session, name="hak-net")
        states = [
            make_network_state(session, network=net, kind=NetworkStateKind.well, composition_hash=h * 64, label=h)
            for h in ("a", "b")
        ]
        channel = make_network_channel(
            session,
            network=net,
            source_state=states[0],
            sink_state=states[1],
            kind=NetworkChannelKind.isomerization,
            channel_key="ch-hak",
        )
        solve = make_network_solve(session, network=net, literature_id=lit.id)  # never reviewed: hidden
        fit = make_network_kinetics(
            session,
            channel=channel,
            solve=solve,
            model_kind=NetworkKineticsModelKind.chebyshev,
            rate_units=ArrheniusAUnits.cm3_mol_s,
        )
        attach_network_kinetics_chebyshev(session, kinetics=fit)
    shared, shared_det, shared_obs = shared_source_with_hidden_determination(session, r1, lot=lot, hidden=hidden)
    kin = make_kinetics(session, reaction_entry=rxe)
    cited = [{"calculation_ref": calc_visible.public_ref, "purpose": "electronic_energy"}]
    if calc_hidden is not None:
        cited.append({"calculation_ref": calc_hidden.public_ref, "purpose": "electronic_energy"})
    kin.protocol_declaration = {"version": 1, "supporting_calculations": cited}
    session.add(KineticsSourceCalculation(kinetics_id=kin.id, calculation_id=calc_visible.id, role=KineticsCalculationRole.ts_energy))
    session.add(KineticsSourceCalculation(kinetics_id=kin.id, calculation_id=shared.id, role=KineticsCalculationRole.reactant_energy))
    if calc_hidden is not None:
        session.add(
            KineticsSourceCalculation(kinetics_id=kin.id, calculation_id=calc_hidden.id, role=KineticsCalculationRole.reactant_energy)
        )
    determination = KineticsDetermination(
        reaction_entry_id=rxe.id,
        direction=KineticsDirection.forward,
        target_kind=KineticsDeterminationTargetKind.resolved_channel,
        target_transition_state_entry_id=tse.id,  # the target: identity, never reviewed (hidden under curated)
        literature_id=lit.id,
        determination_key="hak-det",
        identity_hash=uuid4().hex * 2,
    )
    session.add(determination)
    session.flush()
    kin.determination_id = determination.id
    kin.representation_role = KineticsRepresentationRole.complete
    if fit is not None:
        kin.network_kinetics_id = fit.id
    only_hidden = make_kinetics(session, reaction_entry=rxe, a=3.0e-12) if hidden else None
    session.flush()
    approve(session, R.calculation, calc_visible)
    approve(session, R.calculation, shared)
    approve(session, R.kinetics, kin)
    labels = label_all(
        cv=calc_visible, ch=calc_hidden, kin=kin, tse=tse, solve=solve, only_hidden=only_hidden, rxe=rxe, r1=r1, p1=p1,
        determination=determination, shared=shared,
    )
    labels.update(label_all(shared_det=shared_det, shared_obs=shared_obs))
    if fit is not None:
        labels[fit.public_ref] = "fit"

    def run():
        body = KineticsSelectionRequest.model_validate(KINETICS_BODY)
        selection = select_reaction_entry_kinetics(
            session, reaction_entry_id=rxe.id, request=kinetics_public.to_service_request(body), require_snapshot=False
        )
        return kinetics_public.redact_manifest(selection.manifest, withhold_excluded=kinetics_public.profile_has_floor())

    hidden_refs = [r.public_ref for r in (calc_hidden, solve, fit, only_hidden, shared_obs) if r is not None]
    return Scene(run=run, labels=labels, hidden_refs=hidden_refs)


def test_kinetics_hidden_equals_absent(db_session, captured):
    """Kills: ``unavailable_evidence`` removed from the kinetics loader (a hidden supporting calculation, source link or
    solve counts again); the network context kept when its solve is hidden; the trust evidence view removed."""
    assert_hidden_equals_absent(
        db_session,
        kinetics_scene,
        captured,
        {"candidates", "determinations", "decision", "outcome", "disclosures"},
        tokens_expected=True,
    )


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


def network_scene(session, hidden: bool) -> Scene:
    from tests.services.network_selection._world import add_solve, build_world, fit_spec, validity

    ns = build_world(session, actor_name=f"ns-{uuid4().hex[:8]}")
    other = add_solve(session, ns, fits=[fit_spec("assoc", det="hid")], review=None) if hidden else None
    cited = other.public_ref if other is not None else "nsolve_" + "z" * 26  # a reference to nothing
    visible = add_solve(
        session,
        ns,
        fits=[fit_spec("assoc", det="vis")],
        validation={"version": 1, "entries": [{"kind": "model_fidelity", "domain": validity(), "reference_solve_ref": cited}]},
        review=RecordReviewStatus.approved,
    )
    labels = label_all(net=ns.network, visible=visible, other=other)
    labels[cited] = "cited"
    for solve_label, solve_row in (("visible", visible), ("other", other)):
        if solve_row is not None:
            labels.update({d.public_ref: f"det:{solve_label}:{k}" for k, d in solve_row._dets.items()})
            labels.update({f.public_ref: f"fit:{solve_label}:{i}" for i, f in enumerate(solve_row._fits)})

    def run():
        from app.schemas.reads.scientific_network_selection import NetworkSelectionRequest
        from app.services.network_selection import public as network_public
        from app.services.network_selection.selection import select_network

        body = NetworkSelectionRequest.model_validate(
            {
                "scope": "single_channel",
                "channel_key": "assoc",
                "observable": "product_resolved_coefficient",
                "coefficient_basis": "kernel",
                "temperature_min_k": 500.0,
                "temperature_max_k": 1500.0,
                "pressure_min_bar": 0.5,
                "pressure_max_bar": 5.0,
                "bath": {"components": [{"species_ref": ns.ar.public_ref}]},
                "partition": {"retained": sorted(ns.hashes.values())},
            }
        )
        selection = select_network(
            session, request=network_public.to_service_request(body, network_ref=ns.ref), require_snapshot=False
        )
        return network_public.redact_manifest(selection.manifest, withhold_excluded=network_public.profile_has_floor())

    # The network is a subject the route has already opened: approve it so the seam keeps its own ref.
    approve(session, R.network, ns.network)
    # The network world's species entries are scoping parents (never hidden), the same in both worlds: named by kind.
    return Scene(
        run=run, labels=labels, hidden_refs=[other.public_ref] if other is not None else [], collapse=frozenset({"spe"})
    )


def test_network_hidden_equals_absent(db_session, captured):
    """Kills: the loader's unavailable-reference step removed (the hidden solve is named, or counts as stated evidence).

    A hidden solve and a reference to nothing read the same: ``reference_solve_ref`` null and ``reference_withheld``."""
    assert_hidden_equals_absent(
        db_session, network_scene, captured, {"solves", "assessments", "decision", "outcome", "network"}, tokens_expected=False
    )


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def structure_scene(session, hidden: bool, grain: str = "conformer") -> Scene:
    from app.services.structure_selection.selection import select_entry_structures
    from tests.services.structure_selection._support import make_world, minimum_request
    from tests.services.structure_selection._support import request as calculation_request
    from tests.services.structure_selection.test_service import build_basin

    world = make_world(session)
    # A conformer group is a gated kind: a basin whose observation sits in a hidden group hides with it (see
    # ``test_a_basin_in_a_group_below_the_curated_floor_hides_with_the_group_and_is_not_mentioned``), so the group is
    # approved here and this scene proves the one that remains: a hidden dependency parent of a visible basin.
    group = make_conformer_group(session, world.entry)
    obs = make_conformer_observation(session, conformer_group=group)
    det, opt, sp, freq = build_basin(
        world,
        energy=-76.4,
        observation=obs,
        target=StructureDeterminationTargetKind.conformer_basin,
        statuses={"opt": None},  # approved below, once the edge to its hidden parent exists
    )
    parent = None
    if hidden:
        parent = world.opt("hidden_parent", -76.5, status=None)  # no review: hidden under curated
        attach_dependency(session, parent=parent, child=opt, role=CalculationDependencyRole.optimized_from)
    world.settle()
    approve(session, R.calculation, opt)
    approve(session, R.conformer_observation, obs)  # last: nothing may be added under an accepted observation
    approve(session, R.conformer_group, group)
    labels = label_all(det=det, group=group, obs=obs, parent=parent) | world_labels(world)

    def run():
        return select_entry_structures(
            session,
            entry_id=world.entry.id,
            request=minimum_request() if grain == "conformer" else calculation_request(),
            require_snapshot=False,
        ).manifest

    return Scene(run=run, labels=labels, hidden_refs=[parent.public_ref] if parent is not None else [])


def test_structure_hidden_equals_absent(db_session, captured):
    """Kills: the structure seam removed (the basin's conformer group is named); a hidden dependency parent counted."""
    assert_hidden_equals_absent(
        db_session,
        structure_scene,
        captured,
        {"calculations", "determinations", "assessments", "decision", "outcome"},
        tokens_expected=False,  # a dropped dependency edge leaves no token: nothing names the parent
    )


# ---------------------------------------------------------------------------
# Relative conformer energies
# ---------------------------------------------------------------------------


def relative_scene(session, hidden: bool) -> Scene:
    from app.services.relative_conformer_energy import (
        Certificate,
        EnergyKind,
        LevelSelector,
        RelativeEnergyRequest,
        run_relative_energies,
    )
    from tests.services.structure_selection._support import make_world
    from tests.services.structure_selection.test_service import build_basin

    world = make_world(session)

    def conformer(energy, *, approved):
        group = make_conformer_group(session, world.entry)
        obs = make_conformer_observation(session, conformer_group=group)
        det, *_ = build_basin(
            world, energy=energy, observation=obs, target=StructureDeterminationTargetKind.conformer_basin
        )
        dets[f"d{len(dets)}"] = det
        approvals.extend(((R.conformer_group, group), (R.conformer_observation, obs)) if approved else ())
        return group, obs

    approvals: list = []
    dets: dict = {}

    shown, shown_obs = conformer(-76.400, approved=True)
    other, other_obs = conformer(-76.401, approved=True)
    away = conformer(-76.402, approved=False) if hidden else None  # a whole hidden group, with its basin
    world.settle()
    for record_type, row in approvals:
        approve(session, record_type, row)
    labels = label_all(shown=shown, other=other, shown_obs=shown_obs, other_obs=other_obs) | world_labels(world)
    labels.update({d.public_ref: f"sdet:{name}" for name, d in dets.items()})
    if away is not None:
        labels.update(label_all(away=away[0], away_obs=away[1]))

    def run():
        request = RelativeEnergyRequest(
            reference_conformer_ref=shown.public_ref,
            energy_kind=EnergyKind.electronic_energy,
            level=LevelSelector(level_of_theory_ref=world.lot.public_ref),
            certificate=Certificate.local_minimum_any_surface,
        )
        return run_relative_energies(session, entry_id=world.entry.id, request=request, require_snapshot=False).manifest

    return Scene(
        run=run,
        labels=labels,
        hidden_refs=[away[0].public_ref, away[1].public_ref] if away else [],
        ref_sorted=RELATIVE_REF_SORTED,
    )


def test_relative_energy_hidden_equals_absent(db_session, captured):
    """A hidden conformer group is not in the compared world at all, with its basin and its calculations.

    Kills: the population filter removed (the hidden group is a member, absent or valued)."""
    assert_hidden_equals_absent(
        db_session,
        relative_scene,
        captured,
        {"calculations", "determinations", "assessments", "result", "outcome"},
        tokens_expected=False,
    )


# ---------------------------------------------------------------------------
# Statmech
# ---------------------------------------------------------------------------


def statmech_scene(session, hidden: bool) -> Scene:
    from tests.services.structure_selection._support import make_world

    world = make_world(session)
    shown = make_conformer_group(session, world.entry)
    approve(session, R.conformer_group, shown)
    away = make_conformer_group(session, world.entry)  # never reviewed: hidden under curated
    away_obs = make_conformer_observation(session, conformer_group=away) if hidden else None
    opt = world.opt("opt", -76.4, observation=away_obs)
    freq = world.freq("freq", frequencies=(100.0, 200.0, 300.0))
    legacy = make_statmech(session, species_entry=world.entry, optical_isomers=1, external_symmetry=2)
    attach_statmech_source_calculation(session, statmech=legacy, calculation=opt, role=StatmechCalculationRole.opt)
    attach_statmech_source_calculation(session, statmech=legacy, calculation=freq, role=StatmechCalculationRole.freq)
    member = make_statmech(session, species_entry=world.entry, optical_isomers=1, external_symmetry=2)
    ensemble = make_statmech(session, species_entry=world.entry, optical_isomers=1, external_symmetry=2)
    declaration = StatmechModelDeclaration(
        statmech_id=ensemble.id,
        schema_version=1,
        scope_kind=StatmechModelScopeKind.explicit_equilibrium_ensemble,
        literature_id=make_literature(session).id,
        recipe=dict(BASE_RECIPE),
        content_hash="e" * 64,
    )
    session.add(declaration)
    session.flush()
    session.add(
        StatmechModelMember(
            declaration_id=declaration.id,
            member_key="m1",
            member_statmech_id=member.id,
            conformer_group_id=away.id if hidden else None,  # the member's own group: unavailable, or never stated
            counting_multiplicity=1,
            relative_energy_kj_mol=0.0,
        )
    )
    session.add(
        StatmechModelSource(
            declaration_id=declaration.id,
            source_key="geom",
            role=StatmechModelSourceRole.geometry,
            calculation_id=opt.id,
        )
    )
    session.flush()
    world.settle()
    for row in (legacy, member, ensemble):
        approve(session, R.statmech, row)
    labels = label_all(legacy=legacy, member=member, ensemble=ensemble, shown=shown) | world_labels(world)
    labels[declaration.public_ref] = "declaration"
    if hidden:
        labels.update(label_all(away=away, away_obs=away_obs))
    else:
        labels[away.public_ref] = "away"  # created in both worlds but unused: kept out of the comparison
    body = make(THERMAL, target__species_entry_ref=world.entry.public_ref, target__scope__conformer_group_ref=shown.public_ref)

    def run():
        request = StatmechSelectionRequest.model_validate(body).service_request()
        return select_statmech(session, request=request, owner_id=world.entry.id, require_snapshot=False).manifest

    return Scene(
        run=run,
        labels=labels,
        hidden_refs=[away.public_ref, away_obs.public_ref] if hidden else [],
        ref_sorted=STATMECH_REF_SORTED,
    )


def test_statmech_hidden_equals_absent(db_session, captured):
    """A record anchored only in a hidden group reads as unanchored; a member declared in one reads as stating no group.

    Kills: the unavailable-group filter removed from the anchors or the members."""
    assert_hidden_equals_absent(
        db_session,
        statmech_scene,
        captured,
        {"candidates", "calculations", "assessments", "decision", "outcome"},
        tokens_expected=False,
    )


def test_structure_calculation_grain_hidden_equals_absent(db_session, captured):
    """The calculation grain (``/calculations/select``): the same engine over single calculations.

    Kills: the structure seam removed; a hidden dependency parent counted."""
    from functools import partial

    assert_hidden_equals_absent(
        db_session,
        partial(structure_scene, grain="calculation"),
        captured,
        {"calculations", "determinations", "assessments", "decision", "outcome"},
        tokens_expected=False,
    )


def structure_ts_scene(session, hidden: bool) -> Scene:
    """The transition-state grain (``/evidence/select``): a saddle with connectivity evidence from a hidden IRC."""
    from app.db.models.transition_state import TransitionStateValidationEvidence
    from app.services.structure_selection.selection import select_entry_structures
    from tests.services.structure_selection._support import declaration, make_ts_world, saddle_request

    world = make_ts_world(session, entry_status=None)  # an approved entry is frozen: approved below, last
    opt = world.opt("o", -40.2, declared=declaration(), status=None)
    sp = world.sp("s", -40.3, declared=declaration())
    freq = world.freq("f", frequencies=[-1500.0, 100.0, 200.0], declared=declaration())
    det = world.determination(
        [("geometry_optimization", opt), ("energy", sp), ("curvature", freq)],
        target=StructureDeterminationTargetKind.saddle_point,
    )
    parent = irc = None
    if hidden:
        parent = world.opt("hidden_parent", -40.5, status=None)
        attach_dependency(session, parent=parent, child=opt, role=CalculationDependencyRole.optimized_from)
        irc = world.opt("hidden_irc", None, status=None)
        session.add(
            TransitionStateValidationEvidence(
                transition_state_entry_id=world.entry.id,
                kind="irc",
                passed=True,
                rationale="IRC connects the declared wells",
                reconstruction_calculation_id=irc.id,
            )
        )
        session.flush()
    world.settle()
    approve(session, R.calculation, opt)
    approve(session, R.transition_state_entry, world.entry)
    labels = label_all(det=det, parent=parent, irc=irc) | world_labels(world)

    def run():
        return select_entry_structures(
            session, entry_id=world.entry.id, request=saddle_request(require_connectivity=True), require_snapshot=False
        ).manifest

    return Scene(run=run, labels=labels, hidden_refs=[r.public_ref for r in (parent, irc) if r is not None])


def test_structure_transition_state_grain_hidden_equals_absent(db_session, captured):
    """A hidden IRC is no connectivity evidence, and a hidden dependency parent is no parent.

    Kills: the loader's profile filter on the evidence row's calculation; a hidden parent counted."""
    assert_hidden_equals_absent(
        db_session,
        structure_ts_scene,
        captured,
        {"calculations", "determinations", "assessments", "decision", "outcome"},
        tokens_expected=False,
    )


# ---------------------------------------------------------------------------
# Source findings: a determination hidden only because its transition-state entry is hidden
# ---------------------------------------------------------------------------


def hidden_entry_determination_scene(session, hidden: bool) -> Scene:
    """K uses calculation C (owned by a transition-state entry nobody has reviewed) as its energy source.

    With ``hidden``, a saddle determination D of that entry pins C too, and carries a live ``invalidates`` finding. D has
    NO conformer observation, so only its entry decides whether it is hidden: this is the filter's entry branch alone.
    """
    from app.db.models.common import StructureFindingAuthority, StructureFindingKind, StructureFindingVerdict
    from tests.services.structure_selection._support import make_ts_world

    world = make_ts_world(session, entry_status=None)  # the entry is never reviewed: hidden under curated
    calc = world.sp("c", -40.3)
    determination = None
    if hidden:
        determination = world.determination([("energy", calc)], target=StructureDeterminationTargetKind.saddle_point)
        world.finding(
            kind=StructureFindingKind.identity_incompatibility,
            verdict=StructureFindingVerdict.invalidates,
            authority=StructureFindingAuthority.authorized_adjudication,
            determination=determination,
        )
    world.settle()
    rxe = world.entry.transition_state.reaction_entry
    kin = make_kinetics(session, reaction_entry=rxe)
    session.add(
        KineticsSourceCalculation(kinetics_id=kin.id, calculation_id=calc.id, role=KineticsCalculationRole.ts_energy)
    )
    session.flush()
    approve(session, R.kinetics, kin)
    labels = label_all(kin=kin, rxe=rxe, determination=determination) | world_labels(world)
    labels[world.entry.public_ref] = "tse"

    def run():
        body = KineticsSelectionRequest.model_validate(KINETICS_BODY)
        selection = select_reaction_entry_kinetics(
            session, reaction_entry_id=rxe.id, request=kinetics_public.to_service_request(body), require_snapshot=False
        )
        return kinetics_public.redact_manifest(selection.manifest, withhold_excluded=kinetics_public.profile_has_floor())

    return Scene(run=run, labels=labels, hidden_refs=[], collapse=frozenset({"spe"}))


def test_a_determination_hidden_only_by_its_transition_state_entry_blocks_nothing(db_session, captured):
    """Kills: the entry condition removed from ``_determinations_the_profile_shows`` (the finding on D blocks K)."""
    assert_hidden_equals_absent(db_session, hidden_entry_determination_scene, captured, None, tokens_expected=False)
    # The premise: where the finding IS visible (the full world under exploratory) K is blocked, so the test can fail.
    seen = run_under(EXPLORATORY, hidden_entry_determination_scene(db_session, hidden=True).run)
    assert any(c["assessment"]["blocking"] for c in seen["candidates"])


def test_only_a_declared_list_is_compared_as_a_set():
    """An undeclared list is compared in order, so a reversed tie order is a difference; a declared one is a set."""
    labels = {"calc_" + "a" * 26: "a", "calc_" + "b" * 26: "b"}
    forward = {"order": ["calc_" + "a" * 26, "calc_" + "b" * 26]}
    backward = {"order": ["calc_" + "b" * 26, "calc_" + "a" * 26]}
    assert differences(normal(forward, labels, {}), normal(backward, labels, {}))
    declared = frozenset({".order"})
    assert not differences(normal(forward, labels, {}, ordered=declared), normal(backward, labels, {}, ordered=declared))
