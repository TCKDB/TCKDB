"""Backend import path for ESS program detection.

The detector has one owner, :mod:`tckdb_schemas.ess_software_detection`, so
that upload validation (which reads an inline input deck before judging
imaginary modes) and parameter extraction identify the program the same way.
Nothing is defined here.
"""

from tckdb_schemas.ess_software_detection import (  # noqa: F401
    SoftwareName,
    detect_software_from_text,
)

__all__ = ["SoftwareName", "detect_software_from_text"]
