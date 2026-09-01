import re
from datetime import date
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import FrequencyScaleKind, SpinTreatment
from tckdb_schemas.upload_warning import UploadWarning
from tckdb_schemas.utils import normalize_optional_text, normalize_required_text

if TYPE_CHECKING:
    from tckdb_schemas.literature import LiteratureUploadRequest


#: A declared ``version`` that embeds a parsed ESS startup banner --
#: ``"Gaussian 09, Revision D.01"`` instead of ``version="09",
#: revision="D.01"``. Issue #305 measured 494 Gaussian software_release
#: rows on the deployed archive carrying the composite form, and five
#: real ARC payloads that HTTP 422 refused it (never accepted anywhere)
#: broke real ingestion outright. This code now names a warning, not a
#: refusal: :meth:`SoftwareReleaseRef.normalize_composite_version` splits
#: the composite deterministically wherever a leading package-name token
#: matches the declared ``name`` and reports it under this code. See
#: ``W_SOFTWARE_RELEASE_NAME_LOOKS_WRONG`` for the sibling case where the
#: leading token does *not* match -- a different defect with a different
#: remedy, so it gets its own code rather than sharing this one.
W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE = "software_release_version_is_composite"

#: The version's leading package-name token disagrees with the declared
#: ``name`` -- e.g. ``name="gaussian", version="ORCA 6.0.0"``. Measured
#: live in five ARC records: the calculation actually ran on ORCA (the
#: version is the parser-observed fact) but a stale/default ``name``
#: rode along from an earlier species in the same run. Normalizing this
#: the way the matching case is normalized would manufacture
#: ``name="gaussian", version="6.0.0"`` -- a Gaussian release that never
#: existed -- and destroy the only evidence the record disagrees with
#: itself. Left completely untouched; only the warning fires.
W_SOFTWARE_RELEASE_NAME_LOOKS_WRONG = "software_release_name_looks_wrong"

#: Any internal whitespace is the sole trigger for inspecting ``version``
#: further below. A real version token never has one; a parsed ESS
#: banner always does (it is "<name> <version>[, Revision <revision>]").
#: Checked against real version strings that must stay untouched and
#: silent: "16", "09", "1.1.0" (ARC), "2025.1" (Molpro-style), "6.0-rc2",
#: "v4.2.1", "2021.2.0+cuda", "5.0.3" (ORCA), "7.0.2" (NWChem), and the
#: literal 4-character string "None" (a separate, unrelated defect --
#: see the module docstring below). None contain whitespace.
_HAS_INTERNAL_WHITESPACE = re.compile(r"\s")

#: Applied only to the remainder *after* a matching leading package name
#: has been stripped. Splits "09, Revision D.01" into version="09",
#: revision="D.01"; leaves a remainder with no such suffix (e.g. "6.0.0")
#: alone.
_TRAILING_REVISION_LABEL = re.compile(
    r"^(?P<version>.*?)\s*,\s*revision\s+(?P<revision>\S.*)$",
    re.IGNORECASE,
)

_LEADING_TOKEN_PUNCTUATION = ",:;"


def _split_leading_token(text: str) -> tuple[str, str]:
    """Split ``text`` into its first whitespace-delimited token and the rest.

    :returns: ``(token, remainder)``. ``remainder`` is ``""`` when ``text``
        has no internal whitespace (a single token).
    """
    parts = text.split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return (parts[0] if parts else text), ""


class SoftwareReleaseRef(SchemaBase):
    """Upload-facing reference to a software release."""

    name: str = Field(min_length=1)
    version: str | None = None
    revision: str | None = None
    build: str | None = None
    release_date: date | None = None
    notes: str | None = None

    # Bookkeeping set by ``normalize_composite_version`` below, not part
    # of the wire contract: excluded from serialization by pydantic, and
    # invisible to ``extra="forbid"`` since it is a private attribute
    # rather than a field. Read back through ``version_warning()``.
    _version_warning_code: str | None = PrivateAttr(default=None)
    _version_warning_message: str | None = PrivateAttr(default=None)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_required_text(value)

    @model_validator(mode="after")
    def normalize_optional_fields(self) -> Self:
        self.version = normalize_optional_text(self.version)
        self.revision = normalize_optional_text(self.revision)
        self.build = normalize_optional_text(self.build)
        self.notes = normalize_optional_text(self.notes)
        return self

    @model_validator(mode="after")
    def normalize_composite_version(self) -> Self:
        """Warn on, and deterministically normalise, a composite ``version``.

        Never refuses (issue #305 follow-up: refusing broke real ARC
        ingestion outright -- five real payloads carried
        ``version="ORCA 6.0.0"`` under a stale ``name="gaussian"``, and
        every one of them was a correct deposit of a producer bug this
        archive has no business rejecting). Instead:

        1. If ``version`` has no internal whitespace, it is not a banner
           shape at all -- return silently. This also deliberately leaves
           the unrelated literal string ``"None"`` alone (86 rows on the
           ARC fixtures; a different defect -- something serialised
           Python's ``None`` through ``str()`` upstream -- with no comma,
           space, or structure a version/revision split could act on).

        2. Split off the leading whitespace-delimited token. If it does
           **not** case-insensitively match the declared ``name``, this
           is the mismatch case: the version is the observed fact (it
           came from parsing a real ESS banner) and the name is the
           inherited one (ARC's own default/previous value bleeding into
           a per-calculation software_release). Leave both fields
           completely untouched and warn
           ``W_SOFTWARE_RELEASE_NAME_LOOKS_WRONG`` -- stripping here would
           manufacture a release that never ran.

        3. If it *does* match, strip the leading name and try to split a
           trailing ``", Revision <label>"`` suffix out of what remains.
           If the depositor already supplied their own ``revision``,
           do not choose between it and the one embedded in ``version``
           -- leave both fields exactly as declared and warn. Otherwise
           write ``version``/``revision`` from the split (or just the
           stripped ``version`` when there is no revision suffix) and
           warn ``W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE``.

        A successful split also appends a short provenance note to
        ``notes`` recording the verbatim string as declared -- see the
        note on ``notes`` below for why here and not
        ``calculation.observed_software_banner``.
        """
        if self.version is None or not _HAS_INTERNAL_WHITESPACE.search(self.version):
            return self

        original = self.version
        leading_token, remainder = _split_leading_token(original)
        if not remainder:
            return self

        if (
            leading_token.rstrip(_LEADING_TOKEN_PUNCTUATION).lower()
            != self.name.lower()
        ):
            self._version_warning_code = W_SOFTWARE_RELEASE_NAME_LOOKS_WRONG
            self._version_warning_message = (
                f"software_release.name={self.name!r} does not match the "
                f"leading token ({leading_token!r}) of "
                f"software_release.version={self.version!r}. This looks "
                "like a stale or default name that rode along with a "
                "version observed from a different program -- the version "
                "is the more likely observed fact here. Left both fields "
                "exactly as declared; verify which of name/version is "
                "correct before trusting this record's software identity."
            )
            return self

        candidate = remainder.strip()
        if not candidate:
            return self

        match = _TRAILING_REVISION_LABEL.match(candidate)
        if match is not None:
            split_version = match.group("version").strip()
            split_revision = match.group("revision").strip()
            if self.revision is not None:
                self._version_warning_code = W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE
                self._version_warning_message = (
                    f"software_release.version={self.version!r} looks like "
                    "a parsed software banner embedding its own revision "
                    f"label ({split_revision!r}), but "
                    f"software_release.revision={self.revision!r} was "
                    "already supplied. Left both fields exactly as "
                    "declared rather than choosing between them."
                )
                return self
            self.version = split_version
            self.revision = split_revision
            self._version_warning_code = W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE
            self._version_warning_message = (
                f"software_release.version was declared as {original!r}, a "
                f"parsed software banner. Normalised to "
                f"version={split_version!r}, revision={split_revision!r} "
                f"(the leading {self.name!r} package name was stripped and "
                "the trailing revision label was split out)."
            )
        else:
            self.version = candidate
            self._version_warning_code = W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE
            self._version_warning_message = (
                f"software_release.version was declared as {original!r}, "
                f"embedding the software's own name ({self.name!r}). "
                f"Normalised to version={candidate!r}."
            )

        # ``notes`` is a free-text field on a *deduped* provenance row
        # (``software_release`` dedupes on (software_id, version,
        # revision, build); see resolve_software_release). It is the
        # honest place for this, not calculation.observed_software_banner
        # / software_reconciliation_status (DR-0008): those specifically
        # model a *parser*-observed banner extracted from an ESS output
        # artifact at a later, separate seam, and are already
        # ``declared_only`` for every one of these rows because no
        # parser ever ran here -- setting them from an upload-time string
        # would misrepresent this as parser evidence and could later
        # collide with a real parsed banner for the same calculation.
        # Since normalisation is a pure, deterministic, invertible
        # reformat (strip a matching name prefix; split a trailing
        # "Revision X" label), the original is always mechanically
        # reconstructible from (name, version, revision) -- this note is
        # a convenience trace, not the only record. It survives only on
        # the release row's *first* creation for a given
        # (software_id, version, revision, build) triple, matching this
        # table's existing dedupe-by-identity behaviour: a later deposit
        # that resolves to an already-existing release does not update
        # ``notes`` on it.
        trace = f"[auto] declared software_release.version was {original!r} before normalisation"
        self.notes = f"{self.notes}\n{trace}" if self.notes else trace
        return self

    def version_warning(self, field_prefix: str = "") -> UploadWarning | None:
        """The warning :meth:`normalize_composite_version` produced, if any.

        :param field_prefix: Dot-path prefix naming this ref's position
            in the enclosing request tree, e.g.
            ``"species['ch4'].calculations[2].software_release."``.
        """
        if self._version_warning_code is None:
            return None
        return UploadWarning(
            field=f"{field_prefix}version",
            code=self._version_warning_code,
            message=self._version_warning_message or "",
        )


def collect_software_release_version_warnings(
    root: object,
    *,
    field_prefix: str = "",
) -> list[UploadWarning]:
    """Walk a validated request tree and collect every ``version_warning``.

    Generic over the caller's schema shape on purpose. Every upload route
    embeds ``software_release`` at a different depth and through a
    different local structure -- a bare field on a standalone calculation,
    ``species[i].calculations[j].software_release`` on a reaction bundle,
    ``species[i].conformers[k].calculation.software_release`` one level
    deeper still, a solve-level ref on a PDep network. Enumerating every
    route's field paths by hand is exactly the drift this module's own
    normalisation exists to avoid repeating: a new nesting shape added to
    any one route would silently carry no warning. Walking the already
    -validated pydantic tree instead means every route that embeds a
    ``SoftwareReleaseRef`` anywhere gets the same warning behaviour with
    no route-specific wiring, now or when a new nesting shape is added
    later.

    :param root: Any validated request (sub)tree -- a pydantic model, a
        list/tuple of them, a dict of them, or ``None``.
    :param field_prefix: Dot-path prefix naming ``root``'s position in the
        full request, e.g. ``"species['ch4']."``. Defaults to the empty
        string for a call rooted at the request itself.
    """
    warnings: list[UploadWarning] = []
    _walk_for_software_release_warnings(root, field_prefix, warnings)
    return warnings


def _walk_for_software_release_warnings(
    obj: object, prefix: str, out: list[UploadWarning]
) -> None:
    if obj is None:
        return
    if isinstance(obj, SoftwareReleaseRef):
        warning = obj.version_warning(field_prefix=prefix)
        if warning is not None:
            out.append(warning)
        return
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            _walk_for_software_release_warnings(
                getattr(obj, name), f"{prefix}{name}.", out
            )
        return
    if isinstance(obj, (list, tuple)):
        base = prefix[:-1] if prefix.endswith(".") else prefix
        for i, item in enumerate(obj):
            _walk_for_software_release_warnings(item, f"{base}[{i}].", out)
        return
    if isinstance(obj, dict):
        base = prefix[:-1] if prefix.endswith(".") else prefix
        for key, value in obj.items():
            _walk_for_software_release_warnings(value, f"{base}[{key!r}].", out)
        return


class WorkflowToolReleaseRef(SchemaBase):
    """Upload-facing reference to a workflow tool code state."""

    name: str = Field(min_length=1)
    version: str | None = None
    git_commit: str | None = Field(default=None, min_length=1, max_length=40)
    release_date: date | None = None
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_required_text(value)

    @model_validator(mode="after")
    def normalize_optional_fields(self) -> Self:
        self.version = normalize_optional_text(self.version)
        self.git_commit = normalize_optional_text(self.git_commit)
        self.notes = normalize_optional_text(self.notes)
        return self


class LevelOfTheoryRef(SchemaBase):
    """Upload-facing reference to a level of theory."""

    method: str = Field(min_length=1)
    basis: str | None = None
    aux_basis: str | None = None
    cabs_basis: str | None = None
    dispersion: str | None = None
    solvent: str | None = None
    solvent_model: str | None = None
    keywords: str | None = None
    spin_treatment: SpinTreatment | None = None

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        return normalize_required_text(value)

    @model_validator(mode="after")
    def normalize_optional_fields(self) -> Self:
        self.basis = normalize_optional_text(self.basis)
        self.aux_basis = normalize_optional_text(self.aux_basis)
        self.cabs_basis = normalize_optional_text(self.cabs_basis)
        self.dispersion = normalize_optional_text(self.dispersion)
        self.solvent = normalize_optional_text(self.solvent)
        self.solvent_model = normalize_optional_text(self.solvent_model)
        self.keywords = normalize_optional_text(self.keywords)
        return self


class SoftwareRef(SchemaBase):
    """Upload-facing reference to a software package (name only, no version).

    Used when the relevant identifier is the software product rather than
    a specific release — for example, the software context of a frequency
    scale factor entry.
    """

    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_required_text(value)


class FreqScaleFactorRef(SchemaBase):
    """Content-keyed reference to a frequency scale factor.

    The service layer finds or creates the immutable
    ``frequency_scale_factor`` registry row whose identity matches the
    supplied fields. Identity is the full tuple
    ``(level_of_theory, software, scale_kind, value, source_literature,
    workflow_tool_release)`` and matches the DB unique index on
    ``frequency_scale_factor``. ``note`` is descriptive only and never
    participates in identity/dedupe.

    Source handling:

    * If structured literature is available, pass ``source_literature``;
      it is resolved/created via the standard literature pipeline.
    * If only a citation string is available, pass it in ``note`` and
      leave ``source_literature`` null. Do not synthesize placeholder
      literature rows from raw citation strings.
    * If a workflow tool's curated data file is the proximate source,
      pass ``workflow_tool_release`` and put any descriptive file/source
      reference in ``note``.

    Null ``frequency_scale_factor_id`` on a statmech row means
    "unknown/not recorded". Pass ``value=1.0`` with no source to represent
    explicitly unscaled (a real registry row exists, just with value 1.0).

    :param level_of_theory: Level of theory this factor applies to.
    :param scale_kind: Type of scaling (fundamental, ZPE, enthalpy, etc.).
    :param value: The scale factor value.
    :param software: The ESS software the factor applies to (e.g.
        Gaussian). Null means software-agnostic or unknown.
    :param source_literature: Structured literature provenance, when
        available. Mutually informative with ``workflow_tool_release``;
        either, both, or neither may be supplied.
    :param workflow_tool_release: Workflow tool (e.g. ARC) whose data
        file was the proximate source, when the factor was looked up
        from a tool table rather than directly from a paper.
    :param note: Optional descriptive note. Never used for dedupe.
    """

    level_of_theory: LevelOfTheoryRef
    scale_kind: FrequencyScaleKind = FrequencyScaleKind.fundamental
    value: float = Field(gt=0)
    software: SoftwareRef | None = None
    source_literature: "LiteratureUploadRequest | None" = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


# Resolve the forward ref now that the class body is closed.
from tckdb_schemas.literature import LiteratureUploadRequest  # noqa: E402

FreqScaleFactorRef.model_rebuild()
