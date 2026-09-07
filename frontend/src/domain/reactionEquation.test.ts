import { describe, expect, it } from "vitest"
import { buildEquationSides, type EquationParticipantInput } from "./reactionEquation"

function participant(overrides: Partial<EquationParticipantInput> & Pick<EquationParticipantInput, "species_entry_ref" | "smiles" | "participant_index">): EquationParticipantInput {
    return { stoichiometry: 1, ...overrides }
}

describe("buildEquationSides", () => {
    it("passes through a single reactant/product on each side unchanged", () => {
        const sides = buildEquationSides(
            [participant({ species_entry_ref: "spe_a", smiles: "O", formula: "H2O", participant_index: 0 })],
            [participant({ species_entry_ref: "spe_b", smiles: "C", formula: "CH4", participant_index: 0 })],
        )
        expect(sides.reactants).toEqual([{ speciesEntryRef: "spe_a", speciesEntryLabel: null, smiles: "O", formula: "H2O", coefficient: 1 }])
        expect(sides.products).toEqual([{ speciesEntryRef: "spe_b", speciesEntryLabel: null, smiles: "C", formula: "CH4", coefficient: 1 }])
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
        expect(sides.products).toEqual([{ speciesEntryRef: "spe_nn", speciesEntryLabel: null, smiles: "NN", formula: "H4N2", coefficient: 1 }])
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

    it("carries species_entry_label and null-formula through unchanged", () => {
        const sides = buildEquationSides(
            [participant({ species_entry_ref: "spe_a", smiles: "N=N", formula: null, species_entry_label: "Z", participant_index: 0 })],
            [],
        )
        expect(sides.reactants[0]).toMatchObject({ formula: null, speciesEntryLabel: "Z", smiles: "N=N" })
    })
})
