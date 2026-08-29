import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { EntryTransportSection } from "./EntryTransportSection"

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

function mockRecord(overrides: Record<string, unknown> = {}) {
    return {
        transport: {
            transport_ref: "trn_one",
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
        available_sections: { has_source_calculations: true, has_review: false },
        ...overrides,
    }
}

function mockResponse(records: unknown[] = [mockRecord()]) {
    return {
        review_summary: { approved: 0, under_review: 0, not_reviewed: records.length, deprecated: 0, rejected: 0, total: records.length },
        records,
        pagination: { offset: 0, limit: 50, returned: records.length, total: records.length, post_collapse_total: records.length },
    }
}

describe("EntryTransportSection", () => {
    it("the live zero-transport case: a 200 with an empty list is a real answer, not a load failure", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([]))))
        page()
        expect(await screen.findByText(
            "No transport records are deposited for this entry. This is the archive's own answer — "
            + "not a failed request — so nothing further will load if you retry.",
        )).toBeVisible()
        // Never the vocabulary RecordStatus uses for a genuine failure.
        expect(screen.queryByRole("alert")).not.toBeInTheDocument()
        expect(screen.queryByText(/could not load/i)).not.toBeInTheDocument()
        expect(screen.queryByText(/unavailable/i)).not.toBeInTheDocument()
    })

    it("a genuine load failure reads entirely differently from the empty-list case", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json({ detail: "archive down" }, { status: 503 })))
        page()
        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent("Transport unavailable")
        expect(screen.queryByText("No transport records are deposited for this entry.")).not.toBeInTheDocument()
    })

    it("renders a deposited transport record and never hides a superseded one", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            mockRecord({
                supersession: {
                    superseded_by: "trn_one_v2", current: "trn_one_v2", reason: "corrected sigma",
                    superseded_at: "2026-08-10T00:00:00", chain_length: 1,
                },
            }),
        ]))))
        page()
        await screen.findByText("trn_one")
        expect(screen.getByText("Superseded")).toBeVisible()
        expect(screen.getByText(/trn_one_v2/)).toBeVisible()
        expect(screen.getByText("3.8")).toBeVisible()
    })

    it("fetches an opened on-demand section's own token only, once", async () => {
        const requestedIncludeSets: string[][] = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            requestedIncludeSets.push(new URL(request.url).searchParams.getAll("include"))
            return HttpResponse.json(mockResponse())
        }))
        page()
        await screen.findByText("trn_one")
        expect(requestedIncludeSets).toEqual([[]])

        const section = screen.getByRole("heading", { name: "Source calculations" }).closest("details") as HTMLDetailsElement
        fireEvent.click(screen.getByRole("heading", { name: "Source calculations" }))
        await within(section).findByText("Source calculations loaded.")
        expect(requestedIncludeSets).toEqual([[], ["source_calculations"]])

        // "Review history" was never opened, so it must never be requested.
        expect(requestedIncludeSets.flat()).not.toContain("review")
    })
})
