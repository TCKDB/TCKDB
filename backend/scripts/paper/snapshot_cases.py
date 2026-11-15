"""Generators for the worked cases of the demonstration snapshot (``scripts/paper_snapshot``).

* ``use_case_ch4_oh``: the computed rate coefficient of C + [OH] => O + [CH3], its transition state and k(300 K).
* ``ch3_enthalpy_of_formation``: every thermo record of [CH3] and the H298 selection answer with a level filter.
* ``network_n2h3``: wells, channels and k(T, P) forms of each stored network, and k(1000 K, 1 bar) of NH3N -> N2H3.
* ``chemkin_reconciliation``: reactions of the committed formic acid model file against what was stored.
* ``species_thermo_sources``: species with thermo from more than one source, with H298, S298 and Cp(300 K).
* ``caught_error``: rejected records, the structure-evidence finding behind them and the kinetics selection answer.
* ``dataset_release``: each release's handle, selections, shipped candidates and manifest.

Every subject is found by content (SMILES, multiplicity, InChI key, fixture coefficients), never by a public ref
or database id. Rows are ordered by content; public refs appear only as values; no wall-clock value is emitted.
Derived floating-point values are rounded to six significant figures.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation, CalculationFreqMode
from app.db.models.common import (
    DatasetReleaseStatus,
    KineticsModelKind,
    NetworkStateKind,
    RecordReviewStatus,
    ReleaseSelectionAction,
    SubmissionRecordType,
    ThermoTargetKind,
)
from app.db.models.dataset_release import DatasetRelease, ReleaseSelection
from app.db.models.deposit_warning import DepositWarning
from app.db.models.kinetics import Kinetics, KineticsSourceCalculation
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkKinetics, NetworkState
from app.db.models.reaction import ChemReaction, ReactionEntry, ReactionParticipant
from app.db.models.record_review import RecordReview
from app.db.models.species import Species, SpeciesEntry
from app.db.models.structure_determination import StructureEvidenceFinding
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.thermo import Thermo, ThermoNASA, ThermoSourceCalculation
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.workflow import WorkflowToolRelease
from scripts.paper.generators import (
    MECHANISM_FILE,
    MECHANISM_FIXTURE_DIR,
    _parse_fixture_mechanism,
    _standing_selections,
)
from scripts.paper.snapshot_generators import (
    _content_key,
    _equation,
    _level_label,
    _selection_session,
    _side,
    _software_label,
)

USE_CASE_REACTANTS = ("C", "[OH]")
USE_CASE_PRODUCTS = ("O", "[CH3]")
USE_CASE_TEMPERATURE_K = 300.0
CH3 = ("[CH3]", 2)
ENERGY_METHOD = "dlpno-ccsd(t)-f12"
NETWORK_SOURCE_WELL = ("[N-][NH3+]",)
NETWORK_SINK_WELL = ("[NH]N",)
NETWORK_TEMPERATURE_K = 1000.0
NETWORK_PRESSURE_BAR = 1.0
H298_TEMPERATURE_K = 298.15
CP_TEMPERATURE_K = 300.0

KINETICS_QUESTION = {
    "direction": "forward",
    "target": {"kind": "whole_reaction"},
    "coefficient_basis": "elementary_coefficient",
    "temperature_min_k": 300.0,
    "temperature_max_k": 1000.0,
    "pressure": {"kind": "high_pressure_limit"},
}

ARRHENIUS_FORMS = (KineticsModelKind.arrhenius, KineticsModelKind.modified_arrhenius)
RUN_IDENTITY_NOTE = (
    "The ARC run directory is not stored. A computed record is identified by the computed_reaction submission that "
    "deposited it: the reaction and transition-state label deposited in that submission and the ARC commit."
)
FIXTURE_NOTE_SOURCE = "chem_annotated.yaml species thermo note (committed fixture; TCKDB stores no such note)"


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _v(value: Any) -> Any:
    """An enum member's value; anything else unchanged."""
    return getattr(value, "value", value)


def _sig(value: float | None, digits: int = 6) -> float | None:
    """``value`` rounded to ``digits`` significant figures, or None."""
    if value is None:
        return None
    return float(f"{value:.{digits}g}")


def _species_key(species: Species) -> tuple:
    return (species.inchi_key, species.multiplicity)


def _side_keys(reaction: ChemReaction, role: str) -> tuple:
    keys: list[tuple] = []
    for participant in reaction.participants:
        if _v(participant.role) == role:
            keys += [_species_key(participant.species)] * participant.stoichiometry
    return tuple(sorted(keys))


def _reactions_with(session: Session, reactants: tuple[str, ...], products: tuple[str, ...]) -> list[ChemReaction]:
    """Stored reactions whose reactant and product SMILES are exactly these, in this direction."""
    wanted = (tuple(sorted(reactants)), tuple(sorted(products)))
    species_ids = list(session.scalars(select(Species.id).where(Species.smiles == reactants[0])))
    if not species_ids:
        return []
    reaction_ids = set(
        session.scalars(select(ReactionParticipant.reaction_id).where(ReactionParticipant.species_id.in_(species_ids)))
    )
    found = []
    for reaction in session.scalars(select(ChemReaction).where(ChemReaction.id.in_(reaction_ids))):
        sides = (
            tuple(sorted(_side(reaction, "reactant").split(" + "))),
            tuple(sorted(_side(reaction, "product").split(" + "))),
        )
        if sides == wanted:
            found.append(reaction)
    return sorted(found, key=lambda r: (_equation(r), r.reversible))


def _review_status(session: Session, record_type: SubmissionRecordType, record_id: int) -> str:
    review = session.scalars(
        select(RecordReview).where(RecordReview.record_type == record_type, RecordReview.record_id == record_id)
    ).first()
    return _v(review.status) if review is not None else RecordReviewStatus.not_reviewed.value


def _submissions_of(session: Session, record_type: SubmissionRecordType, record_id: int) -> list[Submission]:
    links = session.scalars(
        select(SubmissionRecordLink).where(
            SubmissionRecordLink.record_type == record_type, SubmissionRecordLink.record_id == record_id
        )
    )
    unique = {link.submission_id: link.submission for link in links}
    return [unique[key] for key in sorted(unique)]


def _workflow_tool(release: WorkflowToolRelease | None) -> dict[str, Any] | None:
    if release is None:
        return None
    return {"name": release.workflow_tool.name, "version": release.version, "git_commit": release.git_commit}


def _lot_label(session: Session, lot_id: int | None) -> str | None:
    return _level_label(session.get(LevelOfTheory, lot_id)) if lot_id is not None else None


def _calculation_owner(calc: Calculation) -> str:
    if calc.species_entry is not None:
        species = calc.species_entry.species
        return f"{species.smiles} (multiplicity {species.multiplicity})"
    if calc.transition_state_entry is not None:
        return "transition state of " + (_equation(calc.transition_state_entry.transition_state.reaction_entry.reaction) or "")
    return "(no owner)"


def _calculation_row(calc: Calculation, role: str | None = None) -> dict[str, Any]:
    row = {
        "calculation_ref": calc.public_ref,
        "type": _v(calc.type),
        "level_of_theory": _level_label(calc.lot),
        "software": _software_label(calc.software_release),
        "subject": _calculation_owner(calc),
    }
    if role is not None:
        row = {"role": role, **row}
    return row


def _imaginary_frequencies(session: Session, calc: Calculation) -> list[float]:
    return sorted(
        session.scalars(
            select(CalculationFreqMode.frequency_cm1).where(
                CalculationFreqMode.calculation_id == calc.id, CalculationFreqMode.is_imaginary.is_(True)
            )
        )
    )


def _warning_counts(session: Session, submissions: list[Submission]) -> dict[str, Any]:
    """Stored deposit warnings of these submissions, counted by code, record type and field."""
    if not submissions:
        return {"warnings_total": 0, "distinct_codes": 0, "by_code": []}
    rows = session.scalars(
        select(DepositWarning).where(
            DepositWarning.submission_id.in_([s.id for s in submissions]), DepositWarning.code.is_not(None)
        )
    )
    counts: Counter[tuple] = Counter()
    for warning in rows:
        record_type = getattr(warning.record_type, "value", warning.record_type) or "(submission)"
        counts[(warning.code, record_type, warning.field or "")] += 1
    by_code = [
        {"code": code, "record_type": record_type, "field": field or None, "count": n}
        for (code, record_type, field), n in sorted(counts.items())
    ]
    return {"warnings_total": sum(counts.values()), "distinct_codes": len({k[0] for k in counts}), "by_code": by_code}


def _submission_context(session: Session, submissions: list[Submission]) -> dict[str, Any]:
    """What a submission deposited besides the record: its reactions and transition-state labels."""
    reactions: set[str] = set()
    ts_labels: set[str] = set()
    kinds: set[str] = set()
    for submission in submissions:
        kinds.add(getattr(submission.submission_kind, "value", submission.submission_kind) or "")
        for link in submission.record_links:
            if link.record_type is SubmissionRecordType.reaction_entry:
                entry = session.get(ReactionEntry, link.record_id)
                if entry is not None:
                    reactions.add(_equation(entry.reaction) or "")
            elif link.record_type is SubmissionRecordType.transition_state:
                ts = session.get(TransitionState, link.record_id)
                if ts is not None and ts.label:
                    ts_labels.add(ts.label)
    return {
        "submission_kinds": sorted(kinds),
        "reactions_deposited_with_it": sorted(reactions),
        "transition_state_labels": sorted(ts_labels),
    }


def _thermo_value(thermo: Thermo, quantity: str, temperature: float) -> dict[str, Any]:
    """H (kJ/mol), S or Cp (J/mol/K) of a thermo record at ``temperature`` and the representation it came from."""
    from app.services.consistency.engine import evaluate

    scalar = {"h": thermo.h298_kj_mol, "s": thermo.s298_j_mol_k}.get(quantity)
    if scalar is not None and temperature == H298_TEMPERATURE_K:
        return {"value": scalar, "from": "stored 298.15 K scalar"}
    point = next((p for p in thermo.points if p.temperature_k == temperature), None)
    column = {"h": "h_kj_mol", "s": "s_j_mol_k", "cp": "cp_j_mol_k"}[quantity]
    if point is not None and getattr(point, column) is not None:
        return {"value": getattr(point, column), "from": f"stored tabulated point at {temperature:g} K"}
    if thermo.nasa is not None:
        value, reason = evaluate(thermo, "nasa7", temperature, quantity)
        if value is not None:
            return {"value": _sig(value), "from": f"NASA7 polynomial evaluated at {temperature:g} K"}
        return {"value": None, "from": f"NASA7 polynomial not evaluable: {reason}"}
    return {"value": None, "from": "no representation gives this value"}


def _fixture_thermo_notes() -> dict[str, str]:
    """Species name -> thermo note of the committed formic acid model's Cantera YAML."""
    import yaml

    text = (MECHANISM_FIXTURE_DIR / "chem_annotated.yaml").read_text(encoding="utf-8")
    start = text.find("\nspecies:\n")
    if start < 0:
        return {}
    block = yaml.load(text[start + 1 :], Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}
    return {
        item["name"]: str((item.get("thermo") or {}).get("note") or "").strip()
        for item in block.get("species") or []
        if isinstance(item, dict) and "name" in item
    }


def _nasa_key_from_entry(entry) -> tuple:
    return (entry.t_low, entry.t_common, entry.t_high, *entry.coeffs_low, *entry.coeffs_high)


def _nasa_key_from_row(nasa: ThermoNASA) -> tuple:
    return (
        nasa.t_low,
        nasa.t_mid,
        nasa.t_high,
        *(getattr(nasa, f"a{i}") for i in range(1, 8)),
        *(getattr(nasa, f"b{i}") for i in range(1, 8)),
    )


def _fixture_thermo_by_label(session: Session, mechanism) -> dict[str, Thermo]:
    """Fixture species name -> the stored thermo record whose NASA7 coefficients are identical to the file's."""
    by_key: dict[tuple, list[Thermo]] = defaultdict(list)
    for nasa, thermo in session.execute(select(ThermoNASA, Thermo).join(Thermo, Thermo.id == ThermoNASA.thermo_id)):
        by_key[_nasa_key_from_row(nasa)].append(thermo)
    out: dict[str, Thermo] = {}
    for name, entry in mechanism.thermo.items():
        hits = by_key.get(_nasa_key_from_entry(entry), [])
        if len(hits) == 1:
            out[name] = hits[0]
    return out


def _fixture_notes_by_thermo(session: Session) -> dict[int, str]:
    """Stored thermo record -> the committed model's thermo note for the species it was deposited from."""
    mechanism = _parse_fixture_mechanism()
    notes = _fixture_thermo_notes()
    return {thermo.id: notes.get(name, "") for name, thermo in _fixture_thermo_by_label(session, mechanism).items()}


def _thermo_level(session: Session, thermo: Thermo, fixture_notes: dict[int, str]) -> dict[str, Any]:
    """The level of theory a thermo record states, or, for a fixture model record, the model's thermo note."""
    if thermo.id in fixture_notes:
        return {"level_of_theory": None, "model_thermo_note": fixture_notes[thermo.id], "note_source": FIXTURE_NOTE_SOURCE}
    roles = sorted(
        {
            (_v(link.role), _level_label(link.calculation.lot))
            for link in session.scalars(select(ThermoSourceCalculation).where(ThermoSourceCalculation.thermo_id == thermo.id))
        }
    )
    return {
        "level_of_theory": _lot_label(session, thermo.energy_level_of_theory_id),
        "source_calculation_levels": [{"role": role, "level_of_theory": level} for role, level in roles],
    }


def _source_label(thermo_or_kinetics) -> str:
    release = thermo_or_kinetics.workflow_tool_release
    if release is not None:
        return release.workflow_tool.name
    if thermo_or_kinetics.literature is not None:
        return "literature " + (thermo_or_kinetics.literature.doi or thermo_or_kinetics.literature.title or "")
    return "(no source stated)"


def _released_in(session: Session, record_type: SubmissionRecordType, record_id: int) -> list[str]:
    tags = []
    for release in session.scalars(select(DatasetRelease).where(DatasetRelease.status != DatasetReleaseStatus.draft)):
        for selection in _standing_selections(session, release):
            if selection.record_type is record_type and selection.record_id == record_id:
                tags.append(release.tag)
    return sorted(set(tags))


# ---------------------------------------------------------------------------
# use_case_ch4_oh
# ---------------------------------------------------------------------------


def _rate_at(kinetics: Kinetics, temperature_k: float) -> dict[str, Any]:
    from app.chemistry.arrhenius import arrhenius_k

    in_range = None
    if kinetics.tmin_k is not None and kinetics.tmax_k is not None:
        in_range = kinetics.tmin_k <= temperature_k <= kinetics.tmax_k
    out: dict[str, Any] = {
        "temperature_k": temperature_k,
        "units": getattr(kinetics.a_units, "value", kinetics.a_units),
        "fit_tmin_k": kinetics.tmin_k,
        "fit_tmax_k": kinetics.tmax_k,
        "within_fit_temperature_range": in_range,
    }
    if kinetics.model_kind not in ARRHENIUS_FORMS or kinetics.a is None:
        return {**out, "k": None, "not_evaluated": f"model_kind {_v(kinetics.model_kind)} is not a scalar Arrhenius fit"}
    k = arrhenius_k(temperature_k, a=kinetics.a, n=kinetics.n, ea_kj_mol=kinetics.ea_kj_mol, t0_k=kinetics.t0_k or 1.0)
    return {**out, "k": _sig(k), "expression": "k = A (T/T0)^n exp(-Ea/RT)"}


def _transition_state_rows(session: Session, entry: ReactionEntry) -> list[dict[str, Any]]:
    rows = []
    states = session.scalars(select(TransitionState).where(TransitionState.reaction_entry_id == entry.id))
    for ts in states:
        for ts_entry in ts.entries:
            calcs = list(ts_entry.calculations)
            freq_rows = []
            for calc in calcs:
                if calc.freq_result is None:
                    continue
                freq_rows.append(
                    {
                        "calculation_ref": calc.public_ref,
                        "level_of_theory": _level_label(calc.lot),
                        "software": _software_label(calc.software_release),
                        "n_imaginary": calc.freq_result.n_imag,
                        "imaginary_frequency_cm1": calc.freq_result.imag_freq_cm1,
                        "imaginary_modes_cm1": _imaginary_frequencies(session, calc),
                        "hessian_stored": calc.hessian is not None,
                    }
                )
            irc_calcs = [c for c in calcs if _v(c.type) == "irc"]
            evidence = sorted(
                (
                    {
                        "kind": _v(ev.kind),
                        "passed": ev.passed,
                        "imaginary_frequency_count": ev.imaginary_frequency_count,
                        "imaginary_frequency_cm1": ev.imaginary_frequency_cm1,
                        "mode_displacement_agrees": ev.mode_displacement_agrees,
                        "rationale": ev.rationale,
                    }
                    for ev in ts_entry.validation_evidence
                ),
                key=lambda r: (r["kind"], _content_key(r)),
            )
            irc_evidence = [ev for ev in evidence if ev["kind"] == "irc"]
            if irc_evidence:
                outcome = "IRC validation evidence deposited: " + ", ".join(
                    "passed" if ev["passed"] else "failed" for ev in irc_evidence
                )
            elif irc_calcs:
                outcome = f"{len(irc_calcs)} IRC calculation(s) deposited, no IRC validation evidence"
            else:
                outcome = "no IRC calculation and no IRC validation evidence deposited"
            rows.append(
                {
                    "transition_state_entry_ref": ts_entry.public_ref,
                    "label": ts.label,
                    "review_status": _review_status(session, SubmissionRecordType.transition_state_entry, ts_entry.id),
                    "calculations_by_type": dict(sorted(Counter(_v(c.type) for c in calcs).items())),
                    "frequency_calculations": sorted(freq_rows, key=_content_key),
                    "irc_calculations": len(irc_calcs),
                    "validation_evidence": evidence,
                    "irc_outcome": outcome,
                }
            )
    return sorted(rows, key=lambda r: (r["label"] or "", _content_key(r)))


def _selection_assessment(session: Session, kinetics: Kinetics) -> dict[str, Any]:
    """How the kinetics selection route assesses this record for a forward, whole-reaction, 300-1000 K question."""
    from app.schemas.reads.scientific_kinetics_selection import KineticsSelectionRequest
    from app.services.kinetics_selection.public import run_selection

    body = KineticsSelectionRequest.model_validate(KINETICS_QUESTION)
    work, owned = _selection_session(session)
    try:
        manifest = run_selection(work, reaction_entry_id=kinetics.reaction_entry_id, body=body).manifest
    finally:
        if owned:
            work.rollback()
            work.close()
    candidate = next((c for c in manifest["candidates"] if c["kinetics_ref"] == kinetics.public_ref), None)
    if candidate is None:
        return {"assessed": False, "outcome": manifest["outcome"]}
    assessment = candidate["assessment"]
    return {
        "assessed": True,
        "outcome": manifest["outcome"],
        "applicability": assessment.get("applicability"),
        "reason_codes": sorted({r["code"] for r in assessment.get("reasons", [])}),
        "advisory": sorted(assessment.get("advisory", [])),
    }


def _use_case_record(session: Session, kinetics: Kinetics) -> dict[str, Any]:
    links = list(
        session.scalars(select(KineticsSourceCalculation).where(KineticsSourceCalculation.kinetics_id == kinetics.id))
    )
    sources = sorted(
        (_calculation_row(link.calculation, role=_v(link.role)) for link in links),
        key=lambda r: (r["role"], r["subject"], r["level_of_theory"], r["type"], _content_key(r)),
    )
    submissions = _submissions_of(session, SubmissionRecordType.kinetics, kinetics.id)
    return {
        "kinetics_ref": kinetics.public_ref,
        "reaction": _equation(kinetics.reaction_entry.reaction),
        "scientific_origin": _v(kinetics.scientific_origin),
        "review_status": _review_status(session, SubmissionRecordType.kinetics, kinetics.id),
        "released_in": _released_in(session, SubmissionRecordType.kinetics, kinetics.id),
        "workflow_tool": _workflow_tool(kinetics.workflow_tool_release),
        "fit": {
            "model_kind": _v(kinetics.model_kind),
            "a": kinetics.a,
            "a_units": getattr(kinetics.a_units, "value", kinetics.a_units),
            "n": kinetics.n,
            "ea_kj_mol": kinetics.ea_kj_mol,
            "t0_k": kinetics.t0_k,
            "tmin_k": kinetics.tmin_k,
            "tmax_k": kinetics.tmax_k,
            "direction": getattr(kinetics.direction, "value", kinetics.direction),
            "tunneling_model": getattr(kinetics.tunneling_model, "value", kinetics.tunneling_model),
        },
        "k_at_300_k": _rate_at(kinetics, USE_CASE_TEMPERATURE_K),
        "declared_energy_level_of_theory": _lot_label(session, kinetics.energy_level_of_theory_id),
        "source_calculations": sources,
        "levels_of_cited_energies": [
            {"role": role, "level_of_theory": level, "software": software}
            for role, level, software in sorted(
                {(r["role"], r["level_of_theory"], r["software"]) for r in sources if r["type"] in ("sp", "composite")}
            )
        ],
        "transition_states": _transition_state_rows(session, kinetics.reaction_entry),
        "deposit_warnings": _warning_counts(session, submissions),
        "deposit": _submission_context(session, submissions),
        "selection_assessment": _selection_assessment(session, kinetics),
    }


def _brief_kinetics(session: Session, kinetics: Kinetics) -> dict[str, Any]:
    return {
        "kinetics_ref": kinetics.public_ref,
        "reaction": _equation(kinetics.reaction_entry.reaction),
        "scientific_origin": _v(kinetics.scientific_origin),
        "model_kind": _v(kinetics.model_kind),
        "source": _source_label(kinetics),
        "review_status": _review_status(session, SubmissionRecordType.kinetics, kinetics.id),
        "k_at_300_k": _rate_at(kinetics, USE_CASE_TEMPERATURE_K),
    }


def _kinetics_on(session: Session, reactions: list[ChemReaction]) -> list[Kinetics]:
    if not reactions:
        return []
    entry_ids = list(
        session.scalars(select(ReactionEntry.id).where(ReactionEntry.reaction_id.in_([r.id for r in reactions])))
    )
    return list(session.scalars(select(Kinetics).where(Kinetics.reaction_entry_id.in_(entry_ids))))


def use_case_ch4_oh(session: Session) -> dict[str, Any]:
    """The computed rate coefficient of C + [OH] => O + [CH3]: fit, k(300 K), transition state, levels and warnings."""
    forward = _reactions_with(session, USE_CASE_REACTANTS, USE_CASE_PRODUCTS)
    reverse = _reactions_with(session, USE_CASE_PRODUCTS, USE_CASE_REACTANTS)
    records = _kinetics_on(session, forward)
    computed = [k for k in records if _v(k.scientific_origin) == "computed"]
    others = [k for k in records if _v(k.scientific_origin) != "computed"]
    rows = sorted((_use_case_record(session, k) for k in computed), key=_content_key)
    return {
        "reaction_asked": " + ".join(USE_CASE_REACTANTS) + " => " + " + ".join(USE_CASE_PRODUCTS),
        "stored_reactions": [_equation(r) for r in forward],
        "computed_records": rows,
        "computed_record_count": len(rows),
        "other_origin_records_of_this_reaction": sorted((_brief_kinetics(session, k) for k in others), key=_content_key),
        "records_of_the_reverse_reaction": sorted(
            (_brief_kinetics(session, k) for k in _kinetics_on(session, reverse)), key=_content_key
        ),
        "note": (
            "Records of other origin on this reaction are listed separately, not in computed_records. "
            "The reverse reaction is a separate stored reaction; its records are listed for reference only."
        ),
    }


# ---------------------------------------------------------------------------
# ch3_enthalpy_of_formation
# ---------------------------------------------------------------------------


def _thermo_selection_summary(manifest: dict[str, Any], h298_by_ref: dict[str, Any]) -> dict[str, Any]:
    population = manifest["population"]
    decision = manifest["decision"]
    candidates = sorted(
        (
            {
                "thermo_ref": c["thermo_ref"],
                "h298_kj_mol": h298_by_ref.get(c["thermo_ref"]),
                "eligible": c.get("eligible"),
                "applicability": c["assessment"].get("applicability"),
                "reason_codes": sorted({r["code"] for r in c["assessment"].get("reasons", [])}),
            }
            for c in manifest["candidates"]
        ),
        key=_content_key,
    )
    return {
        "outcome": manifest["outcome"],
        "filters": manifest["request"].get("filters") or {},
        "records_for_entry": population.get("thermo_rows_for_entry"),
        "funnel": population.get("funnel"),
        "candidates": candidates,
        "selected_thermo_ref": decision.get("selected_ref"),
        "basis": decision.get("basis"),
        "disclosure_notes": sorted((manifest.get("disclosures") or {}).get("notes", [])),
    }


def _run_thermo_selection(session: Session, entry: SpeciesEntry, filters: dict[str, Any], h298_by_ref) -> dict[str, Any]:
    from app.schemas.reads.scientific_thermo_selection import (
        ThermoSelectionFilters,
        ThermoSelectionRequest,
        ThermoSelectionTargetIn,
    )
    from app.services.thermo_selection.public import run_selection

    body = ThermoSelectionRequest(
        target=ThermoSelectionTargetIn(kind=ThermoTargetKind.equilibrium_ensemble),
        filters=ThermoSelectionFilters(**filters),
    )
    work, owned = _selection_session(session)
    try:
        selection, _ = run_selection(work, species_entry_id=entry.id, body=body)
        return _thermo_selection_summary(selection.manifest, h298_by_ref)
    finally:
        if owned:
            work.rollback()
            work.close()


def ch3_enthalpy_of_formation(session: Session) -> dict[str, Any]:
    """Every thermo record of [CH3]: H298, level, deposit; and the H298 selection answer under a level filter."""
    smiles, multiplicity = CH3
    species_ids = list(
        session.scalars(select(Species.id).where(Species.smiles == smiles, Species.multiplicity == multiplicity))
    )
    entries = list(session.scalars(select(SpeciesEntry).where(SpeciesEntry.species_id.in_(species_ids)))) if species_ids else []
    fixture_notes = _fixture_notes_by_thermo(session) if entries else {}
    rows = []
    h298_by_ref: dict[str, Any] = {}
    energy_lots: dict[int, str] = {}
    entries_with_thermo = []
    for entry in entries:
        records = list(session.scalars(select(Thermo).where(Thermo.species_entry_id == entry.id)))
        if records:
            entries_with_thermo.append(entry)
        for thermo in records:
            h298 = _thermo_value(thermo, "h", H298_TEMPERATURE_K)
            h298_by_ref[thermo.public_ref] = h298["value"]
            lot = session.get(LevelOfTheory, thermo.energy_level_of_theory_id) if thermo.energy_level_of_theory_id else None
            if lot is not None and lot.method.lower() == ENERGY_METHOD:
                energy_lots[lot.id] = _level_label(lot)
            submissions = _submissions_of(session, SubmissionRecordType.thermo, thermo.id)
            rows.append(
                {
                    "thermo_ref": thermo.public_ref,
                    "species_entry_ref": entry.public_ref,
                    "scientific_origin": _v(thermo.scientific_origin),
                    "review_status": _review_status(session, SubmissionRecordType.thermo, thermo.id),
                    "h298_kj_mol": h298["value"],
                    "h298_from": h298["from"],
                    "enthalpy_reference_kind": getattr(thermo.enthalpy_reference_kind, "value", thermo.enthalpy_reference_kind),
                    **_thermo_level(session, thermo, fixture_notes),
                    "source": _source_label(thermo),
                    "workflow_tool": _workflow_tool(thermo.workflow_tool_release),
                    "deposit": _submission_context(session, submissions),
                    "released_in": _released_in(session, SubmissionRecordType.thermo, thermo.id),
                }
            )
    rows.sort(key=lambda r: (r["scientific_origin"], r.get("level_of_theory") or "", r["h298_kj_mol"] or 0.0, _content_key(r)))
    selections = []
    for entry in sorted(entries_with_thermo, key=lambda e: e.public_ref):
        without = _run_thermo_selection(session, entry, {}, h298_by_ref)
        for lot_id, label in sorted(energy_lots.items(), key=lambda item: item[1]):
            lot = session.get(LevelOfTheory, lot_id)
            filtered = _run_thermo_selection(session, entry, {"level_of_theory_ref": lot.public_ref}, h298_by_ref)
            selections.append(
                {
                    "species_entry_ref": entry.public_ref,
                    "level_filter": label,
                    "with_level_filter": filtered,
                    "without_filter_outcome": without["outcome"],
                    "without_filter_candidates": len(without["candidates"]),
                }
            )
    selections.sort(key=lambda r: (r["level_filter"], _content_key(r)))
    return {
        "species": smiles,
        "multiplicity": multiplicity,
        "thermo_records": len(rows),
        "records": rows,
        "run_identification": RUN_IDENTITY_NOTE,
        "selection_question": "formation_enthalpy_298k, gas, target equilibrium_ensemble, filter level_of_theory_ref",
        "selections": selections,
    }


# ---------------------------------------------------------------------------
# network_n2h3
# ---------------------------------------------------------------------------


def _state_label(state: NetworkState) -> str:
    parts = []
    for participant in state.participants:
        entry = participant.species_entry
        name = entry.stereo_smiles or entry.species.smiles
        parts += [name] * participant.stoichiometry
    return " + ".join(sorted(parts))


def _state_species(state: NetworkState) -> tuple[str, ...]:
    names: list[str] = []
    for participant in state.participants:
        names += [participant.species_entry.species.smiles] * participant.stoichiometry
    return tuple(sorted(names))


def _evaluate(session: Session, nk: NetworkKinetics) -> dict[str, Any]:
    from app.services.scientific_read.network_kinetics import evaluate_network_kinetics

    result = evaluate_network_kinetics(
        session,
        network_kinetics_handle=nk.public_ref,
        temperatures_k=[NETWORK_TEMPERATURE_K],
        pressures_bar=[NETWORK_PRESSURE_BAR],
    )
    (point,) = result.points
    return {
        "network_kinetics_ref": nk.public_ref,
        "model_kind": _v(nk.model_kind),
        "temperature_k": point.temperature_k,
        "pressure_bar": point.pressure_bar,
        "k": _sig(point.k),
        "units": getattr(result.k_units, "value", result.k_units),
        "in_fit_range": point.in_range,
        "fit_range": {"tmin_k": nk.tmin_k, "tmax_k": nk.tmax_k, "pmin_bar": nk.pmin_bar, "pmax_bar": nk.pmax_bar},
        "evaluated_with": "evaluate_network_kinetics -> app.chemistry.network_kinetics_eval.evaluate_chebyshev",
    }


def network_n2h3(session: Session) -> dict[str, Any]:
    """Per stored network: wells, channels by kind, k(T, P) forms; and k(1000 K, 1 bar) of NH3N -> N2H3."""
    out = []
    for network in session.scalars(select(Network)):
        states = list(network.states)
        wells = sorted(_state_label(s) for s in states if s.kind is NetworkStateKind.well)
        others = sorted((_v(s.kind), _state_label(s)) for s in states if s.kind is not NetworkStateKind.well)
        channels = list(network.channels)
        channel_rows = sorted(
            (
                {
                    "kind": _v(c.kind),
                    "mechanism": _v(c.mechanism),
                    "source": _state_label(c.source_state),
                    "sink": _state_label(c.sink_state),
                    "kinetics_forms": sorted(_v(k.model_kind) for k in c.kinetics_records),
                }
                for c in channels
            ),
            key=lambda r: (r["kind"], r["source"], r["sink"], _content_key(r)),
        )
        forms = Counter(_v(k.model_kind) for c in channels for k in c.kinetics_records)
        target = []
        for channel in channels:
            if channel.source_state.kind is not NetworkStateKind.well or channel.sink_state.kind is not NetworkStateKind.well:
                continue
            if _state_species(channel.source_state) == NETWORK_SOURCE_WELL and _state_species(channel.sink_state) == NETWORK_SINK_WELL:
                for nk in channel.kinetics_records:
                    target.append({"channel_kind": _v(channel.kind), **_evaluate(session, nk)})
        out.append(
            {
                "name": network.name,
                "wells": wells,
                "well_count": len(wells),
                "other_states": [{"kind": kind, "species": label} for kind, label in others],
                "channel_count": len(channels),
                "channels_by_kind": dict(sorted(Counter(_v(c.kind) for c in channels).items())),
                "channels_by_mechanism": dict(sorted(Counter(_v(c.mechanism) for c in channels).items())),
                "channels": channel_rows,
                "kinetics_forms_by_model_kind": dict(sorted(forms.items())),
                "solves": len(network.solves),
                "nh3n_to_n2h3": sorted(target, key=_content_key),
            }
        )
    out.sort(key=lambda r: (r["name"] or "", _content_key(r)))
    return {
        "networks": out,
        "network_count": len(out),
        "channel_asked": " + ".join(NETWORK_SOURCE_WELL) + " -> " + " + ".join(NETWORK_SINK_WELL),
        "point_asked": {"temperature_k": NETWORK_TEMPERATURE_K, "pressure_bar": NETWORK_PRESSURE_BAR},
    }


# ---------------------------------------------------------------------------
# chemkin_reconciliation
# ---------------------------------------------------------------------------


def _chemkin_side(pairs: list[tuple[int, str]]) -> str:
    return " + ".join(f"{n} {name}" if n != 1 else name for n, name in pairs)


def _chemkin_equation(reaction) -> str:
    """The reaction as the file writes it, third body included."""
    if reaction.is_falloff:
        suffix = f" (+{reaction.falloff_collider or 'M'})"
    elif reaction.is_third_body:
        suffix = " + M"
    else:
        suffix = ""
    arrow = "<=>" if reaction.reversible else "=>"
    return f"{_chemkin_side(reaction.reactants)}{suffix} {arrow} {_chemkin_side(reaction.products)}{suffix}"


def _form(reaction) -> str:
    if reaction.chebyshev is not None:
        return "chebyshev"
    if reaction.plog:
        return "plog"
    if reaction.troe is not None:
        return "troe"
    if reaction.sri is not None:
        return "sri"
    if reaction.is_falloff:
        return "lindemann"
    if reaction.is_third_body:
        return "third_body"
    return "arrhenius"


def _merge_cause(first, later) -> tuple[str, str]:
    """Why ``later`` shares the stored reaction of ``first``: a cause code and its explanation."""
    same_labels = (first.reactant_names, first.product_names) == (later.reactant_names, later.product_names)
    forms = (_form(first), _form(later))
    if not same_labels:
        return (
            "different_labels_same_species",
            "the two lines name different CHEMKIN species that resolve to the same stored species",
        )
    if first.duplicate and later.duplicate:
        return (
            "duplicate_pair_not_summed",
            f"a DUPLICATE pair of {forms[1]} rates; the adapter sums only plain Arrhenius DUPLICATE lines, so each "
            "line is its own record on the one reaction",
        )
    if first.duplicate != later.duplicate:
        return ("duplicate_flag_on_one_line", "the same reaction written twice, DUPLICATE on only one of the lines")
    if forms[0] != forms[1]:
        return (
            "same_reaction_two_rate_forms",
            f"the same reaction written twice without DUPLICATE, once as {forms[0]} and once as {forms[1]}; "
            "a third body is not a participant, so both lines are one stored reaction",
        )
    return (
        "undeclared_repeat",
        f"the same {forms[1]} reaction written twice without DUPLICATE (the adapter's chemkin_duplicate_undeclared warning)",
    )


def chemkin_reconciliation(session: Session) -> dict[str, Any]:
    """Reactions of the committed formic acid model file against the rate records and reactions stored from it.

    Species names are matched to stored species through identical NASA7 coefficients; a line's stored reaction is
    the stored reaction with the same participants, direction and reversibility. Lines are listed in file order.
    """
    mechanism = _parse_fixture_mechanism()
    from tckdb_chemkin.normalizer import normalize_mechanism

    normalized = normalize_mechanism(mechanism).reactions
    lines = sorted(r.line_no for r in mechanism.reactions)
    record_lines = {r.line_no for r in normalized}
    collapsed_groups = [r for r in normalized if r.model_kind == "multi_arrhenius"]
    by_line = {r.line_no: r for r in mechanism.reactions}

    thermo_by_label = _fixture_thermo_by_label(session, mechanism)
    species_by_label = {name: thermo.species_entry.species_id for name, thermo in thermo_by_label.items()}
    model_releases = sorted({t.workflow_tool_release_id for t in thermo_by_label.values() if t.workflow_tool_release_id})
    stored_records = (
        list(session.scalars(select(Kinetics).where(Kinetics.workflow_tool_release_id.in_(model_releases))))
        if model_releases
        else []
    )
    stored_reaction_ids = {k.reaction_entry.reaction_id for k in stored_records}

    stored_keys: dict[tuple, ChemReaction] = {}
    for reaction in session.scalars(select(ChemReaction).where(ChemReaction.id.in_(stored_reaction_ids))) if stored_reaction_ids else []:
        reactants: Counter[int] = Counter()
        products: Counter[int] = Counter()
        for participant in reaction.participants:
            (reactants if _v(participant.role) == "reactant" else products)[participant.species_id] += participant.stoichiometry
        stored_keys[(reaction.reversible, tuple(sorted(reactants.items())), tuple(sorted(products.items())))] = reaction

    def key_of(rxn) -> tuple | None:
        if any(name not in species_by_label for name in rxn.reactant_names + rxn.product_names):
            return None
        reactants: Counter[int] = Counter()
        products: Counter[int] = Counter()
        for coefficient, name in rxn.reactants:
            reactants[species_by_label[name]] += coefficient
        for coefficient, name in rxn.products:
            products[species_by_label[name]] += coefficient
        return (rxn.reversible, tuple(sorted(reactants.items())), tuple(sorted(products.items())))

    merged: list[dict[str, Any]] = []
    for group in collapsed_groups:
        head = by_line[group.line_no]
        members = sorted(
            r.line_no
            for r in mechanism.reactions
            if r.line_no not in record_lines
            and (r.reactant_names, r.product_names, r.reversible) == (head.reactant_names, head.product_names, head.reversible)
        )
        for line in members:
            merged.append(
                {
                    "line": line,
                    "equation": _chemkin_equation(by_line[line]),
                    "cause": "duplicate_pair_summed",
                    "explanation": "a DUPLICATE line summed with its partner into one multi_arrhenius rate record",
                    "merged_into_line": head.line_no,
                    "merged_into_equation": _chemkin_equation(head),
                    "stored_reaction": _equation(stored_keys.get(key_of(head))),
                    "stored_as": "a term of the partner's multi_arrhenius record",
                }
            )

    first_by_key: dict[tuple, Any] = {}
    unmatched = 0
    for rxn in sorted(normalized, key=lambda r: r.line_no):
        key = key_of(by_line[rxn.line_no])
        if key is None or key not in stored_keys:
            unmatched += 1
            continue
        if key in first_by_key:
            first = first_by_key[key]
            cause, explanation = _merge_cause(by_line[first.line_no], by_line[rxn.line_no])
            merged.append(
                {
                    "line": rxn.line_no,
                    "equation": _chemkin_equation(by_line[rxn.line_no]),
                    "cause": cause,
                    "explanation": explanation,
                    "merged_into_line": first.line_no,
                    "merged_into_equation": _chemkin_equation(by_line[first.line_no]),
                    "stored_reaction": _equation(stored_keys[key]),
                    "stored_as": "its own rate record on the reaction stored for the earlier line",
                }
            )
        else:
            first_by_key[key] = rxn
    merged.sort(key=lambda r: r["line"])

    reverse_pairs = sum(
        1 for (rev, reactants, products) in stored_keys if (rev, products, reactants) in stored_keys
    ) // 2
    rows_summed = len(lines) - len(normalized)
    records_sharing = len(normalized) - unmatched - len(first_by_key)
    deposited = bool(stored_records)
    causes = Counter(row["cause"] for row in merged)
    return {
        "mechanism_file": MECHANISM_FILE,
        "model_deposited": deposited,
        "species_names_resolved_to_stored_species": len(species_by_label),
        "species_names_in_file": len(mechanism.thermo),
        "reactions_in_file": len(lines),
        "rate_records_built_by_adapter": len(normalized),
        "rate_records_stored": len(stored_records),
        "rate_records_matched_to_a_stored_reaction": len(normalized) - unmatched,
        "distinct_reactions_stored": len(stored_reaction_ids),
        "arithmetic": {
            "reactions_in_file": len(lines),
            "minus_lines_summed_into_a_partner_record": rows_summed,
            "equals_rate_records": len(normalized),
            "minus_records_on_a_reaction_already_stored_for_an_earlier_line": records_sharing,
            "equals_distinct_reactions": len(normalized) - records_sharing - unmatched,
            "adds_up": deposited
            and len(normalized) == len(stored_records)
            and len(normalized) - records_sharing - unmatched == len(stored_reaction_ids)
            and len(lines) == rows_summed + len(normalized)
            and len(merged) == rows_summed + records_sharing,
        },
        "multi_arrhenius_records": len(collapsed_groups),
        "lines_not_stored_as_their_own_reaction": len(merged),
        "by_cause": dict(sorted(causes.items())),
        "reverse_direction_pairs_stored_as_two_reactions": reverse_pairs,
        "merged_lines": merged,
    }


# ---------------------------------------------------------------------------
# species_thermo_sources
# ---------------------------------------------------------------------------


def _thermo_source_row(session: Session, thermo: Thermo, fixture_notes: dict[int, str]) -> dict[str, Any]:
    return {
        "thermo_ref": thermo.public_ref,
        "scientific_origin": _v(thermo.scientific_origin),
        "source": _source_label(thermo),
        "workflow_tool": _workflow_tool(thermo.workflow_tool_release),
        **_thermo_level(session, thermo, fixture_notes),
        "h298_kj_mol": _thermo_value(thermo, "h", H298_TEMPERATURE_K),
        "s298_j_mol_k": _thermo_value(thermo, "s", H298_TEMPERATURE_K),
        "cp_300_j_mol_k": _thermo_value(thermo, "cp", CP_TEMPERATURE_K),
        "review_status": _review_status(session, SubmissionRecordType.thermo, thermo.id),
    }


def species_thermo_sources(session: Session) -> dict[str, Any]:
    """Every species with thermo records from more than one source, each record with H298, S298 and Cp(300 K)."""
    by_species: dict[int, list[Thermo]] = defaultdict(list)
    for thermo in session.scalars(select(Thermo)):
        by_species[thermo.species_entry.species_id].append(thermo)
    multi = {sid: records for sid, records in by_species.items() if len({_source_label(t) for t in records}) > 1}
    fixture_notes = _fixture_notes_by_thermo(session) if multi else {}
    rows = []
    for species_id, records in multi.items():
        species = session.get(Species, species_id)
        record_rows = sorted(
            (_thermo_source_row(session, t, fixture_notes) for t in records),
            key=lambda r: (r["scientific_origin"], r["source"], r.get("level_of_theory") or "", _content_key(r)),
        )
        rows.append(
            {
                "species": species.smiles,
                "multiplicity": species.multiplicity,
                "sources": sorted({r["source"] for r in record_rows}),
                "records_by_source": dict(sorted(Counter(r["source"] for r in record_rows).items())),
                "records": record_rows,
            }
        )
    rows.sort(key=lambda r: (r["species"], r["multiplicity"], _content_key(r)))
    return {
        "species_with_several_sources": len(rows),
        "species_list": [f"{r['species']} (multiplicity {r['multiplicity']})" for r in rows],
        "source_is": "the workflow tool of the record (or its literature when it names no tool)",
        "species": rows,
    }


# ---------------------------------------------------------------------------
# caught_error
# ---------------------------------------------------------------------------


_RECORD_MODELS = {
    SubmissionRecordType.kinetics: Kinetics,
    SubmissionRecordType.thermo: Thermo,
    SubmissionRecordType.transition_state: TransitionState,
    SubmissionRecordType.transition_state_entry: TransitionStateEntry,
    SubmissionRecordType.calculation: Calculation,
    SubmissionRecordType.species_entry: SpeciesEntry,
    SubmissionRecordType.reaction_entry: ReactionEntry,
}


def _describe(session: Session, record_type: SubmissionRecordType, record) -> dict[str, Any]:
    """A record's content: what a reader needs to know which record it is without its ref."""
    if record is None:
        return {}
    if record_type is SubmissionRecordType.kinetics:
        return {
            "reaction": _equation(record.reaction_entry.reaction),
            "scientific_origin": _v(record.scientific_origin),
            "model_kind": _v(record.model_kind),
            "source": _source_label(record),
        }
    if record_type is SubmissionRecordType.thermo:
        species = record.species_entry.species
        return {
            "species": species.smiles,
            "multiplicity": species.multiplicity,
            "scientific_origin": _v(record.scientific_origin),
            "h298_kj_mol": record.h298_kj_mol,
            "energy_level_of_theory": _lot_label(session, record.energy_level_of_theory_id),
        }
    if record_type is SubmissionRecordType.transition_state:
        return {"reaction": _equation(record.reaction_entry.reaction), "label": record.label}
    if record_type is SubmissionRecordType.transition_state_entry:
        freqs = sorted(f for c in record.calculations for f in _imaginary_frequencies(session, c))
        return {
            "reaction": _equation(record.transition_state.reaction_entry.reaction),
            "label": record.transition_state.label,
            "imaginary_frequencies_cm1": freqs,
        }
    if record_type is SubmissionRecordType.calculation:
        row = _calculation_row(record)
        row.pop("calculation_ref")
        row["imaginary_frequencies_cm1"] = _imaginary_frequencies(session, record)
        return row
    if record_type is SubmissionRecordType.species_entry:
        return {"species": record.species.smiles, "multiplicity": record.species.multiplicity}
    if record_type is SubmissionRecordType.reaction_entry:
        return {"reaction": _equation(record.reaction)}
    return {}


def _described_key(row: dict[str, Any]) -> tuple:
    """Order rows carrying a described ``record``: by species or reaction, multiplicity, H298, then content."""
    record = row.get("record") or {}
    h298 = record.get("h298_kj_mol")
    return (
        row.get("record_type") or "",
        record.get("species") or record.get("reaction") or "",
        record.get("multiplicity") or 0,
        h298 is None,
        h298 if h298 is not None else 0.0,
        _content_key(row),
    )


def _record(session: Session, record_type: SubmissionRecordType, record_id: int):
    model = _RECORD_MODELS.get(record_type)
    return session.get(model, record_id) if model is not None else None


def _kinetics_selection(session: Session, entry: ReactionEntry, rejected_refs: set[str]) -> dict[str, Any]:
    from app.schemas.reads.scientific_kinetics_selection import KineticsSelectionRequest
    from app.services.kinetics_selection.public import run_selection

    body = KineticsSelectionRequest.model_validate(KINETICS_QUESTION)
    work, owned = _selection_session(session)
    try:
        manifest = run_selection(work, reaction_entry_id=entry.id, body=body).manifest
    finally:
        if owned:
            work.rollback()
            work.close()
    population = manifest["population"]
    candidates = sorted(
        (
            {
                "kinetics_ref": c["kinetics_ref"],
                "eligible": c.get("eligible"),
                "applicability": c["assessment"].get("applicability"),
                "reason_codes": sorted({r["code"] for r in c["assessment"].get("reasons", [])}),
            }
            for c in manifest["candidates"]
        ),
        key=_content_key,
    )
    excluded = sorted(
        ({"kinetics_ref": e["kinetics_ref"], "review_status": e["review_status"], "reason": e["reason"]} for e in population.get("excluded_by_review", [])),
        key=_content_key,
    )
    records = sorted(
        (
            {
                "kinetics_ref": k.public_ref,
                "review_status": _review_status(session, SubmissionRecordType.kinetics, k.id),
                "is_rejected_record": k.public_ref in rejected_refs,
            }
            for k in entry.kinetics_records
        ),
        key=_content_key,
    )
    candidate_refs = {c["kinetics_ref"] for c in candidates}
    return {
        "reaction": _equation(entry.reaction),
        "kinetics_records": records,
        "outcome": manifest["outcome"],
        "kinetics_rows_for_entry": population.get("kinetics_rows_for_entry"),
        "visible_candidates": population.get("visible_candidates"),
        "excluded_by_review": excluded,
        "excluded_by_default": (population.get("funnel") or {}).get("excluded_by_default", {}),
        "candidates": candidates,
        "rejected_record_is_a_candidate": bool(candidate_refs & rejected_refs),
        "selected_determination_ref": manifest["decision"].get("selected_determination_ref"),
        "basis": manifest["decision"].get("basis"),
    }


def _finding_row(session: Session, finding: StructureEvidenceFinding) -> dict[str, Any]:
    subject_calcs = []
    if finding.subject_geometry_id is not None:
        from app.db.models.calculation import CalculationOutputGeometry

        calc_ids = session.scalars(
            select(CalculationOutputGeometry.calculation_id).where(
                CalculationOutputGeometry.geometry_id == finding.subject_geometry_id
            )
        )
        subject_calcs = sorted(
            (_describe(session, SubmissionRecordType.calculation, session.get(Calculation, cid)) for cid in set(calc_ids)),
            key=_content_key,
        )
    if finding.subject_calculation_id is not None:
        subject_calcs.append(
            _describe(session, SubmissionRecordType.calculation, session.get(Calculation, finding.subject_calculation_id))
        )
    source = session.get(Calculation, finding.source_calculation_id) if finding.source_calculation_id else None
    supersedes = session.get(StructureEvidenceFinding, finding.supersedes_finding_id) if finding.supersedes_finding_id else None
    return {
        "finding_ref": finding.public_ref,
        "kind": _v(finding.kind),
        "scope": _v(finding.scope),
        "verdict": _v(finding.verdict),
        "authority": _v(finding.authority),
        "role": getattr(finding.role, "value", finding.role),
        "semantic_version": finding.semantic_version,
        "rationale": finding.rationale,
        "subject_calculations": subject_calcs,
        "source_calculation": _describe(session, SubmissionRecordType.calculation, source) if source else None,
        "supersedes_finding_ref": supersedes.public_ref if supersedes else None,
    }


def _same_reaction_entries(session: Session, reaction: ChemReaction) -> list[ReactionEntry]:
    """Entries of every stored reaction with the same participants by InChI key and multiplicity, either SMILES."""
    reactants = _side_keys(reaction, "reactant")
    products = _side_keys(reaction, "product")
    inchi_keys = {key[0] for key in reactants}
    species_ids = list(session.scalars(select(Species.id).where(Species.inchi_key.in_(inchi_keys))))
    reaction_ids = set(
        session.scalars(select(ReactionParticipant.reaction_id).where(ReactionParticipant.species_id.in_(species_ids)))
    )
    entries = []
    for other in session.scalars(select(ChemReaction).where(ChemReaction.id.in_(reaction_ids))):
        if (_side_keys(other, "reactant"), _side_keys(other, "product")) == (reactants, products):
            entries += list(other.entries)
    return entries


def caught_error(session: Session) -> dict[str, Any]:
    """Rejected records, the structure-evidence findings, the deposit warnings and the kinetics selection answer."""
    reviews = list(session.scalars(select(RecordReview).where(RecordReview.status == RecordReviewStatus.rejected)))
    rejected = []
    rejected_kinetics: list[Kinetics] = []
    rejected_keys = {(r.record_type, r.record_id) for r in reviews}
    for review in reviews:
        record = _record(session, review.record_type, review.record_id)
        rejected.append(
            {
                "record_type": _v(review.record_type),
                "record_ref": getattr(record, "public_ref", None),
                "record": _describe(session, review.record_type, record),
                "review_status": _v(review.status),
                "review_note": review.note,
            }
        )
        if review.record_type is SubmissionRecordType.kinetics and record is not None:
            rejected_kinetics.append(record)
    rejected.sort(key=_described_key)

    rejected_refs = {k.public_ref for k in rejected_kinetics}
    deposits = []
    for kinetics in rejected_kinetics:
        submissions = _submissions_of(session, SubmissionRecordType.kinetics, kinetics.id)
        warnings = []
        if submissions:
            for warning in session.scalars(
                select(DepositWarning).where(
                    DepositWarning.submission_id.in_([s.id for s in submissions]), DepositWarning.code.is_not(None)
                )
            ):
                record = _record(session, warning.record_type, warning.record_id) if warning.record_type else None
                warnings.append(
                    {
                        "code": warning.code,
                        "field": warning.field,
                        "record_type": getattr(warning.record_type, "value", warning.record_type),
                        "record": _describe(session, warning.record_type, record) if record is not None else {},
                        "on_a_rejected_record": (warning.record_type, warning.record_id) in rejected_keys,
                    }
                )
        warnings.sort(key=lambda r: (r["code"], r["record_type"] or "", _content_key(r)))
        entries = _same_reaction_entries(session, kinetics.reaction_entry.reaction)
        deposits.append(
            {
                "rejected_kinetics_ref": kinetics.public_ref,
                "reaction": _equation(kinetics.reaction_entry.reaction),
                "deposit_warnings": warnings,
                "warning_codes": dict(sorted(Counter(w["code"] for w in warnings).items())),
                "kinetics_question": KINETICS_QUESTION,
                "selection_by_reaction_entry": sorted(
                    (_kinetics_selection(session, entry, rejected_refs) for entry in entries), key=_content_key
                ),
            }
        )
    deposits.sort(key=_content_key)
    findings = sorted(
        (_finding_row(session, f) for f in session.scalars(select(StructureEvidenceFinding))), key=_content_key
    )
    return {
        "rejected_records": rejected,
        "rejected_by_type": dict(sorted(Counter(r["record_type"] for r in rejected).items())),
        "structure_evidence_findings": findings,
        "rejected_kinetics": deposits,
        "note": (
            "selection_by_reaction_entry covers every stored reaction with the same participants by InChI key and "
            "multiplicity, so a rerun deposited under another SMILES of the same species is included."
        ),
    }


# ---------------------------------------------------------------------------
# dataset_release
# ---------------------------------------------------------------------------


def _ref_lookup(session: Session, record_type: str, ref: str | None):
    try:
        kind = SubmissionRecordType(record_type)
    except ValueError:
        return None, None
    model = _RECORD_MODELS.get(kind)
    if model is None or ref is None:
        return kind, None
    return kind, session.scalars(select(model).where(model.public_ref == ref)).first()


def _artifact_lines(artifact) -> list[dict[str, Any]]:
    return [json.loads(line) for line in artifact.content.decode("utf-8").splitlines() if line.strip()]


def dataset_release(session: Session) -> dict[str, Any]:
    """Each release: handle, standing selections, shipped candidates, manifest artifacts and version pins."""
    from app.services.release.manifest import load_manifest
    from app.services.scientific_read.releases import get_release_manifest

    releases = []
    for release in session.scalars(select(DatasetRelease)):
        selections = []
        for row in session.scalars(select(ReleaseSelection).where(ReleaseSelection.dataset_release_id == release.id)):
            record = _record(session, row.record_type, row.record_id)
            subject = _record(session, row.subject_type, row.subject_id)
            selections.append(
                {
                    "action": _v(row.action),
                    "record_type": _v(row.record_type),
                    "record_ref": getattr(record, "public_ref", None),
                    "record": _describe(session, row.record_type, record),
                    "subject_type": _v(row.subject_type),
                    "subject": _describe(session, row.subject_type, subject),
                    "rationale": row.rationale,
                }
            )
        selections.sort(key=_described_key)
        standing = {(_v(s.record_type), s.record_id) for s in _standing_selections(session, release)}
        manifest = load_manifest(session, release)
        candidates: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        manifest_block: dict[str, Any] | None = None
        if manifest is not None:
            for artifact in sorted(manifest.artifacts, key=lambda a: a.path):
                artifacts.append(
                    {
                        "path": artifact.path,
                        "kind": _v(artifact.kind),
                        "media_type": artifact.media_type,
                        "record_count": artifact.record_count,
                        "sha256_matches_stored_content": hashlib.sha256(artifact.content).hexdigest() == artifact.sha256,
                    }
                )
                if _v(artifact.kind) == "candidate_records":
                    for line in _artifact_lines(artifact):
                        kind, record = _ref_lookup(session, line.get("record_type"), line.get("record_ref"))
                        candidates.append(
                            {
                                "record_type": line.get("record_type"),
                                "record_ref": line.get("record_ref"),
                                "record": _describe(session, kind, record) if kind is not None else {},
                                "subject_type": line.get("subject_type"),
                                "selected_in_release": line.get("selected_in_release"),
                                "review_status": (line.get("review") or {}).get("status"),
                            }
                        )
            verification = get_release_manifest(session, release.tag)
            manifest_block = {
                "profile": manifest.profile,
                "manifest_format": {"name": manifest.manifest_format_name, "version": manifest.manifest_format_version},
                "selected_record_count": manifest.selected_record_count,
                "candidate_record_count": manifest.candidate_record_count,
                "version_pins": {
                    "alembic_revision": manifest.alembic_revision,
                    "backend_version": manifest.backend_version,
                    "schemas_package_version": manifest.schemas_package_version,
                    "review_policy": {"name": manifest.review_policy_name, "version": manifest.review_policy_version},
                    "recovery_archive_format": {
                        "name": manifest.recovery_archive_format_name,
                        "version": manifest.recovery_archive_format_version,
                    },
                    "curation_policy": {"name": manifest.curation_policy_name, "version": manifest.curation_policy_version},
                },
                "rights_summary": manifest.rights_summary_json,
                "artifacts": artifacts,
                "verified": verification.verification.verified,
                "artifacts_checked": verification.verification.artifacts_checked,
                "verification_problems": sorted(verification.verification.problems),
                "live_database_diverged_since_publication": verification.live_divergence.diverged,
            }
        candidates.sort(key=_described_key)
        releases.append(
            {
                "tag": release.tag,
                "release_ref": release.public_ref,
                "title": release.title,
                "status": _v(release.status),
                "data_license": release.data_license,
                "code_license": release.code_license,
                "doi": release.doi,
                "has_doi": bool(release.doi),
                "citation_text": release.citation_text,
                "changelog_entry": release.changelog_entry,
                "curation_policy": {"name": release.curation_policy.name, "version": release.curation_policy.version},
                "selections": selections,
                "standing_selections": len(standing),
                "withdrawn_selections": sum(1 for s in selections if s["action"] == ReleaseSelectionAction.withdraw.value),
                "candidates_shipped": candidates,
                "candidates_by_type": dict(sorted(Counter(c["record_type"] for c in candidates).items())),
                "manifest": manifest_block,
                "cite_as": {"tag": release.tag, "citation_text": release.citation_text, "doi": release.doi},
            }
        )
    releases.sort(key=lambda r: (r["tag"], _content_key(r)))
    return {"releases": releases, "release_count": len(releases)}


__all__ = [
    "caught_error",
    "ch3_enthalpy_of_formation",
    "chemkin_reconciliation",
    "dataset_release",
    "network_n2h3",
    "species_thermo_sources",
    "use_case_ch4_oh",
]
