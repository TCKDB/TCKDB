import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { EntryStatmechSection } from "./EntryStatmechSection"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const entryRef = "spe_test_ch3"
const ENDPOINT = `/api/v1/scientific/species-entries/${entryRef}/statmech`

function page() {
    return render(
        <MemoryRouter>
            <EntryStatmechSection entryRef={entryRef} />
        </MemoryRouter>,
    )
}

function baseRecord(overrides: Record<string, unknown> = {}) {
    return {
        statmech: {
            statmech_ref: "sm_one",
            scientific_origin: "computed",
            statmech_treatment: "rrho",
            rigid_rotor_kind: "asymmetric_top",
            point_group: "D3h",
            external_symmetry: 6,
            is_linear: false,
            uses_projected_frequencies: null,
            optical_isomers: 1,
            rotational_constant_a_cm1: null,
            rotational_constant_b_cm1: null,
            rotational_constant_c_cm1: null,
            frequency_scale_factor_value: 0.999,
            note: null,
            created_at: "2026-07-21T12:14:32.845900",
            review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        },
        supersession: null,
        species: {
            species_ref: "spc_ch3", species_entry_ref: entryRef, species_entry_label: null,
            canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", charge: 0, multiplicity: 2,
        },
        transition_state: null,
        frequency_scale_factor: null,
        software_release: { software_release_ref: "srel_1", software: "Arkane", version: null },
        workflow_tool_release: { workflow_tool_release_ref: "wfr_1", workflow_tool: "ARC", version: "1.1.0" },
        literature: null,
        evidence_summary: {
            source_calculation_count: 3, has_opt_calculation: true, has_freq_calculation: true,
            has_sp_calculation: true, sp_from_optimization: false, has_rotor_scans: false,
            torsion_count: 0, has_frequency_scale_factor: true, has_conformer_context: true,
        },
        available_sections: {
            has_source_calculations: true, has_torsions: false, has_electronic_levels: false,
            has_frequencies: true, has_conformers: true, has_review: true,
        },
        ...overrides,
    }
}

/**
 * Two records that deliberately DIFFER in `available_sections.has_torsions`
 * (`sm_one`: false, `sm_two`: true — with a real torsion row) — a fixture
 * with all records agreeing on this flag could not distinguish "records
 * genuinely differ" from "the flag is hardcoded", which is exactly the
 * defect issue #268 found on the conformer surface. `sm_two` is also given
 * a non-null supersession so that path is covered too.
 */
function mockRecords() {
    return [
        baseRecord(),
        baseRecord({
            statmech: {
                ...baseRecord().statmech,
                statmech_ref: "sm_two",
                scientific_origin: "estimated",
            },
            supersession: {
                superseded_by: "sm_two_v2", current: "sm_two_v2", reason: "refit with new frequencies",
                superseded_at: "2026-08-10T00:00:00", chain_length: 1,
            },
            available_sections: {
                has_source_calculations: true, has_torsions: true, has_electronic_levels: false,
                has_frequencies: true, has_conformers: false, has_review: true,
            },
        }),
    ]
}

function mockResponse(records = mockRecords()) {
    return {
        review_summary: { approved: 0, under_review: 0, not_reviewed: 1, deprecated: 0, rejected: 0, total: 2 },
        records,
        pagination: { offset: 0, limit: 50, returned: records.length, total: records.length, post_collapse_total: records.length },
    }
}

describe("EntryStatmechSection", () => {
    it("renders every deposited record independently, without merging or picking one", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("sm_one")
        expect(screen.getByText("sm_one")).toBeVisible()
        expect(screen.getByText("sm_two")).toBeVisible()
    })

    it("never hides a superseded record", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("sm_two")
        const twoCard = screen.getByText("sm_two").closest("article") as HTMLElement
        expect(within(twoCard).getByText("Superseded")).toBeVisible()
        expect(within(twoCard).getByText(/sm_two_v2/)).toBeVisible()

        const oneCard = screen.getByText("sm_one").closest("article") as HTMLElement
        expect(within(oneCard).queryByText("Superseded")).not.toBeInTheDocument()
    })

    it("renders an available on-demand section as idle until opened, and fetches exactly its own token once", async () => {
        const requestedIncludeSets: string[][] = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            requestedIncludeSets.push(new URL(request.url).searchParams.getAll("include"))
            return HttpResponse.json(mockResponse())
        }))
        page()
        await screen.findByText("sm_one")
        expect(requestedIncludeSets).toEqual([[]])

        const section = screen.getByRole("heading", { name: "Torsions" }).closest("details") as HTMLDetailsElement
        expect(section.open).toBe(false)
        expect(within(section).getByText("Expand to load this section from the archive.")).toBeInTheDocument()

        fireEvent.click(screen.getByRole("heading", { name: "Torsions" }))
        await within(section).findByText("Torsions loaded.")
        expect(requestedIncludeSets).toEqual([[], ["torsions"]])
    })

    it("shares one fetch across every record's disclosure for the same token, and never merges one record's rows into another's", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            if (includes.includes("torsions")) {
                return HttpResponse.json(mockResponse([
                    baseRecord({ torsions: [] }),
                    baseRecord({
                        statmech: { ...baseRecord().statmech, statmech_ref: "sm_two" },
                        available_sections: { ...baseRecord().available_sections, has_torsions: true },
                        torsions: [{
                            torsion_index: 0, treatment_kind: "hindered_rotor", symmetry_number: 3,
                            dimension: 1, top_description: null, invalidated_reason: null, note: null,
                            source_scan_calculation_ref: "calc_scan_1", coordinates: [],
                        }],
                    }),
                ]))
            }
            return HttpResponse.json(mockResponse())
        }))
        page()
        await screen.findByText("sm_one")

        fireEvent.click(screen.getByRole("heading", { name: "Torsions" }))
        const section = screen.getByRole("heading", { name: "Torsions" }).closest("details") as HTMLDetailsElement
        await within(section).findByText("Torsions loaded.")

        // sm_one's own row within the Torsions section reports "not
        // present" (its own available_sections.has_torsions is false),
        // never sm_two's torsion row.
        const oneRow = within(section).getByText("sm_one").closest("div.science-record") as HTMLElement
        expect(within(oneRow).getByText("Not present for this record.")).toBeVisible()
        expect(within(oneRow).queryByText("hindered rotor")).not.toBeInTheDocument()

        const twoRow = within(section).getByText("sm_two").closest("div.science-record") as HTMLElement
        expect(within(twoRow).getByText("hindered rotor")).toBeVisible()
        expect(within(twoRow).getByText("calc_scan_1")).toBeVisible()
    })

    it("renders a heavy section as a static, request-free line when no record on the entry has it", async () => {
        let requestCount = 0
        server.use(http.get(ENDPOINT, () => {
            requestCount += 1
            return HttpResponse.json(mockResponse())
        }))
        page()
        await screen.findByText("sm_one")
        // Neither sm_one nor sm_two has electronic levels.
        const heading = screen.getByRole("heading", { name: "Electronic levels" })
        expect(heading.closest("details")).toBeNull()
        expect(screen.getByText("No electronic levels are recorded for any statmech record on this entry.")).toBeVisible()
        expect(requestCount).toBe(1)
    })

    it("states honestly when no statmech records are deposited for this entry", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([]))))
        page()
        expect(await screen.findByText("No statistical-mechanics records are deposited for this entry.")).toBeVisible()
    })
})
