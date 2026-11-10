"""Backend import path for the deposit-warning sentences.

The sentences have one owner, :mod:`tckdb_schemas.deposit_warning_messages`, so that the producers that live in
``tckdb_schemas`` and the ones in this backend build a warning's ``message`` from the same table that a read renders
it from. Nothing is defined here.
"""

from tckdb_schemas.deposit_warning_messages import RENDERED, normalise_field, render_message

__all__ = ["RENDERED", "normalise_field", "render_message"]
