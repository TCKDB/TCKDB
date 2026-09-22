"""Resolve public references and explicitly invoke one advisory check."""
from sqlalchemy import select

from app.db.models.kinetics import Kinetics
from app.db.models.species import SpeciesEntry
from app.db.models.thermo import Thermo
from app.services.consistency.core import AdvisoryResult, currency, record_result
from app.services.consistency.kinetics import compare_kinetics
from app.services.consistency.thermo import compare_thermo


def _resolve(session, model, ref, prefix):
    if not isinstance(ref, str) or not ref.startswith(prefix):
        raise ValueError(f"expected a {prefix} public reference")
    record = session.scalar(select(model).where(model.public_ref == ref))
    if record is None:
        raise ValueError(f"no record with public reference {ref!r}")
    return record


def compare(session, *, check, target_ref, comparison_thermo_ref=None, reverse_kinetics_ref=None,
            thermo_mapping=None, temperature_grid=()):
    """Read-only resolution: no preferred records, no autoflush, no hidden trigger."""
    with session.no_autoflush:
        if check == "thermo-kinetics":
            if comparison_thermo_ref is not None:
                raise ValueError("thermo-kinetics uses an explicit species-entry/thermo mapping")
            forward = _resolve(session, Kinetics, target_ref, "kin_")
            reverse = _resolve(session, Kinetics, reverse_kinetics_ref, "kin_")
            mapping = {}
            for entry_ref, thermo_ref in (thermo_mapping or {}).items():
                entry = _resolve(session, SpeciesEntry, entry_ref, "spe_")
                mapping[entry.id] = _resolve(session, Thermo, thermo_ref, "thm_")
            return compare_kinetics(forward, reverse, mapping, temperature_grid=temperature_grid)
        if reverse_kinetics_ref is not None or thermo_mapping:
            raise ValueError("reverse kinetics and thermo mapping apply only to thermo-kinetics")
        thermo = _resolve(session, Thermo, target_ref, "thm_")
        if check == "thermo":
            neighbour = (_resolve(session, Thermo, comparison_thermo_ref, "thm_")
                         if comparison_thermo_ref is not None else None)
            return compare_thermo(thermo, comparison=neighbour, temperature_grid=temperature_grid)
        if check == "external-cp":
            if comparison_thermo_ref is not None or temperature_grid:
                raise ValueError("external-cp uses observation temperatures and the target thermo")
            from app.services.external_comparison import cp
            result = cp.compare_thermo_with_cp_observations(session, thermo.id)
            return AdvisoryResult(thermo, cp.RUNNER_VERSION, cp.EXTERNAL_CP_COMPARISON_V2,
                                  cp.findings_for_result(result),
                                  result.inputs_json)
        raise ValueError("unknown advisory check")


def invoke(session, *, commit=False, **request):
    """Dry-run by default; commit=True stages one append for caller transaction control."""
    result = compare(session, **request)
    row = record_result(session, result) if commit else None
    return result, row


def current(session, **request):
    """Resolve live inputs before classifying this check's own latest row."""
    result = compare(session, **request)
    return currency(session, result)
