"""The rules a selection engine applies, held in a :class:`~app.versioning.VersionRegistry` keyed by (rule id, version).

Every selection engine (thermo, kinetics, structure, statmech, network) ships a registry of rules. A decision manifest
records, for each rule it applied, the rule's id and its integer version; replaying the manifest asks the registry for
exactly that pair. A rule the registry does not hold -- an unknown id, or a version this release no longer carries -- is
refused (:class:`~app.versioning.UnknownVersion`, which each engine turns into its own replay refusal); it is never
replaced by the rule that is registered today, because a replay that quietly used a different rule than the one that
made the decision is not a replay.

A rule's id is its stable *name* and never contains a version. Today every rule is registered once, as the current
version of its id. A future version of a rule would be registered beside it from its own manifest file, not as a
subclass named for the version.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar

from app.versioning import UnknownVersion, VersionRegistry

E = TypeVar("E", bound=Exception)


def rules_registry(family: str, rules: Iterable[Any]) -> VersionRegistry[Any]:
    """A registry holding exactly *rules*, each as the current version of its ``rule_id``.

    Refuses (``ValueError``) a rule whose id embeds a version, whose version is not an integer of at least 1, or two
    rules that share an id: the registry has one current version per name.
    """
    registry: VersionRegistry[Any] = VersionRegistry(family)
    for rule in rules:
        registry.register(rule.rule_id, rule.version, rule, current=True)
    registry.validate()
    return registry


def rule_recorded_in(registry: VersionRegistry[Any], entry: Mapping[str, Any], refuse: Callable[[str], E]) -> Any:
    """The registered rule a manifest's decision entry names, or ``refuse(message)`` raised.

    The entry's ``rule_id`` and ``version`` are what the manifest *recorded*. A version that is not exactly an integer
    (a text version such as ``"1.0.0"``, a ``True``) or a pair the registry does not hold is refused with
    ``rule <id> version <version> is not in the running registry``; it is never replaced by the current version.
    """
    rule_id, version = entry.get("rule_id"), entry.get("version")
    message = f"rule {rule_id} version {version} is not in the running registry"
    if not isinstance(rule_id, str) or type(version) is not int:
        raise refuse(message)
    try:
        return registry.get(rule_id, version)
    except UnknownVersion:
        raise refuse(message) from None
