"""Calculation builder and its provenance handles.

A :class:`Calculation` instance describes one quantum-chemistry
calculation along with its software/level-of-theory provenance,
optional input/output geometries, and optional typed result block.
Use the :meth:`Calculation.opt`, :meth:`Calculation.freq`, and
:meth:`Calculation.sp` factories — the bare constructor is internal.

This module also hosts :class:`LevelOfTheory` and
:class:`SoftwareRelease`. They are short value types tightly bound to
``Calculation`` and live here to keep the package layout flat
(see ``clients/python/docs/builder_api_mvp.md`` §5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tckdb_client.builders.artifact import Artifact
from tckdb_client.builders.geometry import Geometry
from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_int,
    ensure_non_empty_str,
    ensure_optional_non_empty_str,
)

__all__ = [
    "LevelOfTheory",
    "SoftwareRelease",
    "Calculation",
]

# Allowed calculation types in Phase 1. The full backend
# ``CalculationType`` enum has more values (``irc``, ``scan``,
# ``path_search``, ``conf``) — they are out of scope for this phase
# (see ``docs/builder_api_mvp.md`` §16).
_ALLOWED_TYPES: frozenset[str] = frozenset({"opt", "freq", "sp"})


@dataclass
class LevelOfTheory:
    """Level-of-theory provenance handle.

    Emits a ``LevelOfTheoryRef`` fragment in the bundle payload.
    """

    method: str
    basis: str | None = None
    aux_basis: str | None = None
    cabs_basis: str | None = None
    dispersion: str | None = None
    solvent: str | None = None
    solvent_model: str | None = None
    keywords: str | None = None
    label: str | None = None
    _validated: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.method = ensure_non_empty_str(self.method, field="method")
        for attr in (
            "basis",
            "aux_basis",
            "cabs_basis",
            "dispersion",
            "solvent",
            "solvent_model",
            "keywords",
            "label",
        ):
            setattr(
                self,
                attr,
                ensure_optional_non_empty_str(getattr(self, attr), field=attr),
            )
        self._validated = True

    def to_payload(self) -> dict[str, Any]:
        """Return the ``LevelOfTheoryRef`` fragment dict."""
        out: dict[str, Any] = {"method": self.method}
        for attr in (
            "basis",
            "aux_basis",
            "cabs_basis",
            "dispersion",
            "solvent",
            "solvent_model",
            "keywords",
        ):
            value = getattr(self, attr)
            if value is not None:
                out[attr] = value
        return out


@dataclass
class SoftwareRelease:
    """Software-release provenance handle.

    Emits a ``SoftwareReleaseRef`` fragment in the bundle payload.
    Note that the bundle schema field is named ``name`` (not
    ``software``); this builder maps the friendlier kwarg to the
    server's wire field at emit time.
    """

    software: str
    version: str | None = None
    revision: str | None = None
    build: str | None = None
    label: str | None = None
    _validated: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.software = ensure_non_empty_str(self.software, field="software")
        for attr in ("version", "revision", "build", "label"):
            setattr(
                self,
                attr,
                ensure_optional_non_empty_str(getattr(self, attr), field=attr),
            )
        self._validated = True

    def to_payload(self) -> dict[str, Any]:
        """Return the ``SoftwareReleaseRef`` fragment dict."""
        out: dict[str, Any] = {"name": self.software}
        for attr in ("version", "revision", "build"):
            value = getattr(self, attr)
            if value is not None:
                out[attr] = value
        return out


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------


@dataclass
class Calculation:
    """One quantum-chemistry calculation in a computed-species upload.

    Construct via the type-specific factories; the bare constructor is
    not part of the public contract.

    Attributes are emitted into a ``CalculationInBundle`` payload by
    :meth:`ComputedSpeciesUpload.to_payload`. The bundle's ``key``
    field is assigned at payload time by the upload-level key minter
    — :class:`Calculation` carries an optional ``label`` only.
    """

    type: str
    software_release: SoftwareRelease
    level_of_theory: LevelOfTheory
    input_geometry: Geometry | None = None
    output_geometry: Geometry | None = None
    depends_on: list["Calculation"] = field(default_factory=list)
    label: str | None = None
    # Local-only annotation kept on the builder for producer / adapter
    # ergonomics (e.g. ARC-style "lowest-energy converged structure;
    # conformer search history retained as artifacts"). Today's bundle
    # schemas do NOT carry a per-calc note field — see
    # ``_calc_payload`` in ``uploads.py`` — so this value is preserved
    # locally but not emitted on the wire. Lift to the payload only
    # once the backend schemas grow a matching field.
    note: str | None = None

    # Result-block fields (one cluster per type).
    final_energy_hartree: float | None = None
    converged: bool | None = None
    n_steps: int | None = None

    frequencies_cm1: list[float] | None = None
    n_imag: int | None = None
    imag_freq_cm1: float | None = None
    zpe_hartree: float | None = None

    electronic_energy_hartree: float | None = None

    # Builder-attached artifact metadata. Files are NOT embedded in
    # the scientific upload payload — they are uploaded in a second
    # phase via :meth:`ComputedSpeciesUpload.artifact_plan` /
    # :meth:`ComputedReactionUpload.artifact_plan` once the server
    # has assigned ``calculation.id`` values. See
    # ``tckdb_client/builders/artifact.py``.
    artifacts: list[Artifact] = field(default_factory=list)

    _validated: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.type not in _ALLOWED_TYPES:
            raise TCKDBBuilderValidationError(
                f"Calculation.type must be one of {sorted(_ALLOWED_TYPES)}, "
                f"got {self.type!r}."
            )
        if not isinstance(self.software_release, SoftwareRelease):
            raise TCKDBBuilderValidationError(
                "software_release must be a SoftwareRelease builder."
            )
        if not isinstance(self.level_of_theory, LevelOfTheory):
            raise TCKDBBuilderValidationError(
                "level_of_theory must be a LevelOfTheory builder."
            )
        for attr in ("input_geometry", "output_geometry"):
            value = getattr(self, attr)
            if value is not None and not isinstance(value, Geometry):
                raise TCKDBBuilderValidationError(
                    f"{attr}, when supplied, must be a Geometry builder."
                )
        for dep in self.depends_on:
            if not isinstance(dep, Calculation):
                raise TCKDBBuilderValidationError(
                    "depends_on entries must be Calculation builders."
                )
        if self.n_steps is not None:
            n_steps = ensure_int(self.n_steps, field="n_steps")
            if n_steps < 0:
                raise TCKDBBuilderValidationError(
                    f"n_steps must be >= 0, got {n_steps}."
                )
        if self.n_imag is not None:
            n_imag = ensure_int(self.n_imag, field="n_imag")
            if n_imag < 0:
                raise TCKDBBuilderValidationError(
                    f"n_imag must be >= 0, got {n_imag}."
                )
        if self.frequencies_cm1 is not None:
            for i, freq in enumerate(self.frequencies_cm1):
                if isinstance(freq, bool) or not isinstance(freq, (int, float)):
                    raise TCKDBBuilderValidationError(
                        f"frequencies_cm1[{i}] must be numeric, got "
                        f"{type(freq).__name__}."
                    )
        self.label = ensure_optional_non_empty_str(self.label, field="label")
        self.note = ensure_optional_non_empty_str(self.note, field="note")
        for i, art in enumerate(self.artifacts):
            if not isinstance(art, Artifact):
                raise TCKDBBuilderValidationError(
                    f"artifacts[{i}] must be an Artifact builder, got "
                    f"{type(art).__name__}."
                )
        self._validated = True

    # ----- Artifact attachment --------------------------------------

    def add_artifact(
        self,
        path: "str | Path",
        kind: str,
        *,
        label: str | None = None,
        sha256: str | None = None,
        bytes: int | None = None,
    ) -> Artifact:
        """Attach a local file to this calculation for later upload.

        File existence is not checked here — the path is materialised
        when the artifact plan is executed (so reused manifests don't
        fail at build time on a machine that hasn't downloaded the
        files yet). The kind / sha256 / bytes are validated up front
        because they form part of the artifact upload request shape;
        catching typos here turns into a deterministic builder error
        instead of a server-side 422.
        """
        artifact = Artifact(
            path=path,
            kind=kind,
            label=label,
            sha256=sha256,
            bytes=bytes,
        )
        self.artifacts.append(artifact)
        return artifact

    # ----- Factories ------------------------------------------------

    @classmethod
    def opt(
        cls,
        software_release: SoftwareRelease,
        level_of_theory: LevelOfTheory,
        *,
        input_geometry: Geometry | None = None,
        output_geometry: Geometry | None = None,
        final_energy_hartree: float | None = None,
        converged: bool | None = None,
        n_steps: int | None = None,
        depends_on: "Calculation | list[Calculation] | None" = None,
        label: str | None = None,
        note: str | None = None,
    ) -> "Calculation":
        """Geometry-optimisation calculation.

        ``note`` is a local-only free-text annotation; it is preserved
        on the builder for producer/adapter ergonomics but is not
        emitted on the wire (today's bundle schemas do not carry a
        per-calc note field).
        """
        return cls(
            type="opt",
            software_release=software_release,
            level_of_theory=level_of_theory,
            input_geometry=input_geometry,
            output_geometry=output_geometry,
            depends_on=_normalise_depends_on(depends_on),
            label=label,
            note=note,
            final_energy_hartree=final_energy_hartree,
            converged=converged,
            n_steps=n_steps,
        )

    @classmethod
    def freq(
        cls,
        software_release: SoftwareRelease,
        level_of_theory: LevelOfTheory,
        *,
        input_geometry: Geometry | None = None,
        output_geometry: Geometry | None = None,
        frequencies_cm1: list[float] | None = None,
        n_imag: int | None = None,
        imag_freq_cm1: float | None = None,
        zpe_hartree: float | None = None,
        depends_on: "Calculation | list[Calculation] | None" = None,
        label: str | None = None,
        note: str | None = None,
    ) -> "Calculation":
        """Harmonic-frequency calculation.

        ``note`` is local-only — see :meth:`Calculation.opt` for the
        emission caveat.
        """
        return cls(
            type="freq",
            software_release=software_release,
            level_of_theory=level_of_theory,
            input_geometry=input_geometry,
            output_geometry=output_geometry,
            depends_on=_normalise_depends_on(depends_on),
            label=label,
            note=note,
            frequencies_cm1=(
                list(frequencies_cm1) if frequencies_cm1 is not None else None
            ),
            n_imag=n_imag,
            imag_freq_cm1=imag_freq_cm1,
            zpe_hartree=zpe_hartree,
        )

    @classmethod
    def sp(
        cls,
        software_release: SoftwareRelease,
        level_of_theory: LevelOfTheory,
        *,
        input_geometry: Geometry | None = None,
        output_geometry: Geometry | None = None,
        electronic_energy_hartree: float | None = None,
        depends_on: "Calculation | list[Calculation] | None" = None,
        label: str | None = None,
        note: str | None = None,
    ) -> "Calculation":
        """Single-point energy calculation.

        ``note`` is local-only — see :meth:`Calculation.opt` for the
        emission caveat.
        """
        return cls(
            type="sp",
            software_release=software_release,
            level_of_theory=level_of_theory,
            input_geometry=input_geometry,
            output_geometry=output_geometry,
            depends_on=_normalise_depends_on(depends_on),
            label=label,
            note=note,
            electronic_energy_hartree=electronic_energy_hartree,
        )

    # ----- Internal helpers used by ComputedSpeciesUpload -----------

    def result_block(self) -> tuple[str, dict[str, Any]] | None:
        """Return ``(field_name, payload)`` for this calc's result block.

        ``field_name`` is one of ``opt_result`` / ``freq_result`` /
        ``sp_result``. Returns ``None`` when no result data was set
        — the bundle schema accepts a calc with no inline result.
        """
        if self.type == "opt":
            block: dict[str, Any] = {}
            if self.converged is not None:
                block["converged"] = self.converged
            if self.n_steps is not None:
                block["n_steps"] = self.n_steps
            if self.final_energy_hartree is not None:
                block["final_energy_hartree"] = self.final_energy_hartree
            return ("opt_result", block) if block else None
        if self.type == "freq":
            block = {}
            if self.n_imag is not None:
                block["n_imag"] = self.n_imag
            if self.imag_freq_cm1 is not None:
                block["imag_freq_cm1"] = self.imag_freq_cm1
            if self.zpe_hartree is not None:
                block["zpe_hartree"] = self.zpe_hartree
            modes = self._modes_payload()
            if modes is not None:
                block["modes"] = modes
            return ("freq_result", block) if block else None
        if self.type == "sp":
            if self.electronic_energy_hartree is None:
                return None
            return (
                "sp_result",
                {"electronic_energy_hartree": self.electronic_energy_hartree},
            )
        return None

    def result_fields_flat(self) -> dict[str, Any]:
        """Return flat result fields for the computed-reaction endpoint.

        The computed-reaction wire shape uses flat per-type fields
        (``opt_converged``, ``freq_n_imag``, ``freq_zpe_hartree``,
        ``freq_frequencies_cm1``, ``sp_electronic_energy_hartree``)
        rather than the nested result blocks the computed-species
        endpoint accepts (see ``CalculationIn`` in
        ``app/schemas/workflows/network_pdep_upload.py``). Builders
        therefore expose both shapes and let the upload object pick
        the right one.

        The returned dict only includes fields the user actually set;
        missing fields stay absent so the server gets a minimal,
        idempotent representation.
        """
        out: dict[str, Any] = {}
        if self.type == "opt":
            if self.converged is not None:
                out["opt_converged"] = self.converged
            if self.n_steps is not None:
                out["opt_n_steps"] = self.n_steps
            if self.final_energy_hartree is not None:
                out["opt_final_energy_hartree"] = self.final_energy_hartree
        elif self.type == "freq":
            if self.n_imag is not None:
                out["freq_n_imag"] = self.n_imag
            if self.imag_freq_cm1 is not None:
                out["freq_imag_freq_cm1"] = self.imag_freq_cm1
            if self.zpe_hartree is not None:
                out["freq_zpe_hartree"] = self.zpe_hartree
            if self.frequencies_cm1 is not None:
                # Sign-vs-n_imag cross-check (shares logic with
                # _modes_payload so producers don't see two different
                # local-validation outcomes for the same input).
                negatives = sum(
                    1 for f in self.frequencies_cm1 if f < 0
                )
                if self.n_imag is not None and negatives != self.n_imag:
                    raise TCKDBBuilderValidationError(
                        f"freq calc: n_imag={self.n_imag} disagrees "
                        f"with {negatives} negative entries in "
                        "frequencies_cm1."
                    )
                out["freq_frequencies_cm1"] = [
                    float(f) for f in self.frequencies_cm1
                ]
        elif self.type == "sp":
            if self.electronic_energy_hartree is not None:
                out["sp_electronic_energy_hartree"] = (
                    self.electronic_energy_hartree
                )
        return out

    def _modes_payload(self) -> list[dict[str, Any]] | None:
        """Emit a ``FrequencyModePayload`` list from ``frequencies_cm1``.

        Convention: negative frequency entries are imaginary. When
        ``n_imag`` is also set, the local count of negative entries must
        match it — otherwise we raise rather than guess.
        """
        if self.frequencies_cm1 is None:
            return None
        modes: list[dict[str, Any]] = []
        negatives = 0
        for i, freq in enumerate(self.frequencies_cm1):
            is_imag = freq < 0
            if is_imag:
                negatives += 1
            modes.append(
                {
                    "mode_index": i + 1,
                    "frequency_cm1": float(freq),
                    "is_imaginary": is_imag,
                }
            )
        if self.n_imag is not None and negatives != self.n_imag:
            raise TCKDBBuilderValidationError(
                f"freq calc: n_imag={self.n_imag} disagrees with "
                f"{negatives} negative entries in frequencies_cm1."
            )
        return modes

    def infer_dependency_role(self, parent: "Calculation") -> str:
        """Map a (self, parent) pair to a ``CalculationDependencyRole``.

        Phase 1 supports three unambiguous shapes:

        - ``freq`` on ``opt`` → ``freq_on``
        - ``sp`` on ``opt`` → ``single_point_on``
        - ``opt`` on any other calc → ``optimized_from``

        Anything else is rejected locally with a useful message so
        producers don't accidentally upload a misclassified edge.
        """
        if self.type == "freq" and parent.type == "opt":
            return "freq_on"
        if self.type == "sp" and parent.type == "opt":
            return "single_point_on"
        if self.type == "opt":
            return "optimized_from"
        raise TCKDBBuilderValidationError(
            "cannot infer dependency role for "
            f"{self.type!r} depending on {parent.type!r}; supported "
            "phase-1 shapes are freq→opt, sp→opt, opt→<any>."
        )


def _normalise_depends_on(
    value: "Calculation | list[Calculation] | None",
) -> list[Calculation]:
    """Coerce ``depends_on`` into a list, preserving caller order."""
    if value is None:
        return []
    if isinstance(value, Calculation):
        return [value]
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, Calculation):
                raise TCKDBBuilderValidationError(
                    "depends_on list entries must be Calculation builders."
                )
        return list(value)
    raise TCKDBBuilderValidationError(
        "depends_on must be a Calculation, list of Calculation, or None."
    )
