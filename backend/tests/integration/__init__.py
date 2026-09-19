"""Integration tests.

A package, not a bare directory, because ``conftest.py`` here would
otherwise be imported as a top-level ``conftest`` module and shadow the
root harness that other test files reach with ``import conftest``.
"""
