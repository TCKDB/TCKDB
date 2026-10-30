"""The deposit-time warnings a record carries on a scientific read.

Warnings are what a deposit tripped when it was accepted (the ``warn`` tier of the scientific check register).
They are stored beside the record they concern and shown on every detail read of it, by default, in the
envelope's ``record_warnings`` list (one entry per record the response names, each carrying that record's public
ref). List and search responses do not carry them. A warning never changes a value, orders a list or gates
selection.

``status`` answers the question a bare empty list cannot:

* ``recorded`` -- the deposit's warnings were stored with this record; ``items`` is exactly what fired
  (empty when nothing did).
* ``not_recorded`` -- the record was deposited before warnings were stored, or its deposit could not place every
  warning on a record (that deposit then marks none of its records checked). Nothing can be said either way. This is
  not "none".

``status`` describes the deposit-time check and nothing else. ``items`` is every warning stored for the record,
including any a later artifact upload added, so a ``not_recorded`` record can still list items: the artifact's
warning is real, and the deposit's own are unknown.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class DepositWarningItem(BaseModel):
    """One warning, as the depositor was told it."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    #: The payload position the check named.
    field: str
    #: The scientific check register entry number, when the code has one.
    register_entry: int | None = None
    #: The values that triggered the warning, when the check stated them.
    context: dict[str, Any] | None = None
    #: The version of the warning set that judged the deposit.
    check_version: int


class RecordWarnings(BaseModel):
    """The warnings stored for one record."""

    model_config = ConfigDict(extra="forbid")

    #: The public ref of the record these warnings are about.
    record_ref: str
    status: Literal["recorded", "not_recorded"]
    items: list[DepositWarningItem]


__all__ = ["DepositWarningItem", "RecordWarnings"]
