import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { EntryIdentity } from "./SpeciesEntrySummary"

afterEach(cleanup)

function baseEntry(overrides: Partial<SpeciesEntryProjection> = {}): SpeciesEntryProjection {
    return {
        species_entry_ref: "spe_demo",
        species_entry_kind: "minimum",
        electronic_state_kind: "ground",
        review: { status: "not_reviewed" },
        availability: {
            has_thermo: false, has_statmech: false, has_transport: false,
            has_conformers: true, calculation_count: 1,
        },
        speciesRef: "spc_demo",
        canonicalSmiles: "[CH3]",
        inchiKey: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
        formula: "CH3",
        charge: 0,
        multiplicity: 2,
        ...overrides,
    } as SpeciesEntryProjection
}

function renderEntry(entry: SpeciesEntryProjection) {
    return render(<MemoryRouter><EntryIdentity entry={entry} /></MemoryRouter>)
}

describe("EntryIdentity: no pill boxes, every fact exactly once", () => {
    it("states the electronic state exactly once -- not the old state-chip AND the fact row AND a pill row", () => {
        renderEntry(baseEntry())
        // "ground" names this entry's electronic state once, in the
        // labelled "Entry kind / state" row. A mutation reintroducing the
        // retired `.state-chip` beside the <h1>, or a `RecordFacetChips`
        // pill row, would make this find more than one.
        expect(screen.getAllByText(/ground/i)).toHaveLength(1)
        expect(document.querySelector(".state-chip")).not.toBeInTheDocument()
        expect(document.querySelector(".record-facet-chips")).not.toBeInTheDocument()
    })

    it("renders a labelled Stereochemistry row, worded as 'E isomer' (not a bare 'E'), when stereo_label is set", () => {
        // The owner's own report: "why ... does it not show ... E isomer
        // like it does for Review etc. but shows the pill box of it (which
        // I want gone)" -- measured against spe_n5nt4fz3ztsfh2otwlyyvvl2je.
        renderEntry(baseEntry({ stereo_label: "E" }))
        const facts = screen.getByRole("list", { name: "Record facts" })
        const dt = within(facts).getByText("Stereochemistry")
        expect(dt.nextElementSibling).toHaveTextContent("E isomer")
    })

    it("renders no Stereochemistry row at all when stereo_label is null -- absent stereochemistry, not 'not recorded'", () => {
        renderEntry(baseEntry({ stereo_label: null }))
        expect(screen.queryByText("Stereochemistry")).not.toBeInTheDocument()
    })

    it("renders Term symbol and Isotopologue rows only when the entry carries them", () => {
        const { rerender } = renderEntry(baseEntry())
        expect(screen.queryByText("Term symbol")).not.toBeInTheDocument()
        expect(screen.queryByText("Isotopologue")).not.toBeInTheDocument()

        rerender(
            <MemoryRouter>
                <EntryIdentity entry={baseEntry({ term_symbol: "T1", isotope_key: "13C1" })} />
            </MemoryRouter>,
        )
        const facts = screen.getByRole("list", { name: "Record facts" })
        expect(within(facts).getByText("Term symbol").nextElementSibling).toHaveTextContent("T1")
        expect(within(facts).getByText("Isotopologue").nextElementSibling).toHaveTextContent("13C1")
    })
})
