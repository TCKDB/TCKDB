"""Generic upload warning returned alongside successful uploads."""

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, model_validator

from tckdb_schemas.deposit_warning_messages import clean_depositor_supplied, render_message


#: A hook the test suite sets to hold every instance, whichever way it was made, to the rule that its message is the
#: sentence for its code and context. It is ``None`` in production on purpose: a client parses a server's warnings into
#: this class, possibly from another version of the package, and must read whatever message that server sent.
_INSTANCE_CHECK: Callable[["UploadWarning"], None] | None = None


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
        them find the problem is under ``depositor_supplied``: a dict of named strings, lists of strings or lists of
        records of strings (``{"transition_state_label": "TS0"}``), which is depositor-typed text. An entry that
        holds nothing is dropped. No other key holds a string the depositor typed.
    """

    field: str
    code: str
    message: str
    context: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _held_to_the_rule_in_tests(self) -> "UploadWarning":
        if _INSTANCE_CHECK is not None:
            _INSTANCE_CHECK(self)
        return self

    @classmethod
    def from_code(cls, *, field: str, code: str, context: Mapping[str, Any] | None = None) -> "UploadWarning":
        """The warning for ``code``, with TCKDB's sentence as its message.

        This is the one way a producer builds a warning. The sentence is
        :func:`tckdb_schemas.deposit_warning_messages.render_message` of the code and context, the same call a read
        makes on the stored row. A ``depositor_supplied`` entry of a wrong shape is logged and dropped: a warning
        never fails an upload.
        """
        stored = clean_depositor_supplied(context)
        return cls(field=field, code=code, message=render_message(code, stored), context=stored)

    @classmethod
    def from_stored(cls, data: Mapping[str, Any]) -> "UploadWarning":
        """A warning read back from where it was kept (a job's recorded warnings or result), as a dict.

        Its message is built again from its code and context, exactly as :meth:`from_code` builds it, and not read
        from the stored text: what was kept is data, and the sentence is TCKDB's to say.
        """
        return cls.from_code(field=data["field"], code=data["code"], context=data.get("context"))
