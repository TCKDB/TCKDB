"""Re-export shim — ``ArtifactIn`` now lives in ``tckdb_schemas.fragments.artifact``."""

from tckdb_schemas.fragments.artifact import (  # noqa: F401
    KIND_ALLOWED_EXTENSIONS,
    MAX_FILENAME_LENGTH,
    ArtifactIn,
)
