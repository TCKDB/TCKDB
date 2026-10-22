"""The parsers TCKDB owns, held in a registry keyed by (name, version).

A value TCKDB extracts from an uploaded file -- the execution-control parameters of a calculation, a Hessian, a ThermoML
record, a CCCBDB table -- is stamped with the parser that produced it: the parser's stable ``name`` beside its
``version``. This registry is where those names and integer versions live; a parser module asks it for its own entry
(``PARSERS.get("gaussian_parameters")``) instead of restating a version string of its own.

Only parsers TCKDB owns are here. A parser a *producer* owns (a client's own log reader) arrives on the wire as a
:class:`~tckdb_schemas.fragments.version.ParserStamp` whose version is the producer's text, recorded as given and never
renumbered.

The stored stamp is text on both fields (``parser_name``, ``parser_version``), because the same column also holds a
producer's version. A TCKDB parser writes ``str(integer version)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from tckdb_schemas.fragments.version import ParserStamp

from app.versioning import VersionRegistry


@dataclass(frozen=True)
class ParserInfo:
    """One parser TCKDB owns: its stable name and its integer version."""

    name: str
    version: int

    def stamp(self) -> ParserStamp:
        """The stamp recorded beside a value this parser produced: the name, and the version spelled as text."""
        return ParserStamp(name=self.name, version=str(self.version))


PARSERS: VersionRegistry[ParserInfo] = VersionRegistry("parsers", startup_checked=True)
for _name in (
    "gaussian_parameters",
    "orca_parameters",
    "molpro_parameters",
    "psi4_parameters",
    "hessian",
    "composite_log",
    "thermoml_cp",
    "thermoml_cp_mapping",
    "cccbdb_experimental_species",
    "cccbdb_payload_builder",
    "cccbdb_form_resolver",
    "cccbdb_resolver_diagnostics",
):
    PARSERS.register(_name, 1, ParserInfo(_name, 1), current=True)
del _name
PARSERS.validate()
