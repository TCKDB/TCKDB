"""Backend re-export shim — identity payloads and the identity-text
validator mixin now live in ``tckdb_schemas.fragments.identity``.
"""

from tckdb_schemas.fragments.identity import (
    SpeciesEntryIdentityPayload,
    SpeciesEntryIdentityValidatorMixin,
    SpeciesIdentityPayload,
)
