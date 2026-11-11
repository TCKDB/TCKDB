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
        definition: "One version of a species: a particular charge, spin state, stereochemistry or isotope pattern. A species can have several entries, and each holds its own records; values are never copied from one to another.",
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
        definition: "One version of a reaction, tied to specific species entries for its reactants and products. A reaction can have several, each a separate record with its own numbers.",
    },
    "transition-state": {
        term: "Transition state",
        definition: "The arrangement of atoms at the top of the energy barrier between reactants and products. It is identified by the reaction it connects, not by a molecular graph of its own.",
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

export type GuideTopic = { id: string; term: string; text: string }

/** Longer explanations that record pages link to instead of repeating. Each is one or two plain sentences. */
export const GUIDE_TOPICS: GuideTopic[] = [
    { id: "order", term: "How lists are ordered", text: "A list's order is never a recommendation. It is the order the archive returns, usually approved first, then newest deposited; a column you sort by is your own choice." },
    { id: "geometry-sharing", term: "Geometry sharing", text: "One stored set of atom positions can be used by several calculations, as input or as output, with a role saying which." },
    { id: "energy-profile", term: "Energy profile", text: "Electronic energies, no zero-point correction, in kJ/mol with the reactants as zero. The transition state sits at the barrier height (it can be below zero) and the products at the reaction energy." },
    { id: "ts-checks", term: "Transition-state checks", text: "Trust asks how much of the expected evidence for a saddle point was deposited, by a fixed list of checks. IRC asks whether a reaction-path calculation connects it to its reactants and products." },
    { id: "lot-identity", term: "Level-of-theory identity", text: "Two levels can share a method and basis set but differ in dispersion, solvent or spin treatment, so each distinct combination is its own level." },
    { id: "level-carries", term: "What a level carries", text: "Its identity fields, the software seen running it, its energy-correction parameters and its frequency scale factors." },
    { id: "correction-scheme", term: "Correction scheme", text: "Energy corrections deposited against a level of theory, such as per-element or per-bond parameters." },
    { id: "composite-recipe", term: "Composite recipe", text: "The recipe behind a composite energy: a named method such as CBS-QB3, or an extrapolation or sum of single-point energies." },
    { id: "staged-optimisation", term: "Staged optimisation", text: "A coarse optimisation runs first and a finer one refines it from there." },
    { id: "picture", term: "How a molecule is drawn", text: "Bonds are inferred from atom distances; the record stores only atoms and positions. On a conformer's page the picture shows the first final geometry deposited, and other observations may differ slightly." },
    { id: "relative-energy", term: "Relative energies", text: "Shapes are compared in kJ/mol at one level and program, and only if a stored frequency calculation shows each is a minimum. Energies from different programs are not combined." },
    { id: "not-recorded", term: "Not recorded", text: "It means nobody deposited that item. It does not mean a check failed." },
]
