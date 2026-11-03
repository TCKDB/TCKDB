"""Check what a depositor typed against what the uploaded deck or log says.

Three checks, all non-blocking: each earns a deposit warning built from a code and TCKDB's own tokens
(:func:`tckdb_schemas.upload_warning.UploadWarning.from_code`), and nothing is refused or corrected.

1. **Typed against read** (:func:`declared_parameter_warnings`). A parameter the depositor typed
   (``source='upload'``) is compared, per canonical key, with the one the parser read from an artifact
   (``source='parser'``). It is compared only when

   * both rows carry a canonical key *and* a canonical value (absent means "not stated", never "the
     default": a deck that is silent about a setting agrees with every declaration of it);
   * the key says it can change the result (``calculation_parameter_vocab.affects_scientific_result``);
   * the parser's program is the program the calculation declares (a Gaussian declaration is not judged
     against an ORCA log).

   ``freq.hessian_method`` is left to its own check (``hessian_method_deck_check`` and the inline refusal in
   ``hessian_method_assumption``), which knows what the stored tolerance rests on; judging it here too would
   say the same thing twice. ``internal_option.iop`` is left out because its raw key, not its value, names
   the option.

2. **Deck against log** (:func:`merge_artifact_observations`). When both an input deck and an output log
   state the same canonical key with different values, that is itself a warning.

3. **Method and basis** (:func:`level_of_theory_warnings`). The method and basis the parser read from the
   route line or keyword line are compared with the declared level of theory through the same identity
   keys the level of theory is deduplicated by, plus the two spellings the route line adds (a spin prefix,
   a frozen-core option on the method).

Message and context carry the canonical key and TCKDB tokens only. The values on each side are depositor
text (a typed value, a deck's spelling) and go under ``depositor_supplied``.

How an input deck and an output log share a calculation
-------------------------------------------------------
Both are read into ``source='parser'`` rows, tagged with ``artifact_kind``. A re-read replaces only its own
kind, so neither erases the other. Where both state the same setting the **deck wins** and the log's row is
not kept: the deck is what was written to run, the log only echoes it, and a reader of
``calculation_parameter`` must never meet two rows for one setting. The result does not depend on which
artifact arrived first:

* deck first, then log: the log's rows for settings the deck states are dropped before they are stored;
* log first, then deck: the deck's rows are stored and the log's rows for the same settings are deleted.

Either way the stored rows are the deck's rows plus the log's rows for the settings the deck is silent about.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.chemistry.basis_set_names import basis_identity_key
from app.chemistry.method_names import method_identity_key
from app.db.models.calculation import Calculation, CalculationParameter, CalculationParameterVocab
from app.db.models.common import ArtifactKind, ParameterSource
from app.schemas.fragments.calculation import CalculationParameterObservation

logger = logging.getLogger(__name__)

W_PARAMETER_DISAGREES_WITH_ARTIFACT = "parameter_disagrees_with_artifact"
W_INPUT_DECK_DISAGREES_WITH_OUTPUT_LOG = "input_deck_disagrees_with_output_log"
W_LEVEL_OF_THEORY_DISAGREES_WITH_ARTIFACT = "level_of_theory_disagrees_with_artifact"

#: Keys another check owns, or whose value does not name the setting.
_NOT_RECONCILED_HERE = frozenset({"freq.hessian_method", "internal_option.iop"})


def _identity(canonical_key: str | None, section: str | None, raw_key: str, parameter_index: int | None) -> tuple:
    """What makes two parameter rows 'the same setting': the canonical key, else the section and raw key."""
    if canonical_key is not None:
        return ("canonical", canonical_key)
    return ("raw", (section or "").lower(), raw_key.lower(), parameter_index)


def _values_by_key(pairs: Iterable[tuple[str | None, str | None]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for key, value in pairs:
        if key is not None and value is not None:
            out.setdefault(key, set()).add(value)
    return out


def _conflict_warning(key: str, software: str, deck_values: set[str], log_values: set[str]) -> UploadWarning:
    return UploadWarning.from_code(
        field="artifacts",
        code=W_INPUT_DECK_DISAGREES_WITH_OUTPUT_LOG,
        context={
            "canonical_key": key,
            "software": software,
            "depositor_supplied": {"deck_value": sorted(deck_values), "log_value": sorted(log_values)},
        },
    )


def merge_artifact_observations(
    session: Session,
    calculation: Calculation,
    observations: Sequence[CalculationParameterObservation],
    *,
    artifact_kind: ArtifactKind,
    software: str,
) -> tuple[list[CalculationParameterObservation], list[UploadWarning]]:
    """Apply the deck-wins rule to a new read; return the observations to store and the conflicts found.

    For ``output_log``: observations for settings the stored deck rows already state are dropped (and
    compared with the deck's values). For ``input``: nothing is dropped from the new deck rows, but the
    stored log rows for the settings the deck states are compared and deleted.

    Call before storing the new batch. Flushes nothing it does not delete.
    """
    other_kind = ArtifactKind.output_log if artifact_kind is ArtifactKind.input else ArtifactKind.input
    stored = list(
        session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calculation.id,
                CalculationParameter.source == ParameterSource.parser,
                CalculationParameter.artifact_kind == other_kind,
            )
        )
    )
    new_identities = {_identity(o.canonical_key, o.section, o.raw_key, o.parameter_index) for o in observations}
    stored_identities = {_identity(r.canonical_key, r.section, r.raw_key, r.parameter_index) for r in stored}

    new_values = _values_by_key((o.canonical_key, o.canonical_value) for o in observations)
    stored_values = _values_by_key((r.canonical_key, r.canonical_value) for r in stored)
    warnings: list[UploadWarning] = []
    for key in sorted(new_values.keys() & stored_values.keys()):
        if new_values[key] != stored_values[key]:
            deck, log = (
                (new_values[key], stored_values[key])
                if artifact_kind is ArtifactKind.input
                else (stored_values[key], new_values[key])
            )
            warnings.append(_conflict_warning(key, software, deck, log))

    if artifact_kind is ArtifactKind.output_log:
        return [
            o
            for o in observations
            if _identity(o.canonical_key, o.section, o.raw_key, o.parameter_index) not in stored_identities
        ], warnings

    for row in stored:
        if _identity(row.canonical_key, row.section, row.raw_key, row.parameter_index) in new_identities:
            session.delete(row)
    return list(observations), warnings


# ---------------------------------------------------------------------------
# typed against read
# ---------------------------------------------------------------------------


def declared_software(calculation: Calculation) -> str | None:
    release = calculation.software_release
    if release is None or release.software is None or not release.software.name:
        return None
    return release.software.name.strip().lower()


def declared_parameter_warnings(
    session: Session,
    calculation: Calculation,
    stored_rows: Sequence[CalculationParameter],
    *,
    parsed_software: str,
    artifact_kind: ArtifactKind,
) -> list[UploadWarning]:
    """Warn for each key the depositor typed and ``stored_rows`` (just read) contradict.

    Only the keys in ``stored_rows`` are judged, so an artifact whose rows were all dropped in favour of the
    deck does not repeat the deck's warning.
    """
    declared = declared_software(calculation)
    if declared is None or declared != parsed_software.strip().lower():
        return []
    read = _values_by_key((r.canonical_key, r.canonical_value) for r in stored_rows)
    keys = set(read) - _NOT_RECONCILED_HERE
    if not keys:
        return []

    scientific = set(
        session.scalars(
            select(CalculationParameterVocab.canonical_key).where(
                CalculationParameterVocab.canonical_key.in_(keys),
                CalculationParameterVocab.affects_scientific_result.is_(True),
            )
        )
    )
    typed_rows = session.scalars(
        select(CalculationParameter).where(
            CalculationParameter.calculation_id == calculation.id,
            CalculationParameter.source == ParameterSource.upload,
            CalculationParameter.canonical_key.in_(scientific),
        )
    )
    typed = _values_by_key((r.canonical_key, r.canonical_value) for r in typed_rows)

    warnings: list[UploadWarning] = []
    for key in sorted(scientific & typed.keys()):
        if typed[key] != read[key]:
            warnings.append(
                UploadWarning.from_code(
                    field="parameters",
                    code=W_PARAMETER_DISAGREES_WITH_ARTIFACT,
                    context={
                        "canonical_key": key,
                        "software": declared,
                        "observed_in": artifact_kind.value,
                        "depositor_supplied": {
                            "declared_value": sorted(typed[key]),
                            "observed_value": sorted(read[key]),
                        },
                    },
                )
            )
    return warnings


# ---------------------------------------------------------------------------
# method and basis
# ---------------------------------------------------------------------------

_SPIN_PREFIXES = ("ro", "u", "r")
_BASIS_POLARISATION = (("(d,p)", "**"), ("(d)", "*"))
#: Basis tokens that name no basis set (a general-basis block follows).
_NO_BASIS = frozenset({"gen", "genecp", "chkbasis", "read"})


def _strip_options(method: str) -> str:
    """``CCSD(T,Full)`` -> ``CCSD(T)``; ``MP2(FC)`` -> ``MP2``: drop a trailing core option from the method."""
    lowered = method.strip().lower()
    lowered = re.sub(r",(full|fc|frozencore)\)$", ")", lowered)
    return re.sub(r"\((full|fc|frozencore)\)$", "", lowered)


def _method_keys(method: str) -> set[str]:
    keys = {method_identity_key(method), method_identity_key(_strip_options(method))}
    for key in list(keys):
        for prefix in _SPIN_PREFIXES:
            if key.startswith(prefix) and len(key) > len(prefix) + 1:
                keys.add(key[len(prefix):])
    return keys


def _basis_key(basis: str | None) -> str | None:
    key = basis_identity_key(basis)
    if key is None:
        return None
    for spelled, star in _BASIS_POLARISATION:
        key = key.replace(spelled, star)
    return key


def level_of_theory_warnings(
    calculation: Calculation,
    method_basis: dict | None,
    *,
    parsed_software: str,
    artifact_kind: ArtifactKind,
) -> list[UploadWarning]:
    """Warn when the parsed method or basis differs from the declared level of theory (never refuses).

    Silent when the parsed side states nothing, when the declaration states no basis, when the basis is a
    general-basis marker, and when the calculation's declared program is not the parser's program.
    """
    lot = calculation.lot
    declared = declared_software(calculation)
    if (
        not method_basis
        or lot is None
        or declared is None
        or declared != parsed_software.strip().lower()
    ):
        return []
    warnings: list[UploadWarning] = []

    parsed_methods = method_basis.get("methods") or ([method_basis["method"]] if method_basis.get("method") else [])
    if parsed_methods and lot.method:
        # A deck can name several methods (a correlated method and its reference); the declaration agrees
        # with the deck when it matches any of them.
        if not any(method_identity_key(lot.method) in _method_keys(m) for m in parsed_methods):
            warnings.append(_lot_warning("method", declared, artifact_kind, lot.method, parsed_methods[-1]))

    parsed_basis = method_basis.get("basis")
    if (
        parsed_basis
        and lot.basis
        and parsed_basis.strip().lower() not in _NO_BASIS
        and _basis_key(lot.basis) != _basis_key(parsed_basis)
    ):
        warnings.append(_lot_warning("basis", declared, artifact_kind, lot.basis, parsed_basis))
    return warnings


def _lot_warning(component: str, software: str, kind: ArtifactKind, declared_text: str, observed_text: str) -> UploadWarning:
    return UploadWarning.from_code(
        field="level_of_theory",
        code=W_LEVEL_OF_THEORY_DISAGREES_WITH_ARTIFACT,
        context={
            "component": component,
            "software": software,
            "observed_in": kind.value,
            "depositor_supplied": {"declared_value": declared_text, "observed_value": observed_text},
        },
    )
