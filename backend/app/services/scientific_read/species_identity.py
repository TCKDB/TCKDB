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

from app.db.models.common import SpeciesEntryStateKind

#: The columns of ``uq_species_entry_species_id`` other than ``species_id``.
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
    key) and rewording them would put a spelling in a plot title that appears
    nowhere else in the record.

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
        parts.append(electronic_state_label)
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
        stereo_label=entry.stereo_label,
        electronic_state_kind=entry.electronic_state_kind,
        electronic_state_label=entry.electronic_state_label,
        term_symbol=entry.term_symbol,
        isotope_key=entry.isotope_key,
    )


__all__ = [
    "IDENTITY_COLUMNS",
    "species_entry_label",
    "species_entry_label_for",
]
