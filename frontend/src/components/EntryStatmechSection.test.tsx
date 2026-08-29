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
            // Distinct superseded_by/current refs and chain_length > 1: a
            // fixture where both pointers are the SAME string cannot detect
            // a direction reversal even in principle (both mutation
            // outcomes read identically), and chain_length: 1 would never
            // render the "current" sentence at all (SupersessionNotice only
            // renders it when chain_length > 1) — see EntryThermoSection's
            // thm_beta fixture, which this now matches.
            supersession: {
                superseded_by: "sm_two_v2", current: "sm_two_v3", reason: "refit with new frequencies",
                superseded_at: "2026-08-10T00:00:00", chain_length: 2,
            },
            available_sections: {
                has_source_calculations: true, has_torsions: true, has_electronic_levels: false,
                has_frequencies: true, has_conformers: false, has_review: true,
            },
        }),
    ]
}

/**
 * Binds a `<dt>` label to its own `<dd>` value by DOM adjacency — see the
 * identical helper's docstring in `EntryThermoSection.test.tsx` for why a
 * presence-only text query cannot tell a correctly-labelled row from a
 * swapped one, and why `getByRole("term", ...)` does not work here either.
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

/**
 * Reads the text of the `<td data-label="...">` cell within `row` matching
 * `label` — column-scoped, not row-scoped. `within(row).getByText(value)`
 * only proves a value is somewhere in the row; it cannot tell a value under
 * its correct column from the same value under a swapped one (e.g. Top and
 * Invalidated transposed). See the identical helper in
 * `EntryThermoSection.test.tsx`.
 */
function cellAt(row: HTMLElement, label: string): string {
    const cell = row.querySelector(`td[data-label="${label}"]`)
    if (!cell) throw new Error(`No <td data-label="${label}"> found in this row`)
    return cell.textContent ?? ""
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

    it("never hides a superseded record, and never swaps the direction of the correction pointer", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("sm_two")
        const twoCard = screen.getByText("sm_two").closest("article") as HTMLElement
        expect(within(twoCard).getByText("Superseded")).toBeVisible()
        // Position-bound: superseded_by must be the ref in the "replaced
        // by" sentence, current must be the ref in the "current record in
        // this chain is" sentence — a direction swap is invisible to a
        // presence-only "does sm_two_v2 appear somewhere" check, since both
        // refs are present on the card either way.
        expect(codeAfter(twoCard, "replaced by")).toBe("sm_two_v2")
        expect(codeAfter(twoCard, "current record in this chain is")).toBe("sm_two_v3")

        const oneCard = screen.getByText("sm_one").closest("article") as HTMLElement
        expect(within(oneCard).queryByText("Superseded")).not.toBeInTheDocument()
    })

    it("binds each record's external symmetry and optical isomer count to their own labelled row — never swapped", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({ statmech: { ...baseRecord().statmech, external_symmetry: 6, optical_isomers: 1 } }),
        ]))))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        expect(ddFor(card, "External symmetry")).toBe("6")
        expect(ddFor(card, "Optical isomers")).toBe("1")
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

    it("renders invalidated_reason as its own column, never silently dropped — a torsion the archive marked invalid must not look identical to a sound one", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            if (includes.includes("torsions")) {
                return HttpResponse.json(mockResponse([
                    baseRecord({
                        available_sections: { ...baseRecord().available_sections, has_torsions: true },
                        // Top is DELIBERATELY distinct between the two rows
                        // (not both "methyl") so a Top<->Invalidated column
                        // swap is observable by value, not merely by
                        // presence — a fixture where both rows shared one
                        // Top value could not tell a swap apart from the
                        // correct rendering.
                        torsions: [
                            {
                                torsion_index: 0, treatment_kind: "hindered_rotor", symmetry_number: 3,
                                dimension: 1, top_description: "methyl-sound", invalidated_reason: null, note: null,
                                source_scan_calculation_ref: "calc_scan_sound", coordinates: [],
                            },
                            {
                                torsion_index: 1, treatment_kind: "hindered_rotor", symmetry_number: 3,
                                dimension: 1, top_description: "methyl-bad", invalidated_reason: "scan did not close",
                                note: null, source_scan_calculation_ref: "calc_scan_bad", coordinates: [],
                            },
                        ],
                    }),
                ]))
            }
            return HttpResponse.json(mockResponse([baseRecord({
                available_sections: { ...baseRecord().available_sections, has_torsions: true },
            })]))
        }))
        page()
        await screen.findByText("sm_one")
        fireEvent.click(screen.getByRole("heading", { name: "Torsions" }))
        const section = screen.getByRole("heading", { name: "Torsions" }).closest("details") as HTMLDetailsElement
        await within(section).findByText("Torsions loaded.")

        const table = within(section).getByRole("table", { name: "Torsions" })
        const rows = within(table).getAllByRole("row").slice(1)
        const soundRow = rows.find((row) => within(row).queryByText("calc_scan_sound"))!
        const invalidRow = rows.find((row) => within(row).queryByText("calc_scan_bad"))!
        // Column-scoped, not row-scoped: binds each value to its OWN
        // column via `data-label`, so a Top<->Invalidated column swap
        // (values transposed, headers untouched) is caught, not just a
        // "does this text appear somewhere in the row" check.
        expect(cellAt(soundRow, "Top")).toBe("methyl-sound")
        expect(cellAt(soundRow, "Invalidated")).toBe("Not invalidated")
        expect(cellAt(invalidRow, "Top")).toBe("methyl-bad")
        expect(cellAt(invalidRow, "Invalidated")).toBe("scan did not close")
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

    it("states honestly when no statmech records are deposited for this entry, without noise from six separate empty lazy sections", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([]))))
        page()
        expect(await screen.findByText("No statistical-mechanics records are deposited for this entry.")).toBeVisible()
        // Pins `StatmechLazySection`'s `if (records.length === 0) return
        // null` guard: without it, an entry with zero statmech records
        // would ALSO show six redundant "No X are recorded for any
        // statmech record on this entry" sections beneath the one honest
        // empty line above — this asserts that noise is absent.
        expect(screen.queryByRole("heading", { name: "Source calculations" })).not.toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Torsions" })).not.toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Electronic levels" })).not.toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Frequencies" })).not.toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Conformer context" })).not.toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Review history" })).not.toBeInTheDocument()
    })
})
