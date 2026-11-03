"""The structured, computed identity facets of a species entry, for blocks that name one.

A block that names a species entry (a reaction participant, a network state
participant, the species context of a conformer, statmech, transport, geometry
or calculation record) used to carry only ``species_entry_label``: the five
identity columns joined into one string. That string mixes parts TCKDB computes
(the stereo descriptor, the state kind, the isotope key) with parts a depositor
typed (``electronic_state_label``, ``term_symbol``), and a client cannot take it
apart safely.

These fields are the computed parts, served separately and named exactly as the
species surface (``SpeciesEntryScientificRecord``) names them, so a client that
renders species pages renders these blocks with the same code. Every one is
``None`` when the column is ``NULL``, never ``""``.

``electronic_state_label`` and ``term_symbol`` are deliberately NOT served here.
They are typed text; a strict grammar for them is being added separately, and
they join these blocks only after that.
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
    #: The entry's stereo key (DR-0038), ``None`` only for an achiral species.
    stereo_descriptor: str | None = None
    stereo_resolution: StereoResolution | None = None
    isotope_key: str | None = None
