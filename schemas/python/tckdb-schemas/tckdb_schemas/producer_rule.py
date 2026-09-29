"""Mark a function as a rule a producer's deposit must satisfy.

Why a marker at all
-------------------
Most of what a deposit must satisfy is already machine-readable where it is
enforced: a Pydantic field constraint, a model validator on the payload, or
an entry in the backend's scientific check register. One kind is not. A rule
shared between the server, the command line and the client, and applied by a
*workflow* rather than by a validator on the payload, lives in a plain
function the payload model never mentions -- :func:`tckdb_schemas.
enthalpy_reference.enthalpy_reference_error` is the case that forced this:
every thermo deposit carrying an enthalpy is refused without
``enthalpy_reference_kind``, and nothing a producer could read said so.

Decorating such a function with :func:`producer_rule` is the whole
declaration. The producer contract generator
(``backend/scripts/generate_producer_contract.py``) walks each upload route's
handler through its direct calls, and every marked function it reaches is
printed in that route's section, with the function's own docstring as the
rule text. So the rule is stated once, beside the code that enforces it, and
reaches the contract only on the routes whose code path actually calls it --
there is no list of rules to keep in step with the workflows.

What the docstring must say
---------------------------
It is published verbatim to producers, so it states the rule in the
producer's terms: which fields, which values, what is refused. A marked
function without a docstring is refused at import, because a rule with no
text would reach the contract as an empty bullet.
"""

from collections.abc import Callable
from typing import TypeVar

__all__ = ["PRODUCER_RULE_ATTRIBUTE", "is_producer_rule", "producer_rule"]

F = TypeVar("F", bound=Callable[..., object])

#: Attribute set on a marked function. Read by :func:`is_producer_rule`.
PRODUCER_RULE_ATTRIBUTE = "__tckdb_producer_rule__"


def producer_rule(func: F) -> F:
    """Mark ``func`` as a producer-facing rule and return it unchanged."""
    if not (func.__doc__ or "").strip():
        raise TypeError(
            f"{func.__qualname__} is marked as a producer rule but has no docstring. "
            "The docstring is the rule text the producer contract publishes; write it."
        )
    setattr(func, PRODUCER_RULE_ATTRIBUTE, True)
    return func


def is_producer_rule(obj: object) -> bool:
    """Whether ``obj`` was marked with :func:`producer_rule`."""
    return getattr(obj, PRODUCER_RULE_ATTRIBUTE, False) is True
