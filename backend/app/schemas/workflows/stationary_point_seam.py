"""Shared traversal for the standalone product uploads' inline calculations.

``StatmechUploadRequest``, ``ThermoUploadRequest`` and
``TransportUploadRequest`` all have the same shape at the seam that
matters here: one ``species_entry`` declaring a stationary-point kind,
plus a list of locally-keyed inline calculations scoped to that entry.
Three copies of the same walk would be three places for the tiers to
drift apart, so the walk lives here and each request calls it.

The physics itself is owned by :mod:`tckdb_schemas.stationary_point`;
this module only knows payload shapes.
"""

from __future__ import annotations

from typing import Protocol

from tckdb_schemas.enums import StationaryPointKind
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    evaluate_species_entry_frequency,
)

from app.schemas.fragments.calculation import CalculationWithResultsPayload


class KeyedCalculation(Protocol):
    """An inline calculation carrying a payload-local string key."""

    key: str
    calculation: CalculationWithResultsPayload


def inline_calculation_findings(
    kind: StationaryPointKind,
    calculations: list[KeyedCalculation],
) -> list[StationaryPointFinding]:
    """Judge ``kind`` against every inline calculation's frequency evidence.

    :param kind: The stationary-point kind declared on the request's
        ``species_entry``.
    :param calculations: The request's locally-keyed inline calculations.
    :returns: Findings across all of them, possibly empty.
    """
    findings: list[StationaryPointFinding] = []
    for item in calculations:
        freq_result = item.calculation.freq_result
        if freq_result is None:
            continue
        location = f"calculations['{item.key}'].freq_result"
        findings.extend(
            evaluate_species_entry_frequency(
                kind,
                freq_result.n_imag,
                freq_result.imag_freq_cm1,
                location=location,
            )
        )
        # No reference geometry to fall back to: these three requests
        # carry a product and its evidence, never a conformer. So the
        # completeness question is answerable only for a calculation that
        # named the geometry it ran on, and stays silent otherwise —
        # which is the same rule the bundles apply, reached by a
        # different route.
        findings.extend(
            item.calculation.frequency_completeness_findings(
                location=f"{location}.modes"
            )
        )
    return findings


__all__ = ["KeyedCalculation", "inline_calculation_findings"]
