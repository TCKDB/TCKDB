"""Top-level upload-object builders.

Phase 1 ships :class:`ComputedSpeciesUpload` targeting
``POST /api/v1/uploads/computed-species``. Phase 2 adds
:class:`ComputedReactionUpload` targeting
``POST /api/v1/uploads/computed-reaction``. Both are payload-construction
aids, not parallel APIs — the server remains authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tckdb_client.builders.base import KeyMinter
from tckdb_client.builders.calculation import Calculation
from tckdb_client.builders.geometry import Geometry
from tckdb_client.builders.reaction import ChemReaction, TransitionState
from tckdb_client.builders.species import Species
from tckdb_client.builders.thermo import Thermo
from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_optional_non_empty_str,
)

__all__ = [
    "ComputedSpeciesUpload",
    "ComputedReactionUpload",
]


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
        if self.note is not None:
            bundle["note"] = self.note
        return bundle

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

    Multi-conformer species (more than one opt per species) and
    thermo/statmech/transport blocks are still deferred — see
    ``docs/builder_api_mvp.md`` §16.
    """

    reaction: ChemReaction
    calculations: list[Calculation] = field(default_factory=list)
    primary_ts_calculation: Calculation | None = None
    species_calculations: dict[Species, list[Calculation]] | None = None
    species_thermo: dict[Species, Thermo] | None = None
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

            # Multi-conformer species are explicitly deferred. The
            # bundle schema allows multiple conformers, but until the
            # builder ships a Conformer abstraction, accepting >1 opt
            # per species would silently collapse rotamers into one
            # observation.
            n_opt = sum(1 for c in calcs if c.type == "opt")
            if n_opt > 1:
                sp_label = sp_key.label or sp_key.smiles or "<species>"
                raise TCKDBBuilderValidationError(
                    f"species_calculations[{sp_label!r}] contains "
                    f"{n_opt} opt calculations; the Phase-3A builder "
                    "supports one conformer per species. Use the raw "
                    "payload form for multi-conformer uploads."
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
