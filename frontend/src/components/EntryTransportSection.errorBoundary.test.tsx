import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { EntryTransportSection } from "./EntryTransportSection"

// See the identical mock's docstring in
// `EntryStatmechSection.errorBoundary.test.tsx` — same reasoning, same
// import-indirection requirement (an inline `children` render prop cannot
// be `vi.mock`'d; a named export of its own module can). This is a
// per-test-file mock: it does not leak into `EntryStatmechSection`'s own
// tests even though both files import the same
// `./SourceCalculationsTable` module, since vitest gives each test file
// its own module registry.
vi.mock("./SourceCalculationsTable", () => ({
    SourceCalculationsTable: ({ rows }: { rows: Array<{ calculation_ref: string }> | null | undefined }) => {
        if (rows?.some((row) => row.calculation_ref === "calc_poison")) throw new Error("source calc boom")
        return <p>source calculations rendered fine ({rows?.length ?? 0} row{rows?.length === 1 ? "" : "s"})</p>
    },
}))

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const entryRef = "spe_test_ch3"
const ENDPOINT = `/api/v1/scientific/species-entries/${entryRef}/transport`

function page() {
    return render(
        <MemoryRouter>
            <EntryTransportSection entryRef={entryRef} />
        </MemoryRouter>,
    )
}

function baseRecord(overrides: Record<string, unknown> = {}) {
    return {
        transport: {
            transport_ref: "trn_bad",
            scientific_origin: "computed",
            sigma_angstrom: 3.8,
            epsilon_over_k_k: 250.1,
            dipole_debye: null,
            polarizability_angstrom3: null,
            rotational_relaxation: null,
            note: null,
            created_at: "2026-07-21T12:14:32.845900",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        },
        supersession: null,
        species: {
            species_ref: "spc_ch3", species_entry_ref: entryRef, species_entry_label: null,
            canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", charge: 0, multiplicity: 2,
        },
        software_release: null,
        workflow_tool_release: null,
        literature: null,
        evidence_summary: {
            source_calculation_count: 1, has_source_calculations: true, has_lj_parameters: true,
            has_dipole_moment: false, has_polarizability: false, has_rotational_relaxation: false,
            has_literature_source: false,
        },
        available_sections: { has_source_calculations: true, has_review: true },
        ...overrides,
    }
}

function mockResponse(records: unknown[]) {
    return {
        review_summary: { approved: 0, under_review: 0, not_reviewed: records.length, deprecated: 0, rejected: 0, total: records.length },
        records,
        pagination: { offset: 0, limit: 50, returned: records.length, total: records.length, post_collapse_total: records.length },
    }
}

describe("EntryTransportSection — a broken Source calculations row", () => {
    it("isolates the failing row to its own fallback — sibling rows, sibling sections, record cards and the review summary all survive", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            if (includes.includes("source_calculations")) {
                return HttpResponse.json(mockResponse([
                    baseRecord({
                        source_calculations: [{
                            role: "full_transport", calculation_ref: "calc_poison", calculation_type: "sp",
                            quality: "raw", created_at: "2026-07-21T12:00:00", review: { status: "not_reviewed" },
                            level_of_theory: null, software_release: null, workflow_tool_release: null,
                        }],
                    }),
                    baseRecord({
                        transport: { ...baseRecord().transport, transport_ref: "trn_good" },
                        source_calculations: [{
                            role: "full_transport", calculation_ref: "calc_sound", calculation_type: "sp",
                            quality: "raw", created_at: "2026-07-21T12:00:00", review: { status: "not_reviewed" },
                            level_of_theory: null, software_release: null, workflow_tool_release: null,
                        }],
                    }),
                ]))
            }
            return HttpResponse.json(mockResponse([
                baseRecord(),
                baseRecord({ transport: { ...baseRecord().transport, transport_ref: "trn_good" } }),
            ]))
        }))
        const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {})

        page()
        await screen.findByText("trn_bad")
        expect(screen.getByText("trn_good")).toBeVisible()

        fireEvent.click(screen.getByText("Source calculations", { selector: "summary" }))
        const section = screen.getByText("Source calculations", { selector: "summary" }).closest("details") as HTMLElement
        await within(section).findByText("Source calculations loaded.")

        const badRow = within(section).getByText("trn_bad").closest("div.science-record") as HTMLElement
        expect(within(badRow).getByRole("alert")).toHaveTextContent(/This row could not be displayed/)
        expect(within(badRow).queryByText(/rendered fine/)).not.toBeInTheDocument()

        // Sibling row, same lazy section, unaffected.
        const goodRow = within(section).getByText("trn_good").closest("div.science-record") as HTMLElement
        expect(within(goodRow).getByText("source calculations rendered fine (1 row)")).toBeVisible()
        expect(within(goodRow).queryByRole("alert")).not.toBeInTheDocument()

        // Record cards, review summary and the sibling section survive.
        // Scoped to the eager record-list section: "trn_bad"/"trn_good" now
        // also appear as row headings inside the opened disclosure, so an
        // unscoped query would be ambiguous.
        const recordsSection = screen.getByRole("heading", { name: "Transport" }).closest("section") as HTMLElement
        expect(within(recordsSection).getByText("trn_bad")).toBeVisible()
        expect(within(recordsSection).getByText("trn_good")).toBeVisible()
        expect(screen.getByText("2 records · review: 2 not reviewed")).toBeVisible()
        expect(screen.getByText("Review history", { selector: "summary" })).toBeVisible()

        consoleSpy.mockRestore()
    })
})
