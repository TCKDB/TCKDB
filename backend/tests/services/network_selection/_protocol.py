"""A stored protocol declaration for the network-selection tests."""

from __future__ import annotations


def protocol(reduction: str, **more: str) -> dict:
    return {"version": 1, "reduction_method": reduction, **more}
