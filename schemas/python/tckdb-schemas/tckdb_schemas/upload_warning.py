"""Generic upload warning returned alongside successful uploads."""

from typing import Any

from pydantic import BaseModel


class UploadWarning(BaseModel):
    """A non-blocking warning produced during upload reconciliation.

    :param field: Dot-path to the field that triggered the warning
        (e.g. ``"species_entry_kind"`` or ``"reactants[0].species_entry_kind"``).
    :param code: Machine-readable warning code for programmatic handling.
    :param message: Human-readable explanation, already formatted.
    :param context: The values that triggered the warning, as the check saw them
        (for example the declared and the logged charge). Optional: a producer
        that states none leaves it ``None``. It is stored with the warning and
        shown with the record it concerns; it never carries a database id.
    """

    field: str
    code: str
    message: str
    context: dict[str, Any] | None = None
