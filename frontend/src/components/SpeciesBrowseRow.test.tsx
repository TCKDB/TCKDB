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

// Design-system adoption: the row's box
// (padding/border/radius/background) comes from the shared `.card`
// primitive, and the ref renders through the shared `.data` step instead of
// its own one-off 11px mono run.
describe("SpeciesBrowseRow: design-system primitive adoption", () => {
    it("the row carries the shared .card primitive alongside its own .species-browse-row class", () => {
        renderRow(record())
        const row = document.querySelector(".species-browse-row") as HTMLElement
        expect(row).toHaveClass("card")
        expect(row).toHaveClass("browse-row")
    })

    it("the row prints no public ref", () => {
        renderRow(record())
        const row = document.querySelector(".species-browse-row") as HTMLElement
        expect(within(row).queryByText("spc_one")).not.toBeInTheDocument()
        expect(row.textContent).not.toMatch(/spc_|spe_/)
        expect(row.querySelector(".browse-ref")).toBeNull()
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
