"""The deposit-time warnings a record carries on a scientific read.

Warnings are what a deposit tripped when it was accepted (the ``warn`` tier of the scientific check register).
They are stored where they belong and shown on every detail read, by default, in two envelope blocks (list and
search responses carry neither):

* ``record_warnings`` -- one entry per record the response names, each carrying that record's public ref: the
  warnings that are about that record.
* ``deposit_warnings`` -- the warnings that are about the *deposit* and could not be put on any one record (a
  shared energy-correction scheme in a bundle of many records, or a warning the deposit could not attribute),
  once per submission that a named record belongs to. Nothing is duplicated: these rows stay on the submission.

``status`` (on a ``record_warnings`` entry) answers the question a bare empty list cannot:

* ``recorded`` -- the deposit's warnings were stored with this record; ``items`` is exactly what fired
  (empty when nothing did).
* ``not_recorded`` -- the record was deposited before warnings were stored, or its deposit could not place every
  warning on a record (that deposit then marks none of its records checked, and shows the warning under
  ``deposit_warnings``). Nothing can be said either way. This is not "none".

``status`` describes the deposit-time check and nothing else. ``items`` is every warning stored for the record,
including any a later artifact upload added, so a ``not_recorded`` record can still list items: the artifact's
warning is real, and the deposit's own are unknown.

Whose words
-----------
A warning item's public ``message`` is **TCKDB's** wording: it is rendered at read time from the warning's code
and its structured ``context``, never taken from the text stored at deposit. The public ``field`` is the payload
path with every depositor key replaced (``species[*].thermo``; positional ``[0]`` kept). The stored text can quote
the depositor (a transition-state label, a local calculation key, a species key), and a depositor's words must
not be presented as TCKDB's on any client. That raw text is ``depositor_message`` and ``depositor_field``, and it
is present only for the deposit's owner and for curators and admins (the access rule ``GET /jobs/{id}`` uses); for
everyone else the keys are absent. ``context`` holds values the check states; anything the depositor typed that
a producer puts in it sits under ``context.depositor_supplied`` and is depositor-typed text: a client that
renders it must say so.

``submission_ref`` follows the existing rule for that field on the scientific reads: it is served only to a caller
who authenticates, and the key is omitted for an anonymous reader.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr, model_serializer


class DepositWarningItem(BaseModel):
    """One warning, in TCKDB's words."""

    model_config = ConfigDict(extra="forbid")

    code: str
    #: Rendered from the code and the structured context. Never the stored text, never a depositor string.
    message: str
    #: The payload path with depositor keys replaced by ``[*]``; positional indexes are kept.
    field: str
    #: The scientific check register entry number, when the code has one.
    register_entry: int | None = None
    #: The values the check states. ``depositor_supplied``, when present, is depositor-typed text.
    context: dict[str, Any] | None = None
    #: The version of the warning set that judged the deposit.
    check_version: int
    #: The stored message and payload path, as the depositor's deposit said them. Absent unless the reader owns the
    #: deposit or is a curator or admin.
    depositor_message: str | None = None
    depositor_field: str | None = None

    #: What the reading boundary needs to decide who may see the stored text. Never serialized.
    _raw_message: str = PrivateAttr(default="")
    _raw_field: str = PrivateAttr(default="")
    _owner_id: int | None = PrivateAttr(default=None)

    @model_serializer(mode="wrap")
    def _omit_absent_depositor_text(self, handler):
        data = handler(self)
        for key in ("depositor_message", "depositor_field"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class RecordWarnings(BaseModel):
    """The warnings stored for one record."""

    model_config = ConfigDict(extra="forbid")

    #: The public ref of the record these warnings are about.
    record_ref: str
    status: Literal["recorded", "not_recorded"]
    items: list[DepositWarningItem]


class SubmissionWarnings(BaseModel):
    """The warnings of one submission that no one record could take."""

    model_config = ConfigDict(extra="forbid")

    #: The submission's public ref. Absent for an anonymous reader (``submission_ref`` is auth-gated everywhere).
    submission_ref: str | None = None
    items: list[DepositWarningItem]

    @model_serializer(mode="wrap")
    def _omit_absent_ref(self, handler):
        data = handler(self)
        if data.get("submission_ref") is None:
            data.pop("submission_ref", None)
        return data


__all__ = ["DepositWarningItem", "RecordWarnings", "SubmissionWarnings"]
