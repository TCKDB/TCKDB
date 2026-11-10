"""A database in the shape the stereo-identity migration leaves behind.

The merged-away rows are KEPT and keep what they had: the species its old marked SMILES, InChIKey and public ref; the
entry its old label and marked ``mol``; the reaction its old stoichiometry hash. Only the marker says they are merged. A
fixture that copies the survivor's values into the kept row hides every lookup that matches on those values, which is why
the world is built here, once, with the real values.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models.common import ReactionRole, StereoResolution
from app.db.models.reaction import ChemReaction, ReactionParticipant
from app.db.models.species import Species, SpeciesEntry
from app.services.public_refs import PREFIXES, make_content_ref
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
)

#: The key of 2-butene and the InChIKeys the migration left on the two rows.
KEY_INCHI = "IAQRGUVFOMOMEM-UHFFFAOYSA-N"
MARKED_INCHI = "IAQRGUVFOMOMEM-ONEGZZNKSA-N"


@dataclass
class World:
    species: Species
    entry: SpeciesEntry
    kept_species: Species
    kept_flat_species: Species
    kept_entry: SpeciesEntry
    butane: Species
    butane_entry: SpeciesEntry
    reaction: ChemReaction
    kept_reaction: ChemReaction

    @property
    def kept_refs(self) -> list[str]:
        return [
            self.kept_species.public_ref,
            self.kept_flat_species.public_ref,
            self.kept_entry.public_ref,
            self.kept_reaction.public_ref,
        ]


def build_world(session: Session) -> World:
    species = make_species(session, smiles="CC=CC", inchi_key=KEY_INCHI)
    entry = make_species_entry(session, species, stereo_label="E")
    make_thermo_scalar(session, species_entry=entry)
    butane = make_species(session, smiles="CCCC", inchi_key="IJDNQMDRQITEOD-UHFFFAOYSA-N")
    butane_entry = make_species_entry(session, butane)

    kept_species = Species(
        kind=species.kind,
        smiles="C/C=C/C",  # the marked spelling the row had before the key became the constitution
        inchi_key=MARKED_INCHI,
        charge=0,
        multiplicity=1,
        stereo_kind=species.stereo_kind,
        merged_into_species_id=species.id,
        public_ref=make_content_ref(
            PREFIXES["Species"], "species:smiles=C/C=C/C;charge=0;multiplicity=1;stereo_kind=ez_isomer"
        ),
    )
    session.add(kept_species)
    # The other shape: a loser that was the row already holding the key's SMILES keeps it, beside its survivor.
    kept_flat_species = Species(
        kind=species.kind,
        smiles=species.smiles,
        inchi_key=species.inchi_key,
        charge=0,
        multiplicity=1,
        stereo_kind=species.stereo_kind,
        merged_into_species_id=species.id,
        public_ref=make_content_ref(
            PREFIXES["Species"], "species:smiles=CC=CC;charge=0;multiplicity=1;stereo_kind=unspecified"
        ),
    )
    session.add(kept_flat_species)
    session.flush()
    kept_entry = SpeciesEntry(
        species_id=species.id,  # its results and its parent moved to the survivor's species
        mol="C/C=C/C",
        stereo_label="E",
        stereo_descriptor=entry.stereo_descriptor,
        stereo_resolution=StereoResolution.resolved,
        merged_into_species_entry_id=entry.id,
    )
    session.add(kept_entry)
    session.flush()

    reaction = make_chem_reaction(session, reactants=[species], products=[butane])
    make_reaction_entry(session, reaction=reaction, reactant_entries=[entry], product_entries=[butane_entry])
    kept_reaction = ChemReaction(
        reversible=reaction.reversible,
        stoichiometry_hash="legacy-" + "0" * 57,
    )
    session.add(kept_reaction)
    session.flush()
    for sp, role in ((species, ReactionRole.reactant), (butane, ReactionRole.product)):  # the migration re-pointed these
        session.add(ReactionParticipant(reaction_id=kept_reaction.id, species_id=sp.id, role=role, stoichiometry=1))
    session.flush()
    # Marked only now, as the migration does: the participants it still has were written while it was live.
    session.execute(
        text("UPDATE chem_reaction SET merged_into_chem_reaction_id = :s WHERE id = :l"),
        {"s": reaction.id, "l": kept_reaction.id},
    )

    # Rewrite the survivors so their current row versions sit AFTER the kept rows in the heap: a lookup that forgot to
    # skip merged-away rows must not be rescued by returning the physically first match.
    session.execute(text("UPDATE species SET inchi_key = inchi_key WHERE id = :i"), {"i": species.id})
    session.execute(text("UPDATE species_entry SET kind = kind WHERE id = :i"), {"i": entry.id})
    session.execute(text("UPDATE chem_reaction SET reversible = reversible WHERE id = :i"), {"i": reaction.id})
    # ...and the kept row that holds the key's SMILES last, for the lookups that keep the final match.
    session.execute(text("UPDATE species SET inchi_key = inchi_key WHERE id = :i"), {"i": kept_flat_species.id})
    session.expire_all()
    return World(species, entry, kept_species, kept_flat_species, kept_entry, butane, butane_entry, reaction, kept_reaction)
