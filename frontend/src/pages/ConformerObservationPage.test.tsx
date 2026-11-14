import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ConformerObservationPage from "./ConformerObservationPage"
import { conformerReadHandlers } from "../test/conformerReads"
import { ADVERSARIAL, expectDepositorTextOnlyInNotes, expectNoAdversarialText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
import { rankingWords, textOf } from "../test/rankingWords"

/** Reads the `<dd>` text for a `.kv-list`-shaped `<dt>` term inside `container` --
 *  the same helper `CalculationDetailPage.test.tsx` uses on its own coverage
 *  checklist, reused here now both pages render the identical structure. */
function ddFor(container: HTMLElement, term: string): string {
    const dt = Array.from(container.querySelectorAll("dt")).find((el) => el.textContent === term)
    if (!dt) throw new Error(`No <dt> with text "${term}" found in this container`)
    return dt.nextElementSibling?.textContent ?? ""
}

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
beforeEach(() => server.use(...conformerReadHandlers({ entryRef: "spe_demo" })))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function page() {
    return render(
        <MemoryRouter initialEntries={["/conformer-observations/co_one"]}>
            <Routes>
                <Route path="/conformer-observations/:observationRef" element={<ConformerObservationPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

function mockRecord(overrides: Record<string, unknown> = {}) {
    return {
        conformer_observation: {
            conformer_observation_ref: "co_one",
            scientific_origin: "computed",
            note: "Coarse pre-optimisation basin",
            created_at: "2026-07-21T12:06:50.748258",
            review: { status: "reviewed" },
        },
        conformer_group: {
            conformer_group_ref: "cg_demo",
            label: "conformer_1",
            note: null,
            review: { status: "not_reviewed" },
        },
        species: {
            species_ref: "spc_demo",
            species_entry_ref: "spe_demo",
            species_entry_label: "ground state",
            formula: "CH3",
            canonical_smiles: "[CH3]",
        },
        assignment_scheme: null,
        evidence_summary: {
            calculation_count: 3,
            geometry_count: 2,
            has_opt: true,
            has_freq: true,
            has_sp: false,
            has_geometry_validation: true,
            has_scf_stability: false,
            levels_of_theory: {
                opt: [{ level_of_theory_ref: "lot_1", method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }],
                freq: [{ level_of_theory_ref: "lot_2", method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp" }],
            },
        },
        available_sections: {
            has_observations: true,
            has_selections: false,
            has_calculations: true,
            has_geometries: true,
            has_review: true,
        },
        // Lean sibling projection (`ConformerObservationSiblingSummary`)
        // -- a ref + review badge, nothing else. Siblings no
        // longer carry their own evidence_summary/available_sections/
        // calculations/geometries: that data is one hop away, at the
        // sibling's own detail endpoint.
        observations: [
            {
                conformer_observation: {
                    conformer_observation_ref: "co_one",
                    review: { status: "reviewed" },
                },
            },
            {
                conformer_observation: {
                    conformer_observation_ref: "co_two",
                    review: { status: "not_reviewed" },
                },
            },
        ],
        calculations: [
            {
                calculation_ref: "calc_opt",
                type: "opt",
                quality: "raw",
                review: { status: "reviewed" },
                level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
                software_release: { software: "Gaussian" },
                workflow_tool_release: { workflow_tool: "ARC" },
            },
            {
                calculation_ref: "calc_sp",
                type: "sp",
                quality: "raw",
                review: { status: "approved" },
                level_of_theory: { method: "ccsd(t)", basis: "cc-pvtz" },
                software_release: { software: "Molpro", version: "2024" },
            },
            {
                calculation_ref: "calc_freq",
                type: "freq",
                quality: "raw",
                review: { status: "not_reviewed" },
                level_of_theory: { method: "wb97xd", basis: "def2tzvp" },
                software_release: { software: "Gaussian" },
            },
        ],
        geometries: [
            { calculation_ref: "calc_opt", geometry: { geometry_ref: "geo_one", natoms: 4 } },
            { calculation_ref: "calc_freq", geometry: { geometry_ref: "geo_two", natoms: 4 } },
        ],
        review_history: [
            { status: "reviewed", reviewed_at: "2026-07-25T00:00:00", note: "Looks consistent" },
        ],
        selections: [],
        ...overrides,
    }
}

describe("ConformerObservationPage", () => {
    const SP_RECORD = {
    calculation: { calculation_ref: "calc_sp", calculation_type: "sp", review: { status: "approved" } },
    energy: { energy_hartree: -40.123456789, energy_kind: "electronic" },
    conformer: { conformer_observation_ref: "co_one", conformer_group_ref: "cg_demo" },
    level_of_theory: { method: "ccsd(t)", basis: "cc-pvtz" },
    software_release: { software: "Molpro", version: "2024" },
}

    function serve(record: unknown = mockRecord(), energies: { sp?: unknown[]; opt?: unknown[] } = { sp: [SP_RECORD] }) {
        server.use(
            ...conformerReadHandlers({ entryRef: "spe_demo", energies }),
            http.get("/api/v1/scientific/conformer-observations/co_one", ({ request }) => {
                expect(new URL(request.url).searchParams.getAll("include")).toEqual([
                    "observations", "selections", "calculations", "geometries", "review",
                ])
                return HttpResponse.json({ record })
            }),
        )
    }

    it("titles the record by what it is, never by a ref", async () => {
        serve()
        page()
        const h1 = await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect(h1.textContent).toBe("Observation of Conformer Group 1 of CH3")
        expect(h1.textContent).not.toMatch(/co_|cg_/)
    })

    it("says what the page is in one sentence, with a glyph and the review status as a word", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect(screen.getByText(/One deposit of this conformer\./)).toBeVisible()
        expect(screen.getByText(/3 calculations, in archive order \(not dependency order\)/)).toBeVisible()
        const kickerRow = document.querySelector(".record-identity-kicker-row") as HTMLElement
        expect(kickerRow.querySelector("svg")).not.toBeNull()
        expect(within(kickerRow).getByText("reviewed")).toBeVisible()
    })

    it("carries Molecules, the molecule, its version, the conformer and this observation as breadcrumbs", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        const links = within(breadcrumb).getAllByRole("link")
        expect(links.map((link) => link.getAttribute("href"))).toEqual([
            "/species", "/species/spc_demo", "/species-entries/spe_demo", "/conformer-groups/cg_demo",
        ])
        expect(links[3]).toHaveTextContent("Conformer Group 1")
        expect(within(breadcrumb).getByText("Observation")).toHaveAttribute("aria-current", "page")
    })

    it("renders without a conformer link or list when a curated read withholds the group", async () => {
        serve(mockRecord({ conformer_group: null }))
        const { container } = page()
        expect(await screen.findByRole("heading", { level: 1, name: /^Conformer observation of CH3/ })).toBeVisible()
        expect(container.querySelector('a[href^="/conformer-groups/"]')).toBeNull()
        expect(Array.from(container.querySelectorAll("dt")).some((el) => el.textContent === "Conformer")).toBe(false)
        expect(screen.queryByRole("heading", { name: "Other observations of this conformer" })).toBeNull()
        expect(screen.queryByText("co_two")).toBeNull()
        expect(screen.getAllByText("co_one").length).toBeGreaterThan(0)
        expect(container.querySelector('a[href="/species-entries/spe_demo"]')).not.toBeNull()
    })

    it("draws the observation's first final geometry with a Show in 3D button", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const figure = await screen.findByRole("figure", { name: "Picture of this conformer" })
        expect(await within(figure).findByRole("img", { name: /Ball-and-stick picture of CH3/ })).toBeVisible()
        expect(within(figure).getByRole("button", { name: "Show in 3D" })).toBeVisible()
        expect(within(figure).getByText(/Final geometry of this observation\./)).toBeVisible()
    })

    it("shows a source line with level, program and review under each energy, and the calculations that found the geometry", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const section = screen.getByRole("heading", { name: "Calculations and energies" }).closest("section") as HTMLElement
        const energy = await within(section).findByText("−40.123457")
        expect(energy.closest("a")).toHaveAttribute("href", "/calculations/calc_sp")
        expect(energy).toHaveAttribute("title", "-40.123456789")
        expect(energy.closest("li")).toHaveTextContent(/Source Single-point energy · single-point · at ccsd\(t\)\/cc-pvtz · Molpro 2024 · Review of this record: approved/)
        expect(within(section).getAllByText(/^b3lyp\/def2tzvp/).length).toBeGreaterThan(0)
        const freqLink = section.querySelector('a[href="/calculations/calc_freq"]') as HTMLElement
        expect(freqLink).toHaveAccessibleName("Calculation")
        expect(section.textContent).not.toMatch(/calc_/)
    })

    it("shows only the source link under an optimisation energy whose calculation is under Found by, which carries its level and review", async () => {
        const OPT_RECORD = {
            calculation: { calculation_ref: "calc_opt", calculation_type: "opt", review: { status: "reviewed" } },
            energy: { energy_hartree: -40.2, energy_kind: "electronic" },
            conformer: { conformer_observation_ref: "co_one", conformer_group_ref: "cg_demo" },
            level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
            software_release: { software: "Gaussian" },
        }
        serve(mockRecord(), { sp: [SP_RECORD], opt: [OPT_RECORD] })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const section = screen.getByRole("heading", { name: "Calculations and energies" }).closest("section") as HTMLElement
        const optEnergy = await within(section).findByText("−40.200000")
        expect(optEnergy.closest("li")).toHaveTextContent(/^−40\.200000 hartree \(final energy of the optimisation\)\s*Source Calculation$/)
        const foundBy = within(section).getByText("Found by").closest("div") as HTMLElement
        const foundRow = foundBy.querySelector('a[href="/calculations/calc_opt"]')!.closest("li") as HTMLElement
        expect(foundRow).toHaveTextContent(/at b3lyp\/def2tzvp/)
        expect(within(foundRow).getByText("reviewed")).toBeVisible()
        // The single point is not under Found by, so its source line stays whole.
        expect(within(section).getByText("−40.123457").closest("li")).toHaveTextContent(/Source Single-point energy · single-point · at ccsd\(t\)\/cc-pvtz · Molpro 2024 · Review of this record: approved/)
    })

    it("keeps an energy's level when its Found by row has none of its own", async () => {
        const OPT_RECORD = {
            calculation: { calculation_ref: "calc_opt", calculation_type: "opt", review: { status: "reviewed" } },
            energy: { energy_hartree: -40.2, energy_kind: "electronic" },
            conformer: { conformer_observation_ref: "co_one", conformer_group_ref: "cg_demo" },
            level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
            software_release: { software: "Gaussian" },
        }
        const base = mockRecord() as { calculations: Array<Record<string, unknown>> }
        const calculations = base.calculations.map((c) => (c.calculation_ref === "calc_opt" ? { ...c, level_of_theory: undefined } : c))
        serve(mockRecord({ calculations }), { sp: [SP_RECORD], opt: [OPT_RECORD] })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const section = screen.getByRole("heading", { name: "Calculations and energies" }).closest("section") as HTMLElement
        const optEnergy = await within(section).findByText("−40.200000")
        expect(optEnergy.closest("li")).toHaveTextContent(/Source Calculation · at b3lyp\/def2tzvp$/)
    })

    it("lists no energy that the read attributes to another observation", async () => {
        const other = { ...SP_RECORD, calculation: { ...SP_RECORD.calculation, calculation_ref: "calc_sp_other" }, energy: { energy_hartree: -41.5, energy_kind: "electronic" }, conformer: { conformer_observation_ref: "co_two", conformer_group_ref: "cg_demo" } }
        serve(mockRecord(), { sp: [SP_RECORD, other] })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        await screen.findByText("−40.123457")
        expect(screen.queryByText("−41.500000")).toBeNull()
        expect(document.querySelector('a[href="/calculations/calc_sp_other"]')).toBeNull()
    })

    it("says an energy is not recorded when its read came back without it", async () => {
        serve(mockRecord(), { sp: [] })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect((await screen.findAllByText("Energy not recorded")).length).toBeGreaterThan(0)
    })

    it("says the energies could not be read when that read fails", async () => {
        server.use(
            http.get("/api/v1/scientific/species-calculations/search", () => HttpResponse.json({}, { status: 500 })),
            ...conformerReadHandlers({ entryRef: "spe_demo" }),
            http.get("/api/v1/scientific/conformer-observations/co_one", () => HttpResponse.json({ record: mockRecord() })),
        )
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect(await screen.findByText("The single-point energies could not be read.")).toBeVisible()
    })

    it("links breadcrumbs and provenance rows, and surfaces stable public refs", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect(screen.getByRole("link", { name: "Conformer observation: Another observation" })).toHaveAttribute("href", "/conformer-observations/co_two")
        expect(screen.queryByRole("link", { name: "co_one" })).not.toBeInTheDocument()
        expect(screen.getByRole("heading", { name: "Review history" })).toBeVisible()
        expect(screen.getByText("Looks consistent")).toBeVisible()
        expect(screen.getAllByRole("link", { name: "Geometry: Geometry, 4 atoms" })[0]).toHaveAttribute("href", "/geometries/geo_one")
    })

    it("shows the observation's own ref first in the identity block, with the data face and a copy button", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const identityFacts = screen.getByText("Observation ref").closest(".record-identity-facts") as HTMLElement
        const ownRef = within(identityFacts).getByText("co_one")
        expect(ownRef.tagName).toBe("CODE")
        expect(ownRef).toHaveClass("data")
        expect(Array.from(identityFacts.children)[0]).toHaveTextContent("Observation ref")
        expect(within(identityFacts).getByRole("button", { name: /copy observation ref/i })).toBeVisible()
    })

    it("falls back to the words 'Species entry', never the ref, when the species context carries no SMILES and no formula", async () => {
        serve(mockRecord({
            species: { species_ref: "spc_demo", species_entry_ref: "spe_demo", species_entry_label: "ground state", formula: null, canonical_smiles: null },
        }))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1/ })
        const identityHeader = document.querySelector(".record-identity-header") as HTMLElement
        const link = within(identityHeader).getByRole("link", { name: "Species entry" })
        expect(link).toHaveAttribute("href", "/species-entries/spe_demo")
        expect(link.textContent).toBe("Species entry")
    })

    it("collapses review history to one line when its only entry carries no real event", async () => {
        serve(mockRecord({
            conformer_observation: {
                conformer_observation_ref: "co_one", scientific_origin: "computed", note: null,
                created_at: "2026-07-21T12:06:50.748258", review: { status: "not_reviewed" },
            },
            review_history: [{ status: "not_reviewed", reviewed_at: null, note: null }],
        }))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect(screen.getByRole("heading", { name: "Review history" })).toBeVisible()
        expect(screen.queryByRole("table", { name: "Review history" })).not.toBeInTheDocument()
        expect(screen.getByText("No review events are recorded for this observation.")).toBeVisible()
        expect(screen.queryByText(/The current status is/)).not.toBeInTheDocument()
    })

    it("still renders the review-history table, inside a labelled scroll box, when a real event is recorded", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect(screen.getByRole("table", { name: "Review history" })).toBeVisible()
        expect(screen.getByRole("region", { name: "Table of review history" })).toBeVisible()
        expect(screen.getByText("Looks consistent")).toBeVisible()
    })

    it("reports a check as 'recorded', never as a pass/fail verdict", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const checklist = document.querySelector(".coverage-checklist") as HTMLElement
        expect(ddFor(checklist, "Geometry validation")).toBe("recorded")
        expect(ddFor(checklist, "SCF stability")).toBe("not recorded")
    })

    it("gives the disclosure a real heading so heading-navigation does not skip it", async () => {
        serve(mockRecord({ selections: [{ selection_kind: "lowest_energy" }] }))
        page()
        const heading = await screen.findByRole("heading", { name: "Curation selections (1)" })
        expect(heading.closest("summary")).toBeNull()
    })

    it("never puts a heading (h1-h4) inside a <summary> on this page", async () => {
        serve(mockRecord({ selections: [{ selection_kind: "lowest_energy" }] }))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const summaries = document.querySelectorAll("summary")
        expect(summaries.length).toBeGreaterThan(0)
        for (const summary of summaries) expect(summary.querySelector("h1, h2, h3, h4")).toBeNull()
    })

    it("registers each section in the table of contents", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const toc = await screen.findByRole("navigation", { name: "Sections on this page" })
        await waitFor(() => expect(within(toc).getAllByRole("link").map((link) => link.textContent)).toEqual([
            "Calculations and energies", "Geometries", "Other observations of this conformer", "Review history",
        ]))
    })

    it("uses the one word conformer, and says basin nowhere", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        await screen.findByText("−40.123457")
        const copy = document.body.cloneNode(true) as HTMLElement
        copy.querySelectorAll(".depositor-note").forEach((note) => note.remove())
        expect(copy.textContent).not.toMatch(/basin/i)
    })

    it("ranks nothing: no best, lowest or recommended wording", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        await screen.findByText("−40.123457")
        expect(rankingWords(textOf(document.body))).toEqual([])
    })

    it("prints a depositor's note only inside a note from the depositor", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const note = document.querySelector(".depositor-note blockquote") as HTMLElement
        expect(note).toHaveTextContent("Coarse pre-optimisation basin")
        expect(document.querySelectorAll(".depositor-note")).toHaveLength(1)
    })

    it("shows a specific not-found state for a 404", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => {
            return HttpResponse.json({}, { status: 404 })
        }))
        page()
        expect(await screen.findByRole("heading", { name: "Conformer observation not found" })).toBeVisible()
    })

    it("gives a wrong-handle-type 422 its own non-retryable state, distinct from a transient outage", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => HttpResponse.json({
            code: "handle_type_mismatch",
            detail: "handle_type_mismatch: expected a conformer_observation handle (prefix 'co') but got prefix 'cg'",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a conformer observation reference" })).toBeVisible()
        expect(screen.getByText(/expected a conformer_observation handle/)).toBeVisible()
        expect(screen.queryByRole("heading", { name: "Conformer observation unavailable" })).not.toBeInTheDocument()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    it("gives a malformed-ref 422 (code invalid_handle) its own non-retryable state, distinct from a wrong-prefix ref", async () => {
        // `invalid_handle` — right prefix, unparseable body — is distinct
        // from `handle_type_mismatch` above and is what live traffic
        // actually returns for a malformed ref. This pins the shared
        // `INVALID_HANDLE_CODES` classification in `useScientificRecord`
        // on this page too, not just the surface it was changed for.
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => HttpResponse.json({
            code: "invalid_handle",
            detail: "invalid_handle: 'co_' not a recognised conformer_observation handle",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a conformer observation reference" })).toBeVisible()
        expect(screen.getByText(/not a recognised conformer_observation handle/)).toBeVisible()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    it("treats an absent calculations key as 'not requested', distinct from an empty one", async () => {
        const withoutKey = mockRecord()
        delete (withoutKey as Record<string, unknown>).calculations
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record: withoutKey })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect(screen.getByText("This section was not requested for this view.")).toBeVisible()
        expect(screen.queryByText("No calculation rows were returned for this observation.")).not.toBeInTheDocument()
    })

    it("renders calculations: null the same as calculations: [] — both are 'requested, empty'", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    calculations: null,
                    available_sections: {
                        has_observations: true, has_selections: false,
                        has_calculations: false, has_geometries: true, has_review: true,
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect(screen.getByText("No calculation rows were returned for this observation.")).toBeVisible()
    })

    it("flags a contradiction when the archive marks evidence present but returns none", async () => {
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({
                record: mockRecord({
                    calculations: [],
                    available_sections: {
                        has_observations: true, has_selections: false,
                        has_calculations: true, has_geometries: true, has_review: true,
                    },
                }),
            })
        )))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect(screen.getByText(/The archive marks this observation as having recorded evidence here/)).toBeVisible()
    })

    it("renders a single-observation conformer with no sibling list, not an error", async () => {
        serve(mockRecord({
            observations: [{ conformer_observation: { conformer_observation_ref: "co_one", review: { status: "reviewed" } } }],
        }))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        expect(screen.getByText("No other deposited observations were returned for this conformer.")).toBeVisible()
        expect(screen.queryByRole("link", { name: "Conformer observation: Another observation" })).not.toBeInTheDocument()
    })

    it("states the order of the sibling list", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })
        const section = screen.getByRole("heading", { name: "Other observations of this conformer" }).closest("section") as HTMLElement
        expect(within(section).getByText("Listed in deposit order.", { exact: false })).toBeVisible()
    })

    // The same "not reviewed" status must not show up eight times on one
    // record (hero pill, a Review column on
    // every calculation row, and a pill on every sibling). Review status is
    // shown once, in the hero -- the calculation table drops its Review
    // column entirely (calculations don't carry independent review states
    // on this surface; their evidence is the observation's own), and a
    // sibling only gets its own pill when its status genuinely differs from
    // this record's.
    it("shows the observation's review in the hero, each found-by calculation's own beside it, and a sibling's only when it differs", async () => {
        const siblingObservation = (ref: string) => ({
            conformer_observation: {
                conformer_observation_ref: ref,
                review: { status: "not_reviewed" },
            },
        })

        const record = mockRecord({
            conformer_observation: {
                conformer_observation_ref: "co_one",
                scientific_origin: "computed",
                note: null,
                created_at: "2026-07-21T12:06:50.748258",
                review: { status: "not_reviewed" },
            },
            observations: [
                siblingObservation("co_one"),
                siblingObservation("co_two"),
                siblingObservation("co_three"),
                siblingObservation("co_four"),
            ],
            calculations: [
                { calculation_ref: "calc_opt", type: "opt", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software_release: { software: "Gaussian" } },
                { calculation_ref: "calc_freq", type: "freq", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: { method: "wb97xd", basis: "def2tzvp" }, software_release: { software: "Gaussian" } },
                { calculation_ref: "calc_sp", type: "sp", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: { method: "ccsd(t)", basis: "cbs" }, software_release: { software: "Molpro" } },
                { calculation_ref: "calc_opt2", type: "opt", quality: "raw", review: { status: "not_reviewed" }, level_of_theory: { method: "b3lyp", basis: "def2tzvp" }, software_release: { software: "Gaussian" } },
            ],
        })

        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record })
        )))

        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })

        // The three optimisation and frequency calculations each carry their own review; the single point has no
        // energy record here and is not a found-by row. Siblings share the observation's status, so none shows a chip.
        expect(screen.getAllByText("not reviewed")).toHaveLength(4)
        const siblings = screen.getByRole("heading", { name: "Other observations of this conformer" }).closest("section") as HTMLElement
        expect(within(siblings).queryByText("not reviewed")).toBeNull()
    })

    it("gives a sibling its own review pill only when its status differs from this observation's", async () => {
        // This observation is "not_reviewed"; co_two is deliberately
        // flipped to "reviewed" so it genuinely differs and must show its
        // own pill.
        const record = mockRecord({
            conformer_observation: {
                conformer_observation_ref: "co_one",
                scientific_origin: "computed",
                note: null,
                created_at: "2026-07-21T12:06:50.748258",
                review: { status: "not_reviewed" },
            },
            observations: [
                {
                    conformer_observation: {
                        conformer_observation_ref: "co_one",
                        review: { status: "not_reviewed" },
                    },
                },
                {
                    conformer_observation: {
                        conformer_observation_ref: "co_two",
                        review: { status: "reviewed" },
                    },
                },
            ],
        })

        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => (
            HttpResponse.json({ record })
        )))

        page()
        await screen.findByRole("heading", { level: 1, name: /^Observation of Conformer Group 1 of CH3/ })

        const siblingRow = screen.getByRole("link", { name: "Conformer observation: Another observation" }).closest("li")
        expect(siblingRow).not.toBeNull()
        expect(within(siblingRow as HTMLElement).getByText("reviewed")).toBeVisible()
    })
})

describe("ConformerObservationPage -- depositor text", () => {
    it("prints a depositor's typed fields only inside a note from the depositor", async () => {
        const planted = plantSentinels(mockRecord({
            species: {
                species_ref: "spc_demo", species_entry_ref: "spe_demo", formula: "CH3", canonical_smiles: "[CH3]",
                species_entry_label: `${ADVERSARIAL.joinedLabel} ${ADVERSARIAL.smilesWord}`,
            },
        }), ["label", "note"])
        expectPlanted(planted)
        const { value } = planted
        server.use(http.get("/api/v1/scientific/conformer-observations/co_one", () => HttpResponse.json({ record: value })))

        page()
        expect(await screen.findByRole("heading", { level: 1, name: /^Conformer observation of CH3/ })).toBeVisible()
        expect(screen.getAllByText("co_one").length).toBeGreaterThan(0)

        expectDepositorTextOnlyInNotes()
        expectNoAdversarialText()
    })
})
