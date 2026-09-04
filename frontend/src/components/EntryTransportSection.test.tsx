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

/**
 * Binds a `<dt>` label to its own `<dd>` value by DOM adjacency — see the
 * identical helper's docstring in `EntryThermoSection.test.tsx`.
 */
function ddFor(container: HTMLElement, term: string): string {
    const dt = Array.from(container.querySelectorAll("dt")).find((el) => el.textContent === term)
    if (!dt) throw new Error(`No <dt> with text "${term}" found in this container`)
    return dt.nextElementSibling?.textContent ?? ""
}

/**
 * Finds the `<code>` element whose immediately preceding text node
 * contains `precedingText` — see `EntryThermoSection.test.tsx`'s identical
 * helper for why this is necessary to detect a superseded_by/current swap.
 */
function codeAfter(container: HTMLElement, precedingText: string): string {
    const codes = Array.from(container.querySelectorAll("code"))
    for (const code of codes) {
        if ((code.previousSibling?.textContent ?? "").includes(precedingText)) return code.textContent ?? ""
    }
    throw new Error(`No <code> immediately preceded by text containing "${precedingText}" found`)
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

    it("renders a deposited transport record and never hides or reverses a superseded one", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            mockRecord({
                // Distinct refs, chain_length > 1: see the identical note on
                // EntryStatmechSection.test.tsx's fixture — identical refs
                // or chain_length: 1 cannot detect a direction reversal or
                // exercise the "current" sentence at all.
                supersession: {
                    superseded_by: "trn_one_v2", current: "trn_one_v3", reason: "corrected sigma",
                    superseded_at: "2026-08-10T00:00:00", chain_length: 2,
                },
            }),
        ]))))
        page()
        await screen.findByText("trn_one")
        const card = screen.getByText("trn_one").closest("article") as HTMLElement
        expect(within(card).getByText("Superseded")).toBeVisible()
        expect(codeAfter(card, "replaced by")).toBe("trn_one_v2")
        expect(codeAfter(card, "current record in this chain is")).toBe("trn_one_v3")
        // Sigma is now formatted at 3dp (`quantityFormat.ts`'s digits table).
        expect(within(card).getByText("3.800")).toBeVisible()
    })

    it("binds sigma and epsilon/k to their own labelled row — never swapped", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            mockRecord({ transport: { ...mockRecord().transport, sigma_angstrom: 3.8, epsilon_over_k_k: 250.1 } }),
        ]))))
        page()
        const card = (await screen.findByText("trn_one")).closest("article") as HTMLElement
        // Sigma is now formatted at 3dp (`quantityFormat.ts`'s digits table);
        // epsilon/k at 1dp already matched its input's own precision.
        expect(ddFor(card, "Sigma (Å)")).toBe("3.800")
        expect(ddFor(card, "Epsilon / k (K)")).toBe("250.1")
    })

    it("formats dipole at its own 3dp spec, not the 1dp epsilon/k spec", async () => {
        // 1.8523 rounds to "1.852" at 3dp but "1.9" at 1dp -- a table-row
        // swap (using `transport_epsilon_over_k_k` here instead of
        // `transport_dipole_debye`) produces a visibly different string.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            mockRecord({ transport: { ...mockRecord().transport, dipole_debye: 1.8523 } }),
        ]))))
        page()
        const card = (await screen.findByText("trn_one")).closest("article") as HTMLElement
        expect(ddFor(card, "Dipole (Debye)")).toBe("1.852")
    })

    it("renders Software and Workflow through their own label rules, not stuttered and not swapped", async () => {
        // Same rationale as the identical test in EntryStatmechSection.test.tsx.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            mockRecord({
                software_release: { software_release_ref: "srel_1", software: "Gaussian", version: "Gaussian 16, Revision C.02" },
                workflow_tool_release: { workflow_tool_release_ref: "wfr_1", workflow_tool: "ARC", version: "2.1.0" },
            }),
        ]))))
        page()
        const card = (await screen.findByText("trn_one")).closest("article") as HTMLElement
        const softwareLine = within(card).getByText(/^Software:/).closest("p") as HTMLElement
        expect(softwareLine.textContent).toContain("Gaussian 16, Revision C.02")
        expect(softwareLine.textContent).not.toMatch(/Gaussian Gaussian/)
        expect(softwareLine.textContent).toContain("ARC 2.1.0")
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

        const section = screen.getByText("Source calculations", { selector: "summary" }).closest("details") as HTMLDetailsElement
        fireEvent.click(screen.getByText("Source calculations", { selector: "summary" }))
        await within(section).findByText("Source calculations loaded.")
        expect(requestedIncludeSets).toEqual([[], ["source_calculations"]])

        // "Review history" was never opened, so it must never be requested.
        expect(requestedIncludeSets.flat()).not.toContain("review")
    })
})

describe("EntryTransportSection: design-system adoption (design/species-entry)", () => {
    // Same four invariants as `EntryStatmechSection.test.tsx`'s matching
    // block, checked against this tab's own "Source calculations"/"Review
    // history" disclosures and record table.

    it("never renders an <h2>/<h3>/<h4> inside a <summary>", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("trn_one")
        document.querySelectorAll("details > summary").forEach((summary) => fireEvent.click(summary))
        const headingsInsideSummaries = document.querySelectorAll("summary h1, summary h2, summary h3, summary h4, summary h5, summary h6")
        expect(headingsInsideSummaries).toHaveLength(0)
    })

    it("every <details> on this tab is the shared Disclosure component -- carries the `disclosure` class", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("trn_one")
        const detailsElements = Array.from(document.querySelectorAll("details"))
        expect(detailsElements.length).toBeGreaterThan(0)
        for (const details of detailsElements) {
            expect(details.className.split(" ")).toContain("disclosure")
        }
    })

    it("'not reviewed' renders in exactly one pill style -- .value-pill--muted, never the retired .review-badge", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("trn_one")
        const notReviewedPills = screen.getAllByText("not reviewed")
        expect(notReviewedPills.length).toBeGreaterThan(0)
        for (const pill of notReviewedPills) {
            expect(pill.className.split(" ")).toContain("value-pill--muted")
            expect(pill.className.split(" ")).not.toContain("review-badge")
        }
    })

    it("every record table on this tab (Source calculations, Review history) is the shared .data-table primitive inside .table-scroll, never the retired .stage-table or a stacked fallback", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            mockRecord({
                available_sections: { has_source_calculations: true, has_review: true },
                source_calculations: [{
                    role: "opt", calculation_ref: "calc_1", calculation_type: "opt",
                    quality: "primary", created_at: "2026-07-21T12:14:32.845900",
                    review: { status: "not_reviewed" }, level_of_theory: null,
                }],
                review_history: [{ status: "not_reviewed", reviewed_at: null, note: null }],
            }),
        ]))))
        page()
        await screen.findByText("trn_one")
        fireEvent.click(screen.getByText("Source calculations", { selector: "summary" }))
        await screen.findByRole("table", { name: "Source calculations" })
        fireEvent.click(screen.getByText("Review history", { selector: "summary" }))
        await screen.findByRole("table", { name: "Review history" })
        const tables = Array.from(document.querySelectorAll("table"))
        expect(tables.length).toBeGreaterThan(0)
        for (const table of tables) {
            expect(table.className.split(" ")).toContain("data-table")
            expect(table.className.split(" ")).not.toContain("stage-table")
            expect(table.closest(".table-scroll")).not.toBeNull()
        }
    })
})
