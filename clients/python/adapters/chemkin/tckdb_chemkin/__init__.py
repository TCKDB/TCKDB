"""CHEMKIN mechanism importer adapter for TCKDB.

Five independently-testable stages (spec §3):

1. :mod:`tckdb_chemkin.parser` / :mod:`tckdb_chemkin.transport` — text -> AST.
2. :mod:`tckdb_chemkin.normalizer` — unit resolution + kinetics-form tagging.
3. :mod:`tckdb_chemkin.identity` — CHEMKIN name -> (SMILES, charge, multiplicity).
4. :mod:`tckdb_chemkin.payloads` — AST -> TCKDB upload dicts.
5. :mod:`tckdb_chemkin.uploader` — POST via the generic ``tckdb-client``.

Stages 1-4 are pure (no network, no DB). RDKit is confined to stage 3.
"""

from __future__ import annotations

from .ast import Mechanism, Reaction, ThermoEntry, TransportEntry
from .identity import (
    IdentityResolutionError,
    IdentityResolver,
    ResolvedSpecies,
    parse_species_dictionary,
    parse_species_map_csv,
)
from .normalizer import NormalizedReaction, normalize_mechanism
from .parser import ChemkinParseError, parse_mechanism, parse_thermo_file
from .payloads import (
    BuiltPayloads,
    ImportConfig,
    build_all_payloads,
    build_kinetics_payload,
    build_thermo_payload,
    build_transport_payload,
)
from .transport import parse_transport_file

__all__ = [
    "Mechanism",
    "Reaction",
    "ThermoEntry",
    "TransportEntry",
    "parse_mechanism",
    "parse_thermo_file",
    "parse_transport_file",
    "ChemkinParseError",
    "normalize_mechanism",
    "NormalizedReaction",
    "IdentityResolver",
    "IdentityResolutionError",
    "ResolvedSpecies",
    "parse_species_dictionary",
    "parse_species_map_csv",
    "ImportConfig",
    "BuiltPayloads",
    "build_all_payloads",
    "build_thermo_payload",
    "build_transport_payload",
    "build_kinetics_payload",
]

__version__ = "0.2.0"
