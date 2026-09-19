"""The rights fragment of an upload: under what terms may this deposit ship.

A dataset release republishes deposited records under a data license. The
operator can license their own deposits and nobody else's, so before a
release can carry a second contributor's records the license has to be part
of the upload contract — agreed at deposit time and recorded against the
deposit. This fragment is that agreement, in the wire shape.

Three fields and no identifiers:

* ``license`` — an SPDX identifier such as ``CC-BY-4.0``. Compared with a
  release's data license by exact, case-insensitive match; there is no
  compatibility lattice.
* ``depositor_attests_right_to_license`` — typed as ``Literal[True]`` so a
  ``false`` fails validation instead of being stored as a quiet "no". A
  depositor who cannot attest omits the fragment; a release then refuses
  the records until a curator records a basis with an actor.
* ``source_terms`` — optional citation or quotation of the terms the
  deposit was taken under, for records that are not the depositor's own
  work.

The fragment is optional on every upload request in v1 so existing clients
keep working; absence bites at release time, not at upload.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.utils import normalize_optional_text, normalize_required_text


class DepositRights(SchemaBase):
    """Deposit-time agreement that the records may be licensed as stated."""

    license: str = Field(min_length=1, max_length=64)
    depositor_attests_right_to_license: Literal[True]
    source_terms: str | None = None

    @field_validator("license")
    @classmethod
    def normalize_license(cls, value: str) -> str:
        return normalize_required_text(value)

    @field_validator("source_terms")
    @classmethod
    def normalize_source_terms(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)


__all__ = ["DepositRights"]
