"""One way to name a version: a stable name plus a separate integer.

A thing TCKDB owns that has versions -- a rubric, a rule, a document format,
a parser -- is identified by two fields: a ``name`` that never changes and an
integer ``version`` that goes up when the thing's output or meaning changes.
The name never contains the version. That is the whole rule, and
:class:`VersionRef` is the value that carries it: every stored record, every
served document and every function argument that names a versioned thing
holds a ``VersionRef`` (or the two fields it is made of), never one string
with the version glued on.

Why the pair and not one string. A name with the version fused in
(``computed_thermo_v2``, ``tckdb.archive.v1``, ``ContributionBundleV0``) is
the version twice when the record also has a version field, and it turns a
version bump into a rename: every stored value, every wire document and every
identifier that used the old string has to be found and rewritten. With the
pair, a bump changes one integer.

Only versions TCKDB itself owns are held this way. A version of somebody
else's thing -- an ESS release, a NIST schema, an arXiv revision -- is
recorded as given, as text, in its own field beside the name of the thing it
belongs to; it is never renumbered.

``VERSIONED_NAME`` and the segment patterns below are the single definition of
"a name that embeds a version". :class:`VersionRef` refuses a name that
matches, and the repository's guard test scans the code with the same
patterns, so the two cannot disagree about what counts.
"""

from __future__ import annotations

import re
from typing import Self

from pydantic import ConfigDict, Field, StrictInt, model_validator

from tckdb_schemas.common import SchemaBase

#: A ``vN`` segment inside a snake-, dot-, dash- or ``@``-separated name:
#: ``computed_thermo_v2``, ``tckdb.archive.v1``, ``machine_review_v2_payload``.
#: The lookahead stops at a letter or digit and deliberately lets ``_`` through,
#: which is what a trailing ``\b`` gets wrong -- ``\b`` does not fire between
#: ``2`` and ``_`` because both are word characters, so ``parse_v2_payload``
#: slips past it. A bare ``v1``/``v2`` (a vector variable) has no separator
#: before it and is not matched.
VERSION_SEGMENT = re.compile(r"(?<=[A-Za-z0-9])[_.@-][vV]\d+(?![A-Za-z0-9])")

#: A camel-case ``VN`` suffix: ``ContributionBundleV0``, ``RuleV0_2_0``,
#: ``MachineReviewProviderResultV2``. Requires a lowercase letter or digit
#: before the ``V`` so an acronym like ``HFV2`` is not read as camel case by
#: accident of capitals alone being adjacent.
#:
#: ``V<digits>Z`` is excluded: that is the Dunning basis-set family at
#: zeta 5 and above (``cc-pV5Z``, ``aug-cc-pV6Z``, ``jun-cc-pV5Z``), a
#: chemistry name and not a version. Everything else of the shape
#: ``[a-z]V<digits>`` in the method, basis and dispersion vocabularies is
#: pinned clean by ``backend/tests/test_no_versioned_names.py``.
CAMEL_VERSION = re.compile(r"(?<=[a-z0-9])V\d+(?![a-z0-9Z])")

#: A lowercase ``vN`` glued straight onto a word: ``foov2``, ``thingv10``.
#: At least three letters precede the ``v`` and the letter just before it is
#: not itself ``v``, which keeps ``VV10`` / ``rVV10`` (the nonlocal
#: correlation functional) and short tokens like ``rev2`` out.
LOWER_GLUED_VERSION = re.compile(r"(?<![A-Za-z0-9])[a-z]{2,}[a-uw-z]v\d+(?![A-Za-z0-9])")

#: Three numeric segments in a row, the shape of a semantic version spelled
#: into a name: ``manifest_0_3_0``, ``rule-0.1.0``.
SEMVER_SEGMENTS = re.compile(r"(?:^|(?<=[A-Za-z0-9]))[_.-]?\d+[_.]\d+[_.]\d+(?![0-9])")

#: ``name@2``: a version appended with ``@`` and no ``v``.
AT_VERSION = re.compile(r"(?<=[A-Za-z0-9])@\d+(?![0-9])")

#: Every pattern that marks a *name* as carrying a version.
VERSIONED_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    VERSION_SEGMENT,
    CAMEL_VERSION,
    LOWER_GLUED_VERSION,
    SEMVER_SEGMENTS,
    AT_VERSION,
)


def embeds_version(name: str) -> bool:
    """True if *name* has a version fused into it (see the patterns above)."""
    return any(pattern.search(name) for pattern in VERSIONED_NAME_PATTERNS)


class VersionRef(SchemaBase):
    """A versioned thing TCKDB owns: a stable ``name`` and an integer ``version``.

    Frozen and hashable, so it can key a dict or sit in a set. ``version`` is
    at least 1 and must be a real integer: ``"2"`` and ``2.0`` are refused,
    because a version that arrives as text is the first step back to fusing it
    into a name.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]*[A-Za-z0-9]$",
        max_length=128,
        description=(
            "Stable name of the thing. Never contains a version. Upper case and hyphens are allowed: "
            "a scientific rule id such as K-XYG3-B3LYP-BARRIER is a name."
        ),
    )
    version: StrictInt = Field(
        ge=1,
        description="Integer version of the thing, raised when its meaning changes.",
    )

    @model_validator(mode="after")
    def _name_carries_no_version(self) -> Self:
        if embeds_version(self.name):
            raise ValueError(
                f"name {self.name!r} embeds a version; the version belongs in the "
                "separate 'version' field, never in the name"
            )
        return self

    def __str__(self) -> str:
        return f"{self.name} (version {self.version})"
