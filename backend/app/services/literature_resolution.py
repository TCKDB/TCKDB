from __future__ import annotations

import html
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.common import LiteratureKind
from app.db.models.literature import Literature
from app.schemas.entities.literature import LiteratureCreate
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.services.literature_metadata import (
    fetch_doi_metadata,
    fetch_isbn_metadata,
    normalize_doi,
    normalize_isbn,
)
from app.services.provenance_warnings import (
    W_LITERATURE_TITLE_MISMATCH,
    W_LITERATURE_YEAR_MISMATCH,
)

# ---------------------------------------------------------------------------
# Depositor-vs-fetched-metadata mismatch
# ---------------------------------------------------------------------------
#
# Which fields are compared, and why only these two
# ---------------------------------------------------------------------------
# ``title`` is the field that matters: it is the one piece of fetched
# metadata a depositor is guaranteed to have an independent opinion about
# (they read the paper), so a disagreement is strong evidence the DOI/ISBN
# names a different work than the one actually being cited. ``year`` is a
# cheap second check with the same property (a depositor who knows the
# paper usually knows the year) and essentially no false-positive surface
# once both sides are present integers.
#
# ``journal``/``pages`` are deliberately NOT compared. Both are
# formatting-heavy in a way title/year are not: "J. Phys. Chem. A" vs.
# "The Journal of Physical Chemistry A", "101-110" vs. "101-10" (Crossref
# sometimes drops a shared prefix) are disagreements about how the same
# fact is written, not about which paper is being cited. Warning on those
# would mostly fire on correctly-cited papers and train depositors to
# ignore this warning family. ``volume``/``issue``/``publisher``/``url``
# are not compared for the same reason (formatting/URL-shape noise) and
# because they carry less identifying signal than title/year in the first
# place.
#
# ISBN gets the identical treatment as DOI. It runs through the same
# ``_metadata_to_fields``/precedence code path below, the failure mode is
# the same (a mistyped or wrong-edition ISBN silently attaches someone
# else's book to this record), and singling it out for different handling
# would be a special case with no justification -- the comparison here
# only ever looks at the already-normalized ``metadata_fields`` dict, so
# there is no source-specific code to write.

_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCTUATION_RE = re.compile(r"[\s.,;:!?]+$")


def _normalize_title_for_comparison(title: str) -> str:
    """Fold a title down to a comparison key.

    Deliberately a floor, not fuzzy matching: unescape HTML entities
    (Crossref titles carry literal ``&amp;`` etc.), casefold, collapse
    internal whitespace, and strip trailing punctuation. This absorbs the
    noise actually observed between a depositor's manually-typed title and
    Crossref's/isbnlib's rendering of the same title -- it does not
    attempt to reconcile different subtitle separators (":" vs. "-") or
    reorder words, since either of those could just as easily be masking a
    genuinely different work rather than a formatting difference.
    """

    unescaped = html.unescape(title)
    collapsed = _WHITESPACE_RE.sub(" ", unescaped).strip()
    stripped = _TRAILING_PUNCTUATION_RE.sub("", collapsed)
    return stripped.casefold()


def _resolve_literature_field_mismatches(
    request: LiteratureUploadRequest,
    metadata_fields: dict[str, object],
    *,
    field_prefix: str,
) -> list[UploadWarning]:
    """Warn where a depositor-supplied field disagrees with fetched metadata.

    Never changes what is stored -- ``resolve_literature_submission``'s
    existing ``fetched or declared`` precedence is untouched. This only
    decides whether a disagreement between the two is worth telling the
    depositor about.

    :param field_prefix: Dot-path prefix naming *and including* this
        literature fragment's own field, e.g. ``"literature."`` for a
        top-level ``request.literature``, or
        ``"species['ch4'].source_literature."`` for a nested ref. Callers
        default to ``"literature."`` (see ``resolve_literature_submission``)
        since that is the field name on the overwhelming majority of
        requests that embed one.
    """

    warnings: list[UploadWarning] = []

    fetched_title = metadata_fields.get("title")
    if (
        request.title is not None
        and isinstance(fetched_title, str)
        and _normalize_title_for_comparison(request.title)
        != _normalize_title_for_comparison(fetched_title)
    ):
        warnings.append(
            UploadWarning(
                field=f"{field_prefix}title",
                code=W_LITERATURE_TITLE_MISMATCH,
                message=(
                    f"Supplied literature title {request.title!r} does not "
                    "match the title fetched from the supplied DOI/ISBN "
                    f"({fetched_title!r}). The fetched title was kept; "
                    "verify the identifier is correct for this citation."
                ),
            )
        )

    fetched_year = metadata_fields.get("year")
    if (
        request.year is not None
        and isinstance(fetched_year, int)
        and request.year != fetched_year
    ):
        warnings.append(
            UploadWarning(
                field=f"{field_prefix}year",
                code=W_LITERATURE_YEAR_MISMATCH,
                message=(
                    f"Supplied literature year {request.year!r} does not "
                    "match the year fetched from the supplied DOI/ISBN "
                    f"({fetched_year!r}). The fetched year was kept; "
                    "verify the identifier is correct for this citation."
                ),
            )
        )

    return warnings


def _kind_from_identifiers(
    request: LiteratureUploadRequest,
    *,
    normalized_doi: str | None,
    normalized_isbn: str | None,
) -> LiteratureKind:
    if request.kind is not None:
        return request.kind
    if normalized_isbn is not None:
        return LiteratureKind.book
    if normalized_doi is not None:
        return LiteratureKind.article
    raise ValueError(
        "Unable to infer literature kind without DOI, ISBN, or explicit kind"
    )


def _metadata_to_fields(
    metadata: dict[str, object], *, source: str
) -> dict[str, object]:
    if source == "doi":
        return {
            "title": metadata.get("title"),
            "journal": (
                (metadata.get("container-title") or [None])[0]
                if isinstance(metadata.get("container-title"), list)
                else metadata.get("container-title")
            ),
            "year": metadata.get("issued"),
            "volume": metadata.get("volume"),
            "issue": metadata.get("issue"),
            "pages": metadata.get("page"),
            "publisher": metadata.get("publisher"),
            "url": metadata.get("URL"),
        }

    return {
        "title": metadata.get("Title"),
        "publisher": metadata.get("Publisher"),
        "year": metadata.get("Year"),
    }


def resolve_literature_submission(
    session: Session,
    request: LiteratureUploadRequest,
    *,
    warnings_out: list[UploadWarning] | None = None,
    field_prefix: str = "literature.",
) -> LiteratureCreate:
    """Resolve a workflow literature submission into a canonical create schema.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing literature submission payload.
    :param warnings_out: Optional sink for non-blocking warnings — currently
        a depositor-supplied ``title``/``year`` that disagrees with the
        metadata fetched from a supplied DOI/ISBN (see
        ``_resolve_literature_field_mismatches``). The fetched value is
        always what gets stored; this only reports the disagreement.
        ``None`` (the default) means the caller does not want these
        warnings surfaced -- unresolved callers do not need updating.
    :param field_prefix: Dot-path prefix naming *and including* this
        literature fragment's own field on the enclosing request, for
        ``warnings_out`` entries. Defaults to ``"literature."``, the field
        name on the overwhelming majority of requests that embed a
        literature fragment; a caller whose field is named differently
        (e.g. ``source_literature``) or nested (e.g. a bundle's
        ``species['ch4'].literature``) passes its own dot-path.
    :returns: Canonical ``LiteratureCreate`` schema.
    :raises ValueError: If ISBN normalization fails or manual submission lacks required fields.
    """

    normalized_doi = normalize_doi(request.doi)
    normalized_isbn = normalize_isbn(request.isbn) if request.isbn is not None else None

    if request.isbn is not None and normalized_isbn is None:
        raise ValueError("Invalid ISBN")

    metadata_fields: dict[str, object] = {}
    if normalized_doi is not None:
        metadata_fields = _metadata_to_fields(
            fetch_doi_metadata(normalized_doi) or {}, source="doi"
        )
    elif normalized_isbn is not None:
        metadata_fields = _metadata_to_fields(
            fetch_isbn_metadata(normalized_isbn) or {},
            source="isbn",
        )

    if warnings_out is not None and (
        normalized_doi is not None or normalized_isbn is not None
    ):
        warnings_out.extend(
            _resolve_literature_field_mismatches(
                request, metadata_fields, field_prefix=field_prefix
            )
        )

    kind = _kind_from_identifiers(
        request,
        normalized_doi=normalized_doi,
        normalized_isbn=normalized_isbn,
    )

    title = metadata_fields.get("title") or request.title
    if title is None:
        raise ValueError("Resolved literature submission still requires a title")

    return LiteratureCreate(
        kind=kind,
        title=title,
        journal=metadata_fields.get("journal") or request.journal,
        year=metadata_fields.get("year") or request.year,
        volume=metadata_fields.get("volume") or request.volume,
        issue=metadata_fields.get("issue") or request.issue,
        pages=metadata_fields.get("pages") or request.pages,
        doi=normalized_doi,
        isbn=normalized_isbn,
        url=metadata_fields.get("url") or request.url,
        publisher=metadata_fields.get("publisher") or request.publisher,
        institution=request.institution,
    )


def _select_existing_literature(
    session: Session,
    *,
    normalized_doi: str | None,
    normalized_isbn: str | None,
) -> Literature | None:
    """Look up an existing citation by normalized DOI, then normalized ISBN.

    Shared by the pre-insert check and the concurrent-insert recovery so both
    answer "is this citation already here?" the same way.

    :raises ValueError: If the DOI and ISBN resolve to two different rows.
    """
    existing_by_doi = None
    if normalized_doi is not None:
        existing_by_doi = session.scalar(
            select(Literature).where(Literature.doi == normalized_doi)
        )

    existing_by_isbn = None
    if normalized_isbn is not None:
        existing_by_isbn = session.scalar(
            select(Literature).where(Literature.isbn == normalized_isbn)
        )

    if (
        existing_by_doi is not None
        and existing_by_isbn is not None
        and existing_by_doi.id != existing_by_isbn.id
    ):
        raise ValueError("DOI and ISBN resolve to different existing literature rows")

    return existing_by_doi or existing_by_isbn


def resolve_or_create_literature(
    session: Session,
    request: LiteratureUploadRequest,
    *,
    warnings_out: list[UploadWarning] | None = None,
    field_prefix: str = "literature.",
) -> Literature:
    """Resolve or create a literature row from workflow submission data.

    Idempotent across transactions as well as within one: a concurrent upload
    that creates the same citation first is adopted rather than collided with.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing literature submission payload.
    :param warnings_out: Optional sink for non-blocking warnings (see
        ``resolve_literature_submission``). Only populated on the
        first-creation path -- adopting an already-existing row (matched
        by DOI/ISBN) does not re-fetch metadata, so there is nothing new
        to compare against.
    :param field_prefix: Dot-path prefix for ``warnings_out`` entries.
    :returns: Existing or newly created ``Literature`` row.
    :raises ValueError: If identifier normalization or literature resolution fails.
    """

    normalized_doi = normalize_doi(request.doi)
    normalized_isbn = normalize_isbn(request.isbn) if request.isbn is not None else None

    existing = _select_existing_literature(
        session,
        normalized_doi=normalized_doi,
        normalized_isbn=normalized_isbn,
    )
    if existing is not None:
        return existing

    literature_create = resolve_literature_submission(
        session, request, warnings_out=warnings_out, field_prefix=field_prefix
    )

    def _build() -> Literature:
        return Literature(
            kind=literature_create.kind,
            title=literature_create.title,
            journal=literature_create.journal,
            year=literature_create.year,
            volume=literature_create.volume,
            issue=literature_create.issue,
            pages=literature_create.pages,
            doi=literature_create.doi,
            isbn=literature_create.isbn,
            url=(
                str(literature_create.url)
                if literature_create.url is not None
                else None
            ),
            publisher=literature_create.publisher,
            institution=literature_create.institution,
        )

    # Check-then-insert against the normalized DOI/ISBN uniqueness indexes
    # (``ix_literature_doi_normalized`` / ``ix_literature_isbn_normalized``).
    # Two contributors citing the same paper at the same time both read
    # ``None`` above, and the loser's violation would abort a transaction that
    # already holds its entire upload — literature is resolved *during*
    # deposit, so the collateral is scientific records, not a citation.
    #
    # Every sibling resolver (species, reaction, geometry, software,
    # calculation, energy-correction) already wraps its identity insert this
    # way; this one was the exception. Losing the race is not a conflict: the
    # winner's row is the same citation this call was about to create, so the
    # loser adopts it, exactly as it would have done had it arrived a moment
    # later.
    try:
        with session.begin_nested():
            literature = _build()
            session.add(literature)
            session.flush()
    except IntegrityError:
        existing = _select_existing_literature(
            session,
            normalized_doi=normalized_doi,
            normalized_isbn=normalized_isbn,
        )
        if existing is None:
            # Not the identity race — a genuinely broken row. Let it out.
            raise
        return existing
    return literature
