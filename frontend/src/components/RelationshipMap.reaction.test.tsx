import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { RelationshipMap } from "./RelationshipMap"
import { useReactionEntryNeighbours } from "../hooks/useNeighbours"
import { ADVERSARIAL, DEPOSITOR_SENTINEL, expectNoAdversarialText, expectNoDepositorText } from "../test/depositorSentinel"

afterEach(cleanup)

// Participants as the wire carries them: the depositor-typed species_entry_label rides along and must never be read.
const REACTANTS = [
    { entryRef: "spe_a", formula: "CH4", smiles: "C", species_entry_label: `${DEPOSITOR_SENTINEL}_species_entry_label_1` },
    { entryRef: "spe_b", formula: null, smiles: "[OH]", species_entry_label: ADVERSARIAL.smilesWord },
]
const PRODUCTS = [
    { entryRef: "spe_c", formula: null, smiles: null, species_entry_label: `${DEPOSITOR_SENTINEL}_species_entry_label_3${ADVERSARIAL.pointGroup}` },
]

function Fixture() {
    const state = useReactionEntryNeighbours({ reactionRef: "rxn_1", reactants: REACTANTS, products: PRODUCTS })
    return <RelationshipMap selfKind="reaction" state={state} />
}

describe("reaction entry map labels", () => {
    it("are built from formula, SMILES or ref, never the depositor's label", () => {
        render(<MemoryRouter><Fixture /></MemoryRouter>)
        expect(screen.getAllByText("CH4").length).toBeGreaterThan(0)
        expect(screen.getAllByText("[OH]").length).toBeGreaterThan(0)
        expect(screen.getAllByText("spe_c").length).toBeGreaterThan(0)
        expectNoDepositorText()
        expectNoAdversarialText()
    })
})
