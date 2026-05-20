"""Builder: CCCBDB statmech record → ``StatmechUploadRequest`` dict.

Mapped to first-class fields:

* ``statmech.point_group``        → ``StatmechUploadRequest.point_group``
* ``statmech.symmetry_number``    → ``StatmechUploadRequest.external_symmetry``

Preserved as ``external_source.unparsed`` + a warning (no first-class
home on the existing schema):

* ``statmech.frequencies`` (experimental mode list)
* ``statmech.rotational_constants`` (A/B/C in GHz)
* ``statmech.zpe_kj_mol``

We deliberately do **not** route experimental vibrational frequencies
into ``calc_freq_mode``: that table is calculation-scoped and creating
a placeholder ``Calculation`` row just to host experimental data would
violate the spec's "no fake calculations" rule.
"""

from __future__ import annotations

from typing import Any

from app.importers.cccbdb.builders.common import value_ref_to_dict
from app.importers.cccbdb.models import (
    CCCBDBExperimentalSpeciesRecord,
    CCCBDBFrequencyMode,
    CCCBDBRotationalConstants,
)


def build_statmech_payload(
    record: CCCBDBExperimentalSpeciesRecord,
    species_entry_payload: dict[str, Any] | None,
    warnings: list[str],
    per_value_refs: dict[str, dict[str, Any]],
    unparsed: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a ``StatmechUploadRequest``-compatible dict.

    Returns ``None`` when the parsed record has no point group,
    symmetry number, frequencies, rotational constants, or ZPE.
    """

    statmech = record.statmech
    has_any = any(
        [
            statmech.point_group is not None,
            statmech.symmetry_number is not None,
            statmech.frequencies,
            statmech.rotational_constants is not None,
            statmech.zpe_kj_mol is not None,
        ]
    )
    if not has_any:
        return None

    payload: dict[str, Any] = {
        "scientific_origin": "experimental",
    }
    if species_entry_payload is not None:
        payload["species_entry"] = species_entry_payload
    if statmech.point_group is not None:
        payload["point_group"] = statmech.point_group
    if statmech.symmetry_number is not None:
        payload["external_symmetry"] = statmech.symmetry_number

    if statmech.frequencies:
        unparsed["statmech_frequencies"] = [
            _freq_to_dict(m) for m in statmech.frequencies
        ]
        warnings.append(
            "statmech: experimental vibrational modes have no "
            "first-class TCKDB destination (calc_freq_mode is "
            "calculation-scoped); preserved in "
            "external_source.unparsed.statmech_frequencies"
        )

    if statmech.rotational_constants is not None:
        unparsed["statmech_rotational_constants"] = _rot_to_dict(
            statmech.rotational_constants
        )
        warnings.append(
            "statmech: rotational constants have no first-class "
            "TCKDB field; preserved in "
            "external_source.unparsed.statmech_rotational_constants"
        )

    if statmech.zpe_kj_mol is not None:
        unparsed["statmech_zpe_kj_mol"] = statmech.zpe_kj_mol
        warnings.append(
            "statmech: experimental ZPE has no first-class TCKDB "
            "field; preserved in external_source.unparsed.statmech_zpe_kj_mol"
        )

    if statmech.reference is not None:
        ref_dict = value_ref_to_dict(statmech.reference)
        if ref_dict is not None:
            per_value_refs["statmech"] = ref_dict

    return payload


def _freq_to_dict(mode: CCCBDBFrequencyMode) -> dict[str, Any]:
    return {
        "mode_index": mode.mode_index,
        "frequency_cm1": mode.frequency_cm1,
        "symmetry_label": mode.symmetry_label,
        "raw_value": mode.raw_value,
        "raw_units": mode.raw_units,
        "reference": value_ref_to_dict(mode.reference),
    }


def _rot_to_dict(rc: CCCBDBRotationalConstants) -> dict[str, Any]:
    return {
        "a_ghz": rc.a_ghz,
        "b_ghz": rc.b_ghz,
        "c_ghz": rc.c_ghz,
        "raw_units": rc.raw_units,
        "raw_values": list(rc.raw_values),
        "reference": value_ref_to_dict(rc.reference),
    }
