export type GlossaryKey =
    | "species"
    | "species-entry"
    | "conformer"
    | "observation"
    | "calculation"
    | "level-of-theory"
    | "reaction"
    | "reaction-entry"
    | "transition-state"
    | "network"
    | "review-status"
    | "heat-of-formation"
    | "rate-coefficient"
    | "imaginary-frequency"
    | "energy-units"

export type GlossaryEntry = { term: string; definition: string }

/** TCKDB's own wording for the terms a reader meets on a record page. */
export const GLOSSARY: Record<GlossaryKey, GlossaryEntry> = {
    species: {
        term: "Species",
        definition: "A molecule defined by its atoms and how they are connected. Charge and spin belong to its entries.",
    },
    "species-entry": {
        term: "Species entry",
        definition: "One version of a species: a particular charge, spin state, stereochemistry or isotope pattern. A species can have several entries.",
    },
    conformer: {
        term: "Conformer",
        definition: "One of the 3D shapes a molecule can hold by twisting around single bonds. Deposits that land in the same shape are grouped together.",
    },
    observation: {
        term: "Observation",
        definition: "One deposit that found a conformer: the calculations someone ran and the geometry they ended at.",
    },
    calculation: {
        term: "Calculation",
        definition: "One run of a quantum chemistry program, such as a geometry optimisation, a frequency calculation or a single-point energy.",
    },
    "level-of-theory": {
        term: "Level of theory",
        definition: "The method and basis set a calculation used. Values from different levels are not directly comparable.",
    },
    reaction: {
        term: "Reaction",
        definition: "A transformation of reactant molecules into product molecules, independent of how it was computed or measured.",
    },
    "reaction-entry": {
        term: "Reaction entry",
        definition: "One version of a reaction, tied to specific species entries for its reactants and products.",
    },
    "transition-state": {
        term: "Transition state",
        definition: "The arrangement of atoms at the top of the energy barrier between reactants and products.",
    },
    network: {
        term: "Network",
        definition: "Several reactions that share intermediate molecules, solved together so rates can depend on pressure as well as temperature.",
    },
    "review-status": {
        term: "Review status",
        definition: "Whether a person or an automated check has looked at this record. It describes review, not correctness.",
    },
    "heat-of-formation": {
        term: "Heat of formation",
        definition: "The energy change when a molecule forms from its elements in their standard states. More negative means more stable.",
    },
    "rate-coefficient": {
        term: "Rate coefficient",
        definition: "How fast a reaction proceeds per unit concentration of its reactants. It changes with temperature and sometimes pressure.",
    },
    "imaginary-frequency": {
        term: "Imaginary frequency",
        definition: "The one motion that carries a transition state downhill, toward the reactants one way and the products the other. Programs print it negative or with an i; TCKDB keeps the depositor's sign.",
    },
    "energy-units": {
        term: "Energy units",
        definition: "kJ/mol and kcal/mol are per mole of molecules. A hartree is the atomic unit of energy, about 2625.5 kJ/mol.",
    },
}
