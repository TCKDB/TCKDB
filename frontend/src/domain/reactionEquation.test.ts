import { describe, expect, it } from "vitest"
import { buildEquationSides, equationNeedsStructure, equationText, type EquationParticipantInput } from "./reactionEquation"

function participant(overrides: Partial<EquationParticipantInput> & Pick<EquationParticipantInput, "species_entry_ref" | "smiles" | "participant_index">): EquationParticipantInput {
    return { stoichiometry: 1, ...overrides }
}

describe("buildEquationSides", () => {
    it("passes through a single reactant/product on each side unchanged", () => {
        const sides = buildEquationSides(
            [participant({ species_entry_ref: "spe_a", smiles: "O", formula: "H2O", participant_index: 0 })],
            [participant({ species_entry_ref: "spe_b", smiles: "C", formula: "CH4", participant_index: 0 })],
        )
        expect(sides.reactants).toEqual([{ speciesEntryRef: "spe_a", chips: [], smiles: "O", formula: "H2O", coefficient: 1 }])
        expect(sides.products).toEqual([{ speciesEntryRef: "spe_b", chips: [], smiles: "C", formula: "CH4", coefficient: 1 }])
    })

    // The case named in the brief: `[NH2] + [NH2] <=> NN` -- two SEPARATE
    // participant rows sharing one `species_entry_ref` (a depositor writing
    // the equation out atom by atom, stoichiometry 1 each), collapsed into
    // ONE displayed reactant with an EXACT summed coefficient of 2, never a
    // guess and never a re-count of rows independent of each row's own
    // `stoichiometry`.
    it("collapses repeated identical species_entry_refs on one side into one participant with a summed coefficient", () => {
        const sides = buildEquationSides(
            [
                participant({ species_entry_ref: "spe_nh2", smiles: "[NH2]", formula: "H2N", participant_index: 0 }),
                participant({ species_entry_ref: "spe_nh2", smiles: "[NH2]", formula: "H2N", participant_index: 1 }),
            ],
            [participant({ species_entry_ref: "spe_nn", smiles: "NN", formula: "H4N2", participant_index: 0 })],
        )
        expect(sides.reactants).toHaveLength(1)
        expect(sides.reactants[0]).toMatchObject({ speciesEntryRef: "spe_nh2", coefficient: 2 })
        expect(sides.products).toEqual([{ speciesEntryRef: "spe_nn", chips: [], smiles: "NN", formula: "H4N2", coefficient: 1 }])
    })

    it("a single row already carrying stoichiometry 2 collapses to the same shape trivially", () => {
        const sides = buildEquationSides(
            [participant({ species_entry_ref: "spe_nh2", smiles: "[NH2]", formula: "H2N", participant_index: 0, stoichiometry: 2 })],
            [],
        )
        expect(sides.reactants[0]).toMatchObject({ coefficient: 2 })
    })

    it("sums stoichiometry across more than two repeated rows", () => {
        const sides = buildEquationSides(
            [
                participant({ species_entry_ref: "spe_x", smiles: "X", participant_index: 2, stoichiometry: 1 }),
                participant({ species_entry_ref: "spe_x", smiles: "X", participant_index: 0, stoichiometry: 2 }),
                participant({ species_entry_ref: "spe_x", smiles: "X", participant_index: 1, stoichiometry: 1 }),
            ],
            [],
        )
        expect(sides.reactants).toHaveLength(1)
        expect(sides.reactants[0].coefficient).toBe(4)
    })

    it("orders participants by participant_index, not input array order", () => {
        const sides = buildEquationSides(
            [
                participant({ species_entry_ref: "spe_second", smiles: "B", participant_index: 1 }),
                participant({ species_entry_ref: "spe_first", smiles: "A", participant_index: 0 }),
            ],
            [],
        )
        expect(sides.reactants.map((p) => p.speciesEntryRef)).toEqual(["spe_first", "spe_second"])
    })

    it("keeps distinct species on one side as separate participants, uncollapsed", () => {
        const sides = buildEquationSides(
            [
                participant({ species_entry_ref: "spe_a", smiles: "A", participant_index: 0 }),
                participant({ species_entry_ref: "spe_b", smiles: "B", participant_index: 1 }),
            ],
            [],
        )
        expect(sides.reactants).toHaveLength(2)
    })

    it("carries a null formula through and drops species_entry_label", () => {
        const sides = buildEquationSides(
            [participant({ species_entry_ref: "spe_a", smiles: "N=N", formula: null, species_entry_label: "Z", participant_index: 0 })],
            [],
        )
        expect(sides.reactants[0]).toMatchObject({ formula: null, smiles: "N=N" })
        expect(sides.reactants[0]).not.toHaveProperty("speciesEntryLabel")
        expect(sides.reactants[0].chips).toEqual([])
    })

    it("builds chips from the structured facets only: a structured stereo R reads as an enantiomer, a typed state label does not", () => {
        const sides = buildEquationSides(
            [
                participant({ species_entry_ref: "spe_r", smiles: "CC(F)Cl", participant_index: 0, species_entry_kind: "minimum", electronic_state_kind: "ground", stereo_label: "R", stereo_descriptor: "R" }),
                // A depositor typed "R" as the electronic state label: it only reaches the joined label.
                participant({ species_entry_ref: "spe_typed", smiles: "[H]", participant_index: 1, species_entry_label: "R", species_entry_kind: "minimum", electronic_state_kind: "ground" }),
            ],
            [],
        )
        expect(sides.reactants[0].chips).toEqual(["R enantiomer"])
        expect(sides.reactants[1].chips).toEqual([])
    })
})

describe("equationText", () => {
    it("writes formulas with coefficients, falling back to SMILES, and the arrow for reversibility", () => {
        const reactants = [
            participant({ species_entry_ref: "spe_a", smiles: "[NH2]", formula: "NH2", participant_index: 0 }),
            participant({ species_entry_ref: "spe_a", smiles: "[NH2]", formula: "NH2", participant_index: 1 }),
        ]
        const products = [participant({ species_entry_ref: "spe_b", smiles: "NN", participant_index: 0 })]
        expect(equationText(reactants, products, true)).toBe("2 NH2 ⇌ NN")
        expect(equationText(reactants, products, false)).toBe("2 NH2 → NN")
    })
})

describe("equationNeedsStructure", () => {
    const p = (ref: string, formula: string | null, smiles: string, index = 0) => ({ species_entry_ref: ref, formula, smiles, stoichiometry: 1, participant_index: index })
    it("is false when formulas tell every participant apart", () => {
        expect(equationNeedsStructure([p("a", "CH4", "C"), p("b", "HO", "[OH]", 1)], [p("c", "CH3", "[CH3]"), p("d", "H2O", "O", 1)])).toBe(false)
    })
    it("is true when two distinct entries share a formula", () => {
        expect(equationNeedsStructure([p("a", "C4H8", "C/C=C\\C")], [p("b", "C4H8", "C/C=C/C")])).toBe(true)
    })
    it("is true when both sides read the same, even for one entry", () => {
        expect(equationNeedsStructure([p("a", "H2O", "O")], [p("a", "H2O", "O")])).toBe(true)
    })
    it("does not call a repeated occurrence of one entry ambiguous", () => {
        expect(equationNeedsStructure([p("a", "NH2", "[NH2]"), p("a", "NH2", "[NH2]", 1)], [p("b", "N2H4", "NN")])).toBe(false)
    })
})
