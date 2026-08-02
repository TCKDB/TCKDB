"""The query shapes the Stage 4 benchmark measures.

Each shape is one supported read the API advertises, expressed as the HTTP
request a client actually sends. Shapes are grouped by the question they
answer so a regression in one group is attributable.

A shape carries a ``breadth`` label because the interesting number is not the
average — it is what happens on the *broad* end. A search pinned to one public
ref will be fast on any corpus; a formula search that legitimately matches 160
isomers is where a materialize-then-slice implementation falls over.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QueryShape:
    """One measured read."""

    name: str
    path: str
    params: dict[str, object] = field(default_factory=dict)
    #: ``narrow`` — pinned to a single record or a rare identifier.
    #: ``broad``  — legitimately matches many records; the shape that matters.
    breadth: str = "narrow"
    group: str = "misc"
    #: Set when a shape is expected to be answered by a specific index; the
    #: benchmark asserts the recorded plan actually uses it.
    expects_index: str | None = None
    note: str = ""


def shapes(*, formula: str, smiles: str,
           popular_reactants: list[str], popular_products: list[str],
           rare_reactants: list[str], rare_products: list[str],
           species_ref: str, species_entry_ref: str,
           reaction_entry_ref: str) -> list[QueryShape]:
    """Build the shape list against identifiers drawn from the live corpus.

    Identifiers are passed in rather than hard-coded because they must exist
    in whichever corpus is being measured; a shape that silently matches
    nothing would report an excellent, meaningless latency.
    """
    return [
        # -- species identity -------------------------------------------
        QueryShape(
            name="species_search_by_smiles",
            path="/api/v1/scientific/species/search",
            params={"smiles": smiles},
            breadth="narrow",
            group="species",
            expects_index="uq_species_identity",
        ),
        QueryShape(
            name="species_search_by_formula_broad",
            path="/api/v1/scientific/species/search",
            params={"formula": formula},
            breadth="broad",
            group="species",
            expects_index="ix_species_formula_lookup",
            note="largest isomer family in the corpus",
        ),
        QueryShape(
            name="species_search_by_formula_broad_include_all",
            path="/api/v1/scientific/species/search",
            params={"formula": formula, "include": "all"},
            breadth="broad",
            group="species",
            note="adds thermo/statmech/transport/conformer section ids",
        ),
        QueryShape(
            name="species_search_by_formula_deep_offset",
            path="/api/v1/scientific/species/search",
            params={"formula": formula, "offset": 100, "limit": 50},
            breadth="broad",
            group="species",
            note="deep page — exposes offset pagination cost",
        ),
        QueryShape(
            name="species_search_by_ref",
            path="/api/v1/scientific/species/search",
            params={"species_ref": species_ref},
            breadth="narrow",
            group="species",
        ),
        # -- reaction identity -------------------------------------------
        # Reaction search matches the participant multiset on BOTH sides, so
        # both reactants and products must be supplied or the query matches
        # only product-less entries.
        QueryShape(
            name="reaction_search_popular_participants",
            path="/api/v1/scientific/reactions/search",
            params={"reactants": popular_reactants, "products": popular_products},
            breadth="broad",
            group="reactions",
            note="a reaction whose participants include the most-participating "
                 "species in the corpus — the popularity cliff",
        ),
        QueryShape(
            name="reaction_search_rare_participants",
            path="/api/v1/scientific/reactions/search",
            params={"reactants": rare_reactants, "products": rare_products},
            breadth="narrow",
            group="reactions",
        ),
        QueryShape(
            name="reaction_search_by_ref",
            path="/api/v1/scientific/reactions/search",
            # ``*_ref`` filters take a public ref (``rxe_...``), never an
            # internal id — passing an id is a 422 ``invalid_handle``.
            params={"reaction_entry_ref": reaction_entry_ref},
            breadth="narrow",
            group="reactions",
        ),
        # -- scientific products ------------------------------------------
        QueryShape(
            name="species_thermo_by_entry",
            path=f"/api/v1/scientific/species-entries/{species_entry_ref}/thermo",
            breadth="narrow",
            group="products",
        ),
        QueryShape(
            name="species_statmech_by_entry",
            path=f"/api/v1/scientific/species-entries/{species_entry_ref}/statmech",
            breadth="narrow",
            group="products",
        ),
        QueryShape(
            name="reaction_kinetics_by_entry",
            path=f"/api/v1/scientific/reaction-entries/{reaction_entry_ref}/kinetics",
            breadth="narrow",
            group="products",
        ),
        # -- composed product searches --------------------------------------
        QueryShape(
            name="thermo_search_broad",
            path="/api/v1/scientific/thermo/search",
            params={"formula": formula},
            breadth="broad",
            group="composed",
            note="composed over species search",
        ),
        QueryShape(
            name="kinetics_search_popular",
            path="/api/v1/scientific/kinetics/search",
            params={"reactants": popular_reactants, "products": popular_products},
            breadth="broad",
            group="composed",
            note="composed over reaction search",
        ),
        # -- calculation + statmech searches ---------------------------------
        QueryShape(
            name="statmech_search_by_method",
            path="/api/v1/scientific/statmech/search",
            params={"method": "B3LYP", "limit": 50},
            breadth="broad",
            group="composed",
            note="statmech search takes no formula filter; provenance is its "
                 "broadest available scope",
        ),
        QueryShape(
            name="species_calculations_search_broad",
            path="/api/v1/scientific/species-calculations/search",
            params={"formula": formula},
            breadth="broad",
            group="composed",
        ),
        QueryShape(
            name="calculation_search_by_lot",
            path="/api/v1/scientific/calculations/search",
            params={"calculation_type": "sp", "limit": 50},
            breadth="broad",
            group="calculations",
        ),
        # -- structure search (RDKit GiST) -------------------------------------
        QueryShape(
            name="structure_search_substructure",
            path="/api/v1/scientific/species/structure-search",
            params={"query_smarts": "C(=O)O", "mode": "substructure", "limit": 50},
            breadth="broad",
            group="structure",
            expects_index="ix_species_entry_mol_gist",
            note="carboxyl substructure — matches a large fraction of the corpus",
        ),
        QueryShape(
            name="structure_search_exact",
            path="/api/v1/scientific/species/structure-search",
            params={"query_smiles": smiles, "mode": "exact", "limit": 50},
            breadth="narrow",
            group="structure",
        ),
    ]
