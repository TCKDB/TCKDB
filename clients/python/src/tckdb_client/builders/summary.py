"""Builder-side preview summary surface.

Phase-1 implementation of the design captured in
``clients/python/docs/builder_summary_design.md``. Adds a small
human- and machine-readable ``UploadSummary`` for both
:class:`ComputedSpeciesUpload` and :class:`ComputedReactionUpload`,
plus the per-upload-kind collectors that fill it in.

The summary is a *viewer* of builder state, not a second wire
representation. The canonical wire shape remains
``upload.to_payload()``. The summary is for CLI dry-runs,
notebook previews, workflow logs, pre-upload review, and tests.

Stability layering (see §7 of the design doc):

* ``UploadSummary`` and ``upload.summary()`` are public-beta.
* ``summary.to_dict()`` keys are public-beta: new keys may land
  in minor versions; renaming or removing one is a major version
  event.
* ``summary.to_text()`` formatting is intentionally **not stable**.
  Tests must not assert on substrings beyond the stable section
  markers exported as ``SECTION_MARKERS``.

The collectors deliberately stay close to the public iteration
helpers on the upload classes (``iter_calculations``,
``iter_calculation_entries``, ``iter_artifacts``,
``emission_diagnostics``) so the summary cannot drift from the
upload state without those helpers drifting too.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from tckdb_client.builders.base import KeyMinter

if TYPE_CHECKING:  # pragma: no cover — type-only imports
    from tckdb_client.builders.uploads import (
        ComputedReactionUpload,
        ComputedSpeciesUpload,
    )


__all__ = [
    "SECTION_MARKERS",
    "UploadSummary",
    "summarise_computed_species_upload",
    "summarise_computed_reaction_upload",
]


# Stable section markers used by ``to_text()``. Tests are free to
# pin these strings; the wording around them is not stable.
SECTION_MARKERS: tuple[str, ...] = (
    "Identity",
    "Calculations",
    "Scientific blocks",
    "Artifacts",
    "Diagnostics",
)


@dataclass(frozen=True)
class UploadSummary:
    """Human- and machine-readable preview of one builder upload.

    Construct via :meth:`ComputedSpeciesUpload.summary` or
    :meth:`ComputedReactionUpload.summary`. Two emission methods:

    - :meth:`to_dict` returns a JSON-serialisable dict whose keys
      are part of the public-beta surface (§7 of the design doc).
    - :meth:`to_text` returns a human-readable string. The exact
      formatting may change between minor versions; tests may
      assert on the :data:`SECTION_MARKERS` section labels but
      not on arbitrary substrings.
    """

    kind: str
    data: Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public emission
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable copy of the summary fields."""
        return dict(self.data)

    def to_text(self) -> str:
        """Return a human-readable preview string."""
        if self.kind == "computed_species":
            return _render_computed_species_text(self.data)
        if self.kind == "computed_reaction":
            return _render_computed_reaction_text(self.data)
        # Defensive: if a future upload kind grows a summary collector
        # but forgets to add a renderer, fall back to a key=value dump
        # so the data is at least visible.
        return _render_generic_text(self.kind, self.data)


# =====================================================================
# Computed-species collector
# =====================================================================


def summarise_computed_species_upload(
    upload: "ComputedSpeciesUpload",
) -> UploadSummary:
    """Build the :class:`UploadSummary` for a
    :class:`ComputedSpeciesUpload`."""
    calc_counts_by_type: Counter[str] = Counter()
    for calc in upload.iter_calculations():
        calc_counts_by_type[calc.type] += 1

    primary = upload.primary_calculation
    primary_label = getattr(primary, "label", None) if primary is not None else None
    primary_type = getattr(primary, "type", None) if primary is not None else None
    primary_key = _mint_primary_calc_key(primary, upload.calculations)

    thermo = upload.thermo
    has_thermo = thermo is not None
    thermo_kind = _thermo_kind(thermo)

    artifact_count, artifact_calc_count = _artifact_counts(upload)
    diag_count, diag_codes = _diagnostic_summary(upload)

    species = upload.species
    data: dict[str, Any] = {
        "kind": "computed_species",
        "species_smiles": getattr(species, "smiles", None),
        "species_label": getattr(species, "label", None),
        "charge": getattr(species, "charge", None),
        "multiplicity": getattr(species, "multiplicity", None),
        # Today: always 1 conformer record per the conformer-boundary
        # policy. Surfaced as a field so a future change is visible
        # without callers re-deriving it.
        "conformer_record_count": 1,
        "calculation_count": sum(calc_counts_by_type.values()),
        "calculation_counts_by_type": dict(sorted(calc_counts_by_type.items())),
        "primary_calculation_label": primary_label,
        "primary_calculation_key": primary_key,
        "primary_calculation_type": primary_type,
        "has_thermo": has_thermo,
        "thermo_kind": thermo_kind,
        "has_statmech": upload.statmech is not None,
        "has_transport": upload.transport is not None,
        "artifact_count": artifact_count,
        "artifact_calculation_count": artifact_calc_count,
        "diagnostic_count": diag_count,
        "diagnostic_codes": diag_codes,
    }
    return UploadSummary(kind="computed_species", data=data)


# =====================================================================
# Computed-reaction collector
# =====================================================================


def summarise_computed_reaction_upload(
    upload: "ComputedReactionUpload",
) -> UploadSummary:
    """Build the :class:`UploadSummary` for a
    :class:`ComputedReactionUpload`."""
    reaction = upload.reaction

    reactant_smiles = [_species_smiles(sp) for sp in reaction.reactants]
    reactant_labels = [_species_label(sp) for sp in reaction.reactants]
    product_smiles = [_species_smiles(sp) for sp in reaction.products]
    product_labels = [_species_label(sp) for sp in reaction.products]

    ts_counts: Counter[str] = Counter()
    species_calc_counts: dict[str, int] = {}
    species_calc_counts_by_type: dict[str, dict[str, int]] = {}

    for entry in upload.iter_calculation_entries():
        if entry.bucket == "TS":
            ts_counts[entry.calculation.type] += 1
        else:
            species_calc_counts[entry.bucket] = (
                species_calc_counts.get(entry.bucket, 0) + 1
            )
            per_type = species_calc_counts_by_type.setdefault(entry.bucket, {})
            per_type[entry.calculation.type] = (
                per_type.get(entry.calculation.type, 0) + 1
            )

    # Sort each species's per-type counter for deterministic dict
    # ordering. Outer keys follow the order ``iter_calculation_entries``
    # already produces (``unique_species()`` order).
    species_calc_counts_by_type = {
        sp: dict(sorted(per_type.items()))
        for sp, per_type in species_calc_counts_by_type.items()
    }

    # Per-species scientific blocks. ``species_thermo`` etc. on the
    # upload are dict[Species, Thermo] | None keyed by the user-supplied
    # Species instances; iterate ``unique_species()`` to fix the order.
    species_with_thermo: list[str] = []
    species_with_statmech: list[str] = []
    species_with_transport: list[str] = []
    for sp in reaction.unique_species():
        bucket = _species_bucket(sp)
        if upload.species_thermo and sp in upload.species_thermo:
            species_with_thermo.append(bucket)
        if upload.species_statmech and sp in upload.species_statmech:
            species_with_statmech.append(bucket)
        if upload.species_transport and sp in upload.species_transport:
            species_with_transport.append(bucket)

    artifact_count, artifact_calc_count = _artifact_counts(upload)
    diag_count, diag_codes = _diagnostic_summary(upload)

    data: dict[str, Any] = {
        "kind": "computed_reaction",
        "reactant_smiles": reactant_smiles,
        "reactant_labels": reactant_labels,
        "product_smiles": product_smiles,
        "product_labels": product_labels,
        "reaction_family": reaction.family,
        "species_count": len(reaction.unique_species()),
        "ts_calculation_counts_by_type": dict(sorted(ts_counts.items())),
        "species_calculation_counts": dict(species_calc_counts),
        "species_calculation_counts_by_type": species_calc_counts_by_type,
        "kinetics_count": len(reaction.kinetics),
        "species_with_thermo": species_with_thermo,
        "species_with_statmech": species_with_statmech,
        "species_with_transport": species_with_transport,
        "artifact_count": artifact_count,
        "artifact_calculation_count": artifact_calc_count,
        "diagnostic_count": diag_count,
        "diagnostic_codes": diag_codes,
    }
    return UploadSummary(kind="computed_reaction", data=data)


# =====================================================================
# Shared helpers
# =====================================================================


def _artifact_counts(upload: Any) -> tuple[int, int]:
    """Return ``(total_artifacts, distinct_calculations_with_artifacts)``."""
    total = 0
    calcs_with_artifacts: list[Any] = []
    for calc, _art in upload.iter_artifacts():
        total += 1
        if not any(c is calc for c in calcs_with_artifacts):
            calcs_with_artifacts.append(calc)
    return total, len(calcs_with_artifacts)


def _diagnostic_summary(upload: Any) -> tuple[int, list[str]]:
    """Return ``(diagnostic_count, sorted_unique_codes)``."""
    diags = list(upload.emission_diagnostics())
    codes = sorted({d.code for d in diags})
    return len(diags), codes


def _thermo_kind(thermo: Any) -> str | None:
    """Return the thermo factory's kind tag (``scalar`` / ``nasa`` /
    ``points``), or ``None`` if no thermo block is attached."""
    if thermo is None:
        return None
    # ``Thermo.kind`` is the public read of the factory tag; the bare
    # constructor leaves it as ``"generic"``. We surface whatever
    # tag the builder reports without coupling to the wire-side
    # discrimination.
    kind = getattr(thermo, "kind", None)
    if not kind:
        return None
    return kind


def _species_smiles(sp: Any) -> str | None:
    return getattr(sp, "smiles", None)


def _species_label(sp: Any) -> str | None:
    return getattr(sp, "label", None)


def _species_bucket(sp: Any) -> str:
    """Match the bucket label used by ``iter_calculation_entries``."""
    return (
        getattr(sp, "label", None)
        or getattr(sp, "smiles", None)
        or "<species>"
    )


def _mint_primary_calc_key(
    primary: Any, calculations: list[Any]
) -> str | None:
    """Replicate ``to_payload``'s minter walk to recover the primary
    calc's key.

    The summary must be derivable without invoking ``to_payload()``
    (see §0 of the design doc), but it also needs to surface a key
    that correlates back to the eventual payload. We replicate the
    minter walk in isolation — small and cheap — so the value
    matches what ``to_payload`` will emit.
    """
    if primary is None:
        return None
    minter = KeyMinter(prefix="calc")
    for calc in calculations:
        minter.mint(calc, label=getattr(calc, "label", None))
    try:
        return minter.lookup(primary)
    except Exception:  # pragma: no cover — defensive
        return None


# =====================================================================
# Text renderers
# =====================================================================


def _render_computed_species_text(data: Mapping[str, Any]) -> str:
    lines: list[str] = ["ComputedSpeciesUpload"]

    # Identity
    lines.append("Identity:")
    smiles = data.get("species_smiles")
    label = data.get("species_label")
    species_repr = label or smiles or "<unlabelled>"
    lines.append(f"  species:        {species_repr}")
    if smiles and smiles != species_repr:
        lines.append(f"  smiles:         {smiles}")
    lines.append(f"  charge:         {data.get('charge')}")
    lines.append(f"  multiplicity:   {data.get('multiplicity')}")
    lines.append(
        f"  conformer rec.: {data.get('conformer_record_count')}"
    )

    # Calculations
    lines.append("Calculations:")
    lines.append(f"  total:          {data.get('calculation_count')}")
    by_type = data.get("calculation_counts_by_type") or {}
    if by_type:
        rendered = ", ".join(f"{k}={v}" for k, v in by_type.items())
        lines.append(f"  by type:        {rendered}")
    if data.get("primary_calculation_label") is not None:
        lines.append(
            f"  primary:        {data['primary_calculation_label']!r} "
            f"(type={data.get('primary_calculation_type')}, "
            f"key={data.get('primary_calculation_key')})"
        )
    else:
        lines.append(
            f"  primary:        (type={data.get('primary_calculation_type')}, "
            f"key={data.get('primary_calculation_key')})"
        )

    # Scientific blocks
    lines.append("Scientific blocks:")
    thermo_kind = data.get("thermo_kind")
    if data.get("has_thermo"):
        lines.append(f"  thermo:         yes ({thermo_kind or 'generic'})")
    else:
        lines.append("  thermo:         no")
    lines.append(
        f"  statmech:       {'yes' if data.get('has_statmech') else 'no'}"
    )
    lines.append(
        f"  transport:      {'yes' if data.get('has_transport') else 'no'}"
    )

    # Artifacts
    lines.append("Artifacts:")
    lines.append(f"  total:          {data.get('artifact_count', 0)}")
    lines.append(
        f"  on calcs:       {data.get('artifact_calculation_count', 0)}"
    )

    # Diagnostics
    lines.append("Diagnostics:")
    diag_count = data.get("diagnostic_count", 0)
    lines.append(f"  total:          {diag_count}")
    codes = data.get("diagnostic_codes") or []
    if codes:
        lines.append("  codes:")
        for code in codes:
            lines.append(f"    - {code}")

    return "\n".join(lines)


def _render_computed_reaction_text(data: Mapping[str, Any]) -> str:
    lines: list[str] = ["ComputedReactionUpload"]

    # Identity
    lines.append("Identity:")
    lines.append(
        f"  reactants:      {_format_species_list(data, 'reactant_labels', 'reactant_smiles')}"
    )
    lines.append(
        f"  products:       {_format_species_list(data, 'product_labels', 'product_smiles')}"
    )
    family = data.get("reaction_family")
    lines.append(f"  family:         {family if family is not None else '-'}")
    lines.append(f"  species:        {data.get('species_count')}")

    # Calculations
    lines.append("Calculations:")
    ts_by_type = data.get("ts_calculation_counts_by_type") or {}
    ts_total = sum(ts_by_type.values())
    if ts_by_type:
        rendered = ", ".join(f"{k}={v}" for k, v in ts_by_type.items())
        lines.append(f"  ts:             {ts_total} total ({rendered})")
    else:
        lines.append("  ts:             0")
    species_counts = data.get("species_calculation_counts") or {}
    if species_counts:
        rendered = ", ".join(f"{k}={v}" for k, v in species_counts.items())
        lines.append(f"  species:        {rendered}")
    else:
        lines.append("  species:        (none)")
    lines.append(f"  kinetics:       {data.get('kinetics_count', 0)}")

    # Scientific blocks
    lines.append("Scientific blocks:")
    lines.append(
        f"  thermo on:      {_format_species_keys(data.get('species_with_thermo'))}"
    )
    lines.append(
        f"  statmech on:    {_format_species_keys(data.get('species_with_statmech'))}"
    )
    lines.append(
        f"  transport on:   {_format_species_keys(data.get('species_with_transport'))}"
    )

    # Artifacts
    lines.append("Artifacts:")
    lines.append(f"  total:          {data.get('artifact_count', 0)}")
    lines.append(
        f"  on calcs:       {data.get('artifact_calculation_count', 0)}"
    )

    # Diagnostics
    lines.append("Diagnostics:")
    diag_count = data.get("diagnostic_count", 0)
    lines.append(f"  total:          {diag_count}")
    codes = data.get("diagnostic_codes") or []
    if codes:
        lines.append("  codes:")
        for code in codes:
            lines.append(f"    - {code}")

    return "\n".join(lines)


def _render_generic_text(kind: str, data: Mapping[str, Any]) -> str:  # pragma: no cover
    lines = [f"UploadSummary(kind={kind!r})"]
    for key, value in data.items():
        lines.append(f"  {key}: {value!r}")
    return "\n".join(lines)


def _format_species_list(
    data: Mapping[str, Any], labels_key: str, smiles_key: str
) -> str:
    labels = data.get(labels_key) or []
    smiles = data.get(smiles_key) or []
    parts: list[str] = []
    for i in range(max(len(labels), len(smiles))):
        lbl = labels[i] if i < len(labels) else None
        smi = smiles[i] if i < len(smiles) else None
        parts.append(lbl or smi or "?")
    return ", ".join(parts) if parts else "(none)"


def _format_species_keys(keys: list[str] | None) -> str:
    if not keys:
        return "(none)"
    return ", ".join(keys)
