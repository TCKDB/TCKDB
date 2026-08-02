"""Pure Pydantic wire-contract schemas for TCKDB upload payloads."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

try:
    # pyproject.toml holds the only version literal in this package.
    # Reading it back from distribution metadata is what stops
    # ``__version__`` from drifting away from the version actually
    # shipped — anything negotiating the wire contract on
    # ``__version__`` would otherwise be told a number nobody maintains.
    __version__ = _distribution_version("tckdb-schemas")
except PackageNotFoundError:  # source tree that was never installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
