"""Length bounds for public scientific-read free-text fields.

Centralized here so every request schema imports the same constants
and the hosted abuse-control review needs to check only one file.
See F9 in ``docs/audits/security_public_read_abuse_audit.md`` and
``docs/specs/public_read_abuse_controls.md``.

Values are deliberately generous — chemical identifiers can be long
(IUPAC InChI in particular), and the goal is to reject 10 MB query
parameters, not to validate chemistry. Chemistry validation belongs
in upload paths.
"""

from __future__ import annotations

# Chemical identifiers.
MAX_SMILES_LENGTH: int = 2048
MAX_INCHI_LENGTH: int = 4096
MAX_INCHI_KEY_LENGTH: int = 64
MAX_FORMULA_LENGTH: int = 256

# Computational provenance free-text.
MAX_METHOD_LENGTH: int = 256
MAX_BASIS_LENGTH: int = 256
MAX_DISPERSION_LENGTH: int = 256
MAX_SOLVENT_LENGTH: int = 256
MAX_SOFTWARE_NAME_LENGTH: int = 256
MAX_WORKFLOW_TOOL_LENGTH: int = 256
MAX_FAMILY_LENGTH: int = 256

# Public refs are ``<prefix>_<26 char body>`` (~31 chars). Leave
# headroom for future encodings.
MAX_PUBLIC_REF_LENGTH: int = 64

# Repeated participant lists. A reaction with 32 reactants or
# products is not a real catalogue entry; rejecting larger lists
# avoids amplifying lookup cost.
MAX_PARTICIPANTS_PER_REACTION: int = 32
