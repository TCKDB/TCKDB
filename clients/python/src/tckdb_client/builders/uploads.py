"""Top-level upload-object builders.

Phase 1 ships :class:`ComputedSpeciesUpload` targeting
``POST /api/v1/uploads/computed-species``. Phase 2 adds
:class:`ComputedReactionUpload` targeting
``POST /api/v1/uploads/computed-reaction``. Both are payload-construction
aids, not parallel APIs — the server remains authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

from tckdb_client.builders.artifact import Artifact, PlannedArtifactUpload
from tckdb_client.builders.base import KeyMinter
from tckdb_client.builders.calculation import Calculation
from tckdb_client.builders.diagnostics import DIAG_CODES, Diagnostic
from tckdb_client.builders.geometry import Geometry
from tckdb_client.builders.reaction import ChemReaction, TransitionState
from tckdb_client.builders.species import Species
from tckdb_client.builders.statmech import Statmech
from tckdb_client.builders.thermo import Thermo
from tckdb_client.builders.transport import Transport
from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_optional_non_empty_str,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import
    from tckdb_client.builders.summary import UploadSummary

__all__ = [
    "CalculationEntry",
    "ComputedSpeciesUpload",
    "ComputedReactionUpload",
]


@dataclass(frozen=True)
class CalculationEntry:
    """One `(bucket, species, calculation)` row from a builder walk.

    Yielded by :meth:`ComputedSpeciesUpload.iter_calculation_entries`
    and :meth:`ComputedReactionUpload.iter_calculation_entries` so
    producer code can iterate every calc in an upload — and know
    *which* bucket each calc lives in — without reaching into the
    private ``_species_calc_pairs`` attribute.

    - ``bucket`` is a short, human-readable label: ``"TS"`` for the
      transition-state bucket on the reaction side, or the species's
      ``label`` / ``smiles`` for species-side calcs.
    - ``species`` is the :class:`Species` builder the calc is
      attached to. ``None`` for TS-side reaction calculations.
    - ``calculation`` is the :class:`Calculation` builder itself.

    Frozen so producers can safely store, sort, or aggregate entries.
    """

    bucket: str
    species: "Species | None"
    calculation: Calculation


@dataclass
class ComputedSpeciesUpload:
    """Bundle upload for one computed species.

    The bundle endpoint expects exactly one species identity plus one
    conformer with a primary ``opt`` calculation and zero or more
    additional calculations. The Phase-1 builder maps to that shape
    by treating every calculation past ``primary_calculation`` as an
    "additional" calculation on a single synthesised conformer.
    """

    species: Species
    calculations: list[Calculation]
    primary_calculation: Calculation | None = None
    thermo: Thermo | None = None
    statmech: Statmech | None = None
    transport: Transport | None = None
    note: str | None = None

    upload_kind: str = field(default="computed_species", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.species, Species):
            raise TCKDBBuilderValidationError(
                "ComputedSpeciesUpload.species must be a Species builder."
            )
        if not self.calculations:
            raise TCKDBBuilderValidationError(
                "ComputedSpeciesUpload.calculations must contain at least one "
                "Calculation."
            )
        for i, calc in enumerate(self.calculations):
            if not isinstance(calc, Calculation):
                raise TCKDBBuilderValidationError(
                    f"calculations[{i}] must be a Calculation builder."
                )

        if self.primary_calculation is None:
            self.primary_calculation = self._pick_primary_calculation()
        if not _is_in(self.primary_calculation, self.calculations):
            raise TCKDBBuilderValidationError(
                "primary_calculation must be one of the entries in "
                "calculations."
            )
        if self.primary_calculation.type != "opt":
            raise TCKDBBuilderValidationError(
                "ComputedSpeciesUpload.primary_calculation.type must be "
                f"'opt', got {self.primary_calculation.type!r}; the "
                "bundle endpoint anchors each conformer on an opt."
            )

        # Every depends_on edge must resolve inside this upload.
        for calc in self.calculations:
            for dep in calc.depends_on:
                if not _is_in(dep, self.calculations):
                    raise TCKDBBuilderValidationError(
                        "every depends_on target must also be included in "
                        "ComputedSpeciesUpload.calculations."
                    )

        if self.thermo is not None:
            if not isinstance(self.thermo, Thermo):
                raise TCKDBBuilderValidationError(
                    "ComputedSpeciesUpload.thermo must be a Thermo builder."
                )
            # Thermo source_calculations (when supplied) must resolve
            # against this upload's calculation bucket. The backend's
            # ``ThermoInBundle`` schema accepts the field and validates
            # uniqueness by (calculation_key, role), but does not know
            # which calcs belong to which species — that's the bundle's
            # bookkeeping and the builder's job.
            for role, calc in self.thermo.source_calculations:
                if not _is_in(calc, self.calculations):
                    raise TCKDBBuilderValidationError(
                        f"thermo.source_calculations role={role!r} "
                        "references a Calculation that is not in "
                        "ComputedSpeciesUpload.calculations."
                    )

        if self.statmech is not None:
            if not isinstance(self.statmech, Statmech):
                raise TCKDBBuilderValidationError(
                    "ComputedSpeciesUpload.statmech must be a Statmech "
                    "builder."
                )
            for role, calc in self.statmech.source_calculations:
                if not _is_in(calc, self.calculations):
                    raise TCKDBBuilderValidationError(
                        f"statmech.source_calculations role={role!r} "
                        "references a Calculation that is not in "
                        "ComputedSpeciesUpload.calculations."
                    )

        if self.transport is not None:
            # Forward-compat acceptance: the computed-species bundle
            # schema does not yet carry a ``transport`` field, so this
            # builder validates references locally but does not emit
            # the block on the wire. See ``transport.py`` and the
            # README's transport section for the rationale.
            if not isinstance(self.transport, Transport):
                raise TCKDBBuilderValidationError(
                    "ComputedSpeciesUpload.transport must be a Transport "
                    "builder."
                )
            for role, calc in self.transport.source_calculations:
                if not _is_in(calc, self.calculations):
                    raise TCKDBBuilderValidationError(
                        f"transport.source_calculations role={role!r} "
                        "references a Calculation that is not in "
                        "ComputedSpeciesUpload.calculations."
                    )

        self.note = ensure_optional_non_empty_str(self.note, field="note")

    # ------------------------------------------------------------------
    # Payload assembly
    # ------------------------------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        """Return the dict shape accepted by ``ComputedSpeciesUploadRequest``.

        Two invariants underpin determinism (§10, §11 of the spec):

        - Local keys are minted from ``label`` slugs when present and
          insertion-order indices otherwise. Repeat calls on the same
          object graph produce byte-identical bytes.
        - No ``id()`` / set iteration / random source is used inside
          payload generation; cross-object references resolve via the
          calculations list with ``is``-comparison.
        """
        calc_keys = KeyMinter(prefix="calc")
        for calc in self.calculations:
            calc_keys.mint(calc, label=calc.label)

        assert self.primary_calculation is not None
        conformer_geometry = self._conformer_geometry()
        conformer_key_minter = KeyMinter(prefix="conformer")
        conformer_key = conformer_key_minter.mint(
            self.primary_calculation,
            label=self.primary_calculation.label,
        )

        primary_payload = self._calc_payload(
            self.primary_calculation, calc_keys
        )
        additional_payloads = [
            self._calc_payload(calc, calc_keys)
            for calc in self.calculations
            if calc is not self.primary_calculation
        ]

        bundle: dict[str, Any] = {
            "species_entry": self.species.to_identity_payload(),
            "conformers": [
                {
                    "key": conformer_key,
                    "geometry": conformer_geometry.to_payload(),
                    "primary_calculation": primary_payload,
                    "additional_calculations": additional_payloads,
                }
            ],
        }
        if self.thermo is not None:
            # ``ThermoInBundle`` (computed-species) supports
            # ``source_calculations`` natively — emit them, resolving
            # each :class:`Calculation` against the calc keys we just
            # minted. This is the key difference from the
            # computed-reaction emission path, where the schema does
            # not yet carry the field.
            bundle["thermo"] = self.thermo.to_payload(
                allow_source_calculations=True,
                calc_key_lookup=calc_keys.lookup,
            )
        if self.statmech is not None:
            # ``StatmechInBundle`` always carries ``source_calculations``;
            # the builder unconditionally resolves and emits them.
            bundle["statmech"] = self.statmech.to_payload(
                allow_source_calculations=True,
                calc_key_lookup=calc_keys.lookup,
            )
        if self.note is not None:
            bundle["note"] = self.note
        return bundle

    # ------------------------------------------------------------------
    # Emission diagnostics
    # ------------------------------------------------------------------

    def emission_diagnostics(self) -> list[Diagnostic]:
        """Report fields that the builder accepts but cannot send today.

        Each entry is a :class:`Diagnostic` with a stable ``code``,
        ``level``, ``message``, and ``path``. Producers can call this
        before :meth:`to_payload` (or, better,
        :meth:`tckdb_client.TCKDBClient.upload`) to see what data is
        about to be dropped on the wire because of a schema gap.
        Returns an empty list when every supplied field will be
        emitted.
        """
        out: list[Diagnostic] = []
        if self.transport is not None:
            out.append(
                Diagnostic(
                    level="warning",
                    code=DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_SPECIES_BUNDLE,
                    message=(
                        "Transport is accepted for forward compatibility but "
                        "the computed-species bundle schema does not yet "
                        "carry a transport field — the block will not be "
                        "emitted on the wire. Use the standalone "
                        "/uploads/transport endpoint to ship transport data "
                        "today."
                    ),
                    path="transport",
                )
            )
        for calc in self.calculations:
            if calc.artifacts:
                out.append(_artifact_second_phase_diag(calc))
        return out

    # ------------------------------------------------------------------
    # Artifact plan
    # ------------------------------------------------------------------

    def artifact_plan(self, upload_result: Any) -> list[PlannedArtifactUpload]:
        """Resolve attached artifacts against the server upload result.

        Returns a list of :class:`PlannedArtifactUpload` records,
        one per artifact, with ``calculation_id`` populated from the
        server response. Pass the returned list to
        :meth:`tckdb_client.TCKDBClient.upload_artifacts` to execute
        the second-phase uploads.

        Raises :class:`TCKDBBuilderValidationError` when:

        - the response does not match the computed-species shape
          (no ``conformers`` list with ``primary_calculation`` /
          ``additional_calculations`` entries carrying ``key`` and
          ``calculation_id``); the server may be older than the
          builder, or the response was tampered with;
        - a builder calculation has artifacts but its local key
          doesn't appear in the response — typically a workflow bug.
        """
        key_to_id = _extract_computed_species_calc_keys(upload_result)
        return _build_plan(self.calculations, key_to_id)

    def artifact_plan_preview(
        self, *, starting_calculation_id: int = 1000,
    ) -> list[PlannedArtifactUpload]:
        """Return the same shape as :meth:`artifact_plan` against
        synthetic calculation IDs — useful for offline demos, CI
        fixtures, and producer debugging.

        Synthetic IDs are minted deterministically by walking the
        upload's payload in payload order and assigning
        ``starting_calculation_id, +1, +2, …``. Same upload state →
        same preview, every time. The returned IDs are **not** real
        server-side calculation primary keys; they exist solely to
        let producers see what :meth:`artifact_plan` would produce
        once the bundle has been uploaded for real.
        """
        synthetic_response = _build_species_preview_response(
            self.to_payload(), start=starting_calculation_id,
        )
        return self.artifact_plan(synthetic_response)

    # ------------------------------------------------------------------
    # Public iteration
    # ------------------------------------------------------------------

    def iter_calculations(
        self, *, with_artifacts_only: bool = False,
    ) -> Iterator[Calculation]:
        """Yield every :class:`Calculation` in this upload in payload
        order. Set ``with_artifacts_only=True`` to skip calcs without
        attached artifacts — convenient for second-phase artifact
        planning."""
        for calc in self.calculations:
            if with_artifacts_only and not calc.artifacts:
                continue
            yield calc

    def iter_calculation_entries(
        self, *, with_artifacts_only: bool = False,
    ) -> Iterator[CalculationEntry]:
        """Yield :class:`CalculationEntry` rows tagged with their
        bucket and (where applicable) the :class:`Species` they're
        attached to.

        For computed-species uploads every calc shares the same
        bucket — the upload's species — so the entries carry that
        species. The method mirrors
        :meth:`ComputedReactionUpload.iter_calculation_entries`
        so producer code that walks both upload kinds doesn't have
        to branch on type.
        """
        bucket = self.species.label or self.species.smiles or "<species>"
        for calc in self.iter_calculations(
            with_artifacts_only=with_artifacts_only,
        ):
            yield CalculationEntry(
                bucket=bucket, species=self.species, calculation=calc,
            )

    def iter_artifacts(self) -> Iterator[tuple[Calculation, Artifact]]:
        """Yield ``(calculation, artifact)`` pairs, one per attached
        artifact, in walk order."""
        for calc in self.iter_calculations(with_artifacts_only=True):
            for art in calc.artifacts:
                yield calc, art

    def summary(self) -> "UploadSummary":
        """Return a small human- and machine-readable preview.

        See ``clients/python/docs/builder_summary_design.md`` for the
        stability contract; ``upload.to_payload()`` remains the
        canonical wire representation.
        """
        from tckdb_client.builders.summary import (
            summarise_computed_species_upload,
        )

        return summarise_computed_species_upload(self)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _pick_primary_calculation(self) -> Calculation:
        """Pick the first ``opt`` calculation when the caller omits one.

        Many producers think of "the calculations" and "the primary"
        as the same concept; the builder accepts that shorthand and
        promotes the first opt. Anything else makes the caller be
        explicit by passing ``primary_calculation=...``.
        """
        for calc in self.calculations:
            if calc.type == "opt":
                return calc
        raise TCKDBBuilderValidationError(
            "ComputedSpeciesUpload requires either an explicit "
            "primary_calculation=, or at least one opt calculation in "
            "calculations."
        )

    def _conformer_geometry(self) -> Geometry:
        """The conformer-level geometry, taken from the primary opt.

        The bundle schema requires a conformer geometry, so the primary
        opt must offer either an ``output_geometry`` (preferred) or an
        ``input_geometry`` (fallback). Failing both, raise locally —
        the server would otherwise reject the payload with 422.
        """
        assert self.primary_calculation is not None
        for source in ("output_geometry", "input_geometry"):
            geom = getattr(self.primary_calculation, source)
            if geom is not None:
                return geom
        raise TCKDBBuilderValidationError(
            "primary_calculation must declare output_geometry or "
            "input_geometry so the conformer carries a reference "
            "structure."
        )

    def _calc_payload(
        self, calc: Calculation, minter: KeyMinter
    ) -> dict[str, Any]:
        """Build the ``CalculationInBundle`` dict for one calculation."""
        out: dict[str, Any] = {
            "key": minter.lookup(calc),
            "type": calc.type,
            "level_of_theory": calc.level_of_theory.to_payload(),
            "software_release": calc.software_release.to_payload(),
        }

        if calc.input_geometry is not None:
            out["input_geometries"] = [calc.input_geometry.to_payload()]
        if calc.output_geometry is not None:
            # Phase 1: a singular ``output_geometry`` is always emitted
            # with role="final" — opt's converged structure, freq's
            # post-step structure, or sp's echo of the input.
            out["output_geometries"] = [
                {
                    "geometry": calc.output_geometry.to_payload(),
                    "role": "final",
                }
            ]

        if calc.depends_on:
            out["depends_on"] = [
                {
                    "parent_calculation_key": minter.lookup(parent),
                    "role": calc.infer_dependency_role(parent),
                }
                for parent in calc.depends_on
            ]

        result = calc.result_block()
        if result is not None:
            field_name, block = result
            out[field_name] = block

        return out


def _is_in(target: object, items: list[object]) -> bool:
    """``is``-comparison membership check (no hashing of builder objects)."""
    for item in items:
        if item is target:
            return True
    return False


# ---------------------------------------------------------------------------
# Artifact plan helpers
# ---------------------------------------------------------------------------


def _artifact_second_phase_diag(calc: Calculation) -> Diagnostic:
    """One ``artifact_upload_requires_second_phase`` diagnostic per calc."""
    label = calc.label or calc.type
    return Diagnostic(
        level="warning",
        code=DIAG_CODES.ARTIFACT_UPLOAD_REQUIRES_SECOND_PHASE,
        message=(
            f"Calculation {label!r} has "
            f"{len(calc.artifacts)} attached artifact(s). Artifacts are "
            "not included in the scientific upload payload — call "
            "upload.artifact_plan(result) on the upload response and "
            "pass the plan to client.upload_artifacts(plan)."
        ),
        path=f"calculations[{label}].artifacts",
    )


def _extract_computed_species_calc_keys(
    upload_result: Any,
) -> dict[str, int]:
    """Walk a ``ComputedSpeciesUploadResult`` for calc-key → id pairs.

    The computed-species response nests calc refs under each conformer
    (``conformers[i].primary_calculation`` plus
    ``conformers[i].additional_calculations``), every ref carrying
    both ``key`` and ``calculation_id``. We assemble that into a flat
    dict for plan resolution.
    """
    if not isinstance(upload_result, dict):
        raise TCKDBBuilderValidationError(
            "artifact_plan(upload_result) requires the server response "
            "dict from client.upload(upload). Got "
            f"{type(upload_result).__name__}."
        )
    conformers = upload_result.get("conformers")
    if not isinstance(conformers, list):
        raise TCKDBBuilderValidationError(
            "artifact_plan: upload_result does not look like a "
            "ComputedSpeciesUploadResult (missing 'conformers' list). "
            "Pass the dict client.upload(...) returned."
        )
    out: dict[str, int] = {}
    for ci, conf in enumerate(conformers):
        if not isinstance(conf, dict):
            raise TCKDBBuilderValidationError(
                f"artifact_plan: conformers[{ci}] is not a dict."
            )
        for slot in ("primary_calculation",):
            ref = conf.get(slot)
            if ref is None:
                continue
            _record_calc_ref(out, ref, f"conformers[{ci}].{slot}")
        for ai, ref in enumerate(conf.get("additional_calculations", [])):
            _record_calc_ref(
                out, ref,
                f"conformers[{ci}].additional_calculations[{ai}]",
            )
    return out


def _extract_computed_reaction_calc_keys(
    upload_result: Any,
) -> dict[str, int]:
    """Read ``calculation_keys`` off a ``ComputedReactionUploadResult``.

    Response-only field added alongside this PR. Servers older than
    that change won't have the field and the builder raises clearly.
    """
    if not isinstance(upload_result, dict):
        raise TCKDBBuilderValidationError(
            "artifact_plan(upload_result) requires the server response "
            "dict from client.upload(upload). Got "
            f"{type(upload_result).__name__}."
        )
    mapping = upload_result.get("calculation_keys")
    if not isinstance(mapping, dict):
        raise TCKDBBuilderValidationError(
            "artifact_plan: the computed-reaction upload response is "
            "missing the 'calculation_keys' mapping. The server is "
            "older than the tckdb-client artifact-planning feature; "
            "upload artifacts directly via "
            "client.upload_artifact(calculation_id, …) once you know "
            "each calculation's server id."
        )
    out: dict[str, int] = {}
    for key, value in mapping.items():
        if not isinstance(key, str) or not isinstance(value, int):
            raise TCKDBBuilderValidationError(
                "artifact_plan: calculation_keys entries must be "
                f"str → int, got {key!r} -> {type(value).__name__}."
            )
        out[key] = value
    return out


def _record_calc_ref(
    out: dict[str, int], ref: Any, path: str,
) -> None:
    """Pull (key, calculation_id) off one ref entry into ``out``."""
    if not isinstance(ref, dict):
        raise TCKDBBuilderValidationError(
            f"artifact_plan: {path} is not a dict."
        )
    key = ref.get("key")
    calc_id = ref.get("calculation_id")
    if not isinstance(key, str) or not isinstance(calc_id, int):
        raise TCKDBBuilderValidationError(
            f"artifact_plan: {path} is missing 'key' (str) or "
            "'calculation_id' (int)."
        )
    out[key] = calc_id


def _build_plan(
    calculations: "list[Calculation]",
    key_to_id: dict[str, int],
) -> list[PlannedArtifactUpload]:
    """Render one ``PlannedArtifactUpload`` per attached artifact.

    The calculation's bundle-local key is recomputed from its label
    via the same :class:`KeyMinter` rules the upload assembler uses
    so the plan and the payload speak the same key vocabulary.
    """
    plan: list[PlannedArtifactUpload] = []
    if not calculations:
        return plan

    minter = KeyMinter(prefix="calc")
    for calc in calculations:
        minter.mint(calc, label=calc.label)

    for calc in calculations:
        if not calc.artifacts:
            continue
        calc_key = minter.lookup(calc)
        if calc_key not in key_to_id:
            raise TCKDBBuilderValidationError(
                f"artifact_plan: calculation key {calc_key!r} has "
                "attached artifacts but the upload response does not "
                "list it. The bundle may have skipped persisting the "
                "calc, or the response was tampered with."
            )
        calc_id = key_to_id[calc_key]
        for art in calc.artifacts:
            plan.append(
                PlannedArtifactUpload(
                    calculation_key=calc_key,
                    calculation_id=calc_id,
                    path=art.path,
                    kind=art.kind,
                    label=art.label,
                    sha256=art.sha256,
                    bytes=art.bytes,
                )
            )
    return plan


def _build_species_preview_response(
    payload: dict, *, start: int,
) -> dict:
    """Synthesise a ``ComputedSpeciesUploadResult``-shaped dict.

    Walks ``payload["conformers"]`` (the computed-species wire shape)
    and assigns ``start, start+1, …`` to every calc key in payload
    order — primary first, then additionals per conformer. The
    resulting dict is exactly what
    :func:`_extract_computed_species_calc_keys` expects, so calling
    :meth:`ComputedSpeciesUpload.artifact_plan` against it reproduces
    the real artifact-plan path without server access.
    """
    next_id = start
    conformers_resp: list[dict] = []
    for conf in payload.get("conformers", []):
        primary = conf["primary_calculation"]
        primary_ref = {
            "key": primary["key"],
            "calculation_id": next_id,
            "type": primary["type"],
            "role": "primary",
        }
        next_id += 1
        additional_refs: list[dict] = []
        for extra in conf.get("additional_calculations", []):
            additional_refs.append(
                {
                    "key": extra["key"],
                    "calculation_id": next_id,
                    "type": extra["type"],
                    "role": "additional",
                }
            )
            next_id += 1
        conformers_resp.append(
            {
                "key": conf["key"],
                "primary_calculation": primary_ref,
                "additional_calculations": additional_refs,
            }
        )
    return {
        "type": "computed_species",
        "species_entry_id": 0,
        "conformers": conformers_resp,
    }


def _build_reaction_preview_response(
    payload: dict, *, start: int,
) -> dict:
    """Synthesise a ``ComputedReactionUploadResult``-shaped dict.

    Walks the computed-reaction wire shape (``transition_state``
    then per-species ``conformers`` + ``calculations``) and assigns
    ``start, start+1, …`` to every emitted calc key in payload
    order. The returned dict carries the ``calculation_keys``
    response field :func:`_extract_computed_reaction_calc_keys`
    needs.
    """
    keys: list[str] = []
    ts = payload.get("transition_state")
    if ts is not None:
        keys.append(ts["calculation"]["key"])
        for extra in ts.get("calculations", []):
            keys.append(extra["key"])
    for sp in payload.get("species", []):
        for conf in sp.get("conformers", []):
            keys.append(conf["calculation"]["key"])
        for extra in sp.get("calculations", []):
            keys.append(extra["key"])
    return {
        "type": "computed_reaction",
        "calculation_keys": {k: start + i for i, k in enumerate(keys)},
    }


# ---------------------------------------------------------------------------
# Computed reaction (Phase 2)
# ---------------------------------------------------------------------------


@dataclass
class ComputedReactionUpload:
    """Bundle upload for one computed elementary reaction.

    Maps to the backend's ``ComputedReactionUploadRequest`` (target
    endpoint ``POST /api/v1/uploads/computed-reaction``).

    Two calculation buckets are accepted:

    - ``calculations`` — transition-state-side calculations. The
      primary TS opt anchors the transition_state geometry; non-opt
      entries (freq, sp, …) attach to the TS conformer with
      ``geometry_key`` pointing at it.
    - ``species_calculations`` — reactant / product calculations
      keyed by the :class:`Species` they belong to. Each species
      entry produces one conformer on the wire, anchored by its
      single opt; additional non-opt calcs attach to that conformer.

    Exactly one opt per species: the builder ships one scientifically
    meaningful conformer per species upload by design — see
    ``docs/conformer_semantic_boundary.md``. Producers wanting to
    submit several records for the same species do so as independent
    submissions, not as a candidate list bundled into one upload.
    """

    reaction: ChemReaction
    calculations: list[Calculation] = field(default_factory=list)
    primary_ts_calculation: Calculation | None = None
    species_calculations: dict[Species, list[Calculation]] | None = None
    species_thermo: dict[Species, Thermo] | None = None
    species_statmech: dict[Species, Statmech] | None = None
    species_transport: dict[Species, Transport] | None = None
    note: str | None = None

    upload_kind: str = field(default="computed_reaction", init=False)

    # Validated copy of ``species_calculations``: kept as a list of
    # ``(Species, [Calculation, …])`` pairs so payload generation can
    # walk the data in a deterministic order with ``is``-identity
    # rather than relying on dict hashing of builder objects.
    _species_calc_pairs: list[tuple[Species, list[Calculation]]] = field(
        default_factory=list, init=False, repr=False
    )
    # Same deterministic-pair shape for thermo. Order follows
    # ``reaction.unique_species()`` so payload emission stays stable
    # regardless of caller dict order.
    _species_thermo_pairs: list[tuple[Species, Thermo]] = field(
        default_factory=list, init=False, repr=False
    )
    _species_statmech_pairs: list[tuple[Species, Statmech]] = field(
        default_factory=list, init=False, repr=False
    )
    _species_transport_pairs: list[tuple[Species, Transport]] = field(
        default_factory=list, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.reaction, ChemReaction):
            raise TCKDBBuilderValidationError(
                "ComputedReactionUpload.reaction must be a ChemReaction "
                "builder."
            )
        if self.calculations is None:  # type: ignore[truthy-bool]
            self.calculations = []
        for i, calc in enumerate(self.calculations):
            if not isinstance(calc, Calculation):
                raise TCKDBBuilderValidationError(
                    f"calculations[{i}] must be a Calculation builder."
                )

        self._species_calc_pairs = self._normalise_species_calculations()
        self._species_thermo_pairs = self._normalise_species_thermo()
        self._species_statmech_pairs = self._normalise_species_statmech()
        self._species_transport_pairs = self._normalise_species_transport()

        # Resolve / validate the TS primary calculation up front so
        # producers see a deterministic error before payload time.
        if self.reaction.transition_state is not None:
            primary = self.primary_ts_calculation
            if primary is None:
                primary = self._infer_primary_ts_calculation()
            if primary is not None:
                if not _is_in(primary, self.calculations):
                    raise TCKDBBuilderValidationError(
                        "primary_ts_calculation must be one of the "
                        "entries in ComputedReactionUpload.calculations "
                        "(the TS bucket); it cannot be a species-side "
                        "calculation."
                    )
                if primary.type != "opt":
                    raise TCKDBBuilderValidationError(
                        "primary_ts_calculation.type must be 'opt', got "
                        f"{primary.type!r}; the bundle endpoint anchors "
                        "the TS on an opt."
                    )
            self.primary_ts_calculation = primary
        elif self.primary_ts_calculation is not None:
            raise TCKDBBuilderValidationError(
                "primary_ts_calculation was supplied but ChemReaction has "
                "no transition_state."
            )

        # TS-side depends_on must stay inside the TS bucket. Scientific
        # constraint, not a schema constraint — the server accepts any
        # in-bundle parent_calculation_key, but a TS freq depending on
        # a reactant opt would be semantically broken.
        ts_calcs = self.calculations
        for calc in ts_calcs:
            for dep in calc.depends_on:
                if not _is_in(dep, ts_calcs):
                    raise TCKDBBuilderValidationError(
                        "Transition-state calculation depends_on must "
                        "stay inside ComputedReactionUpload.calculations "
                        "(the TS bucket); cross-owner dependencies to "
                        "species calculations are not permitted by the "
                        "builder."
                    )

        # Species-side depends_on must stay inside the same species's
        # calculation set for the same reason.
        for sp, sp_calcs in self._species_calc_pairs:
            for calc in sp_calcs:
                for dep in calc.depends_on:
                    if not _is_in(dep, sp_calcs):
                        sp_label = sp.label or sp.smiles or "<species>"
                        raise TCKDBBuilderValidationError(
                            f"species {sp_label!r}: depends_on must "
                            "reference a calculation attached to the "
                            "same species in species_calculations."
                        )

        # kinetics source_calculations: must resolve to *some* calc
        # in the upload — either the TS bucket or any species bucket.
        for ki, kin in enumerate(self.reaction.kinetics):
            for role, calc in kin.source_calculations_iter():
                if not self._calc_anywhere(calc):
                    raise TCKDBBuilderValidationError(
                        f"reaction.kinetics[{ki}] source_calculation "
                        f"role={role!r} references a Calculation that "
                        "is not present in calculations nor in "
                        "species_calculations."
                    )

        # Thermo source_calculations (when present) must resolve to
        # this species's calculation bucket. The computed-reaction
        # ``BundleThermoIn`` does not actually carry source_calculations
        # on the wire today — see ``thermo.py`` for the rationale — but
        # we still validate up front so producers get a deterministic
        # error rather than silently lost data.
        for sp, thermo in self._species_thermo_pairs:
            if not thermo.source_calculations:
                continue
            sp_label = sp.label or sp.smiles or "<species>"
            sp_calcs = self._calculations_for(sp)
            for role, calc in thermo.source_calculations:
                if not _is_in(calc, sp_calcs):
                    raise TCKDBBuilderValidationError(
                        f"species_thermo[{sp_label!r}] source_calculation "
                        f"role={role!r} references a Calculation that is "
                        "not in species_calculations for the same species."
                    )

        # Statmech source_calculations are carried on the wire by both
        # ``StatmechInBundle`` and ``BundleStatmechIn``. Same scoping
        # rule as thermo: each referenced calc must belong to that
        # species's bucket so the bundle stays self-consistent.
        for sp, sm in self._species_statmech_pairs:
            if not sm.source_calculations:
                continue
            sp_label = sp.label or sp.smiles or "<species>"
            sp_calcs = self._calculations_for(sp)
            for role, calc in sm.source_calculations:
                if not _is_in(calc, sp_calcs):
                    raise TCKDBBuilderValidationError(
                        f"species_statmech[{sp_label!r}] source_calculation "
                        f"role={role!r} references a Calculation that is "
                        "not in species_calculations for the same species."
                    )

        # Transport is forward-compat on the bundle endpoints: neither
        # ``ComputedSpeciesUploadRequest`` nor
        # ``ComputedReactionUploadRequest`` carries a transport field
        # today. The builder validates that any source-calc references
        # stay scoped to the same species bucket — exactly the rule the
        # bundle workflow would enforce once the field lands — so
        # producer code stays portable across the schema change.
        for sp, tr in self._species_transport_pairs:
            if not tr.source_calculations:
                continue
            sp_label = sp.label or sp.smiles or "<species>"
            sp_calcs = self._calculations_for(sp)
            for role, calc in tr.source_calculations:
                if not _is_in(calc, sp_calcs):
                    raise TCKDBBuilderValidationError(
                        f"species_transport[{sp_label!r}] source_calculation "
                        f"role={role!r} references a Calculation that is "
                        "not in species_calculations for the same species."
                    )

        self.note = ensure_optional_non_empty_str(self.note, field="note")

    # ------------------------------------------------------------------
    # species_calculations normalisation + helpers
    # ------------------------------------------------------------------

    def _normalise_species_calculations(
        self,
    ) -> list[tuple[Species, list[Calculation]]]:
        """Convert the input dict into a validated, ordered pair list.

        Order follows :meth:`ChemReaction.unique_species` so payload
        emission walks species in a deterministic order regardless of
        how the dict was constructed.
        """
        if not self.species_calculations:
            return []
        if not isinstance(self.species_calculations, dict):
            raise TCKDBBuilderValidationError(
                "species_calculations must be a "
                "dict[Species, list[Calculation]]."
            )
        reaction_species = self.reaction.unique_species()

        # Walk the user's dict once to flatten + type-check; we keep
        # only entries whose keys are Species the reaction references.
        flattened: list[tuple[Species, list[Calculation]]] = []
        seen_species: list[Species] = []
        for sp_key, calc_list in self.species_calculations.items():
            if not isinstance(sp_key, Species):
                raise TCKDBBuilderValidationError(
                    "species_calculations keys must be Species builders, "
                    f"got {type(sp_key).__name__}."
                )
            if not any(s is sp_key for s in reaction_species):
                sp_label = sp_key.label or sp_key.smiles or "<species>"
                raise TCKDBBuilderValidationError(
                    f"species_calculations key {sp_label!r} is not one "
                    "of the Species objects in reaction.reactants or "
                    "reaction.products."
                )
            if any(s is sp_key for s in seen_species):
                sp_label = sp_key.label or sp_key.smiles or "<species>"
                raise TCKDBBuilderValidationError(
                    f"species_calculations has duplicate Species "
                    f"{sp_label!r}; merge the calculation lists."
                )
            seen_species.append(sp_key)
            if not isinstance(calc_list, (list, tuple)):
                raise TCKDBBuilderValidationError(
                    "species_calculations values must be lists of "
                    f"Calculation builders, got {type(calc_list).__name__}."
                )
            calcs: list[Calculation] = []
            for i, c in enumerate(calc_list):
                if not isinstance(c, Calculation):
                    raise TCKDBBuilderValidationError(
                        f"species_calculations[<species>][{i}] must be a "
                        f"Calculation builder, got {type(c).__name__}."
                    )
                calcs.append(c)

            # One scientifically meaningful conformer per species, by
            # design — see ``docs/conformer_semantic_boundary.md``.
            # The bundle schema accepts a list of conformers, but the
            # builder deliberately does not expose that surface: TCKDB
            # is not a workflow-side conformer-search scratchpad. A
            # producer with several scientifically meaningful records
            # for the same species submits them independently.
            n_opt = sum(1 for c in calcs if c.type == "opt")
            if n_opt > 1:
                sp_label = sp_key.label or sp_key.smiles or "<species>"
                raise TCKDBBuilderValidationError(
                    f"species_calculations[{sp_label!r}] contains "
                    f"{n_opt} opt calculations; the builder ships one "
                    "scientifically meaningful conformer per species "
                    "upload. Submit each as its own upload, or fall "
                    "back to the raw payload form for the rare cases "
                    "where bundling is genuinely required."
                )
            flattened.append((sp_key, calcs))

        # Reorder by reaction.unique_species() so emission is
        # deterministic regardless of caller dict order.
        ordered: list[tuple[Species, list[Calculation]]] = []
        for sp in reaction_species:
            for known, calcs in flattened:
                if known is sp:
                    ordered.append((sp, calcs))
                    break
        return ordered

    def _normalise_species_thermo(self) -> list[tuple[Species, Thermo]]:
        """Convert ``species_thermo`` into a validated, ordered pair list.

        Same identity-hashed contract as
        :meth:`_normalise_species_calculations` — keys must be
        ``Species`` builders that appear in the reaction, values must
        be :class:`Thermo` builders. Duplicate keys (same identity)
        are rejected.
        """
        if not self.species_thermo:
            return []
        if not isinstance(self.species_thermo, dict):
            raise TCKDBBuilderValidationError(
                "species_thermo must be a dict[Species, Thermo]."
            )
        reaction_species = self.reaction.unique_species()
        flattened: list[tuple[Species, Thermo]] = []
        seen_species: list[Species] = []
        for sp_key, thermo in self.species_thermo.items():
            if not isinstance(sp_key, Species):
                raise TCKDBBuilderValidationError(
                    "species_thermo keys must be Species builders, got "
                    f"{type(sp_key).__name__}."
                )
            if not any(s is sp_key for s in reaction_species):
                sp_label = sp_key.label or sp_key.smiles or "<species>"
                raise TCKDBBuilderValidationError(
                    f"species_thermo key {sp_label!r} is not one of the "
                    "Species objects in reaction.reactants or "
                    "reaction.products."
                )
            if any(s is sp_key for s in seen_species):
                sp_label = sp_key.label or sp_key.smiles or "<species>"
                raise TCKDBBuilderValidationError(
                    f"species_thermo has duplicate Species {sp_label!r}."
                )
            seen_species.append(sp_key)
            if not isinstance(thermo, Thermo):
                raise TCKDBBuilderValidationError(
                    "species_thermo values must be Thermo builders, got "
                    f"{type(thermo).__name__}."
                )
            flattened.append((sp_key, thermo))

        ordered: list[tuple[Species, Thermo]] = []
        for sp in reaction_species:
            for known, thermo in flattened:
                if known is sp:
                    ordered.append((sp, thermo))
                    break
        return ordered

    def _thermo_for(self, sp: Species) -> Thermo | None:
        for known, thermo in self._species_thermo_pairs:
            if known is sp:
                return thermo
        return None

    def _normalise_species_statmech(self) -> list[tuple[Species, Statmech]]:
        """Convert ``species_statmech`` into a validated, ordered pair list.

        Mirrors :meth:`_normalise_species_thermo` — same identity-hashed
        contract, same ``reaction.unique_species()`` ordering, same
        duplicate-key rejection.
        """
        if not self.species_statmech:
            return []
        if not isinstance(self.species_statmech, dict):
            raise TCKDBBuilderValidationError(
                "species_statmech must be a dict[Species, Statmech]."
            )
        reaction_species = self.reaction.unique_species()
        flattened: list[tuple[Species, Statmech]] = []
        seen_species: list[Species] = []
        for sp_key, statmech in self.species_statmech.items():
            if not isinstance(sp_key, Species):
                raise TCKDBBuilderValidationError(
                    "species_statmech keys must be Species builders, got "
                    f"{type(sp_key).__name__}."
                )
            if not any(s is sp_key for s in reaction_species):
                sp_label = sp_key.label or sp_key.smiles or "<species>"
                raise TCKDBBuilderValidationError(
                    f"species_statmech key {sp_label!r} is not one of the "
                    "Species objects in reaction.reactants or "
                    "reaction.products."
                )
            if any(s is sp_key for s in seen_species):
                sp_label = sp_key.label or sp_key.smiles or "<species>"
                raise TCKDBBuilderValidationError(
                    f"species_statmech has duplicate Species {sp_label!r}."
                )
            seen_species.append(sp_key)
            if not isinstance(statmech, Statmech):
                raise TCKDBBuilderValidationError(
                    "species_statmech values must be Statmech builders, "
                    f"got {type(statmech).__name__}."
                )
            flattened.append((sp_key, statmech))

        ordered: list[tuple[Species, Statmech]] = []
        for sp in reaction_species:
            for known, sm in flattened:
                if known is sp:
                    ordered.append((sp, sm))
                    break
        return ordered

    def _statmech_for(self, sp: Species) -> Statmech | None:
        for known, sm in self._species_statmech_pairs:
            if known is sp:
                return sm
        return None

    def _normalise_species_transport(self) -> list[tuple[Species, Transport]]:
        """Convert ``species_transport`` into a validated, ordered pair list.

        Same identity-hashed contract as the thermo / statmech
        normalisers — Species keys must appear in the reaction, values
        must be :class:`Transport` builders, duplicate keys (by
        identity) rejected.
        """
        if not self.species_transport:
            return []
        if not isinstance(self.species_transport, dict):
            raise TCKDBBuilderValidationError(
                "species_transport must be a dict[Species, Transport]."
            )
        reaction_species = self.reaction.unique_species()
        flattened: list[tuple[Species, Transport]] = []
        seen_species: list[Species] = []
        for sp_key, transport in self.species_transport.items():
            if not isinstance(sp_key, Species):
                raise TCKDBBuilderValidationError(
                    "species_transport keys must be Species builders, got "
                    f"{type(sp_key).__name__}."
                )
            if not any(s is sp_key for s in reaction_species):
                sp_label = sp_key.label or sp_key.smiles or "<species>"
                raise TCKDBBuilderValidationError(
                    f"species_transport key {sp_label!r} is not one of the "
                    "Species objects in reaction.reactants or "
                    "reaction.products."
                )
            if any(s is sp_key for s in seen_species):
                sp_label = sp_key.label or sp_key.smiles or "<species>"
                raise TCKDBBuilderValidationError(
                    f"species_transport has duplicate Species {sp_label!r}."
                )
            seen_species.append(sp_key)
            if not isinstance(transport, Transport):
                raise TCKDBBuilderValidationError(
                    "species_transport values must be Transport builders, "
                    f"got {type(transport).__name__}."
                )
            flattened.append((sp_key, transport))

        ordered: list[tuple[Species, Transport]] = []
        for sp in reaction_species:
            for known, tr in flattened:
                if known is sp:
                    ordered.append((sp, tr))
                    break
        return ordered

    def _calc_anywhere(self, calc: Calculation) -> bool:
        """Return True if ``calc`` appears in the TS or any species bucket."""
        if _is_in(calc, self.calculations):
            return True
        for _sp, sp_calcs in self._species_calc_pairs:
            if _is_in(calc, sp_calcs):
                return True
        return False

    def _calculations_for(self, sp: Species) -> list[Calculation]:
        for known, calcs in self._species_calc_pairs:
            if known is sp:
                return calcs
        return []

    def _build_species_block(
        self,
        sp: Species,
        *,
        species_key: str,
        geometry_keys: KeyMinter,
        calc_keys: KeyMinter,
        conformer_keys: KeyMinter,
    ) -> dict[str, Any]:
        """Emit a ``BundleSpeciesIn`` block for one species.

        Two shapes are produced depending on whether the user
        attached calculations to this species:

        - Identity-only — when ``species_calculations[sp]`` is empty
          or absent. The block carries ``conformers=[]``,
          ``calculations=[]``. The kinetics workflow's
          ``source_calculations`` then can't reference species-owned
          rows for this species, which is fine for kinetics fits that
          source their reactant/product energies elsewhere.
        - Full — when the user supplied at least one calc for this
          species. The species's single opt becomes the conformer's
          primary opt; the conformer geometry is taken from the opt's
          ``output_geometry`` (falling back to ``input_geometry``,
          then any non-opt calc's geometry). Non-opt calcs go into
          ``species.calculations`` with ``geometry_key`` pointing at
          the conformer geometry — matching the contract enforced by
          ``BundleSpeciesIn.validate_calc_geometry_belongs_to_conformer``.
        """
        sp_calcs = self._calculations_for(sp)
        thermo = self._thermo_for(sp)
        statmech = self._statmech_for(sp)
        block: dict[str, Any] = {
            "key": species_key,
            "species_entry": sp.to_identity_payload(),
            "conformers": [],
            "calculations": [],
        }
        if thermo is not None:
            # ``BundleThermoIn`` in computed-reaction does not carry
            # ``source_calculations`` — see thermo.py. Pass
            # ``allow_source_calculations=False`` so the emit step
            # silently omits it; we already validated upstream that
            # any source calcs supplied resolve into the same species
            # bucket, so producers won't be surprised at upload time.
            block["thermo"] = thermo.to_payload(allow_source_calculations=False)
        if statmech is not None:
            # ``BundleStatmechIn`` in computed-reaction DOES carry
            # ``source_calculations`` — emit them, resolving against
            # the same calc-key minter the rest of the bundle uses.
            block["statmech"] = statmech.to_payload(
                allow_source_calculations=True,
                calc_key_lookup=calc_keys.lookup,
            )
        if not sp_calcs:
            return block

        primary_opt = self._pick_species_primary_opt(sp, sp_calcs)
        sp_label = sp.label or sp.smiles or "<species>"
        conformer_geom = self._resolve_species_geometry(
            sp_calcs, primary_opt, sp_label=sp_label,
        )
        geom_key = geometry_keys.mint(conformer_geom, label=conformer_geom.label)
        conformer_key = conformer_keys.mint(primary_opt, label=primary_opt.label)
        primary_calc_key = calc_keys.lookup(primary_opt)

        block["conformers"] = [
            {
                "key": conformer_key,
                "geometry": {
                    "key": geom_key,
                    "xyz_text": conformer_geom.xyz_text,
                },
                "calculation": self._calc_payload_flat(
                    primary_opt,
                    key=primary_calc_key,
                    calc_keys=calc_keys,
                    # Primary opt anchors the conformer geometry
                    # implicitly; geometry_key is optional on opt.
                    geometry_key=None,
                ),
            }
        ]
        block["calculations"] = [
            self._calc_payload_flat(
                calc,
                key=calc_keys.lookup(calc),
                calc_keys=calc_keys,
                geometry_key=geom_key,
            )
            for calc in sp_calcs
            if calc is not primary_opt
        ]
        return block

    def _pick_species_primary_opt(
        self, sp: Species, sp_calcs: list[Calculation]
    ) -> Calculation:
        """Return the species's single opt, or raise.

        Multi-opt species are rejected upstream in
        :meth:`_normalise_species_calculations`; this helper just
        materialises the "exactly one opt" invariant.
        """
        opts = [c for c in sp_calcs if c.type == "opt"]
        if not opts:
            sp_label = sp.label or sp.smiles or "<species>"
            raise TCKDBBuilderValidationError(
                f"species_calculations[{sp_label!r}] must contain at "
                "least one opt calculation; non-opt calcs need an opt to "
                "anchor a conformer."
            )
        # _normalise_species_calculations rejected >1 already.
        return opts[0]

    def _resolve_species_geometry(
        self,
        sp_calcs: list[Calculation],
        primary_opt: Calculation,
        *,
        sp_label: str,
    ) -> Geometry:
        """Pick the geometry that anchors a species's single conformer.

        Priority: primary opt's ``output_geometry`` → primary opt's
        ``input_geometry`` → first available geometry from any other
        calc. Raises locally when nothing is available, mirroring the
        TS-side resolver.
        """
        if primary_opt.output_geometry is not None:
            return primary_opt.output_geometry
        if primary_opt.input_geometry is not None:
            return primary_opt.input_geometry
        for calc in sp_calcs:
            if calc is primary_opt:
                continue
            if calc.output_geometry is not None:
                return calc.output_geometry
            if calc.input_geometry is not None:
                return calc.input_geometry
        raise TCKDBBuilderValidationError(
            f"species {sp_label!r}: unable to resolve a conformer "
            "geometry. The primary opt has neither output_geometry nor "
            "input_geometry, and no other calc supplies one either."
        )

    # ------------------------------------------------------------------
    # Payload assembly
    # ------------------------------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        """Render the dict accepted by ``ComputedReactionUploadRequest``.

        Determinism mirrors :meth:`ComputedSpeciesUpload.to_payload` —
        all keys (species, conformer, geometry, calculation) are minted
        from a single set of :class:`KeyMinter` instances in a
        deterministic walk order, with ``is``-identity as the only
        cross-object lookup. Calling ``to_payload()`` twice on the
        same object graph produces byte-identical output.
        """
        unique_species = self.reaction.unique_species()

        species_keys = KeyMinter(prefix="species")
        for sp in unique_species:
            species_keys.mint(sp, label=sp.label)

        # Single global namespaces for calc and geometry keys — the
        # bundle schema requires uniqueness across TS and species
        # blocks. Mint species-side calcs FIRST so that a species
        # ``calc`` with no label gets a smaller number than the TS
        # bucket; the resulting walk order is deterministic and
        # cross-section idempotent.
        calc_keys = KeyMinter(prefix="calc")
        geometry_keys = KeyMinter(prefix="geom")
        conformer_keys = KeyMinter(prefix="conformer")
        for _sp, sp_calcs in self._species_calc_pairs:
            for calc in sp_calcs:
                calc_keys.mint(calc, label=calc.label)
        for calc in self.calculations:
            calc_keys.mint(calc, label=calc.label)

        species_blocks: list[dict[str, Any]] = []
        for sp in unique_species:
            species_blocks.append(
                self._build_species_block(
                    sp,
                    species_key=species_keys.lookup(sp),
                    geometry_keys=geometry_keys,
                    calc_keys=calc_keys,
                    conformer_keys=conformer_keys,
                )
            )

        ts_block: dict[str, Any] | None = None
        if self.reaction.transition_state is not None:
            ts_block = self._build_transition_state(
                geometry_keys=geometry_keys,
                calc_keys=calc_keys,
            )

        reactant_keys = [species_keys.lookup(sp) for sp in self.reaction.reactants]
        product_keys = [species_keys.lookup(sp) for sp in self.reaction.products]

        payload: dict[str, Any] = {
            "species": species_blocks,
            "reversible": self.reaction.reversible,
            "reactant_keys": reactant_keys,
            "product_keys": product_keys,
        }
        if self.reaction.family is not None:
            payload["reaction_family"] = self.reaction.family
        if self.reaction.family_source_note is not None:
            payload["reaction_family_source_note"] = (
                self.reaction.family_source_note
            )
        if ts_block is not None:
            payload["transition_state"] = ts_block
        if self.reaction.kinetics:
            payload["kinetics"] = [
                kin.to_payload(
                    reactant_keys=reactant_keys,
                    product_keys=product_keys,
                    calc_key_lookup=calc_keys.lookup,
                )
                for kin in self.reaction.kinetics
            ]
        if self.note is not None:
            payload["note"] = self.note
        return payload

    # ------------------------------------------------------------------
    # Emission diagnostics
    # ------------------------------------------------------------------

    def emission_diagnostics(self) -> list[Diagnostic]:
        """Report fields the builder accepts but cannot send today.

        Today's gaps:

        - ``species_transport`` — bundle schema has no transport field.
        - ``species_thermo[…].source_calculations`` — computed-reaction
          ``BundleThermoIn`` lacks ``source_calculations`` (only the
          computed-species ``ThermoInBundle`` carries it).

        Other accepted blocks (``species_calculations``,
        ``species_statmech`` including their source-calc references,
        kinetics ``source_calculations``) DO emit on the wire and
        therefore produce no diagnostic.
        """
        out: list[Diagnostic] = []
        for sp, _transport in self._species_transport_pairs:
            label = sp.label or sp.smiles or "<species>"
            out.append(
                Diagnostic(
                    level="warning",
                    code=DIAG_CODES.TRANSPORT_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE,
                    message=(
                        "Transport is accepted for forward compatibility but "
                        "the computed-reaction bundle schema does not yet "
                        "carry a per-species transport field — the block "
                        "will not be emitted on the wire. Use the "
                        "standalone /uploads/transport endpoint to ship "
                        "transport data today."
                    ),
                    path=f"species_transport[{label}]",
                )
            )
        for sp, thermo in self._species_thermo_pairs:
            if not thermo.source_calculations:
                continue
            label = sp.label or sp.smiles or "<species>"
            out.append(
                Diagnostic(
                    level="warning",
                    code=(
                        DIAG_CODES
                        .THERMO_SOURCE_CALCULATIONS_NOT_EMITTED_IN_COMPUTED_REACTION_BUNDLE
                    ),
                    message=(
                        "Thermo source_calculations were validated locally "
                        "but the computed-reaction BundleThermoIn schema "
                        "does not carry that field — the references will "
                        "not be emitted on the wire. The computed-species "
                        "endpoint does emit them; if provenance is "
                        "load-bearing, upload via /uploads/computed-species."
                    ),
                    path=f"species_thermo[{label}].source_calculations",
                )
            )
        for calc in self._all_calculations_for_artifacts():
            if calc.artifacts:
                out.append(_artifact_second_phase_diag(calc))
        return out

    # ------------------------------------------------------------------
    # Artifact plan
    # ------------------------------------------------------------------

    def artifact_plan(self, upload_result: Any) -> list[PlannedArtifactUpload]:
        """Resolve attached artifacts against the server upload result.

        Requires the computed-reaction response to expose the
        ``calculation_keys: dict[str, int]`` field (added as a
        response-only follow-up). Servers older than ``tckdb-backend``
        0.22's response shape will trip a clear
        :class:`TCKDBBuilderValidationError`.

        Walks both the TS-side ``calculations`` bucket and every
        ``species_calculations`` entry; one
        :class:`PlannedArtifactUpload` per attached artifact, in
        deterministic order (TS first, then species in
        ``reaction.unique_species()`` order).
        """
        key_to_id = _extract_computed_reaction_calc_keys(upload_result)
        plan: list[PlannedArtifactUpload] = []
        plan.extend(_build_plan(self.calculations, key_to_id))
        for _sp, sp_calcs in self._species_calc_pairs:
            plan.extend(_build_plan(sp_calcs, key_to_id))
        return plan

    def artifact_plan_preview(
        self, *, starting_calculation_id: int = 1000,
    ) -> list[PlannedArtifactUpload]:
        """Return the same shape as :meth:`artifact_plan` against
        synthetic calculation IDs.

        Walks the upload's payload, mints
        ``starting_calculation_id, +1, +2, …`` against every emitted
        bundle-local calc key in payload order, and feeds the
        resulting ``{calc_key: synthetic_id}`` mapping through the
        normal :meth:`artifact_plan` path. Same upload state → same
        preview, every time. Useful for offline demos, CI fixtures,
        and producer debugging; the returned IDs are **not** real
        server-side primary keys.
        """
        synthetic_response = _build_reaction_preview_response(
            self.to_payload(), start=starting_calculation_id,
        )
        return self.artifact_plan(synthetic_response)

    # ------------------------------------------------------------------
    # Public iteration
    # ------------------------------------------------------------------

    def iter_calculations(
        self, *, with_artifacts_only: bool = False,
    ) -> Iterator[Calculation]:
        """Yield every :class:`Calculation` in this upload in payload
        order — TS bucket first, then species buckets in
        ``reaction.unique_species()`` order. Set
        ``with_artifacts_only=True`` to skip calcs without attached
        artifacts.
        """
        for calc in self.calculations:
            if with_artifacts_only and not calc.artifacts:
                continue
            yield calc
        for _sp, sp_calcs in self._species_calc_pairs:
            for calc in sp_calcs:
                if with_artifacts_only and not calc.artifacts:
                    continue
                yield calc

    def iter_calculation_entries(
        self, *, with_artifacts_only: bool = False,
    ) -> Iterator[CalculationEntry]:
        """Yield :class:`CalculationEntry` rows tagged with their
        bucket and (where applicable) the :class:`Species` they're
        attached to.

        TS-side calcs come first with ``bucket="TS"`` and
        ``species=None``. Species-side calcs follow in
        ``reaction.unique_species()`` order with ``bucket`` set to
        the species's ``label`` / ``smiles`` and ``species`` set to
        the :class:`Species` builder.
        """
        for calc in self.calculations:
            if with_artifacts_only and not calc.artifacts:
                continue
            yield CalculationEntry(
                bucket="TS", species=None, calculation=calc,
            )
        for sp, sp_calcs in self._species_calc_pairs:
            bucket = sp.label or sp.smiles or "<species>"
            for calc in sp_calcs:
                if with_artifacts_only and not calc.artifacts:
                    continue
                yield CalculationEntry(
                    bucket=bucket, species=sp, calculation=calc,
                )

    def iter_artifacts(self) -> Iterator[tuple[Calculation, Artifact]]:
        """Yield ``(calculation, artifact)`` pairs, one per attached
        artifact, in walk order."""
        for calc in self.iter_calculations(with_artifacts_only=True):
            for art in calc.artifacts:
                yield calc, art

    def summary(self) -> "UploadSummary":
        """Return a small human- and machine-readable preview.

        See ``clients/python/docs/builder_summary_design.md`` for the
        stability contract; ``upload.to_payload()`` remains the
        canonical wire representation.
        """
        from tckdb_client.builders.summary import (
            summarise_computed_reaction_upload,
        )

        return summarise_computed_reaction_upload(self)

    def _all_calculations_for_artifacts(self) -> "list[Calculation]":
        """Return every Calculation that could carry an artifact list.

        Internal helper kept for the existing artifact emission
        diagnostic. The public iteration API is
        :meth:`iter_calculations` / :meth:`iter_calculation_entries`.
        """
        return list(self.iter_calculations())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _infer_primary_ts_calculation(self) -> Calculation | None:
        """Pick the first ``opt`` in ``calculations`` as the TS primary.

        Returns ``None`` when no opt is available; the caller decides
        whether that is acceptable (it currently is not, because the
        backend's ``BundleTransitionStateIn.calculation`` field is
        required when ``transition_state`` is present).
        """
        for calc in self.calculations:
            if calc.type == "opt":
                return calc
        return None

    def _build_transition_state(
        self,
        *,
        geometry_keys: KeyMinter,
        calc_keys: KeyMinter,
    ) -> dict[str, Any]:
        ts = self.reaction.transition_state
        assert ts is not None  # narrowed by caller
        primary = self.primary_ts_calculation
        if primary is None:
            raise TCKDBBuilderValidationError(
                "ChemReaction has a transition_state but no primary TS "
                "calculation could be resolved. Provide "
                "primary_ts_calculation= or include an opt in "
                "calculations."
            )

        ts_geometry = self._resolve_ts_geometry(ts, primary)
        ts_geom_key = geometry_keys.mint(ts_geometry, label=ts_geometry.label)
        primary_calc_key = calc_keys.lookup(primary)

        ts_payload: dict[str, Any] = {
            "charge": ts.charge,
            "multiplicity": ts.multiplicity,
            "geometry": {
                "key": ts_geom_key,
                "xyz_text": ts_geometry.xyz_text,
            },
            "calculation": self._calc_payload_flat(
                primary, key=primary_calc_key, calc_keys=calc_keys,
                # Primary opt anchors the TS geometry implicitly; the
                # backend treats geometry_key as optional on opt and
                # falls back to the TS geometry.
                geometry_key=None,
            ),
            "calculations": [
                self._calc_payload_flat(
                    calc,
                    key=calc_keys.lookup(calc),
                    calc_keys=calc_keys,
                    geometry_key=ts_geom_key,
                )
                for calc in self.calculations
                if calc is not primary
            ],
        }
        if ts.smiles is not None:
            ts_payload["unmapped_smiles"] = ts.smiles
        if ts.label is not None:
            ts_payload["label"] = ts.label
        return ts_payload

    def _resolve_ts_geometry(
        self, ts: TransitionState, primary: Calculation
    ) -> Geometry:
        """Pick the geometry that anchors the TS conformer.

        Priority: explicit ``TransitionState.geometry`` → primary
        opt's ``output_geometry`` → primary opt's ``input_geometry``.
        Raises locally when none of the three is available — the
        backend would otherwise reject the payload with 422.
        """
        if ts.geometry is not None:
            return ts.geometry
        if primary.output_geometry is not None:
            return primary.output_geometry
        if primary.input_geometry is not None:
            return primary.input_geometry
        raise TCKDBBuilderValidationError(
            "Unable to resolve a TS geometry: TransitionState.geometry "
            "is unset and the primary TS opt has neither "
            "output_geometry nor input_geometry."
        )

    def _calc_payload_flat(
        self,
        calc: Calculation,
        *,
        key: str,
        calc_keys: KeyMinter,
        geometry_key: str | None,
    ) -> dict[str, Any]:
        """Render one calculation in the computed-reaction wire shape.

        The wire shape (``CalculationIn`` in
        ``app/schemas/workflows/network_pdep_upload.py``) uses flat
        per-type result fields rather than nested result blocks.
        """
        out: dict[str, Any] = {
            "key": key,
            "type": calc.type,
            "software_release": calc.software_release.to_payload(),
            "level_of_theory": calc.level_of_theory.to_payload(),
        }
        if geometry_key is not None:
            out["geometry_key"] = geometry_key
        out.update(calc.result_fields_flat())
        if calc.depends_on:
            out["depends_on"] = [
                {
                    "parent_calculation_key": calc_keys.lookup(parent),
                    "role": calc.infer_dependency_role(parent),
                }
                for parent in calc.depends_on
            ]
        return out
