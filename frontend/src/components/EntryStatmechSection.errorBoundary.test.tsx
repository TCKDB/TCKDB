import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { EntryStatmechSection } from "./EntryStatmechSection"

// vitest hoists `vi.mock` calls to the top of the file at transform time
// regardless of where they're written, so `EntryStatmechSection` above
// receives a `TorsionsTable` that throws for any row carrying the sentinel
// `torsion_index: 999` — standing in for a future rendering bug in that one
// lazy section's row body, without needing to construct a real one (every
// field this table touches is zod-validated before the record reaches
// "ready" state — see `api/statmechApi.ts` — so this is not reachable via
// any real archive response today; the isolation mechanism is what's under
// test). The mock is conditional, not unconditional, specifically so this
// test can also prove SIBLING-ROW isolation within the same lazy section,
// not only "the whole page didn't crash": `sm_good`'s torsion row must
// render normally right next to `sm_bad`'s failed one.
//
// Kept in its own file, separate from `EntryStatmechSection.test.tsx`,
// because this mock would otherwise break every other test in that file
// that expects a real Torsions table — mirrors
// `GeometryDetailPage.errorBoundary.test.tsx`'s `GeometryViewer` mock.
//
// This mocks a named export of `./StatmechTorsionsTable`, a module
// SEPARATE from `EntryStatmechSection.tsx`, which imports and renders it as
// `<TorsionsTable rows={rows} />`. That import indirection is what makes
// the mock actually reach the component `StatmechLazySection` renders — an
// inline arrow function passed as a `children` render prop (the shape this
// file's previous version tested against, and the shape the prior review
// round rightly rejected) cannot be targeted by `vi.mock` at all, so
// reverting the extraction would silently stop this file from testing
// anything.
vi.mock("./StatmechTorsionsTable", () => ({
    TorsionsTable: ({ rows }: { rows: Array<{ torsion_index: number }> | null | undefined }) => {
        if (rows?.some((row) => row.torsion_index === 999)) throw new Error("torsions boom")
        return <p>torsions rendered fine ({rows?.length ?? 0} row{rows?.length === 1 ? "" : "s"})</p>
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
            statmech_ref: "sm_bad",
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
        software_release: null,
        workflow_tool_release: null,
        literature: null,
        evidence_summary: {
            source_calculation_count: 3, has_opt_calculation: true, has_freq_calculation: true,
            has_sp_calculation: true, sp_from_optimization: false, has_rotor_scans: true,
            torsion_count: 1, has_frequency_scale_factor: true, has_conformer_context: true,
        },
        available_sections: {
            has_source_calculations: true, has_torsions: true, has_electronic_levels: false,
            has_frequencies: true, has_conformers: true, has_review: true,
        },
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

describe("EntryStatmechSection — a broken Torsions row", () => {
    it("isolates the failing row to its own fallback — sibling rows, sibling sections, record cards and the review summary all survive", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            if (includes.includes("torsions")) {
                return HttpResponse.json(mockResponse([
                    baseRecord({
                        torsions: [{
                            torsion_index: 999, treatment_kind: "hindered_rotor", symmetry_number: 3,
                            dimension: 1, top_description: null, invalidated_reason: null, note: null,
                            source_scan_calculation_ref: null, coordinates: [],
                        }],
                    }),
                    baseRecord({
                        // `point_group` deliberately differs from `sm_bad`'s
                        // "D3h" -- otherwise the two records are
                        // scientifically IDENTICAL and finding 7's grouping
                        // (`identicalRecordGroups.ts`) would fold them into
                        // one "2 records with identical values" card,
                        // defeating this file's whole point: proving
                        // per-row isolation between two SEPARATE cards.
                        statmech: { ...baseRecord().statmech, statmech_ref: "sm_good", point_group: "C2v" },
                        torsions: [{
                            torsion_index: 0, treatment_kind: "hindered_rotor", symmetry_number: 3,
                            dimension: 1, top_description: null, invalidated_reason: null, note: null,
                            source_scan_calculation_ref: null, coordinates: [],
                        }],
                    }),
                ]))
            }
            return HttpResponse.json(mockResponse([
                baseRecord(),
                baseRecord({ statmech: { ...baseRecord().statmech, statmech_ref: "sm_good", point_group: "C2v" } }),
            ]))
        }))
        // SectionErrorBoundary's own componentDidCatch logs to console.error;
        // that is expected here and not itself under test.
        const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {})

        page()
        await screen.findByText("sm_bad")
        expect(screen.getByText("sm_good")).toBeVisible()

        fireEvent.click(screen.getByRole("heading", { name: "Torsions" }))
        const torsionsSection = screen.getByRole("heading", { name: "Torsions" }).closest("details") as HTMLElement
        await within(torsionsSection).findByText("Torsions loaded.")

        // The failing row's own fallback — not a page-wide crash message.
        const badRow = within(torsionsSection).getByText("sm_bad").closest("div.science-record") as HTMLElement
        expect(within(badRow).getByRole("alert")).toHaveTextContent(/This row could not be displayed/)
        expect(within(badRow).queryByText(/torsions rendered fine/)).not.toBeInTheDocument()

        // The SIBLING row, in the SAME lazy section, is unaffected — this
        // is what proves per-row isolation rather than "the crash just
        // happened not to propagate this time".
        const goodRow = within(torsionsSection).getByText("sm_good").closest("div.science-record") as HTMLElement
        expect(within(goodRow).getByText("torsions rendered fine (1 row)")).toBeVisible()
        expect(within(goodRow).queryByRole("alert")).not.toBeInTheDocument()

        // Before LazyRowBody existed, a throw here would have unmounted the
        // entire tab: both record cards, the review summary, and every
        // sibling lazy section — reproducing the slice-4 defect one level
        // up. None of that happened. Scoped to the eager record-list
        // section: "sm_bad"/"sm_good" now also appear as row headings
        // inside the opened Torsions disclosure, so an unscoped query would
        // be ambiguous.
        const recordsSection = screen.getByRole("heading", { name: "Statistical mechanics" }).closest("section") as HTMLElement
        expect(within(recordsSection).getByText("sm_bad")).toBeVisible()
        expect(within(recordsSection).getByText("sm_good")).toBeVisible()
        expect(screen.getByText("2 records · review: 2 not reviewed")).toBeVisible()
        expect(screen.getByRole("heading", { name: "Source calculations" })).toBeVisible()
        // "Electronic levels" has no record on this entry (both fixtures
        // set `has_electronic_levels: false`) -- it collapses to one line,
        // no heading (finding 6), not a full section over a dashed empty
        // box.
        expect(screen.queryByRole("heading", { name: "Electronic levels" })).not.toBeInTheDocument()
        expect(screen.getByText("No electronic levels are recorded for any statmech record on this entry.")).toBeVisible()
        // "Frequencies" is no longer its own global section at all -- it
        // moved onto each record card (finding 6's `FrequenciesBlock`).
        expect(screen.queryByRole("heading", { name: "Frequencies" })).not.toBeInTheDocument()
        expect(screen.getByRole("heading", { name: "Conformer context" })).toBeVisible()
        expect(screen.getByRole("heading", { name: "Review history" })).toBeVisible()

        consoleSpy.mockRestore()
    })
})
