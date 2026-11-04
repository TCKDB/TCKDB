"""The one spelling of what tells two entries of a single species apart.

A ``species`` row is a graph identity: SMILES, charge, multiplicity. Two
``species_entry`` rows under it are *different molecules with different
thermochemistry* — cis- and trans-diazene both sit under one ``N=N`` — and
what separates them lives entirely in the entry's identity columns.

A read surface that serves ``species_entry_ref`` and the species' graph
identity but drops those columns therefore hands a reader two records whose
every served field is byte-identical, with nothing on the wire saying that
anything was withheld. That is not a missing feature: the reader picks a ref
and may get the isomer they did not want.

This module holds the derivation so that the eleven read blocks that name a
species entry cannot drift into eleven spellings of the same molecule. It is
deliberately dependency-free beyond the enum it reads, so any surface can
import it without dragging a subsystem in.

``tests/api/scientific/test_api_species_entry_identity.py`` enumerates
``app.schemas.reads`` and fails on any response block that names a species
and an entry but carries no ``species_entry_label``, so a new surface joins
this guarantee the day it is written rather than the day someone notices.
"""

from __future__ import annotations

from sqlalchemy import case, literal
from tckdb_schemas.electronic_state import canonical_electronic_state_label, canonical_term_symbol

from app.db.models.common import SpeciesEntryStateKind, StereoResolution
from app.db.models.species import SpeciesEntry

#: SQL counterpart of :func:`stereo_display`, for queries that select the label inputs by column
#: (``STEREO_DISPLAY.label("stereo_display")``): the descriptor, with `` partial`` or `` unresolved``
#: after it when an element is unknown, and the stored ``stereo_label`` when there is no descriptor.
STEREO_DISPLAY = case(
    (SpeciesEntry.stereo_descriptor.is_(None), SpeciesEntry.stereo_label),
    (
        SpeciesEntry.stereo_resolution == StereoResolution.unresolved,
        SpeciesEntry.stereo_descriptor + literal(" unresolved"),
    ),
    (
        SpeciesEntry.stereo_resolution == StereoResolution.partial,
        SpeciesEntry.stereo_descriptor + literal(" partial"),
    ),
    else_=SpeciesEntry.stereo_descriptor,
)


def stereo_display(descriptor: str | None, resolution, label: str | None) -> str | None:
    """The stereo part of an entry's discriminator label (DR-0038).

    ``stereo_label`` is NULL while an entry's stereo is wholly unknown, so a label built from it alone
    leaves an unresolved entry looking unlabelled. This is built from the descriptor and its resolution
    instead, as the entry page does: the descriptor; ``E,? partial``; ``? unresolved``. NULL only for
    an achiral species (no descriptor), where it falls back to whatever label is stored.
    """

    if descriptor is None:
        return label
    value = getattr(resolution, "value", resolution)
    if value == "unresolved":
        return f"{descriptor} unresolved"
    if value == "partial":
        return f"{descriptor} partial"
    return descriptor

#: The display counterparts of the columns of ``uq_species_entry_species_id`` other than
#: ``species_id``. ``stereo_label`` is the display text of the key column ``stereo_descriptor``:
#: the descriptor itself for a resolved or partial entry, and nothing for an entry whose
#: descriptor is all ``?`` (or for an achiral species, whose descriptor is NULL). Among the
#: LIVE entries of one species there is at most one with an all-``?`` or NULL descriptor (the
#: unique key is over live rows), so two live entries that differ in the key differ in this
#: label too. A merged-away entry keeps its own label and is not listed (DR-0038).
#: Named here because the guarantee this module makes is a restatement of
#: that constraint: two entries of one species differ in at least one of
#: these by construction.
IDENTITY_COLUMNS: tuple[str, ...] = (
    "stereo_label",
    "electronic_state_kind",
    "electronic_state_label",
    "term_symbol",
    "isotope_key",
)


#: The word that introduces the electronic-state label in a joined entry label (``excited state A``).
STATE_LABEL_MARKER = "state"


def species_entry_label(
    *,
    stereo_label: str | None,
    electronic_state_kind: SpeciesEntryStateKind | None,
    electronic_state_label: str | None,
    term_symbol: str | None,
    isotope_key: str | None,
) -> str | None:
    """Return the short discriminator that tells two entries of one species apart.

    Built from every column of ``uq_species_entry_species_id`` except the
    species itself, which is what makes the result a real discriminator rather
    than a hint: two entries of one species differ in at least one of these by
    construction, so they cannot both render as ``None`` and cannot render the
    same. Two entries that agree on all five are one row.

    ``ground`` electronic state is omitted because it is the default and
    saying so of every ordinary species would bury the one entry that is not
    ground in noise. Everything else is spelled as stored: these are the
    depositor's own labels (``E``, ``Z``, ``T1``, a term symbol, an isotope
    key), except that the electronic-state label is introduced by the word
    ``state`` (``state T1``) so it can never be read as a stereo descriptor.
    Rewording the labels themselves would put a spelling in a plot title that
    appears nowhere else in the record.

    An entry with no label of any kind returns ``None``, never ``""``. There
    is nothing stored to render, and an empty string would read as a label
    that happens to be blank — the same trap this function exists to close.

    :returns: A compact label, or ``None`` for the plain ground-state,
        all-standard, stereo-unlabelled entry.
    """

    parts: list[str] = []
    if stereo_label:
        parts.append(stereo_label)
    if (
        electronic_state_kind is not None
        and electronic_state_kind != SpeciesEntryStateKind.ground
    ):
        parts.append(electronic_state_kind.value)
    if electronic_state_label:
        # Marked, never bare: a stored state label may not read as a stereo descriptor
        # (``R``/``S``/``E``/``Z``). New uploads cannot carry one
        # (``tckdb_schemas.electronic_state``), but rows stored before that rule can.
        parts.append(f"{STATE_LABEL_MARKER} {electronic_state_label}")
    if term_symbol:
        parts.append(term_symbol)
    if isotope_key:
        parts.append(isotope_key)
    return " ".join(parts) if parts else None


def species_entry_label_for(entry) -> str | None:
    """:func:`species_entry_label` for anything exposing the five columns.

    Takes a loaded ``SpeciesEntry`` ORM object or a SQLAlchemy ``Row`` that
    selected the five columns under their own names — both reach them by
    attribute, so one helper serves both and adding a sixth identity column
    later is one edit here rather than one per call site.

    The keyword form above stays for callers that hold the values loose.
    """
    return species_entry_label(
        stereo_label=(
            entry.stereo_display
            if hasattr(entry, "stereo_display")  # a selected row, labelled by the query
            else stereo_display(entry.stereo_descriptor, entry.stereo_resolution, entry.stereo_label)
        ),
        electronic_state_kind=entry.electronic_state_kind,
        electronic_state_label=entry.electronic_state_label,
        term_symbol=entry.term_symbol,
        isotope_key=entry.isotope_key,
    )


#: Columns a query selects so :func:`entry_facets_of` can read the computed facets off a row. Together with the
#: columns :func:`species_entry_label_for` already needs (``STEREO_DISPLAY`` as ``stereo_display``,
#: ``electronic_state_kind``, ``electronic_state_label``, ``term_symbol``, ``isotope_key``) they come from the same
#: ``species_entry`` row the query already reads, so a block serving the facets costs no extra query.
ENTRY_FACET_COLUMNS = (
    SpeciesEntry.kind.label("entry_kind"),
    SpeciesEntry.stereo_label,
    SpeciesEntry.stereo_descriptor,
    SpeciesEntry.stereo_resolution,
)


def derived_stereo_label(descriptor: str | None, resolution) -> str | None:
    """The stereo display text, derived from the descriptor as ``ResolvedStereo.label`` derives it.

    The descriptor, except that a fully unknown configuration (``unresolved``) displays as nothing. The raw
    ``species_entry.stereo_label`` column is not used: before the descriptor existed it was free text up to 64
    characters, and a contradicted legacy row kept whatever text it was deposited with
    (``stereo_identity_merge.py``), so it can hold a string that is not a stereo mark at all.
    """
    value = getattr(resolution, "value", resolution)
    if descriptor is None or value is None or value == "unresolved":
        return None
    return descriptor


def public_electronic_state_label(value: str | None) -> str | None:
    """The stored spelling of an electronic-state label that satisfies the write-time grammar, else ``None``.

    Uses ``tckdb_schemas.electronic_state`` (the one definition of the grammar and of the canonical form). Never
    raises: a row stored before the grammar was enforced can hold any text, and it reads as no label at all.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return canonical_electronic_state_label(value)
    except ValueError:
        return None


def public_term_symbol(value: str | None) -> str | None:
    """The canonical spelling of a term symbol that satisfies the write-time grammar, else ``None``. Never raises."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return canonical_term_symbol(value)
    except ValueError:
        return None


def entry_facets_of(entry) -> dict:
    """The computed facets of a species entry, as keyword arguments for an ``EntryFacetFields`` block.

    Takes a loaded ``SpeciesEntry`` or a ``Row`` that selected :data:`ENTRY_FACET_COLUMNS` and
    ``electronic_state_kind`` / ``isotope_key``. ``stereo_label`` is derived from the descriptor and resolution, never
    the raw column. ``electronic_state_label`` and ``term_symbol`` are typed text: they are served only when they
    satisfy the write-time grammar (in their canonical spelling), and read ``None`` otherwise, so a row stored
    before the grammar existed never puts free text on a public page.
    """
    return {
        "species_entry_kind": entry.entry_kind if hasattr(entry, "entry_kind") else entry.kind,
        "electronic_state_kind": entry.electronic_state_kind,
        "stereo_label": derived_stereo_label(entry.stereo_descriptor, entry.stereo_resolution),
        "stereo_descriptor": entry.stereo_descriptor,
        "stereo_resolution": entry.stereo_resolution,
        "isotope_key": entry.isotope_key,
        "electronic_state_label": public_electronic_state_label(getattr(entry, "electronic_state_label", None)),
        "term_symbol": public_term_symbol(getattr(entry, "term_symbol", None)),
    }


__all__ = [
    "ENTRY_FACET_COLUMNS",
    "IDENTITY_COLUMNS",
    "derived_stereo_label",
    "entry_facets_of",
    "public_electronic_state_label",
    "public_term_symbol",
    "species_entry_label",
    "species_entry_label_for",
]
