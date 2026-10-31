"""Generic upload warning returned alongside successful uploads."""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from tckdb_schemas.deposit_warning_messages import render_message


class UploadWarning(BaseModel):
    """A non-blocking warning produced during upload reconciliation.

    Build one with :meth:`from_code`. The ``message`` is TCKDB's sentence for the ``code``, rendered from the code and
    the structured ``context``, so the upload response, the job result, the stored row and the public read all say
    the same thing. It never carries text the depositor typed (a transition-state label, a local payload key, a file
    name); a label that helps the depositor find the problem sits under ``context["depositor_supplied"]``.

    :param field: Dot-path to the field that triggered the warning
        (e.g. ``"species_entry_kind"`` or ``"reactants[0].species_entry_kind"``). It is the path into the
        depositor's own request, so it may name a local key; a read replaces those keys.
    :param code: Machine-readable warning code for programmatic handling. Match on this, never on ``message``.
    :param message: TCKDB's sentence for ``code``, already formatted.
    :param context: The values that triggered the warning, as the check saw them
        (for example the declared and the logged charge). Optional: a producer
        that states none leaves it ``None``. It is stored with the warning and
        shown with the record it concerns; it never carries a database id. Anything the depositor typed that helps
        them find the problem is under ``depositor_supplied`` and is depositor-typed text.
    """

    field: str
    code: str
    message: str
    context: dict[str, Any] | None = None

    @classmethod
    def from_code(cls, *, field: str, code: str, context: Mapping[str, Any] | None = None) -> "UploadWarning":
        """The warning for ``code``, with TCKDB's sentence as its message.

        This is the one way a producer builds a warning. The sentence is
        :func:`tckdb_schemas.deposit_warning_messages.render_message` of the code and context, the same call a read
        makes on the stored row.
        """
        stored = dict(context) if context else None
        return cls(field=field, code=code, message=render_message(code, stored), context=stored)
