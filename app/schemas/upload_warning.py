"""Generic upload warning returned alongside successful uploads."""

from pydantic import BaseModel


class UploadWarning(BaseModel):
    """A non-blocking warning produced during upload reconciliation.

    :param field: Dot-path to the field that triggered the warning
        (e.g. ``"species_entry_kind"`` or ``"reactants[0].species_entry_kind"``).
    :param code: Machine-readable warning code for programmatic handling.
    :param message: Human-readable explanation, already formatted.
    """

    field: str
    code: str
    message: str
