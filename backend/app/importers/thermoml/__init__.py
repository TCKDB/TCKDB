"""ThermoML Cp importer (Phase C-E2).

Turns a NIST TRC ThermoML Archive article (XML + JSON twin) into
:class:`~app.schemas.entities.molecular_property_observation.MolecularPropertyObservationCreate`
payloads for ``property_kind=heat_capacity_cp``.

Mirrors the layering of ``backend/app/importers/cccbdb/``:
``archive.py`` (fetch/snapshot) -> ``validate.py`` (schema) ->
``parser.py`` (XML -> dataclasses) -> ``mapping.py`` (dataclasses ->
TCKDB payloads). **No database writes anywhere in this package** --
persistence is Phase C-E3 (``backend/app/services/thermoml_cp_import.py``,
not part of this package).

Pilot species (Phase C plan amendment): fluoroethane,
``UHCBBWUQDAVSMS-UHFFFAOYSA-N``, DOI ``10.1016/j.fluid.2016.07.034``, 38
flow-calorimetry Cp values at 315.33-365.75 K and 101.325 kPa, phase
tagged "Gas". Real-gas rows are stored with their pressure and are
comparable later with the non-ideality flagged (``state_basis``), not
rejected.
"""

from __future__ import annotations

#: Human-readable source name, used in ``external_source.name``.
SOURCE_NAME = "NIST TRC ThermoML Archive"

#: The specific dataset release this importer is pinned to. NIST PDR
#: record ``mds2-2422``, dataset version 1.2.6 (the record's own
#: ``version`` field), archive contents version
#: ``ThermoML.v2020-09-30.tgz`` (per its ``dateCit`` extraction date and
#: the archive landing page). Both halves matter: the PDR record can be
#: revised (metadata, added files) independently of a new archive .tgz
#: being cut.
SOURCE_RELEASE = "mds2-2422 v1.2.6 / ThermoML.v2020-09-30"

#: Dataset DOI (NOT the article DOI -- this identifies the archive
#: itself). Confirmed 2026-09-20 via the NIST PDR record API
#: (``https://data.nist.gov/rmm/records/mds2-2422``), field ``doi``.
SOURCE_DATABASE_DOI = "10.18434/mds2-2422"

#: The single URL ``archive.py:fetch_archive`` is allowed to fetch.
#: Confirmed 2026-09-20 against the NIST PDR record API's
#: ``components`` list (``downloadURL`` for the ``ThermoML.v2020-09-30.tgz``
#: component) -- NOT ``trc.nist.gov/ThermoML/``, which is a client-side
#: JS application with no stable per-file download endpoint.
ARCHIVE_URL = "https://data.nist.gov/od/ds/mds2-2422/ThermoML.v2020-09-30.tgz"

#: SHA-256 of the pinned archive, confirmed 2026-09-20 against the same
#: PDR record API response (``components[].checksum.hash`` for the
#: ``ThermoML.v2020-09-30.tgz`` component).
ARCHIVE_SHA256 = (
    "231161b5e443dc1ae0e5da8429d86a88474cb722016e5b790817bb31c58d7ec2"
)

#: Size of the pinned archive in bytes, from the same PDR record.
ARCHIVE_SIZE = 189433115

#: Canonical XSD download location NIST/TRC documents alongside the
#: archive (the schema is also mirrored at the PDR download endpoint
#: used above for ``ARCHIVE_URL``; both serve byte-identical content --
#: verified 2026-09-20, both hash to ``XSD_SHA256`` below). The
#: committed copy at ``schema/ThermoML.xsd`` is what every validation
#: actually runs against; this URL is provenance metadata only, never
#: fetched at runtime.
XSD_URL = "https://trc.nist.gov/ThermoML.xsd"

#: SHA-256 of ``schema/ThermoML.xsd`` as committed. Confirmed against
#: the PDR record API's ``ThermoML.xsd`` component checksum.
XSD_SHA256 = (
    "5c9945ce07c2a0c4d7bd249ba4d1f76b4a37eba0b872f1f1f6656b4df247ab89"
)

#: Version string for this package's XML->dataclass parser
#: (``parser.py``). Bump on any change to how ThermoML elements are
#: walked or joined.
PARSER_VERSION = "thermoml-cp-parser/0.1.0"

#: Version string for this package's dataclass->TCKDB mapping
#: (``mapping.py``). Bump on any change to the mapping rules
#: themselves (unit, state-basis, uncertainty precedence, origin
#: classification), independent of parser changes.
MAPPING_VERSION = "thermoml-cp-mapping/0.1.0"

#: Where the NIST open-data license lives. This is the *general* NIST
#: license, not a ThermoML-specific one -- the ThermoML-specific terms
#: (journal-publisher permission) are quoted in ``TERMS_TEXT`` below.
TERMS_URL = "https://www.nist.gov/open/license"

#: Verbatim NIST/TRC terms text for the ThermoML Archive, assembled
#: from two fetches taken 2026-09-20:
#:
#: 1. The general-purpose disclaimer paragraph, quoted verbatim from
#:    the dataset's own NERDm metadata record
#:    (``https://data.nist.gov/rmm/records/mds2-2422``, ``description``
#:    field, second paragraph) -- the same text rendered on the dataset
#:    landing page at ``https://data.nist.gov/od/id/mds2-2422``.
#: 2. The journal-publisher-permission sentence, quoted verbatim from
#:    ``https://www.nist.gov/mml/acmd/trc/thermoml/thermoml-archive``.
#:
#: Neither sentence is paraphrased; both are copied character-for-
#: character from the fetched page text (whitespace-collapsed only).
TERMS_TEXT = (
    "The data and other information throughout this digital resource "
    "(including the website, API, JSON, and ThermoML files) have been "
    "carefully extracted from the original articles by NIST/TRC "
    "personnel. Neither the Journal publisher, nor its editors, nor "
    "NIST/TRC warrant or represent, expressly or implied, the "
    "correctness or accuracy of the content of information contained "
    "throughout this digital resource, nor its fitness for any use or "
    "for any purpose, nor can they, or will they, accept any liability "
    "or responsibility whatever for the consequences of its use or "
    "misuse by anyone. In any individual case of application, the "
    "respective user must check the correctness by consulting other "
    "relevant sources of information.\n\n"
    "The ThermoML files corresponding to articles in the journals are "
    "available here with permission of the journal publishers.\n\n"
    "Cite this dataset as: Riccardi, D., Bazyleva, A., Paulechka, E., "
    "Diky, V., Magee, J. W., Kazakov, A. F., Townsend, S. A., Muzny, "
    "C. D. ThermoML/Data Archive, National Institute of Standards and "
    "Technology, https://doi.org/10.18434/mds2-2422 (data.nist.gov "
    "record mds2-2422, retrieved 2026-09-20). A downstream user who "
    "reproduces content from a specific article must also cite that "
    "article's own DOI, carried per-row in "
    "raw_payload_json['citation']."
)

__all__ = [
    "ARCHIVE_SHA256",
    "ARCHIVE_SIZE",
    "ARCHIVE_URL",
    "MAPPING_VERSION",
    "PARSER_VERSION",
    "SOURCE_DATABASE_DOI",
    "SOURCE_NAME",
    "SOURCE_RELEASE",
    "TERMS_TEXT",
    "TERMS_URL",
    "XSD_SHA256",
    "XSD_URL",
]
