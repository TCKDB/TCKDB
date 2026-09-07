import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { ReactionEquation } from "./ReactionEquation"
import type { EquationParticipantInput } from "../domain/reactionEquation"

afterEach(() => cleanup())

function participant(overrides: Partial<EquationParticipantInput> & Pick<EquationParticipantInput, "species_entry_ref" | "smiles" | "participant_index">): EquationParticipantInput {
    return { stoichiometry: 1, ...overrides }
}

function renderEquation(reactants: EquationParticipantInput[], products: EquationParticipantInput[], reversible: boolean) {
    return render(
        <MemoryRouter>
            <h1><ReactionEquation reactants={reactants} products={products} reversible={reversible} /></h1>
        </MemoryRouter>,
    )
}

describe("ReactionEquation", () => {
    it("renders each participant's formula with subscripts when formula is served", () => {
        const { container } = renderEquation(
            [participant({ species_entry_ref: "spe_a", smiles: "O", formula: "H2O", participant_index: 0 })],
            [participant({ species_entry_ref: "spe_b", smiles: "C", formula: "CH4", participant_index: 0 })],
            false,
        )
        const link = container.querySelector('a[href="/species-entries/spe_a"]')!
        expect(link.querySelector("sub")?.textContent).toBe("2")
        expect(link.textContent).toBe("H2O")
    })

    // The brief's own case: formula absent -> the participant's SMILES,
    // never the bare species-entry ref, in the data face.
    it("falls back to the SMILES (not the ref) in a data-face code element when formula is absent", () => {
        const { container } = renderEquation(
            [participant({ species_entry_ref: "spe_a", smiles: "[N-]=[NH2+]", formula: null, participant_index: 0 })],
            [participant({ species_entry_ref: "spe_b", smiles: "C", formula: "CH4", participant_index: 0 })],
            false,
        )
        const link = container.querySelector('a[href="/species-entries/spe_a"]')!
        const code = link.querySelector("code.data")
        expect(code).not.toBeNull()
        expect(code!.textContent).toBe("[N-]=[NH2+]")
        expect(link.textContent).not.toContain("spe_a")
    })

    it("shows a leading coefficient only when greater than 1, from the collapsed equation sides", () => {
        const { container } = renderEquation(
            [
                participant({ species_entry_ref: "spe_nh2", smiles: "[NH2]", formula: "H2N", participant_index: 0 }),
                participant({ species_entry_ref: "spe_nh2", smiles: "[NH2]", formula: "H2N", participant_index: 1 }),
            ],
            [participant({ species_entry_ref: "spe_nn", smiles: "NN", formula: "H4N2", participant_index: 0 })],
            true,
        )
        expect(container.textContent).toContain("2 H2N")
        // The product side has coefficient 1 -- no leading "1 ".
        expect(container.textContent).not.toContain("1 H4N2")
    })

    it("uses <wbr> after the + and after the arrow, with a non-breaking space before each", () => {
        const { container } = renderEquation(
            [
                participant({ species_entry_ref: "spe_a", smiles: "O", formula: "H2O", participant_index: 0 }),
                participant({ species_entry_ref: "spe_b", smiles: "[CH3]", formula: "CH3", participant_index: 1 }),
            ],
            [participant({ species_entry_ref: "spe_c", smiles: "C", formula: "CH4", participant_index: 0 })],
            true,
        )
        const wbrs = container.querySelectorAll("wbr")
        // One after the "+" between the two reactants, one after the arrow.
        expect(wbrs.length).toBe(2)
        expect(container.textContent).toContain(" +")
    })

    // Post-review fix: `SpeciesEntryLink` (used everywhere ELSE in the app)
    // renders its optional stereo-label suffix as bare, unwrapped text --
    // fine in a normal-size `<dd>`, but MEASURED to inherit the full 36px
    // `--type-display-2` h1 size here and read as the single most
    // prominent text on the chooser page ("· Z isomer" outweighing the
    // chemistry itself). This component builds its own markup instead so
    // the chip is a real, CSS-targetable element.
    it("wraps a served stereo label in .reaction-equation-chip (formula present)", () => {
        const { container } = renderEquation(
            [participant({ species_entry_ref: "spe_a", smiles: "N=N", formula: "H2N2", species_entry_label: "Z", participant_index: 0 })],
            [participant({ species_entry_ref: "spe_b", smiles: "C", formula: "CH4", participant_index: 0 })],
            false,
        )
        const link = container.querySelector('a[href="/species-entries/spe_a"]')!
        const chip = link.querySelector(".reaction-equation-chip")
        expect(chip).not.toBeNull()
        expect(chip!.textContent).toBe(" · Z isomer")
        // The chip is a SIBLING of the formula, not swallowing it.
        expect(link.querySelector("sub")?.textContent).toBe("2")
    })

    it("wraps a served stereo label in .reaction-equation-chip (formula absent, SMILES fallback)", () => {
        const { container } = renderEquation(
            [participant({ species_entry_ref: "spe_a", smiles: "N=N", formula: null, species_entry_label: "Z", participant_index: 0 })],
            [participant({ species_entry_ref: "spe_b", smiles: "C", formula: "CH4", participant_index: 0 })],
            false,
        )
        const link = container.querySelector('a[href="/species-entries/spe_a"]')!
        expect(link.querySelector(".reaction-equation-chip")?.textContent).toBe(" · Z isomer")
        expect(link.querySelector("code.data")?.textContent).toBe("N=N")
    })

    it("renders ⇌ with a reversible aria-label when reversible, → with a non-reversible one otherwise", () => {
        const reversible = renderEquation(
            [participant({ species_entry_ref: "spe_a", smiles: "O", formula: "H2O", participant_index: 0 })],
            [participant({ species_entry_ref: "spe_b", smiles: "C", formula: "CH4", participant_index: 0 })],
            true,
        )
        expect(reversible.container.textContent).toContain("⇌")
        expect(reversible.container.querySelector('[aria-label="reacts reversibly with"]')).not.toBeNull()
        reversible.unmount()

        const oneWay = renderEquation(
            [participant({ species_entry_ref: "spe_a", smiles: "O", formula: "H2O", participant_index: 0 })],
            [participant({ species_entry_ref: "spe_b", smiles: "C", formula: "CH4", participant_index: 0 })],
            false,
        )
        expect(oneWay.container.textContent).toContain("→")
        expect(oneWay.container.querySelector('[aria-label="reacts to form"]')).not.toBeNull()
    })
})
