"""The deposit-time warnings a record carries on a scientific read.

Warnings are what a deposit tripped when it was accepted (the ``warn`` tier of the scientific check register).
They are stored beside the record they concern and shown on every detail read of it, by default, in the
envelope's ``warnings_by_ref`` (keyed by the record's public ref, like ``review_status_by_ref``). List and
search responses do not carry them. A warning never changes a value, orders a list or gates selection.

``status`` answers the question a bare empty list cannot:

* ``recorded`` -- the deposit's warnings were stored with this record; ``items`` is exactly what fired
  (empty when nothing did).
* ``not_recorded`` -- the record was deposited before warnings were stored, so nothing can be said either
  way. This is not "none".
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


class DepositWarningsFragment(BaseModel):
    """The warnings stored for one record."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["recorded", "not_recorded"]
    items: list[DepositWarningItem]


__all__ = ["DepositWarningItem", "DepositWarningsFragment"]
