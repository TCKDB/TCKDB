"""The base error of the deposit builder."""

from __future__ import annotations


class DepositError(RuntimeError):
    """Base class: the deposit was not built, and the message says why."""

    exit_code = 1
