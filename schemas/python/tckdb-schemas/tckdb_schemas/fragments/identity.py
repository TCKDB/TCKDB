"""Identity upload fragments and the identity-text validator mixin.

Combines:

* ``SpeciesEntryIdentityValidatorMixin`` (from the backend
  ``app.schemas.entities.species_entry`` module) — the shared
  identity-text normalizer also reused by the backend's read/write
  species-entry schemas.
* ``SpeciesIdentityPayload`` and ``SpeciesEntryIdentityPayload`` (from
  the backend ``app.schemas.fragments.identity`` module) — the
  upload-facing identity fragments embedded in every computed-species
  and computed-reaction bundle.

Backend-only read/CRUD species-entry schemas remain in the backend
package and continue to import the mixin from this module via the shim.
"""

from typing import Self

from pydantic import Field, field_validator, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import (
    MoleculeKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
)
from tckdb_schemas.utils import normalize_optional_text, normalize_required_text

_IDENTITY_TEXT_FIELDS = (
    "unmapped_smiles",
    "stereo_label",
    "electronic_state_label",
    "term_symbol_raw",
    "term_symbol",
)


class SpeciesEntryIdentityValidatorMixin:
    @model_validator(mode="after")
    def normalize_identity_text_fields(self) -> Self:
        """Normalize optional identity text fields without imposing stricter semantics yet."""

        for field_name in _IDENTITY_TEXT_FIELDS:
            setattr(
                self,
                field_name,
                normalize_optional_text(getattr(self, field_name, None)),
            )

        return self


#: The reserved token that declares a free electron as a reaction participant.
#:
#: An electron has no SMILES — RDKit does not parse ``[e-]`` and no
#: standard notation names one — so this string is a *sentinel*, never parsed
#: as chemistry. It is spelled the way plasma- and ion-chemistry mechanisms
#: write it so a depositor recognises it, and it is the only ``smiles`` value
#: ``MoleculeKind.electron`` accepts, so a payload can never claim to be an
#: electron while carrying a structure or claim a structure while marked an
#: electron.
ELECTRON_SMILES = "[e-]"

#: An electron's charge and spin multiplicity, which are not opinions. Stating
#: anything else is a contradiction in the payload, not an unusual deposit.
ELECTRON_CHARGE = -1
ELECTRON_MULTIPLICITY = 2

#: The participant kinds that are *known* to carry no atoms.
#:
#: ``pseudo`` is deliberately absent. A lumped construct's composition is
#: **unknown**, not empty, so an empty atom list on a pseudo participant would
#: not be the statement "this has no atoms" — it would be a real molecule's
#: atoms going unaccounted for, and the service-layer element check skips a
#: pseudo participant precisely because its composition is not an atom-resolved
#: fact. ``electron`` is the opposite case: exactly known to be nothing. That
#: is the same distinction ``MoleculeKind`` itself is written around, and the
#: same one ``_element_counts_for_species`` makes in the backend, where an
#: electron returns an empty count and a pseudo-species returns ``None``.
ATOMLESS_MOLECULE_KINDS = frozenset({MoleculeKind.electron})


def participant_has_no_atoms(molecule_kind: MoleculeKind) -> bool:
    """Say whether a participant of this kind has no atoms at all.

    The single place the rule is written, because two wire surfaces have to
    agree about it: an IRC participant mapping says which saddle-point atoms
    become which participant, and an atom map refines that partition into a
    bijection. Both must accept "none, because there are none" from a free
    electron and refuse it from anything else, and two surfaces enforcing
    different standards on one claim is not a defensible position for either.
    """

    return molecule_kind in ATOMLESS_MOLECULE_KINDS


class SpeciesIdentityPayload(SchemaBase):
    """Reusable upload fragment for graph identity resolution.

    :param molecule_kind: What sort of participant this is — see
        :class:`~tckdb_schemas.enums.MoleculeKind`. Nearly every deposit is a
        ``molecule``.
    :param smiles: Input graph identity SMILES string. Isotopic substitution is
        expressed with standard SMILES isotope notation (``[2H]``, ``[13C]``,
        ``[18O]``) and is atom-resolved: ``[2H]CO`` and ``[2H]OC`` are
        different molecules. Isotope labels do **not** fork the species-level
        graph identity — they resolve a distinct ``species_entry`` under one
        shared ``species``.
    :param charge: Expected formal charge for the uploaded identity.
    :param multiplicity: Expected spin multiplicity for the uploaded identity.

    Declaring a free electron
    -------------------------
    Set ``molecule_kind`` to ``electron`` with ``smiles="[e-]"``,
    ``charge=-1``, ``multiplicity=2``::

        {"molecule_kind": "electron", "smiles": "[e-]",
         "charge": -1, "multiplicity": 2}

    That is the whole interface — no database id, and the electron's identity
    row is resolved server-side like any other participant's. It exists so
    that ``OH- + H -> H2O + e-`` (associative detachment) and its siblings —
    dissociative attachment, photoionization, photodetachment — can be
    deposited as balanced reactions rather than being refused for the electron
    they legitimately release. An electron is exactly known, so declaring one
    exempts a reaction from nothing: it adds no atoms to the elemental balance
    and -1 to the charge sum, and both conservation checks still have to pass.

    The three values are pinned rather than trusted. An electron carrying
    charge 0 would silently switch charge conservation off, which is the whole
    property this participant exists to preserve.
    """

    molecule_kind: MoleculeKind = MoleculeKind.molecule
    smiles: str = Field(min_length=1)
    charge: int
    multiplicity: int = Field(ge=1)

    @field_validator("smiles")
    @classmethod
    def normalize_smiles(cls, value: str) -> str:
        return normalize_required_text(value)

    @model_validator(mode="after")
    def check_electron_identity(self) -> Self:
        """Bind ``electron`` and ``[e-]`` to each other, and to -1 / 2.

        Both directions matter. An ``electron`` carrying a real SMILES would
        make a structure invisible to elemental balance; a ``molecule``
        carrying ``[e-]`` would be handed to RDKit, which cannot parse it.
        """

        is_electron_kind = self.molecule_kind == MoleculeKind.electron
        is_electron_smiles = self.smiles == ELECTRON_SMILES

        if is_electron_kind != is_electron_smiles:
            raise ValueError(
                f"molecule_kind={MoleculeKind.electron.value!r} and "
                f"smiles={ELECTRON_SMILES!r} must be declared together: a free "
                "electron has no structure, and no structure is a free "
                "electron."
            )

        if not is_electron_kind:
            return self

        if self.charge != ELECTRON_CHARGE or self.multiplicity != ELECTRON_MULTIPLICITY:
            raise ValueError(
                "A free electron carries charge "
                f"{ELECTRON_CHARGE} and multiplicity {ELECTRON_MULTIPLICITY}; "
                f"got charge={self.charge}, multiplicity={self.multiplicity}. "
                "These are not deposit-specific values, and an electron "
                "declared neutral would switch off the charge conservation "
                "this participant exists to preserve."
            )

        return self


class SpeciesEntryIdentityPayload(
    SpeciesEntryIdentityValidatorMixin,
    SpeciesIdentityPayload,
):
    """Reusable upload fragment for resolved species-entry identity.

    :param species_entry_kind: Stationary-point kind for the resolved entry.
    :param unmapped_smiles: Optional display/search SMILES for the resolved entry.
    :param stereo_kind: Stereo classification for the resolved entry.
    :param stereo_label: Optional stereo label such as ``R`` or ``E``.
    :param electronic_state_kind: Electronic-state classification.
    :param electronic_state_label: Optional state label such as ``X`` or ``A``.
    :param term_symbol_raw: Optional raw uploaded term symbol.
    :param term_symbol: Optional canonicalized term symbol.

    Isotopic resolution is *not* a field on this payload. It is derived
    server-side from the atom-resolved isotope labels in ``smiles`` (see
    ``SpeciesIdentityPayload.smiles``). The former free-text
    ``isotopologue_label`` has been removed: an arbitrary string could mint
    two species identities that no scientific content distinguished, and a
    derived key may never be accepted from an uploader.
    """

    species_entry_kind: StationaryPointKind = StationaryPointKind.minimum
    unmapped_smiles: str | None = None

    stereo_kind: StereoKind = StereoKind.unspecified
    stereo_label: str | None = Field(default=None, max_length=64)

    electronic_state_kind: SpeciesEntryStateKind = SpeciesEntryStateKind.ground
    electronic_state_label: str | None = Field(default=None, max_length=8)

    term_symbol_raw: str | None = Field(default=None, max_length=64)
    term_symbol: str | None = Field(default=None, max_length=64)
