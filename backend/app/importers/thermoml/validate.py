"""XSD validation for ThermoML documents.

``lxml`` is imported lazily, inside :func:`validate_bytes`, so importing
this module (or the rest of the ``thermoml`` package) never requires
the ``thermoml`` extra to be installed -- only actually validating a
document does. Schema-invalid documents are rejected here and never
reach ``parser.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: The XSD committed alongside this package. See
#: ``app.importers.thermoml.XSD_SHA256`` for the pinned digest and
#: ``schema/SHA256SUMS`` for the checked-in record of it.
XSD_PATH = Path(__file__).parent / "schema" / "ThermoML.xsd"


class ThermoMLConfigurationError(RuntimeError):
    """Raised when ``lxml`` is not installed.

    Install the ``thermoml`` extra: ``pip install -e ".[thermoml]"``
    (or, for the conda/mamba workflow, ``conda install -n tckdb_env -c
    conda-forge lxml`` -- see ``backend/environment.yml``).
    """


@dataclass(frozen=True)
class SchemaReport:
    """Outcome of validating one document against ``ThermoML.xsd``.

    :param valid: Whether the document is schema-valid.
    :param errors: Formatted ``lxml`` error-log entries. Empty when
        ``valid`` is ``True``.
    """

    valid: bool
    errors: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def _compiled_schema():
    try:
        from lxml import etree
    except ImportError as exc:  # pragma: no cover - exercised via the
        # configuration-error test, which does not actually uninstall
        # lxml; the branch is trivial and stable.
        raise ThermoMLConfigurationError(
            "lxml is required to validate ThermoML documents. Install "
            "the 'thermoml' extra (pip install -e '.[thermoml]') or, "
            "for conda/mamba developers, add lxml from conda-forge -- "
            "see backend/environment.yml."
        ) from exc

    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
    )
    xsd_doc = etree.parse(str(XSD_PATH), parser=parser)
    return etree.XMLSchema(xsd_doc)


def validate_bytes(xml_bytes: bytes) -> SchemaReport:
    """Validate ``xml_bytes`` against the committed ``ThermoML.xsd``.

    Uses ``lxml.etree.XMLSchema`` with a parser built with
    ``resolve_entities=False``, ``no_network=True`` and
    ``huge_tree=False`` -- no external entity resolution, no network
    access, and a hard cap on document size/depth, since this parser
    runs against bytes pulled from a third-party archive.

    :param xml_bytes: The raw XML document bytes.
    :returns: A :class:`SchemaReport`. Never raises on a schema-invalid
        (but well-formed) document -- callers check ``.valid``. Raises
        ``lxml.etree.XMLSyntaxError`` for bytes that are not
        well-formed XML at all, and :class:`ThermoMLConfigurationError`
        if ``lxml`` is not installed.
    """

    from lxml import etree

    schema = _compiled_schema()
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
    )
    doc = etree.fromstring(xml_bytes, parser=parser)
    valid = schema.validate(doc)
    if valid:
        return SchemaReport(valid=True, errors=())
    errors = tuple(str(e) for e in schema.error_log)
    return SchemaReport(valid=False, errors=errors)


__all__ = [
    "XSD_PATH",
    "SchemaReport",
    "ThermoMLConfigurationError",
    "validate_bytes",
]
