import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { SpeciesBrowseRecord } from "../api/browseApi"
import { SpeciesBrowseRow } from "./SpeciesBrowseRow"

afterEach(cleanup)

function renderRow(record: SpeciesBrowseRecord) {
    return render(
        <MemoryRouter>
            <ul>
                <SpeciesBrowseRow record={record} />
            </ul>
        </MemoryRouter>,
    )
}

function record(overrides: Partial<SpeciesBrowseRecord> = {}): SpeciesBrowseRecord {
    return {
        species_ref: "spc_one",
        canonical_smiles: "[CH3]",
        formula: "CH3",
        charge: 0,
        multiplicity: 2,
        entries: [{
            species_entry_ref: "spe_one",
            species_entry_kind: "minimum",
            electronic_state_kind: "ground",
            review: { status: "not_reviewed" },
        }],
        ...overrides,
    } as SpeciesBrowseRecord
}

// PR D (design-system adoption on the index/record pages): the row's box
// (padding/border/radius/background) now comes from the shared `.card`
// primitive, and the ref renders through the shared `.data` step instead of
// its own one-off 11px mono run.
describe("SpeciesBrowseRow: design-system primitive adoption", () => {
    it("the row carries the shared .card primitive alongside its own .species-browse-row class", () => {
        renderRow(record())
        const row = document.querySelector(".species-browse-row") as HTMLElement
        expect(row).toHaveClass("card")
        expect(row).toHaveClass("browse-row")
    })

    it("the ref renders through .browse-ref and .data (the shared data step), not the retired .browse-row-ref", () => {
        renderRow(record())
        const row = document.querySelector(".species-browse-row") as HTMLElement
        const ref = within(row).getByText("spc_one")
        expect(ref).toHaveClass("browse-ref")
        expect(ref).toHaveClass("data")
        expect(ref).not.toHaveClass("browse-row-ref")
    })

    it("the review-status pill uses the shared .value-pill--muted variant, alongside the classification .value-pill", () => {
        renderRow(record())
        const row = document.querySelector(".species-browse-row") as HTMLElement
        const kindPill = within(row).getByText("minimum · ground")
        expect(kindPill.closest(".value-pill")).toBeTruthy()
        const reviewPill = within(row).getByText("not reviewed")
        expect(reviewPill).toHaveClass("value-pill")
        expect(reviewPill).toHaveClass("value-pill--muted")
    })
})
