"""Which enums a public response can actually put on the wire.

This is the mechanical half of the glossary's inclusion rule (see
:mod:`app.glossary`). Half 2 — "chemistry does not decode it" — is a
judgement and is declared. Half 1 — "a reader can meet it" — is a fact
about the code, so it is computed here and asserted in
``backend/tests/scripts/test_api_vocabulary.py`` rather than trusted.

Two surfaces, because the wire has two shapes:

* **Read schemas.** Every Pydantic model defined under
  ``app/schemas/reads/`` is a request or response body of a
  ``/api/v1/scientific/*`` endpoint. Walking their field annotations
  finds the enums a response can serialise directly.

* **The trust fragment.** ``trust.evidence`` is typed ``dict`` on
  :class:`~app.services.trust.models.TrustFragment` — it is built with
  ``EvidenceEvaluation.model_dump()`` — so an annotation walk over the
  read schemas cannot see the badge, the check outcomes or the hard-fail
  reason at all, even though they are three of the most-read tokens
  TCKDB emits. The evaluation model is walked separately, minus the
  fields ``build_trust_fragment`` excludes.

A vocabulary reachable through neither is not something a reader meets,
whatever its name suggests.
"""

from __future__ import annotations

import enum
import importlib
import pkgutil
import typing

from pydantic import BaseModel

__all__ = [
    "envelope_enums",
    "read_schema_enums",
    "trust_fragment_enums",
    "wire_enums",
]

#: Fields of ``EvidenceEvaluation`` that never reach a reader.
#: ``build_trust_fragment`` dumps the evaluation with
#: ``exclude={"check_results"}``, so the per-check spec metadata — and
#: with it ``EvidenceCheckKind`` — is serialised nowhere.
_EVALUATION_EXCLUDED_FIELDS = frozenset({"check_results"})

#: The models every ``/scientific/*`` response carries regardless of what
#: was asked for. Their enums are the ones no reader can avoid, so the
#: test suite requires each to be documented — this is the guard against
#: *under*-inclusion, which a declared inclusion rule cannot provide by
#: itself.
_ENVELOPE_MODELS = (
    "ProfiledRequestEcho",
    "RecordReviewBadge",
    "ReviewStatusSummary",
    "Pagination",
)


def _collect(annotation: object, sink: set[type[enum.Enum]]) -> None:
    """Add every enum class reachable from a type annotation."""
    for argument in typing.get_args(annotation) or ():
        _collect(argument, sink)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        sink.add(annotation)


def _model_enums(model: type[BaseModel], *, exclude: frozenset[str] = frozenset()) -> set[type[enum.Enum]]:
    found: set[type[enum.Enum]] = set()
    for name, field in model.model_fields.items():
        if name in exclude:
            continue
        _collect(field.annotation, found)
    return found


def read_schema_enums() -> frozenset[type[enum.Enum]]:
    """Enums reachable from a field of any ``app/schemas/reads`` model."""
    import app.schemas.reads as reads

    found: set[type[enum.Enum]] = set()
    for module_info in pkgutil.walk_packages(reads.__path__, reads.__name__ + "."):
        module = importlib.import_module(module_info.name)
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module_info.name
            ):
                found |= _model_enums(value)
    return frozenset(found)


def trust_fragment_enums() -> frozenset[type[enum.Enum]]:
    """Enums a serialised ``trust`` fragment can contain."""
    from app.services.trust.models import EvidenceEvaluation

    return frozenset(
        _model_enums(EvidenceEvaluation, exclude=_EVALUATION_EXCLUDED_FIELDS)
    )


def envelope_enums() -> frozenset[type[enum.Enum]]:
    """Enums every scientific response carries, whatever was asked for."""
    from app.schemas.reads import scientific_common

    found: set[type[enum.Enum]] = set()
    for name in _ENVELOPE_MODELS:
        model = getattr(scientific_common, name)
        found |= _model_enums(model)
    return frozenset(found) | trust_fragment_enums()


def wire_enums() -> frozenset[type[enum.Enum]]:
    """Every enum a public response can put on the wire."""
    return read_schema_enums() | trust_fragment_enums()
