import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { bySummaryText } from "../test/disclosureQueries"
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

// Owner decision: "yes show each record's own ref inline". This entry IS
// its own `RecordIdentityHeader` subject, so its own ref reaches the
// header via the `ownRef` prop (not `identity.speciesEntryRef`, which this
// component deliberately omits -- see the component's own comment) and
// must render first, visible at rest, and never duplicated in the
// collapsed References disclosure below it.
describe("EntryIdentity: own ref inline, first, never duplicated in References", () => {
    it("shows the entry's own ref first in the identity block, with the data face and a copy button", () => {
        renderEntry(baseEntry())
        const identityFacts = document.querySelector("dl.kv-list.record-identity-facts") as HTMLElement
        expect(identityFacts).not.toBeNull()
        expect(Array.from(identityFacts.children)[0]).toHaveTextContent("Species entry ref")
        const ownRefValue = within(identityFacts).getByText("spe_demo")
        expect(ownRefValue).toBeVisible()
        expect(ownRefValue.tagName).toBe("CODE")
        expect(ownRefValue).toHaveClass("data")
        expect(within(identityFacts).getByRole("button", { name: /copy species entry ref/i })).toBeVisible()
    })

    it("keeps the entry's own ref out of the collapsed References disclosure -- only the related parent species ref stays there", async () => {
        renderEntry(baseEntry())
        // Collapsed by default: the ref is not visible until opened.
        const summary = screen.getByText(bySummaryText(/References \(1\)/))
        expect(summary).toBeVisible()
        fireEvent.click(summary)
        const disclosure = summary.closest("details") as HTMLElement
        expect(within(disclosure).queryByText("spe_demo")).not.toBeInTheDocument()
        expect(within(disclosure).getByText("spc_demo")).toBeVisible()
        // Exactly one occurrence of the entry's own ref anywhere on the page.
        expect(screen.getAllByText("spe_demo")).toHaveLength(1)
    })
})
