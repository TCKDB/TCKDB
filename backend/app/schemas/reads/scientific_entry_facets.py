"""The structured, computed identity facets of a species entry, for blocks that name one.

A block that names a species entry (a reaction participant, a network state
participant, the species context of a conformer, statmech, transport, geometry
or calculation record) carries ``species_entry_label``: the five
identity columns joined into one string. That string mixes parts TCKDB computes
(the stereo descriptor, the state kind, the isotope key) with parts a depositor
typed (``electronic_state_label``, ``term_symbol``), and a client cannot take it
apart safely.

These fields are the computed parts, served separately and named exactly as the
species surface (``SpeciesEntryScientificRecord``) names them, so a client that
renders species pages renders these blocks with the same code. Every one is
``None`` when the column is ``NULL``, never ``""``.

``electronic_state_label`` and ``term_symbol`` are typed text. They are served
here only when they satisfy the write-time grammar
(``tckdb_schemas.electronic_state``), in their canonical NFC spelling (``T1``,
``X ²Π``); a value that fails it (a row stored before the grammar was enforced)
reads ``None``. They are for the display of excited and spin states and are
never to be parsed as stereo descriptors.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.db.models.common import SpeciesEntryStateKind, StationaryPointKind, StereoResolution


class EntryFacetFields(BaseModel):
    """Mixin: the computed identity facets of the species entry a block names."""

    #: ``minimum`` or ``vdw_complex``.
    species_entry_kind: StationaryPointKind | None = None
    electronic_state_kind: SpeciesEntryStateKind | None = None
    #: Display text of the stereo key (``R``, ``E``, ...); ``None`` while nothing is resolved.
    stereo_label: str | None = None
    #: The entry's stereo key, ``None`` only for an achiral species.
    stereo_descriptor: str | None = None
    stereo_resolution: StereoResolution | None = None
    isotope_key: str | None = None
    #: ``S1``, ``T1``, ``X``, ``Ã``...: the grammar-valid label, canonical spelling; ``None`` when absent or invalid.
    electronic_state_label: str | None = None
    #: ``²Π``, ``X ¹A₁``...: the grammar-valid term symbol, canonical spelling; ``None`` when absent or invalid.
    term_symbol: str | None = None
