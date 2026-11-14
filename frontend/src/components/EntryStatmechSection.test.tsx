import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { EntryStatmechSection } from "./EntryStatmechSection"
import { ADVERSARIAL, expectNoAdversarialText, expectNoDepositorText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
// `design-system.css` is normally reached only via `index.css`'s `@import`
// -- see `ReactionEntryPage.test.tsx`'s identical import for why a
// component test rendered in isolation needs it directly to exercise
// `.t-preserve-case`'s real computed style.
import "../design-system.css"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const entryRef = "spe_test_ch3"
const ENDPOINT = `/api/v1/scientific/species-entries/${entryRef}/statmech`

function page(conformer?: ConformerProjection, conformers?: ConformerProjection[]) {
    return render(
        <MemoryRouter>
            <EntryStatmechSection entryRef={entryRef} conformer={conformer} conformers={conformers} />
        </MemoryRouter>,
    )
}

// Three conformer groups -- the minimum that can prove a record naming all
// three files under whichever ONE is selected, not always the first.
function conformerGroup(ref: string, label: string): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: ref, label },
        observations_summary: { total: 1 },
        evidence_summary: {
            calculation_count: 1, optimization_chain_count: 1, geometry_count: 1,
            evidence_coverage: { opt: 1, freq: 1, sp: 1 }, levels_of_theory: {},
        },
        observations: [], calculations: [], geometries: [],
    } as unknown as ConformerProjection
}
const conformerGroups = [conformerGroup("cg_one", "conformer_1"), conformerGroup("cg_two", "conformer_2"), conformerGroup("cg_three", "conformer_3")]

/**
 * A conformer group whose ONE observation actually carries calculation
 * rows -- unlike the bare `conformerGroup()` fixture above (empty
 * `observations`), this is what `deriveStatmechConformer`
 * (`domain/statmechConformerDerivation.ts`) needs to resolve a statmech
 * record's source-calculation refs to a conformer.
 */
function conformerGroupWithCalcs(ref: string, label: string, observationRef: string, calculationRefs: string[]): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: ref, label },
        observations_summary: { total: 1 },
        evidence_summary: {
            calculation_count: calculationRefs.length, optimization_chain_count: 1, geometry_count: 1,
            evidence_coverage: { opt: 1, freq: 1, sp: 1 }, levels_of_theory: {},
        },
        observations: [{
            conformer_observation: { conformer_observation_ref: observationRef },
            calculations: calculationRefs.map((calcRef) => ({ calculation_ref: calcRef, type: "opt" })),
        }],
        calculations: [], geometries: [],
    } as unknown as ConformerProjection
}

function sourceCalc(role: string, calculationRef: string) {
    return {
        role, calculation_id: null, calculation_ref: calculationRef, calculation_type: role,
        quality: "raw", created_at: "2026-07-21T12:14:32.845900",
        review: { status: "not_reviewed", reviewed_at: null, reviewer_kind: null },
        level_of_theory: null, software_release: null, workflow_tool_release: null,
    }
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
 * defect a hardcoded flag causes. `sm_two` is also given
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

/** The value of the labelled row in a card's References: binds each ref to its role, so a swap of the two correction pointers shows. */
function refValue(container: HTMLElement, label: string): string {
    const row = Array.from(container.querySelectorAll(".ref-item")).find((item) => item.querySelector(".ref-item-label")?.textContent === label)
    if (!row) throw new Error(`No reference labelled "${label}" found`)
    return row.querySelector(".ref-item-value")?.textContent ?? ""
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
        pagination: { offset: 0, limit: 50, returned: records.length, total: records.length },
    }
}

/** The card of a record, found by the id of its own heading. */
function cardOf(ref: string): HTMLElement {
    const heading = document.getElementById(`statmech-heading-${ref}`)
    if (!heading) throw new Error(`No card heading for ${ref}`)
    return heading.closest("article") as HTMLElement
}

/** The record row of a group table, found by its number. */
function groupRow(table: HTMLElement, position: number): HTMLElement {
    const cell = Array.from(table.querySelectorAll('td[data-label="Record"]')).find((candidate) => candidate.textContent === `Record ${position}`)
    if (!cell) throw new Error(`No row for Record ${position}`)
    return cell.closest("tr") as HTMLElement
}

describe("EntryStatmechSection", () => {
    it("renders every deposited record independently, without merging or picking one", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("sm_one")
        // Each record is its own card, found by its heading id; the ref itself
        // is in the card's collapsed references.
        expect(cardOf("sm_one")).toBeVisible()
        expect(cardOf("sm_two")).toBeVisible()
        expect(within(cardOf("sm_one")).getByText("sm_one")).toBeInTheDocument()
        expect(within(cardOf("sm_one")).getByText("sm_one")).not.toBeVisible()
    })

    it("links a transition-state record's subject to its page instead of saying none exists", async () => {
        const record = baseRecord({ species: null, transition_state: { transition_state_ref: "ts_one", transition_state_entry_ref: "tse_one" } })
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([record]))))
        page()
        await screen.findByText("sm_one")
        expect(screen.getByRole("link", { name: "Transition state" })).toHaveAttribute("href", "/transition-state-entries/tse_one")
        expect(document.body.textContent).not.toContain("no dedicated page")
    })

    it("never hides a superseded record, and never swaps the direction of the correction pointer", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("sm_two")
        const twoCard = cardOf("sm_two")
        expect(within(twoCard).getByText("Superseded")).toBeVisible()
        // Position-bound: superseded_by must be the ref in the "replaced
        // by" sentence, current must be the ref in the "current record in
        // this chain is" sentence — a direction swap is invisible to a
        // presence-only "does sm_two_v2 appear somewhere" check, since both
        // refs are present on the card either way.
        expect(twoCard).toHaveTextContent("replaced by a newer record")
        expect(twoCard).toHaveTextContent("current record in this chain is a newer record")
        expect(twoCard.querySelector(".supersession-notice code")).toBeNull()
        expect(refValue(twoCard, "Replaced by")).toBe("sm_two_v2")
        expect(refValue(twoCard, "Current record in this chain")).toBe("sm_two_v3")

        const oneCard = cardOf("sm_one")
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

    it("formats the frequency scale factor at its own 4dp spec, not the 6dp electronic-energy spec", async () => {
        // 0.98765 rounds to "0.9877" at 4dp but "0.987650" at 6dp -- a
        // table-row swap (using `calculation_electronic_energy_hartree`
        // here instead of `statmech_frequency_scale_factor`) produces a
        // visibly different string, not just a different length.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({ statmech: { ...baseRecord().statmech, frequency_scale_factor_value: 0.98765 } }),
        ]))))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        expect(ddFor(card, "Frequency scale factor")).toBe("0.9877")
    })

    it("renders Record software and Workflow through their own label rules, not stuttered and not swapped", async () => {
        // software_release's version already opens with its name (the
        // "Gaussian Gaussian 16" shape); workflow_tool_release's does not.
        // If the two `<code>Record software:</code>`/`<code>Workflow:</code>`
        // fields were ever swapped to call the wrong helper on the wrong
        // shaped object (`toolReleaseLabel` reads `.workflow_tool`, which
        // `software_release` doesn't have), the swapped field would read
        // "not recorded" instead of the value asserted below.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({
                software_release: { software_release_ref: "srel_1", software: "Gaussian", version: "Gaussian 16, Revision C.02" },
                workflow_tool_release: { workflow_tool_release_ref: "wfr_1", workflow_tool: "ARC", version: "2.1.0" },
            }),
        ]))))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        const softwareLine = within(card).getByText(/^Record software:/).closest("p") as HTMLElement
        expect(softwareLine.textContent).toContain("Gaussian 16, Revision C.02")
        expect(softwareLine.textContent).not.toMatch(/Gaussian Gaussian/)
        expect(softwareLine.textContent).toContain("ARC 2.1.0")
    })

    it("attributes the scale factor's software to the scale factor, distinct from and never swapped with the record's own software", async () => {
        // The record's own software (Arkane, the analysis tool) and the
        // scale factor's software (ORCA, the code the frequencies it was
        // fit against actually ran in) are DELIBERATELY DIFFERENT codes
        // here. A fixture where both happened to be the same code could not
        // catch a swap -- reading either value under the other's label
        // would still pass. Two distinct codes make a swap visible: the
        // scale-factor line must show ORCA, and the record-software line
        // must show Arkane, never the reverse.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({
                frequency_scale_factor: {
                    frequency_scale_factor_ref: "fsf_1",
                    value: 0.998,
                    scale_kind: "harmonic",
                    level_of_theory: null,
                    software: { software_release_ref: "srel_orca", software: "ORCA", version: "5.0.4" },
                    source_literature: null,
                },
                software_release: { software_release_ref: "srel_arkane", software: "Arkane", version: null },
            }),
        ]))))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        // "Scale factor software" (this block) vs. "Record software" (the
        // separate line below it) are the two genuinely different softwares
        // this card carries -- distinguished by row LABEL, not merely by
        // both values appearing somewhere on the card.
        expect(ddFor(card, "Scale factor software")).toBe("ORCA 5.0.4")

        const recordSoftwareLine = within(card).getByText(/^Record software:/).closest("p") as HTMLElement
        expect(recordSoftwareLine.textContent).toContain("Arkane")
        expect(recordSoftwareLine.textContent).not.toContain("ORCA")
        expect(ddFor(card, "Scale factor software")).not.toContain("Arkane")
    })

    it("renders no software placeholder on the scale factor line when the archive recorded none", async () => {
        // Absence describes the request/data shape here, not "unknown
        // software" -- there must be no "not recorded" stand-in, unlike
        // the record-software line which does use that placeholder.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({
                frequency_scale_factor: {
                    frequency_scale_factor_ref: "fsf_2",
                    value: 1.0,
                    scale_kind: "harmonic",
                    level_of_theory: null,
                    software: null,
                    source_literature: null,
                },
            }),
        ]))))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        // No "Scale factor software" row at all -- absence describes the
        // request/data shape here, not "unknown software", so there must be
        // no row (not even a "not recorded" placeholder row) when the
        // archive recorded none.
        expect(within(card).queryByText("Scale factor software")).not.toBeInTheDocument()
    })

    it("renders a categorical value (scale kind) as a pill, and the scale factor's page as a words-only link, never the reverse", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({
                frequency_scale_factor: {
                    frequency_scale_factor_ref: "fsf_pill_check",
                    value: 0.995,
                    scale_kind: "fundamental",
                    level_of_theory: null,
                    software: null,
                    source_literature: null,
                },
            }),
        ]))))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement

        // The categorical scale-kind value renders INSIDE a `.value-pill`.
        const scaleKindPill = within(card).getByText("fundamental")
        expect(scaleKindPill).toHaveClass("value-pill")

        // The scale factor's own page is a link named in words; its ref is not printed.
        expect(Array.from(card.querySelectorAll("dt")).some((el) => el.textContent === "Frequency scale factor ref")).toBe(false)
        expect(ddFor(card, "Frequency scale factor page")).toBe("Frequency scale factor")
        const refLink = within(card).getByRole("link", { name: "Scheme: Frequency scale factor" })
        expect(refLink).toHaveAttribute("href", "/methods/frequency-scale-factors/fsf_pill_check")
        expect(refLink).not.toHaveClass("value-pill")
        expect(card.textContent).not.toContain("fsf_pill_check")
    })

    it("renders an available on-demand section as idle until opened, and fetches exactly its own token once -- unlike source_calculations and frequencies, which now load eagerly", async () => {
        const requestedIncludeSets: string[][] = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            requestedIncludeSets.push(new URL(request.url).searchParams.getAll("include"))
            return HttpResponse.json(mockResponse())
        }))
        page()
        await screen.findByText("sm_one")
        // `source_calculations` is fetched eagerly (every record card
        // derives its conformer from it by default), and so is
        // `frequencies` now (every card's own `FrequenciesBlock` shows it
        // by default) -- `torsions` is not, it still waits for
        // its own disclosure to be opened.
        await waitFor(() => expect(requestedIncludeSets).toEqual([[], ["source_calculations"], ["frequencies"], ["calculation_settings"]]))

        const section = screen.getByText("Torsions").closest("details") as HTMLDetailsElement
        expect(section.open).toBe(false)
        expect(within(section).getByText("Expand to load this section from the archive.")).toBeInTheDocument()

        fireEvent.click(screen.getByText("Torsions"))
        await within(section).findByText("Torsions loaded.")
        expect(requestedIncludeSets).toEqual([[], ["source_calculations"], ["frequencies"], ["calculation_settings"], ["torsions"]])
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

        fireEvent.click(screen.getByText("Torsions"))
        const section = screen.getByText("Torsions").closest("details") as HTMLDetailsElement
        await within(section).findByText("Torsions loaded.")

        // sm_one's own row within the Torsions section reports "not
        // present" (its own available_sections.has_torsions is false),
        // never sm_two's torsion row.
        const [oneRow, twoRow] = Array.from(section.querySelectorAll("div.science-record")) as HTMLElement[]
        expect(within(oneRow).getByText("Not present for this record.")).toBeVisible()
        expect(within(oneRow).queryByText("hindered rotor")).not.toBeInTheDocument()

        expect(within(twoRow).getByText("hindered rotor")).toBeVisible()
        expect(twoRow.querySelector('a[href="/calculations/calc_scan_1"]')).not.toBeNull()
        expect(twoRow.textContent).not.toContain("calc_scan_1")
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
        fireEvent.click(screen.getByText("Torsions"))
        const section = screen.getByText("Torsions").closest("details") as HTMLDetailsElement
        await within(section).findByText("Torsions loaded.")

        const table = within(section).getByRole("table", { name: "Torsions" })
        const rows = within(table).getAllByRole("row").slice(1)
        const soundRow = rows.find((row) => row.querySelector('a[href="/calculations/calc_scan_sound"]'))!
        const invalidRow = rows.find((row) => row.querySelector('a[href="/calculations/calc_scan_bad"]'))!
        // Column-scoped, not row-scoped: binds each value to its OWN
        // column via `data-label`, so a Top<->Invalidated column swap
        // (values transposed, headers untouched) is caught, not just a
        // "does this text appear somewhere in the row" check.
        expect(cellAt(soundRow, "Invalidated")).toBe("not invalidated")
        expect(cellAt(invalidRow, "Invalidated")).toBe("invalidated")
        // `top_description` and `invalidated_reason` are depositor free text: neither is printed.
        expect(within(table).queryByText("methyl-sound")).toBeNull()
        expect(within(table).queryByText("scan did not close")).toBeNull()
    })

    it("renders a heavy section as a static, request-free line when no record on the entry has it", async () => {
        let requestCount = 0
        server.use(http.get(ENDPOINT, () => {
            requestCount += 1
            return HttpResponse.json(mockResponse())
        }))
        page()
        await screen.findByText("sm_one")
        // Neither sm_one nor sm_two has electronic levels. This
        // collapses to one line, with no heading at all -- there is no
        // destination to jump to, so no `SectionHeading` registers one.
        expect(screen.queryByRole("heading", { name: "Electronic levels" })).not.toBeInTheDocument()
        expect(screen.getByText("No electronic levels are recorded for any statmech record on this entry.")).toBeVisible()
        // Four requests, never a fifth for "Electronic levels" (which
        // stays request-free): the base list load, plus the three eager
        // fetches every record card needs by default -- `source_calculations`
        // (conformer derivation), `frequencies` (`FrequenciesBlock`) and `calculation_settings` (the "Settings
        // behind this value" block) -- see the eager-load test above.
        await waitFor(() => expect(requestCount).toBe(4))
    })

    it("shows each record's own 'Settings behind this value' from the calculation_settings include, never another record's", async () => {
        const settingsFor = (calc: string, state: string) => [
            { role: "geometry", calculation_ref: calc, calculation_type: "opt", settings: [
                { setting: "grid", state, assumed_value: "defgrid2", reported: [] },
                { setting: "scf_convergence", state: "not_stated", reported: [] },
            ] },
            { role: "frequency", calculation_ref: null, absent_reason: "not_linked", settings: [] },
            { role: "energy", calculation_ref: null, absent_reason: "not_linked", settings: [] },
        ]
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const [one, two] = mockRecords()
            if (includes.includes("calculation_settings")) {
                return HttpResponse.json(mockResponse([
                    { ...(one as Record<string, unknown>), calculation_settings: settingsFor("calc_for_one", "assumed_default") },
                    { ...(two as Record<string, unknown>), calculation_settings: settingsFor("calc_for_two", "not_stated") },
                ] as unknown as ReturnType<typeof mockRecords>))
            }
            return HttpResponse.json(mockResponse())
        }))
        page()
        await screen.findByText("sm_one")
        await waitFor(() => expect(document.querySelectorAll('a[href^="/calculations/calc_for_"]')).toHaveLength(2))
        const links = Array.from(document.querySelectorAll('a[href^="/calculations/calc_for_"]'))
        expect(links.map((link) => link.getAttribute("href")).sort()).toEqual(["/calculations/calc_for_one", "/calculations/calc_for_two"])
        for (const link of links) expect(link.textContent).toBe("Optimisation")
        const first = cardOf("sm_one")
        expect(first.querySelector('a[href="/calculations/calc_for_one"]')).not.toBeNull()
        expect(first.querySelector('a[href="/calculations/calc_for_two"]')).toBeNull()
        expect(within(first).getByText("Assumed default")).toBeInTheDocument()
        expect(within(first).getAllByText("No calculation linked")).toHaveLength(2)
    })

    it("says so when the calculation_settings fetch fails, instead of silently showing nothing", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            if (new URL(request.url).searchParams.getAll("include").includes("calculation_settings")) {
                return HttpResponse.json({ detail: "settings unavailable" }, { status: 500 })
            }
            return HttpResponse.json(mockResponse())
        }))
        page()
        await screen.findByText("sm_one")
        expect((await screen.findAllByText("settings unavailable")).length).toBeGreaterThan(0)
    })

    it("prints no depositor-typed text from the calculation_settings block", async () => {
        const block = [
            { role: "geometry", calculation_ref: "calc_for_one", calculation_type: "opt", settings: [
                { setting: "grid", state: "requested", requested_value: "typed", requested_from: "typed", reported: [{ key: "k", value: "v" }], assumed_value: "typed" },
            ] },
            { role: "frequency", calculation_ref: null, absent_reason: "not_linked", settings: [] },
            { role: "energy", calculation_ref: null, absent_reason: "not_linked", settings: [] },
        ]
        const planted = plantSentinels(block, ["requested_value", "assumed_value", "key", "value"])
        expectPlanted(planted)
        server.use(http.get(ENDPOINT, ({ request }) => {
            if (new URL(request.url).searchParams.getAll("include").includes("calculation_settings")) {
                const [one] = mockRecords()
                return HttpResponse.json(mockResponse([{ ...(one as Record<string, unknown>), calculation_settings: planted.value }] as unknown as ReturnType<typeof mockRecords>))
            }
            return HttpResponse.json(mockResponse())
        }))
        page()
        await screen.findByText("sm_one")
        await screen.findByText("Settings behind this value")
        expectNoDepositorText()
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

describe("EntryStatmechSection: linked record on a later page", () => {
    it("opens on the page holding the record a fragment names", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const params = new URL(request.url).searchParams
            const offset = Number(params.get("offset") ?? 0)
            const limit = Number(params.get("limit") ?? 1)
            const all = mockRecords()
            const records = all.slice(offset, offset + limit)
            return HttpResponse.json({ ...mockResponse(records), pagination: { offset, limit, returned: records.length, total: all.length } })
        }))
        render(<MemoryRouter initialEntries={["/#statmech-heading-sm_two"]}><EntryStatmechSection entryRef={entryRef} /></MemoryRouter>)
        expect(await screen.findByText("Showing 2\u20132 of 2 statistical-mechanics records.")).toBeVisible()
        expect(document.getElementById("statmech-heading-sm_two")).not.toBeNull()
    })
})

describe("EntryStatmechSection conformer-scoped attribution", () => {
    // One statmech record names all three
    // of that entry's conformer groups. Selecting conformer_2 or
    // conformer_3 must file the record under THAT group, never always
    // under conformer_1 -- the exact defect a first-match implementation
    // produces and a fixture naming only two groups could not catch (with
    // two groups, "not group A" and "always group A" are indistinguishable
    // half the time).
    it("files a statmech record naming all three conformer groups under whichever ONE is selected, never always the first-named group", async () => {
        function recordResponse(includeConformers: boolean) {
            const record = baseRecord({
                available_sections: { ...baseRecord().available_sections, has_conformers: true },
                ...(includeConformers ? {
                    conformers: [
                        { conformer_group_ref: "cg_one", label: "conformer_1" },
                        { conformer_group_ref: "cg_two", label: "conformer_2" },
                        { conformer_group_ref: "cg_three", label: "conformer_3" },
                    ],
                } : {}),
            })
            return mockResponse([record])
        }
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            return HttpResponse.json(recordResponse(includes.includes("conformers")))
        }))

        page(conformerGroups[1], conformerGroups) // conformer_2 selected
        await screen.findByText("sm_one")
        const primaryHeading = await screen.findByRole("heading", { name: "From Conformer Group 2" })
        const primaryGroup = primaryHeading.closest(".conformer-evidence-group") as HTMLElement
        expect(within(primaryGroup).getByText("sm_one")).toBeInTheDocument()
        // Never files under the first-named group instead.
        expect(screen.queryByRole("heading", { name: "From Conformer Group 1" })).not.toBeInTheDocument()
    })

    it("files the SAME multi-group record under conformer_3 when conformer_3 is selected -- position in the name list is irrelevant", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const record = baseRecord({
                available_sections: { ...baseRecord().available_sections, has_conformers: true },
                ...(includes.includes("conformers") ? {
                    conformers: [
                        { conformer_group_ref: "cg_one", label: "conformer_1" },
                        { conformer_group_ref: "cg_two", label: "conformer_2" },
                        { conformer_group_ref: "cg_three", label: "conformer_3" },
                    ],
                } : {}),
            })
            return HttpResponse.json(mockResponse([record]))
        }))
        page(conformerGroups[2], conformerGroups) // conformer_3 selected
        await screen.findByText("sm_one")
        const primaryHeading = await screen.findByRole("heading", { name: "From Conformer Group 3" })
        const primaryGroup = primaryHeading.closest(".conformer-evidence-group") as HTMLElement
        expect(within(primaryGroup).getByText("sm_one")).toBeInTheDocument()
    })

    it("says plainly when the selected conformer has no statmech record, rather than silently showing another conformer's as though it did", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const record = baseRecord({
                available_sections: { ...baseRecord().available_sections, has_conformers: true },
                ...(includes.includes("conformers") ? { conformers: [{ conformer_group_ref: "cg_one", label: "conformer_1" }] } : {}),
            })
            return HttpResponse.json(mockResponse([record]))
        }))
        page(conformerGroups[1], conformerGroups) // conformer_2 selected; record only names conformer_1
        await screen.findByText("No statmech record is linked to this conformer yet.")
        const answer = screen.getByText("No statmech record is linked to this conformer yet.")
        expect(answer).toHaveClass("conformer-attribution-answer")
        // sm_one is still fully present -- reachable, just demoted --
        // inside the collapsed other-conformers disclosure.
        const otherDetails = document.querySelector(".conformer-attribution-other") as HTMLDetailsElement
        expect(otherDetails.open).toBe(false)
        expect(within(otherDetails).getByText("sm_one")).toBeInTheDocument()
    })

    // Mirrors the identical case in
    // `EntryThermoSection.test.tsx`: `renderStatmechRecords` groups by
    // scientific fingerprint INSIDE whatever record list it's called
    // with, and `ConformerAttributionGroups` calls it once per bucket
    // (this-conformer / other-conformer / no-link), never once over the
    // whole entry. No test proved that a record naming a DIFFERENT
    // conformer than the selected one, but reporting an IDENTICAL
    // point-group/symmetry/frequency-scale-factor fingerprint, stays its
    // own ungrouped card in its OWN bucket rather than being folded into
    // a cross-bucket "2 records with identical values" group.
    it("never groups two identical-value records together when they name DIFFERENT conformers -- each bucket groups its own records only", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const withConformers = includes.includes("conformers")
            const recordG1 = baseRecord({
                statmech: { ...baseRecord().statmech, statmech_ref: "sm_g1" },
                available_sections: { ...baseRecord().available_sections, has_conformers: true },
                ...(withConformers ? { conformers: [{ conformer_group_ref: "cg_one", label: "conformer_1" }] } : {}),
            })
            const recordG2 = baseRecord({
                statmech: { ...baseRecord().statmech, statmech_ref: "sm_g2" },
                available_sections: { ...baseRecord().available_sections, has_conformers: true },
                ...(withConformers ? { conformers: [{ conformer_group_ref: "cg_two", label: "conformer_2" }] } : {}),
            })
            return HttpResponse.json(mockResponse([recordG1, recordG2]))
        }))
        page(conformerGroups[0], conformerGroups) // conformer_1 selected

        await screen.findByRole("heading", { name: "From Conformer Group 1" })

        // The selected conformer's own bucket shows ONLY its own record,
        // as a plain (ungrouped) card -- never the "N records with
        // identical values" wrapper a same-bucket duplicate would get.
        const primaryGroup = screen.getByRole("heading", { name: "From Conformer Group 1" }).closest(".conformer-evidence-group") as HTMLElement
        expect(within(primaryGroup).getByText("sm_g1")).toBeInTheDocument()
        expect(within(primaryGroup).queryByText("sm_g2")).not.toBeInTheDocument()
        expect(within(primaryGroup).queryByText(/records with identical values/)).not.toBeInTheDocument()

        // The other-conformer record is demoted into its own collapsed
        // disclosure -- not merged into the primary bucket's card just
        // because the two report the same numbers.
        const otherDetails = document.querySelector(".conformer-attribution-other") as HTMLDetailsElement
        expect(otherDetails).not.toBeNull()
        expect(within(otherDetails).getByText("sm_g2")).toBeInTheDocument()
        expect(within(otherDetails).queryByText("sm_g1")).not.toBeInTheDocument()
        expect(within(otherDetails).queryByText(/records with identical values/)).not.toBeInTheDocument()

        // Nowhere on the page do the two get grouped under one card.
        expect(screen.queryByText(/records with identical values/)).not.toBeInTheDocument()
    })
})

// `statmech` has no conformer column at all (entry-scoped, never
// conformer-scoped) -- these prove the READ-TIME DERIVATION from source
// calculations (`domain/statmechConformerDerivation.ts`), cross-referenced
// against the already-loaded conformer projections, and specifically the
// disagreement case, tested with a constructed fixture rather than found live.
describe("EntryStatmechSection -- conformer derived from source calculations", () => {
    it("shows the single conformer every source calculation agrees on, labelled as derived", async () => {
        const conformers = [conformerGroupWithCalcs("cg_one", "conformer_1", "co_1", ["calc_opt_1", "calc_freq_1", "calc_sp_1"])]
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const record = baseRecord({
                ...(includes.includes("source_calculations") ? {
                    source_calculations: [
                        sourceCalc("opt", "calc_opt_1"),
                        sourceCalc("freq", "calc_freq_1"),
                        sourceCalc("sp", "calc_sp_1"),
                    ],
                } : {}),
            })
            return HttpResponse.json(mockResponse([record]))
        }))
        page(undefined, conformers)
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        const note = (await within(card).findByText(/Conformer \(derived from source calculations\)/)).closest("p") as HTMLElement
        expect(within(note).getByRole("link", { name: "Conformer Group 1" })).toHaveAttribute("href", "/conformer-groups/cg_one")
    })

    it("names every conformer involved -- never picks one -- when source calculations disagree", async () => {
        // opt traces to cg_one; freq and sp both trace to cg_two -- a real,
        // constructed disagreement (the live archive has none today).
        const conformers = [
            conformerGroupWithCalcs("cg_one", "conformer_1", "co_1", ["calc_opt_1"]),
            conformerGroupWithCalcs("cg_two", "conformer_2", "co_2", ["calc_freq_1", "calc_sp_1"]),
        ]
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const record = baseRecord({
                ...(includes.includes("source_calculations") ? {
                    source_calculations: [
                        sourceCalc("opt", "calc_opt_1"),
                        sourceCalc("freq", "calc_freq_1"),
                        sourceCalc("sp", "calc_sp_1"),
                    ],
                } : {}),
            })
            return HttpResponse.json(mockResponse([record]))
        }))
        page(undefined, conformers)
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        const note = (await within(card).findByText(/span more than one conformer/)).closest("p") as HTMLElement
        expect(note).toHaveAttribute("role", "alert")
        expect(within(note).getByRole("link", { name: "Conformer Group 1" })).toBeInTheDocument()
        expect(within(note).getByRole("link", { name: "Conformer Group 2" })).toBeInTheDocument()
        // Full rendered text, including punctuation -- an em dash on both
        // sides of the conformer list, never a plain " -- " (this sentence
        // is built from a text node/{" "}/text node sequence around the
        // link list, which is exactly the shape a naive dash-normalization
        // pass over single text nodes can miss at the node's own edge).
        expect(note).toHaveTextContent(
            "Conformer: this record's source calculations span more than one conformer — "
            + "Conformer Group 1, Conformer Group 2 — so no single conformer is shown here.",
        )
        // Never silently collapses to the single-conformer phrasing.
        expect(within(card).queryByText(/Conformer \(derived from source calculations\)/)).not.toBeInTheDocument()
    })

    it("says the derivation could not resolve when source calculations exist but none trace to a loaded conformer observation", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const record = baseRecord({
                ...(includes.includes("source_calculations") ? { source_calculations: [sourceCalc("opt", "calc_unlinked")] } : {}),
            })
            return HttpResponse.json(mockResponse([record]))
        }))
        page(undefined, [])
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        await within(card).findByText(/do not trace to any conformer observation loaded/)
    })

    it("shows no derivation note when the record has zero source calculations -- already stated by the evidence-summary count", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({ evidence_summary: { ...baseRecord().evidence_summary, source_calculation_count: 0 } }),
        ]))))
        page(undefined, conformerGroups)
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        expect(within(card).queryByText(/Conformer \(derived/)).not.toBeInTheDocument()
        expect(within(card).queryByText(/do not trace to any conformer/)).not.toBeInTheDocument()
        expect(within(card).queryByText(/span more than one conformer/)).not.toBeInTheDocument()
    })
})

// ---------------------------------------------------------------------------
// The vibrational-frequency evidence -- the CONTENT of a
// statmech record -- renders on each card directly, loaded by default,
// gated by that record's own `available_sections.has_frequencies`.
// ---------------------------------------------------------------------------
describe("EntryStatmechSection: frequencies render on the card by default", () => {
    it("shows the source frequency calculation refs on the card itself, without any click, when the record has frequency evidence", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const record = baseRecord({
                ...(includes.includes("frequencies") ? {
                    frequencies: { source_freq_calculation_refs: ["calc_freq_1"], frequency_scale_factor_value: 0.999, note: null },
                } : {}),
            })
            return HttpResponse.json(mockResponse([record]))
        }))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        // No click, no expand -- present as soon as the card renders.
        const link = await within(card).findByRole("link", { name: "Calculation: Frequency calculation" })
        expect(link).toHaveAttribute("href", "/calculations/calc_freq_1")
        expect(card.textContent).not.toContain("calc_freq_1")
        // The "Frequencies" heading lives on the CARD now (inside the
        // `<article>`), not behind a global "Expand to load this section"
        // `<details>` disclosure the way the other five lazy sections are.
        const freqHeading = screen.getByRole("heading", { name: "Frequencies" })
        expect(card.contains(freqHeading)).toBe(true)
        expect(freqHeading.closest("details")).toBeNull()
    })

    it("renders no Frequencies block at all for a record with no frequency evidence -- vanishes rather than showing an empty box", async () => {
        // sm_one: has_frequencies false. sm_two: has_frequencies true, with
        // real frequencies data -- included so this test can WAIT for
        // sm_two's positive content (a real network round trip) before
        // asserting sm_one shows nothing. Both records share ONE
        // `frequenciesState` (see `useStatmechSection`'s own docstring), so
        // once sm_two's link is visible the shared fetch has genuinely
        // settled to "ready" -- checking sm_one's absence any earlier would
        // pass vacuously whether or not the gate actually works, since
        // nothing renders for either record while the fetch is still
        // in flight.
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const records = [
                baseRecord({ available_sections: { ...baseRecord().available_sections, has_frequencies: false } }),
                baseRecord({
                    statmech: { ...baseRecord().statmech, statmech_ref: "sm_two", point_group: "C2v" },
                    ...(includes.includes("frequencies") ? {
                        frequencies: { source_freq_calculation_refs: ["calc_freq_2"], frequency_scale_factor_value: 0.999, note: null },
                    } : {}),
                }),
            ]
            return HttpResponse.json(mockResponse(records))
        }))
        page()
        const oneCard = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        const twoCard = (await screen.findByText("sm_two")).closest("article") as HTMLElement
        // Positive wait: proves the shared frequencies fetch has resolved.
        await within(twoCard).findByRole("link", { name: "Calculation: Frequency calculation" })
        // Only now is sm_one's absence a real assertion about the gate,
        // not an accident of timing.
        expect(within(oneCard).queryByText("Source frequency calculations")).not.toBeInTheDocument()
        expect(within(oneCard).queryByText(/frequency evidence/)).not.toBeInTheDocument()
        expect(within(oneCard).queryByRole("heading", { name: "Frequencies" })).not.toBeInTheDocument()
    })

    it("never prints the server's own developer-facing note (an API path) on the card, even when the wire carries one", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const record = baseRecord({
                ...(includes.includes("frequencies") ? {
                    frequencies: {
                        source_freq_calculation_refs: ["calc_freq_1"],
                        frequency_scale_factor_value: 0.999,
                        note: "Per-mode frequency arrays live on the source freq calculation(s); fetch the full array via /api/v1/scientific/calculations/{calculation_ref}?include=freq_modes.",
                    },
                } : {}),
            })
            return HttpResponse.json(mockResponse([record]))
        }))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        await within(card).findByRole("link", { name: "Calculation: Frequency calculation" })
        expect(screen.queryByText(/\/api\/v1\//)).not.toBeInTheDocument()
        expect(screen.queryByText(/include=freq_modes/)).not.toBeInTheDocument()
        expect(screen.queryByText(/fetch the full array/)).not.toBeInTheDocument()
        // A plain, reader-facing line takes its place.
        await within(card).findByText(/Per-mode frequency values are recorded on the source calculation/)
    })
})

// ---------------------------------------------------------------------------
// Seven byte-identical statmech records render as one card, not fourteen
// full cards (the same rule as thermo's). See
// `EntryThermoSection.test.tsx`'s matching describe block for the rule
// applied to `statmechRecordFingerprint`.
// ---------------------------------------------------------------------------
describe("EntryStatmechSection: identical-value records group under one card", () => {
    /** Three clones, identical in every fingerprinted field, differing only
     *  in `statmech_ref` and one PROVENANCE field (`software_release`) --
     *  the review's own worked case: six records say "Record software: not
     *  recorded" and one says "Arkane" while sharing every scientific
     *  value. */
    function identicalClones() {
        return [
            baseRecord({ statmech: { ...baseRecord().statmech, statmech_ref: "sm_g1" }, software_release: null }),
            baseRecord({ statmech: { ...baseRecord().statmech, statmech_ref: "sm_g2" }, software_release: null }),
            baseRecord({
                statmech: { ...baseRecord().statmech, statmech_ref: "sm_g3" },
                software_release: { software_release_ref: "srel_arkane", software: "Arkane", version: null },
            }),
        ]
    }

    it("groups records reporting identical point group, symmetry, and scale factor under one card, listing every record by number", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse(identicalClones()))))
        page()
        await screen.findByText("3 records with identical values")

        expect(document.querySelectorAll("article.identical-record-group")).toHaveLength(1)
        const groupCard = document.querySelector("article.identical-record-group") as HTMLElement
        const ownHeadingRow = groupCard.querySelector(":scope > .science-record-heading") as HTMLElement
        expect(ownHeadingRow.querySelector("h3")?.textContent).toBe("computed statmech record 1")

        const refsTable = within(groupCard).getByRole("table", { name: "Records sharing these identical values" })
        expect(within(refsTable).getAllByRole("columnheader")[0]).toHaveTextContent("Record")
        expect(within(refsTable).getByText("Record 1")).toBeVisible()
        expect(within(refsTable).getByText("Record 2")).toBeVisible()
        expect(within(refsTable).getByText("Record 3")).toBeVisible()
        expect(refsTable.textContent).not.toMatch(/sm_g/)
    })

    // An eighth column ("Workflow tool") would clip at
    // the `.table-scroll` edge at 1920 with no scroll affordance. The structural
    // guarantee this table keeps is "at most 9 columns (6 original,
    // ALWAYS plus the three Geometry/Frequencies/Energy level-of-theory
    // columns) -- "Record software"/"Workflow tool" render as a
    // provenance row beneath each record's own row instead of two more
    // columns.
    it("keeps the group table to at most 9 columns, with software/workflow tool on a provenance row instead", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse(identicalClones()))))
        page()
        await screen.findByText("3 records with identical values")
        const refsTable = screen.getByRole("table", { name: "Records sharing these identical values" })
        const headerCells = within(refsTable).getAllByRole("columnheader")
        // 6 original columns + the three Geometry/Frequencies/Energy level
        // columns, ALWAYS shown now regardless of whether this fixture's
        // clones' own levels agree.
        expect(headerCells.length).toBeLessThanOrEqual(9)
        expect(headerCells.map((cell) => cell.textContent)).toEqual(expect.arrayContaining(["Geometry", "Frequencies", "Energy"]))
        expect(headerCells.map((cell) => cell.textContent)).not.toContain("Level of theory")
        expect(headerCells.map((cell) => cell.textContent)).not.toContain("Record software")
        expect(headerCells.map((cell) => cell.textContent)).not.toContain("Workflow tool")

        const provenanceRows = refsTable.querySelectorAll("tr.data-table-provenance-row")
        expect(provenanceRows).toHaveLength(3)
        provenanceRows.forEach((row) => {
            expect(row.textContent).toMatch(/^Software: .+ · Workflow tool: .+$/)
            // Spans every remaining column, not a 7th/8th cell of its own.
            expect(row.querySelector("td")?.getAttribute("colspan")).toBe(String(headerCells.length))
        })
    })

    // "Record
    // software"/"Workflow tool" are not their own `<td data-label>`
    // columns -- they sit in a `.data-table-provenance-row` spanning the
    // row directly beneath each record's own row, so the table fits at
    // 1920 without clipping (see `IdenticalStatmechGroupRefs`'s own
    // comment). This finds that sibling row rather than a named cell.
    function provenanceRowFor(recordRow: HTMLElement): HTMLElement {
        const next = recordRow.nextElementSibling as HTMLElement | null
        if (!next?.classList.contains("data-table-provenance-row")) {
            throw new Error("Expected a .data-table-provenance-row immediately after this record's row")
        }
        return next
    }

    it("keeps provenance that differs across an identical-value group visible per ref -- six 'not recorded', one 'Arkane'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse(identicalClones()))))
        page()
        await screen.findByText("3 records with identical values")
        const refsTable = screen.getByRole("table", { name: "Records sharing these identical values" })
        const rows = within(refsTable).getAllByRole("row").slice(1)
        expect(rows.length).toBeGreaterThan(0)
        const g1Row = groupRow(refsTable, 1)
        const g3Row = groupRow(refsTable, 3)
        expect(provenanceRowFor(g1Row).textContent).toContain("Software: not recorded")
        expect(provenanceRowFor(g3Row).textContent).toContain("Software: Arkane")
    })

    it("show-all mounts every member's own full card, in a disclosure closed by default", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse(identicalClones()))))
        page()
        await screen.findByText("3 records with identical values")
        const detail = screen.getByText("Show all 3 records individually").closest("details") as HTMLDetailsElement
        expect(detail.open).toBe(false)
        const memberCards = within(detail).getAllByRole("article") as HTMLElement[]
        expect(memberCards).toHaveLength(3)
        const memberIds = memberCards.map((card) => card.querySelector("h3")?.id)
        expect(memberIds.sort()).toEqual(["statmech-heading-sm_g1", "statmech-heading-sm_g2", "statmech-heading-sm_g3"])
        fireEvent.click(screen.getByText("Show all 3 records individually"))
        expect(detail.open).toBe(true)
    })

    it("never wraps a lone record in a '1 identical' group -- a single record renders as a plain card", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([baseRecord()]))))
        page()
        await screen.findByText("sm_one")
        expect(screen.queryByText(/records with identical values/)).not.toBeInTheDocument()
        expect(screen.queryByText(/Show all \d+ records individually/)).not.toBeInTheDocument()
        expect(screen.queryByText("Records in this group")).not.toBeInTheDocument()
    })

    it("keeps records that differ in scientific_origin as separate cards, even when every other field matches", async () => {
        // The file's own mockRecords() fixture: sm_one is "computed", sm_two
        // is "estimated" -- otherwise near-identical. A computed value and
        // an experimental one that happen to share a number are NOT the
        // same record.
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("sm_one")
        expect(screen.queryByText(/records with identical values/)).not.toBeInTheDocument()
        expect(cardOf("sm_one")).toBeVisible()
        expect(cardOf("sm_two")).toBeVisible()
    })

    /**
     * Three clones sharing every fingerprinted field but citing THREE
     * DIFFERENT opt/freq/sp source calculations and three different
     * frequency-calculation refs -- the live bug this fix was written
     * against: the group card must not print the representative's
     * "Record software: Arkane · Workflow: ARC 1.1.0", its own
     * source-calculation count, and its single freq-calculation ref as
     * though they held for the whole group, when the real ethene entry's 7
     * statmech records cite 7 different freq calculations.
     */
    function statmechClonesWithDifferentCalculations() {
        return ["g1", "g2", "g3"].map((suffix) => baseRecord({
            statmech: { ...baseRecord().statmech, statmech_ref: `sm_${suffix}` },
        }))
    }

    it("lists each record's OWN opt/freq/sp source calculations and frequency calculation in the group table, without expanding anything -- never one record's evidence standing in for the whole group", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const records = statmechClonesWithDifferentCalculations().map((record) => ({
                ...record,
                ...(includes.includes("source_calculations") ? {
                    source_calculations: [
                        sourceCalc("opt", `calc_${record.statmech.statmech_ref}_opt`),
                        sourceCalc("freq", `calc_${record.statmech.statmech_ref}_freq`),
                        sourceCalc("sp", `calc_${record.statmech.statmech_ref}_sp`),
                    ],
                } : {}),
                ...(includes.includes("frequencies") ? {
                    frequencies: { source_freq_calculation_refs: [`calc_${record.statmech.statmech_ref}_freqcalc`], frequency_scale_factor_value: 0.999, note: null },
                } : {}),
            }))
            return HttpResponse.json(mockResponse(records))
        }))
        page()
        await screen.findByText("3 records with identical values")

        const groupCard = document.querySelector("article.identical-record-group") as HTMLElement
        const detail = groupCard.querySelector(".identical-record-group-detail") as HTMLElement
        const refsTable = await within(groupCard).findByRole("table", { name: "Records sharing these identical values" })
        // Reachable WITHOUT expanding "Show all".
        expect(detail.contains(refsTable)).toBe(false)

        await waitFor(() => expect(refsTable.querySelectorAll('td[data-label="Opt calc"] a')).toHaveLength(3))
        for (const [index, suffix] of ["g1", "g2", "g3"].entries()) {
            const ref = `sm_${suffix}`
            const row = groupRow(refsTable, index + 1)
            expect(cellAt(row, "Opt calc")).toBe("Optimisation")
            expect(cellAt(row, "Freq calc")).toBe("Frequency calculation")
            expect(cellAt(row, "SP calc")).toBe("Single-point energy")
            expect(cellAt(row, "Source freq calcs")).toBe("Frequency calculation")
            const hrefs = Array.from(row.querySelectorAll("a")).map((link) => link.getAttribute("href"))
            expect(hrefs).toEqual([`/calculations/calc_${ref}_opt`, `/calculations/calc_${ref}_freq`, `/calculations/calc_${ref}_sp`, `/calculations/calc_${ref}_freqcalc`])
            expect(row.textContent).not.toMatch(/calc_|sm_/)
        }

        // The shared body (outside the table, outside "Show all") never
        // shows a per-record "Record software" line or a "Frequencies"
        // heading standing in for the whole group -- "Show all"'s own
        // member cards legitimately carry both, so this checks only the
        // part of the group card that sits OUTSIDE that collapsed detail.
        const softwareLinesOutsideDetail = Array.from(groupCard.querySelectorAll("p"))
            .filter((paragraph) => /^Record software:/.test(paragraph.textContent ?? "") && !detail.contains(paragraph))
        expect(softwareLinesOutsideDetail).toHaveLength(0)
        const freqHeadingsOutsideDetail = Array.from(groupCard.querySelectorAll("h4"))
            .filter((heading) => heading.textContent === "Frequencies" && !detail.contains(heading))
        expect(freqHeadingsOutsideDetail).toHaveLength(0)
    })

    it("never mints a duplicate DOM id between the group card's own heading and the same representative record's card inside 'Show all'", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse(identicalClones()))))
        page()
        await screen.findByText("3 records with identical values")
        fireEvent.click(screen.getByText("Show all 3 records individually"))

        const idCounts = new Map<string, number>()
        document.querySelectorAll("[id]").forEach((el) => {
            idCounts.set(el.id, (idCounts.get(el.id) ?? 0) + 1)
        })
        const duplicates = [...idCounts.entries()].filter(([, count]) => count > 1)
        expect(duplicates).toEqual([])
    })
})

describe("EntryStatmechSection: design-system adoption (design/species-entry)", () => {
    // Four invariants, checked directly
    // against the rendered DOM, not inferred from source text, so a future
    // regression that reintroduces the old shape is caught by rendering
    // the real component tree, exactly like every other test in this file.

    it("never renders an <h2>/<h3>/<h4> inside a <summary> -- the lazy sections (Source calculations, Torsions, Electronic levels, Conformer context & review history) render a plain-text summary through the shared Disclosure component now, not a SectionHeading", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("sm_one")
        // Open every disclosure that has one, so a heading hiding inside a
        // collapsed <details> would still be caught (jsdom renders
        // collapsed <details> content in the DOM either way, but this also
        // exercises onToggle so the fetch-triggering wiring stays intact).
        document.querySelectorAll("details > summary").forEach((summary) => fireEvent.click(summary))
        const headingsInsideSummaries = document.querySelectorAll("summary h1, summary h2, summary h3, summary h4, summary h5, summary h6")
        expect(headingsInsideSummaries).toHaveLength(0)
    })

    // This tab's own "N records ·
    // review: …" line carries `.records-note` (margin-only) and must also
    // carry `.note`, or it falls back to unstyled 16px body text.
    it("the 'N records · review: …' line renders through .note, not a bare unstyled paragraph", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("sm_one")
        const recordsNote = document.querySelector(".records-note")
        expect(recordsNote).not.toBeNull()
        expect(recordsNote!.className.split(" ")).toContain("note")
    })

    it("every <details> on this tab is the shared Disclosure component -- carries the `disclosure` class design-system.css's chrome is keyed on, not a bare <details>", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("sm_one")
        // The default two-record fixture already renders four lazy-section
        // <details> (Source calculations, Torsions, Electronic levels,
        // Conformer context & review history) regardless of open/closed
        // state -- a <details> element and its class list are present in
        // the DOM either way, only its body's visibility differs.
        const detailsElements = Array.from(document.querySelectorAll("details"))
        expect(detailsElements.length).toBeGreaterThan(0)
        for (const details of detailsElements) {
            expect(details.className.split(" ")).toContain("disclosure")
        }
    })

    it("'not reviewed' renders in exactly one pill style -- .value-pill--muted, never .review-badge", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse())))
        page()
        await screen.findByText("sm_one")
        const notReviewedPills = screen.getAllByText("not reviewed")
        expect(notReviewedPills.length).toBeGreaterThan(0)
        for (const pill of notReviewedPills) {
            expect(pill.className.split(" ")).toContain("value-pill--muted")
            expect(pill.className.split(" ")).not.toContain("review-badge")
        }
    })

    it("every record table on this tab is the shared .data-table primitive, never .stage-table, and scrolls its own container rather than a page-body stacked fallback", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({
                available_sections: { ...baseRecord().available_sections, has_electronic_levels: true },
                electronic_levels: [{ level_index: 0, energy_cm1: 0, degeneracy: 1 }],
            }),
        ]))))
        page()
        await screen.findByText("sm_one")
        // Electronic levels is the one lazy section in this file whose
        // populated body is a <table> -- open it so that table mounts.
        fireEvent.click(screen.getByText("Electronic levels", { selector: "summary" }))
        await screen.findByRole("table", { name: "Electronic levels" })
        const tables = Array.from(document.querySelectorAll("table"))
        expect(tables.length).toBeGreaterThan(0)
        for (const table of tables) {
            expect(table.className.split(" ")).toContain("data-table")
            expect(table.className.split(" ")).not.toContain("stage-table")
            // Every real table on this tab sits inside a `.table-scroll`
            // wrapper (horizontal scroll), never bare in the page body.
            expect(table.closest(".table-scroll")).not.toBeNull()
        }
    })

    // SCIENTIFIC ERROR regression guard (sweep finding, same class as
    // `ArrheniusChart.test.tsx`'s "s⁻¹"/"log₁₀ k" guards): `.data-table th`
    // (design-system.css) uppercases every header via `--type-label-
    // strong-transform` -- "cm⁻¹" (wavenumber) would read "CM⁻¹". A
    // `textContent` assertion cannot see this -- the DOM text stays
    // correctly-cased; only the COMPUTED style differs -- so this asserts
    // `getComputedStyle`, per `vite.config.ts`'s `test.css: true`.
    it("numbers a record the same in its card, its lazy rows and the tables", async () => {
        const withLevels = (record: ReturnType<typeof baseRecord>) => ({
            ...record,
            available_sections: { ...record.available_sections, has_electronic_levels: true },
            electronic_levels: [{ level_index: 0, energy_cm1: 0, degeneracy: 1 }],
        })
        const [one, two] = mockRecords()
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([withLevels(one), withLevels(two)]))))
        page()
        await screen.findByText("sm_one")
        fireEvent.click(screen.getByText("Electronic levels", { selector: "summary" }))
        await screen.findAllByRole("table", { name: "Electronic levels" })
        const headings = Array.from(document.querySelectorAll("h3")).map((heading) => heading.textContent)
        const numbered = headings.filter((text) => text?.includes("statmech record"))
        expect(numbered.length).toBeGreaterThanOrEqual(4)
        expect(new Set(numbered)).toEqual(new Set(["computed statmech record 1", "estimated statmech record 2"]))
        expect(document.getElementById("statmech-heading-sm_two")).toHaveTextContent("estimated statmech record 2")
    })

    it("computed text-transform is none on the Electronic-levels table's 'Energy (cm⁻¹)' header", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({
                available_sections: { ...baseRecord().available_sections, has_electronic_levels: true },
                electronic_levels: [{ level_index: 0, energy_cm1: 0, degeneracy: 1 }],
            }),
        ]))))
        page()
        await screen.findByText("sm_one")
        fireEvent.click(screen.getByText("Electronic levels", { selector: "summary" }))
        const table = await screen.findByRole("table", { name: "Electronic levels" })
        expect(getComputedStyle(within(table).getByRole("columnheader", { name: "Level" })).textTransform).not.toBe("none")
        expect(getComputedStyle(within(table).getByRole("columnheader", { name: "Energy (cm⁻¹)" })).textTransform).toBe("none")
    })
})

// ---------------------------------------------------------------------------
// "Conformer context" and "Review history" fold into one disclosure, one
// table, one row per record (`ConformerAndReviewSection`).
// ---------------------------------------------------------------------------
describe("EntryStatmechSection: conformer context & review history fold into one table", () => {
    it("renders one combined disclosure, not two, with each record's ref appearing exactly once outside the group table", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const records = mockRecords().map((record) => ({
                ...record,
                ...(includes.includes("conformers")
                    ? { conformers: [{ conformer_group_ref: "cg_one", label: "conformer_1" }] }
                    : {}),
                ...(includes.includes("review")
                    ? { review_history: [{ status: "not_reviewed", reviewed_at: null, note: null }] }
                    : {}),
            }))
            return HttpResponse.json(mockResponse(records))
        }))
        page()
        await screen.findByText("sm_one")

        expect(screen.queryByText("Conformer context", { selector: "summary" })).not.toBeInTheDocument()
        expect(screen.queryByText("Review history", { selector: "summary" })).not.toBeInTheDocument()
        const summary = screen.getByText("Conformer context & review history", { selector: "summary" })
        expect(summary).toBeVisible()

        fireEvent.click(summary)
        const table = await screen.findByRole("table", { name: "Conformer context and review history" })
        for (const label of ["Record 1", "Record 2"]) {
            const matches = within(table).getAllByText(label)
            expect(matches).toHaveLength(1)
        }
        expect(table.textContent).not.toMatch(/sm_/)
        const rows = within(table).getAllByRole("row").slice(1)
        expect(rows).toHaveLength(2)
        // The cell names the conformer group in words, never the ref and
        // never the depositor's own `label` (e.g. "conformer_1") -- no
        // depositor-typed labels on public pages.
        expect(cellAt(rows[0], "Conformer")).toContain("Conformer group 1")
        expect(cellAt(rows[0], "Conformer")).not.toContain("cg_one")
        expect(cellAt(rows[0], "Conformer")).not.toContain("conformer_1")
        expect(cellAt(rows[0], "Review")).toBe("not reviewed")
    })

    it("numbers a conformer group once for the whole table, not again for each row", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const [one, two] = mockRecords()
            const group = (ref: string) => ({ conformer_group_ref: ref, label: "x" })
            return HttpResponse.json(mockResponse([
                { ...one, ...(includes.includes("conformers") ? { conformers: [group("cg_a")] } : {}) },
                { ...two, ...(includes.includes("conformers") ? { conformers: [group("cg_b"), group("cg_a")] } : {}) },
            ]))
        }))
        page()
        await screen.findByText("sm_one")
        fireEvent.click(screen.getByText("Conformer context & review history", { selector: "summary" }))
        const table = await screen.findByRole("table", { name: "Conformer context and review history" })
        const rows = within(table).getAllByRole("row").slice(1)
        expect(cellAt(rows[0], "Conformer")).toBe("Conformer group 1")
        expect(cellAt(rows[1], "Conformer")).toBe("Conformer group 2 · Conformer group 1")
    })

    it("collapses multiple conformer-context or review-history entries for one record into a single cell, joined, rather than adding rows", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const record = baseRecord({
                ...(includes.includes("conformers")
                    ? {
                        conformers: [
                            { conformer_group_ref: "cg_one", label: "conformer_1" },
                            { conformer_group_ref: "cg_two", label: "conformer_2" },
                        ],
                    }
                    : {}),
                ...(includes.includes("review")
                    ? {
                        review_history: [
                            { status: "not_reviewed", reviewed_at: null, note: null },
                            { status: "approved", reviewed_at: "2026-01-05T00:00:00", note: "looks right" },
                        ],
                    }
                    : {}),
            })
            return HttpResponse.json(mockResponse([record]))
        }))
        page()
        await screen.findByText("sm_one")
        fireEvent.click(screen.getByText("Conformer context & review history", { selector: "summary" }))
        const table = await screen.findByRole("table", { name: "Conformer context and review history" })
        const rows = within(table).getAllByRole("row").slice(1)
        expect(rows).toHaveLength(1)
        expect(cellAt(rows[0], "Conformer")).toContain("Conformer group 1")
        expect(cellAt(rows[0], "Conformer")).toContain("Conformer group 2")
        expect(cellAt(rows[0], "Conformer")).not.toMatch(/cg_/)
        expect(cellAt(rows[0], "Conformer")).not.toContain("conformer_1")
        expect(cellAt(rows[0], "Conformer")).not.toContain("conformer_2")
        expect(cellAt(rows[0], "Review")).toContain("not reviewed")
        expect(cellAt(rows[0], "Review")).toContain("approved")
    })
})

// ---------------------------------------------------------------------------
// A statmech record can use different levels of
// theory for its optimised geometry, its frequencies, and its electronic
// energy. These prove `domain/productLevels.ts`'s rendering rule end to
// end: ALWAYS three labelled facts -- Geometry/Frequencies/Energy -- never
// collapsed to one "Level of theory" fact even when all three agree, and
// read identically whether the server sends the additive `levels` field
// directly or this client derives it from `source_calculations[]` roles.
// ---------------------------------------------------------------------------
const geomLot = { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", level_of_theory_ref: "lot_geom" }
const energyLot = { method: "ccsd(t)", basis: "cc-pvtz", display: "ccsd(t)/cc-pvtz", level_of_theory_ref: "lot_energy" }

function sourceCalcWithLot(role: string, calculationRef: string, levelOfTheory: unknown) {
    return { ...sourceCalc(role, calculationRef), level_of_theory: levelOfTheory }
}

describe("EntryStatmechSection -- geometry/frequency/energy levels of theory", () => {
    it("still renders all three Geometry/Frequencies/Energy facts, plus an 'all at the same level' note, when the opt/freq/sp roles all resolve to the same level", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const record = baseRecord({
                ...(includes.includes("source_calculations") ? {
                    source_calculations: [
                        sourceCalcWithLot("opt", "calc_opt", geomLot),
                        sourceCalcWithLot("freq", "calc_freq", geomLot),
                        sourceCalcWithLot("sp", "calc_sp", geomLot),
                    ],
                } : {}),
            })
            return HttpResponse.json(mockResponse([record]))
        }))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        await within(card).findByText("Geometry", { selector: "dt" })
        expect(ddFor(card, "Geometry")).toBe("b3lyp/def2tzvp")
        expect(ddFor(card, "Frequencies")).toBe("b3lyp/def2tzvp")
        // The sp role landing at the SAME level as opt/freq is still a real
        // sp-sourced energy -- the note follows `energy_source === "sp"`
        // unconditionally now, agreeing or not (see `ProductLevelsTableCells`'s
        // own doc on why "this row's own agreement" stopped being the gate).
        expect(ddFor(card, "Energy")).toContain("b3lyp/def2tzvp")
        expect(ddFor(card, "Energy")).toContain("single point on the optimised geometry")
        expect(within(card).queryByText("Level of theory", { selector: "dt" })).not.toBeInTheDocument()
        expect(within(card).getByText("all at the same level")).toBeInTheDocument()
    })

    it("expands to Geometry/Frequencies/Energy facts, with a note under Energy, when the sp role is at a different level than opt/freq", async () => {
        server.use(http.get(ENDPOINT, ({ request }) => {
            const includes = new URL(request.url).searchParams.getAll("include")
            const record = baseRecord({
                ...(includes.includes("source_calculations") ? {
                    source_calculations: [
                        sourceCalcWithLot("opt", "calc_opt", geomLot),
                        sourceCalcWithLot("freq", "calc_freq", geomLot),
                        sourceCalcWithLot("sp", "calc_sp", energyLot),
                    ],
                } : {}),
            })
            return HttpResponse.json(mockResponse([record]))
        }))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        await within(card).findByText("Geometry", { selector: "dt" })
        expect(ddFor(card, "Geometry")).toBe("b3lyp/def2tzvp")
        expect(ddFor(card, "Frequencies")).toBe("b3lyp/def2tzvp")
        expect(ddFor(card, "Energy")).toContain("ccsd(t)/cc-pvtz")
        expect(ddFor(card, "Energy")).toContain("single point on the optimised geometry")
        expect(within(card).queryByText("Level of theory", { selector: "dt" })).not.toBeInTheDocument()
    })

    it("renders the agreeing case identically whether the server sends `levels` directly or this client derives it from source_calculations", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({ levels: { geometry: geomLot, frequency: geomLot, energy: geomLot, energy_source: "opt" } }),
        ]))))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        await within(card).findByText("Geometry", { selector: "dt" })
        expect(ddFor(card, "Geometry")).toBe("b3lyp/def2tzvp")
        expect(ddFor(card, "Frequencies")).toBe("b3lyp/def2tzvp")
        expect(ddFor(card, "Energy")).toBe("b3lyp/def2tzvp")
        expect(within(card).queryByText("Level of theory", { selector: "dt" })).not.toBeInTheDocument()
        expect(within(card).getByText("all at the same level")).toBeInTheDocument()
    })

    it("renders the differing case identically whether the server sends `levels` directly or this client derives it from source_calculations", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({ levels: { geometry: geomLot, frequency: geomLot, energy: energyLot, energy_source: "sp" } }),
        ]))))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        await within(card).findByText("Geometry", { selector: "dt" })
        expect(ddFor(card, "Geometry")).toBe("b3lyp/def2tzvp")
        expect(ddFor(card, "Frequencies")).toBe("b3lyp/def2tzvp")
        expect(ddFor(card, "Energy")).toContain("ccsd(t)/cc-pvtz")
        expect(ddFor(card, "Energy")).toContain("single point on the optimised geometry")
    })

    it("shows a composite/imported energy_source as a muted pill, not a note", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({ levels: { geometry: geomLot, frequency: geomLot, energy: null, energy_source: "composite" } }),
        ]))))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        await within(card).findByText("Geometry", { selector: "dt" })
        const energyDt = Array.from(card.querySelectorAll("dt")).find((el) => el.textContent === "Energy")!
        const energyDd = energyDt.nextElementSibling as HTMLElement
        expect(within(energyDd).getByText("composite")).toHaveClass("value-pill", "value-pill--muted")
        expect(energyDd).not.toHaveTextContent("single point on the optimised geometry")
    })

    it("shows an UNRECOGNISED energy_source ('ambiguous', not composite/imported/opt/sp) as a muted pill too -- any source word gets a pill, not just the two known ones", async () => {
        server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
            baseRecord({ levels: { geometry: geomLot, frequency: geomLot, energy: null, energy_source: "ambiguous" } }),
        ]))))
        page()
        const card = (await screen.findByText("sm_one")).closest("article") as HTMLElement
        await within(card).findByText("Geometry", { selector: "dt" })
        const energyDt = Array.from(card.querySelectorAll("dt")).find((el) => el.textContent === "Energy")!
        const energyDd = energyDt.nextElementSibling as HTMLElement
        expect(within(energyDd).getByText("ambiguous")).toHaveClass("value-pill", "value-pill--muted")
        // Never "not recorded" once a source word is present to explain the
        // absence -- "not recorded" would say nothing at all is known,
        // which is false here.
        expect(energyDd).not.toHaveTextContent("not recorded")
    })

    describe("in the 'Records in this group' table", () => {
        function threeIdenticalRecords() {
            return [
                baseRecord({ statmech: { ...baseRecord().statmech, statmech_ref: "sm_g1" } }),
                baseRecord({ statmech: { ...baseRecord().statmech, statmech_ref: "sm_g2" } }),
                baseRecord({ statmech: { ...baseRecord().statmech, statmech_ref: "sm_g3" } }),
            ]
        }

        it("always shows Geometry/Frequencies/Energy columns, even when every member's own levels agree with itself", async () => {
            server.use(http.get(ENDPOINT, ({ request }) => {
                const includes = new URL(request.url).searchParams.getAll("include")
                const records = threeIdenticalRecords().map((record) => ({
                    ...record,
                    ...(includes.includes("source_calculations") ? {
                        source_calculations: [sourceCalcWithLot("opt", `calc_opt_${record.statmech.statmech_ref}`, geomLot)],
                    } : {}),
                }))
                return HttpResponse.json(mockResponse(records))
            }))
            page()
            await screen.findByText("3 records with identical values")
            const refsTable = screen.getByRole("table", { name: "Records sharing these identical values" })
            // Wait for the real level of theory to land (not the transient
            // "loading…" placeholder), same reasoning as the disagreeing
            // case's own wait below -- otherwise this assertion could pass
            // vacuously while `sourceCalcsState` is still loading.
            // `findAllByText` (plural): all three rows report the same
            // level, and `findByText` would treat that as a "multiple
            // elements" failure on every retry until it times out.
            await within(refsTable).findAllByText("b3lyp/def2tzvp")
            const headers = within(refsTable).getAllByRole("columnheader").map((cell) => cell.textContent)
            expect(headers).toEqual(expect.arrayContaining(["Geometry", "Frequencies", "Energy"]))
            expect(headers).not.toContain("Level of theory")
        })

        it("shows Geometry/Frequencies/Energy columns (always) when one member's own levels disagree, and the shared body above the table shows nothing instead of guessing", async () => {
            server.use(http.get(ENDPOINT, ({ request }) => {
                const includes = new URL(request.url).searchParams.getAll("include")
                const records = threeIdenticalRecords().map((record) => ({
                    ...record,
                    ...(includes.includes("source_calculations") ? {
                        source_calculations: record.statmech.statmech_ref === "sm_g3"
                            ? [sourceCalcWithLot("opt", "calc_opt_g3", geomLot), sourceCalcWithLot("sp", "calc_sp_g3", energyLot)]
                            : [sourceCalcWithLot("opt", `calc_opt_${record.statmech.statmech_ref}`, geomLot)],
                    } : {}),
                }))
                return HttpResponse.json(mockResponse(records))
            }))
            page()
            await screen.findByText("3 records with identical values")
            const refsTable = screen.getByRole("table", { name: "Records sharing these identical values" })
            // Both the entry-scoped `source_calculations` include and this
            // main listing resolve concurrently -- wait for the former's own
            // eager fetch to land (an opt-calc ref this table's "Opt calc"
            // column only shows once `sourceCalcsState` is ready) before
            // reading which level-of-theory column layout it settled on.
            await waitFor(() => expect(groupRow(refsTable, 3).querySelector('a[href="/calculations/calc_opt_g3"]')).not.toBeNull())

            const headers = within(refsTable).getAllByRole("columnheader").map((cell) => cell.textContent)
            expect(headers).toEqual(expect.arrayContaining(["Geometry", "Frequencies", "Energy"]))
            expect(headers).not.toContain("Level of theory")
            const g3Row = groupRow(refsTable, 3)
            expect(cellAt(g3Row, "Energy")).toContain("ccsd(t)/cc-pvtz")
            const g1Row = groupRow(refsTable, 1)
            expect(cellAt(g1Row, "Energy")).toContain("b3lyp/def2tzvp")

            // The group's own shared body (everything above "Records in this
            // group", excluding the "Show all" disclosure's own nested
            // per-record cards) shows no levels fact at all -- disagreement
            // among the group's own members means nothing is safe to lift.
            const groupCard = document.querySelector("article.identical-record-group") as HTMLElement
            const sharedDts = Array.from(groupCard.querySelectorAll("dt")).filter((dt) => !dt.closest("details"))
            expect(sharedDts.map((dt) => dt.textContent)).not.toContain("Geometry")
            expect(sharedDts.map((dt) => dt.textContent)).not.toContain("Level of theory")
        })

        it("shows the sp note on EVERY sp-sourced row, not only the one whose own three levels happen to disagree", async () => {
            // All three cite an sp role (energy_source: "sp"); only sm_g2's
            // own energy is at a DIFFERENT level than its geometry/
            // frequency -- sm_g1 and sm_g3 are sp-derived at the SAME level
            // as their own geometry/frequency (a real, if unremarkable,
            // case: a dedicated sp job that happens to land on the same
            // level). Now that every table always shows three columns,
            // "this row's own agreement" is no longer a meaningful gate for
            // the note -- it simply follows `energy_source === "sp"`, so
            // all three rows get it.
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
                baseRecord({
                    statmech: { ...baseRecord().statmech, statmech_ref: "sm_g1" },
                    levels: { geometry: geomLot, frequency: geomLot, energy: geomLot, energy_source: "sp" },
                }),
                baseRecord({
                    statmech: { ...baseRecord().statmech, statmech_ref: "sm_g2" },
                    levels: { geometry: geomLot, frequency: geomLot, energy: energyLot, energy_source: "sp" },
                }),
                baseRecord({
                    statmech: { ...baseRecord().statmech, statmech_ref: "sm_g3" },
                    levels: { geometry: geomLot, frequency: geomLot, energy: geomLot, energy_source: "sp" },
                }),
            ]))))
            page()
            await screen.findByText("3 records with identical values")
            const refsTable = screen.getByRole("table", { name: "Records sharing these identical values" })
            await within(refsTable).findByText("ccsd(t)/cc-pvtz")

            const headers = within(refsTable).getAllByRole("columnheader").map((cell) => cell.textContent)
            expect(headers).toEqual(expect.arrayContaining(["Geometry", "Frequencies", "Energy"]))

            const g1Row = groupRow(refsTable, 1)
            const g2Row = groupRow(refsTable, 2)
            const g3Row = groupRow(refsTable, 3)
            expect(cellAt(g1Row, "Energy")).toContain("single point on the optimised geometry")
            expect(cellAt(g2Row, "Energy")).toContain("single point on the optimised geometry")
            expect(cellAt(g3Row, "Energy")).toContain("single point on the optimised geometry")
        })

        it("shows an unrecognised energy_source as a muted pill in the table too, never 'not recorded'", async () => {
            server.use(http.get(ENDPOINT, () => HttpResponse.json(mockResponse([
                baseRecord({
                    statmech: { ...baseRecord().statmech, statmech_ref: "sm_g1" },
                    levels: { geometry: geomLot, frequency: geomLot, energy: null, energy_source: "ambiguous" },
                }),
                baseRecord({
                    statmech: { ...baseRecord().statmech, statmech_ref: "sm_g2" },
                    levels: { geometry: geomLot, frequency: geomLot, energy: energyLot, energy_source: "sp" },
                }),
                baseRecord({
                    statmech: { ...baseRecord().statmech, statmech_ref: "sm_g3" },
                    levels: { geometry: geomLot, frequency: geomLot, energy: energyLot, energy_source: "sp" },
                }),
            ]))))
            page()
            await screen.findByText("3 records with identical values")
            const refsTable = screen.getByRole("table", { name: "Records sharing these identical values" })
            // sm_g2 and sm_g3 both report "ccsd(t)/cc-pvtz" -- `findAllByText`
            // (plural), since `findByText` treats two matches as failure on
            // every retry until it times out (see the earlier column tests'
            // own note on this).
            await within(refsTable).findAllByText("ccsd(t)/cc-pvtz")

            const g1Row = groupRow(refsTable, 1)
            const energyCell = g1Row.querySelector('td[data-label="Energy"]') as HTMLElement
            expect(within(energyCell).getByText("ambiguous")).toHaveClass("value-pill", "value-pill--muted")
            expect(energyCell).not.toHaveTextContent("not recorded")
        })
    })
})

describe("EntryStatmechSection: paging", () => {
    it("pages the list, and the section requests it makes for the page on screen carry that page's window", async () => {
        const seen: Array<{ offset: string | null; includes: string[] }> = []
        server.use(http.get(ENDPOINT, ({ request }) => {
            const params = new URL(request.url).searchParams
            const offset = Number(params.get("offset") ?? 0)
            const limit = Number(params.get("limit") ?? 1)
            seen.push({ offset: params.get("offset"), includes: params.getAll("include") })
            const all = mockRecords()
            const records = all.slice(offset, offset + limit)
            return HttpResponse.json({
                ...mockResponse(records),
                pagination: { offset, limit, returned: records.length, total: all.length },
            })
        }))
        page()
        expect(await screen.findByText(`Showing 1–1 of ${mockRecords().length} statistical-mechanics records.`)).toBeVisible()
        fireEvent.click(screen.getByRole("button", { name: "Next" }))
        expect(await screen.findByText(`Showing 2–2 of ${mockRecords().length} statistical-mechanics records.`)).toBeVisible()
        // The eager on-demand sections (source calculations, frequencies) are refetched for THIS window, never
        // read from the first page's response: a record on page 2 must not be matched against page 1's data.
        await waitFor(() => expect(seen.some((call) => call.offset === "1" && call.includes.includes("frequencies"))).toBe(true))
    })
})

describe("EntryStatmechSection -- no depositor-typed text", () => {
    it("prints none of the typed fields: point group, torsion top and reasons, notes, joined species label", async () => {
        const withTorsions = baseRecord({
            statmech: { ...baseRecord().statmech, statmech_ref: "sm_two" },
            available_sections: { ...baseRecord().available_sections, has_torsions: true },
            torsions: [{
                torsion_index: 0, treatment_kind: "hindered_rotor", symmetry_number: 3, dimension: 1,
                top_description: "x", invalidated_reason: "x", note: "x", source_scan_calculation_ref: "calc_scan", coordinates: [],
            }],
        })
        const planted = plantSentinels(mockResponse([baseRecord(), withTorsions]), ["point_group", "top_description", "invalidated_reason", "note"])
        expectPlanted(planted)
        const { value } = planted
        // A point group that is not a Schoenflies symbol, and a species label that joins in typed parts.
        for (const record of value.records as { statmech: { point_group: string }; species: { species_entry_label: string | null } }[]) {
            record.species.species_entry_label = ADVERSARIAL.joinedLabel
        }
        ;(value.records[0] as { statmech: { point_group: string } }).statmech.point_group = ADVERSARIAL.pointGroup
        server.use(http.get(ENDPOINT, () => HttpResponse.json(value)))
        page()
        await screen.findByText("sm_one")
        fireEvent.click(screen.getByText("Torsions"))
        const section = screen.getByText("Torsions").closest("details") as HTMLDetailsElement
        await within(section).findByText("Torsions loaded.")
        // The fixture reached the page: the torsion rows and both records rendered, the typed point group as "not shown".
        expect(within(section).getAllByRole("row").length).toBeGreaterThan(1)
        expect(cardOf("sm_two")).toBeInTheDocument()
        expect(screen.getAllByText("not shown").length).toBeGreaterThan(0)

        expectNoDepositorText()
        expectNoAdversarialText()
    })
})
