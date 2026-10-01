import re
from datetime import date
from typing import Self

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.common import SchemaBase
from tckdb_schemas.composite_scheme_rules import (
    COMPOSITE_SCHEME_NESTED,
    assert_composite_scheme_definition,
    assert_method_xor_composite_scheme,
)
from tckdb_schemas.enums import (
    CompositeExtrapolationFormula,
    CompositeInputSlot,
    CompositeSchemeKind,
    CompositeTermOperation,
    CoreTreatment,
    EnergyComponentKind,
    FrequencyScaleKind,
    SpinTreatment,
)
from tckdb_schemas.literature import LiteratureUploadRequest
from tckdb_schemas.upload_warning import UploadWarning
from tckdb_schemas.utils import normalize_optional_text, normalize_required_text


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

#: ``LevelOfTheoryRef.method`` contains ``//``: an ``energy//geometry`` pair
#: written as one method name (ARC's ``level_of_theory: "x//y"`` shorthand).
#: That is two ordinary levels of theory, not one method (ADR 0021), and a
#: single row named by both would be a level no program ran. Refused.
LEVEL_OF_THEORY_METHOD_IS_COMPOUND = "level_of_theory_method_is_compound"

#: ``LevelOfTheoryRef.method`` is a named composite method followed by a
#: correction-table label (``cbs-qb3-paraskevas``) or a year (``cbsqb32023``).
#: Those select a set of AEC/BAC parameters in Arkane, not a method: the
#: calculation that ran is CBS-QB3. The name is stored as sent (never aliased,
#: ADR 0021) and this warning tells the producer to send the method and name
#: the table as an energy correction scheme.
W_LEVEL_OF_THEORY_METHOD_NAMES_CORRECTION_TABLE = (
    "level_of_theory_method_names_correction_table"
)

#: ``G3//B3LYP`` and ``G3(MP2)//B3LYP`` are the literature names (Baboul et al.,
#: J. Chem. Phys. 110, 7650 (1999)) of the Gaussian keywords G3B3 and G3MP2B3: one
#: recipe, not a pair of levels. They are refused like any ``//``, with advice
#: that names the method to send, and are not aliased: an alias would store a
#: ``//`` in a method name. Matched against the lower-cased, space-free name.
_NAMED_METHODS_WRITTEN_AS_PAIRS = re.compile(
    r"(?P<recipe>g3|g3mp2|g3\(mp2\))//b3(?:lyp)?(?:/6-31g\(d\))?"
)


def _named_method_written_as_pair(method: str) -> str | None:
    """Return ``"G3B3"`` / ``"G3MP2B3"`` for the literature spellings, else ``None``."""
    match = _NAMED_METHODS_WRITTEN_AS_PAIRS.fullmatch("".join(method.lower().split()))
    if match is None:
        return None
    return "G3B3" if match.group("recipe") == "g3" else "G3MP2B3"


#: Method names a correction-table label can follow: the named composite methods
#: (any spelling the curated aliases join, or the hyphen-free one) and the
#: ordinary methods Arkane keys corrections on. A fixed list on purpose: a
#: pattern for "any name then a year" would warn on real names, and a stem the
#: list does not name is left alone. Parentheses are balanced inside a stem.
_CORRECTION_TABLE_STEMS = (
    r"rocbs-?qb3|cbs-?qb3|cbs-?4m|cbs-?apno"
    r"|g3(?:mp2|\(mp2\))?(?:b3)?|g4(?:mp2|\(mp2\))?"
    r"|w1(?:u|bd|ro)?|w2"
    r"|b3lyp|cam-?b3lyp|b2plyp(?:-?d3(?:bj)?)?|pbe0?|wb97x-?d3?|wb97xd|m06-?2x|m06l|m06hf"
    r"|bp86|blyp|tpss|revpbe|hf|mp2|ccsd|ccsd\(t\)|ccsd\(t\)-?f12"
    r"|dlpno-?ccsd\(t\)(?:-?f12)?"
)

#: A stem immediately followed by ``-paraskevas`` (``cbsqb3paraskevas`` once
#: Arkane has stripped the hyphens) or a four-digit year, with or without a
#: hyphen. Matched against the lower-cased method.
_CORRECTION_TABLE_METHOD = re.compile(
    rf"(?P<stem>{_CORRECTION_TABLE_STEMS})(?P<table>-?paraskevas|-?(?:19|20)\d{{2}})"
)


def correction_table_method_stem(method: str) -> str | None:
    """Return the composite stem when ``method`` names a correction table.

    :param method: A method name as a producer wrote it.
    :returns: The named-method part (``"cbs-qb3"`` for
        ``"cbs-qb3-paraskevas"``), or ``None`` when ``method`` is not such a
        name. Nothing is rewritten; this only recognises the shape.
    """
    match = _CORRECTION_TABLE_METHOD.fullmatch(method.strip().lower())
    return match.group("stem") if match else None

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
    """Upload-facing reference to a software release.

    A release is identified by ``(name, version, revision, build)``; two
    references that agree on all four (an absent value matches only another
    absent value) are the same release row. ``release_date`` and ``notes``
    are descriptive and do not take part in identity.

    Which field holds what:

    * ``version``: the release number the vendor or project publishes, such
      as ``16`` for Gaussian, ``6.0.1`` for ORCA or ``2025.1`` for Molpro.
      Give the number alone. A parsed startup banner such as
      ``"Gaussian 16, Revision C.02"`` is accepted, split into version
      ``16`` and revision ``C.02``, and reported as a warning.
    * ``revision``: a finer label inside one version, written the way the
      producer writes it. For a vendor program this is the vendor's own
      revision label, such as Gaussian ``C.02``. For analysis software that
      has no revision labels it is the source state the run used, such as
      the commit hash of an Arkane or RMG checkout, because
      ``software_release`` has no separate commit field (see below).
    * ``build``: a variant of the same version and revision that was
      compiled or packaged differently and can give different numbers or
      different capabilities, such as ``mpi``, ``cuda`` or a distributor's
      build identifier. Leave it out when the program was not built in a
      way that matters; two references that differ only here are kept as
      two releases, so do not put a timestamp or host name in it.

    There is no ``git_commit`` field on this reference. A commit hash goes in
    ``revision`` until one is added; ``WorkflowToolReleaseRef`` does have a
    ``git_commit`` because workflow tools are identified by code state.
    """

    name: str = Field(min_length=1)
    version: str | None = Field(
        default=None,
        description=(
            "Published release number, for example 16, 6.0.1 or 2025.1. "
            "Number only; a full startup banner is split into version and "
            "revision with a warning."
        ),
    )
    revision: str | None = Field(
        default=None,
        description=(
            "Finer label within one version: the vendor revision label "
            "(Gaussian C.02), or the commit hash for analysis software "
            "that has no revision labels. Part of release identity."
        ),
    )
    build: str | None = Field(
        default=None,
        description=(
            "Compile or packaging variant of the same version and revision, "
            "for example mpi or cuda. Part of release identity; omit "
            "unless the build changes results or capabilities."
        ),
    )
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


def collect_ref_warnings(
    root: object,
    *,
    field_prefix: str = "",
) -> list[UploadWarning]:
    """Collect every ref-level warning in a validated request tree.

    The software-release version warnings of
    :func:`collect_software_release_version_warnings` plus the
    :class:`LevelOfTheoryRef` method warnings, from one walk, so a route that
    calls this one function gets both and a new warning on a ref needs no new
    call site.

    :param root: Any validated request (sub)tree.
    :param field_prefix: Dot-path prefix naming ``root``'s position.
    """
    warnings: list[UploadWarning] = []
    _walk_for_software_release_warnings(
        root, field_prefix, warnings, include_level_of_theory=True
    )
    return warnings


def _walk_for_software_release_warnings(
    obj: object,
    prefix: str,
    out: list[UploadWarning],
    *,
    include_level_of_theory: bool = False,
) -> None:
    if obj is None:
        return
    if isinstance(obj, SoftwareReleaseRef):
        warning = obj.version_warning(field_prefix=prefix)
        if warning is not None:
            out.append(warning)
        return
    if include_level_of_theory and isinstance(obj, OrdinaryLevelOfTheoryRef):
        method_warning = obj.method_warning(field_prefix=prefix)
        if method_warning is not None:
            out.append(method_warning)
        # A user-built scheme's input levels are ordinary levels with their own
        # method warnings; the walk must not stop at the level that holds them.
        definition = getattr(obj, "composite_scheme", None)
        if definition is not None:
            _walk_for_software_release_warnings(
                definition, f"{prefix}composite_scheme.", out, include_level_of_theory=True
            )
        return
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            _walk_for_software_release_warnings(
                getattr(obj, name),
                f"{prefix}{name}.",
                out,
                include_level_of_theory=include_level_of_theory,
            )
        return
    if isinstance(obj, (list, tuple)):
        base = prefix[:-1] if prefix.endswith(".") else prefix
        for i, item in enumerate(obj):
            _walk_for_software_release_warnings(
                item, f"{base}[{i}].", out, include_level_of_theory=include_level_of_theory
            )
        return
    if isinstance(obj, dict):
        base = prefix[:-1] if prefix.endswith(".") else prefix
        for key, value in obj.items():
            _walk_for_software_release_warnings(
                value, f"{base}[{key!r}].", out, include_level_of_theory=include_level_of_theory
            )
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


class OrdinaryLevelOfTheoryRef(SchemaBase):
    """Upload-facing reference to an ordinary level of theory: a method a program ran.

    The fields and checks every level of theory has. :class:`LevelOfTheoryRef`
    adds the one thing an *ordinary* level cannot be: a user-built composite
    scheme. The input levels of such a scheme are ordinary (a composite is never
    built from another composite), so they are typed as this class.
    """

    method: str = Field(min_length=1)
    basis: str | None = None
    aux_basis: str | None = None
    cabs_basis: str | None = None
    dispersion: str | None = None
    solvent: str | None = None
    solvent_model: str | None = None
    keywords: str | None = None
    spin_treatment: SpinTreatment | None = None
    #: Frozen-core or all-electron (ADR 0021). State it only when the run
    #: says so; leave it out otherwise. It joins the level's identity only
    #: when given, so a payload that omits it is the level it always was.
    core_treatment: CoreTreatment | None = None

    # Bookkeeping, as on ``SoftwareReleaseRef``: not wire fields, read back
    # through ``method_warning()``.
    _method_warning_code: str | None = PrivateAttr(default=None)
    _method_warning_message: str | None = PrivateAttr(default=None)

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str | None) -> str | None:
        if value is None:
            # Only a ``LevelOfTheoryRef`` that names a ``composite_scheme`` instead
            # reaches here with no method; an ordinary level's field is required.
            return None
        value = normalize_required_text(value)
        if "//" in value:
            named = _named_method_written_as_pair(value)
            if named is not None:
                raise CodedValidationError(
                    LEVEL_OF_THEORY_METHOD_IS_COMPOUND,
                    (
                        f"level_of_theory.method={value!r} is the literature name of the "
                        f"named composite method {named!r}, which is one recipe run by one "
                        f"program keyword, not an energy//geometry pair. Send method="
                        f"{named!r}. (A genuine pair of levels is sent as separate "
                        "calculations, each with its own level of theory.)"
                    ),
                    context={"field": "method", "value": value, "named_method": named},
                    message_prefix=False,
                )
            raise CodedValidationError(
                LEVEL_OF_THEORY_METHOD_IS_COMPOUND,
                (
                    f"level_of_theory.method={value!r} contains '//', which writes an "
                    "energy level and a geometry level as one name (energy//geometry). "
                    "That is two levels of theory, not one method. Send the single-point "
                    "and the optimization levels as separate calculations, each with its "
                    "own level of theory."
                ),
                context={"field": "method", "value": value},
                message_prefix=False,
            )
        return value

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

    @model_validator(mode="after")
    def warn_on_correction_table_method(self) -> Self:
        """Warn when ``method`` is a composite name plus a correction-table label.

        Never refuses and never rewrites: the verbatim name is what is stored,
        and it is a different identity from the method it is a table for.
        """
        stem = None if self.method is None else correction_table_method_stem(self.method)
        if stem is not None:
            self._method_warning_code = W_LEVEL_OF_THEORY_METHOD_NAMES_CORRECTION_TABLE
            self._method_warning_message = (
                f"level_of_theory.method={self.method!r} is the method {stem!r} "
                "followed by a label that selects an energy-correction table (a "
                "correction-set name or a year), not a method. The calculation ran "
                f"{stem!r}. Stored as sent, as a separate level of theory from "
                f"{stem!r}. Send method={stem!r} and name the table on the energy "
                "correction scheme instead."
            )
        return self

    def method_warning(self, field_prefix: str = "") -> UploadWarning | None:
        """The warning :meth:`warn_on_correction_table_method` produced, if any.

        :param field_prefix: Dot-path prefix naming this ref's position in the
            enclosing request tree.
        """
        if self._method_warning_code is None:
            return None
        return UploadWarning(
            field=f"{field_prefix}method",
            code=self._method_warning_code,
            message=self._method_warning_message or "",
        )


class CompositeSchemeInputLevel(OrdinaryLevelOfTheoryRef):
    """An input level of a user-built composite scheme: an ordinary level, never composite.

    A composite is built from ordinary levels of theory. An input that itself
    carries a ``composite_scheme`` is a nested composite and is refused with
    ``composite_scheme_nested`` (a named composite method such as CBS-QB3 used as
    an input is refused by the server, which holds the catalogue). State
    ``core_treatment`` when a term depends on it: a core-valence difference is
    the same level with ``all_electron`` against ``frozen_core``.
    """

    @model_validator(mode="before")
    @classmethod
    def refuse_nested_composite(cls, data: object) -> object:
        if isinstance(data, dict) and "composite_scheme" in data:
            raise CodedValidationError(
                COMPOSITE_SCHEME_NESTED,
                (
                    "a composite scheme's input level carries its own composite_scheme. A "
                    "composite is built from ordinary levels of theory (method, basis, ...); "
                    "nesting composites is refused. Send the ordinary levels the inner recipe "
                    "would read as separate terms of this scheme."
                ),
                context={"field": "level_of_theory.composite_scheme.terms[].inputs[].level_of_theory"},
                message_prefix=False,
            )
        return data


class CompositeSchemeTermInputIn(SchemaBase):
    """One input of a scheme term: the level it reads, and which slot it fills.

    :param slot: ``value`` (base / value term), ``high`` / ``low`` (difference),
        or ``cardinal`` (one point of an extrapolation).
    :param level_of_theory: The ordinary level the input calculation ran at.
    :param cardinal_number: The declared cardinal number (2 for double-zeta, 3
        for triple-zeta, ...). Required on a ``cardinal`` slot; never derived from
        the basis name. Part of the scheme's identity.
    """

    slot: CompositeInputSlot
    level_of_theory: CompositeSchemeInputLevel
    cardinal_number: int | None = Field(default=None, ge=1, le=32767)


class CompositeSchemeTermIn(SchemaBase):
    """One term of a user-built scheme; the scheme's total is the sum of its terms.

    :param key: Your name for the term (for example ``"corr"``, ``"dcv"``).
        Local to this definition and **not part of the scheme's identity**: the
        calculation's ``composite_result.inputs`` name terms by it, and two
        depositors who key the same recipe differently get the same scheme.
    :param operation: ``base`` / ``value`` (one input taken as it is),
        ``extrapolation``, or ``difference`` (high minus low). ``empirical`` is
        refused.
    :param energy_component: Which part of the input energy the term reads:
        ``total``, ``reference``, ``correlation`` (the whole correlation energy,
        triples included), ``triples``, ``dboc``, ``scalar_relativistic``.
    :param formula: The extrapolation formula (``extrapolation`` terms only).
    :param exponent: The formula's exponent where it has one (``inverse_power``,
        ``inverse_power_shifted_half``). Part of the identity: exponent 3 and 3.4
        are two schemes.
    :param inputs: The levels the term reads, each filling a slot.
    """

    key: str = Field(min_length=1)
    operation: CompositeTermOperation
    energy_component: EnergyComponentKind
    formula: CompositeExtrapolationFormula | None = None
    exponent: float | None = Field(default=None, allow_inf_nan=False)
    inputs: list[CompositeSchemeTermInputIn] = Field(min_length=1)

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return normalize_required_text(value)


class CompositeSchemeDefinition(SchemaBase):
    """A user-built composite scheme, sent inline as ``level_of_theory.composite_scheme``.

    The server resolves each input level, canonicalises the definition and
    names the level of theory itself (a readable label such as
    ``CBS[ref:HF/cc-pVQZ + corr:CCSD(T)/cc-pV{T,Q}Z; inverse_power x=3 n=3,4]``);
    you do not choose the name. Formula, exponent and cardinal numbers are part of
    identity; ``key`` names, ``literature`` and order of the inputs within a term
    are not.

    :param kind: ``extrapolation`` (value and extrapolation terms) or
        ``additive`` (at least one difference term). ``named_method`` is the
        server's own kind and is refused.
    :param terms: The terms, in order. The position of a term is its place in
        this list.
    :param literature: The paper that defines the recipe, when there is one.
        Provenance only: it is not part of the scheme's identity.
    """

    kind: CompositeSchemeKind
    terms: list[CompositeSchemeTermIn] = Field(min_length=1)
    literature: LiteratureUploadRequest | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        assert_composite_scheme_definition(self)
        return self


class LevelOfTheoryRef(OrdinaryLevelOfTheoryRef):
    """Upload-facing reference to a level of theory: a program's method, or your own recipe.

    Send exactly one of ``method`` and ``composite_scheme``. ``method`` is a
    method a program ran (including a named composite method such as CBS-QB3,
    sent by name alone). ``composite_scheme`` defines a recipe of your own
    (a CCSD(T)/CBS extrapolation, a focal-point sum) from ordinary levels of
    theory; the server names the resulting level of theory and binds it to the
    recipe. Both, or neither, is refused. With ``composite_scheme`` the other
    level fields (basis, dispersion, ...) are not sent: they belong to the
    scheme's input levels.
    """

    method: str | None = Field(default=None, min_length=1)
    composite_scheme: CompositeSchemeDefinition | None = None

    @model_validator(mode="after")
    def validate_method_xor_composite_scheme(self) -> Self:
        assert_method_xor_composite_scheme(self.method, self.composite_scheme)
        if self.composite_scheme is not None:
            ordinary = {
                name: getattr(self, name)
                for name in (
                    "basis",
                    "aux_basis",
                    "cabs_basis",
                    "dispersion",
                    "solvent",
                    "solvent_model",
                    "keywords",
                    "spin_treatment",
                    "core_treatment",
                )
                if getattr(self, name) is not None
            }
            if ordinary:
                raise CodedValidationError(
                    "composite_scheme_malformed",
                    (
                        "level_of_theory carries composite_scheme together with "
                        f"{', '.join(sorted(ordinary))}. These describe a single method's run; a "
                        "composite scheme's levels are stated on its inputs. Remove them from the "
                        "level of theory and put them on the scheme's input levels."
                    ),
                    context={
                        "field": "level_of_theory",
                        "rule": "ordinary_fields_with_scheme",
                        "fields": sorted(ordinary),
                    },
                    message_prefix=False,
                )
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
    ``(level_of_theory, software_release, scale_kind, value,
    source_literature, workflow_tool_release)`` and matches the DB unique
    index on ``frequency_scale_factor``. ``note`` is descriptive only and
    never participates in identity/dedupe.

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
    :param software: The ESS program *release* the factor was fit against
        (e.g. Gaussian 16, Revision C.02). A harmonic frequency scale
        factor is fit against a program's own build-level vibrational
        frequencies, which change between releases of the same program
        (correction-scheme-provenance plan v2 §6, mirroring
        ``EnergyCorrectionSchemeRef.software``'s reasoning) -- so the
        factor is release-specific, not merely program-specific. Only
        ``name`` is required; a depositor who knows only the program
        resolves to the version-less release row for it. Null means
        software-agnostic or unknown.
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
    software: SoftwareReleaseRef | None = None
    source_literature: LiteratureUploadRequest | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


FreqScaleFactorRef.model_rebuild()
