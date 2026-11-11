import { StrictMode } from "react"
import { http, HttpResponse, type JsonBodyType } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ConformerGroupPage from "./ConformerGroupPage"
import { conformerReadHandlers } from "../test/conformerReads"
import { ADVERSARIAL, expectDepositorTextOnlyInNotes, expectNoAdversarialText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
import { rankingWords, textOf } from "../test/rankingWords"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
beforeEach(() => server.use(...conformerReadHandlers({ entryRef: "spe_demo" })))
afterEach(() => {
    server.resetHandlers()
    cleanup()
    vi.useRealTimers()
})
afterAll(() => server.close())

function page() {
    return render(
        <MemoryRouter initialEntries={["/conformer-groups/cg_demo"]}>
            <Routes>
                <Route path="/conformer-groups/:groupRef" element={<ConformerGroupPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

// `main.tsx` wraps the whole app in `<React.StrictMode>`, which in dev
// double-invokes every effect: mount -> run cleanup -> mount again, all
// synchronously, before the real (surviving) mount's effect has any
// chance to observe a response. `useScientificRecord`/`useSpeciesEntry`
// route their fetch through `api/requestCache.ts`'s `dedupedFetch`, which
// used to run the FIRST mount's own `AbortSignal` into the shared
// request -- so the discarded probe mount's cleanup aborted the one real
// request before the surviving mount ever got a response of its own. See
// `requestCache.ts`'s module docstring for the fix (subscriber counting
// with a deferred abort).
function strictPage() {
    return render(
        <StrictMode>
            <MemoryRouter initialEntries={["/conformer-groups/cg_demo"]}>
                <Routes>
                    <Route path="/conformer-groups/:groupRef" element={<ConformerGroupPage />} />
                </Routes>
            </MemoryRouter>
        </StrictMode>,
    )
}

const payload = {
    record: {
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
        observations_summary: { total: 2, by_scientific_origin: { computed: 2 } },
        evidence_summary: {
            calculation_count: 3,
            optimization_chain_count: 1,
            geometry_count: 2,
            evidence_coverage: { opt: 2, freq: 1, sp: 1 },
        },
        observations: [{
            conformer_observation: {
                conformer_observation_ref: "co_one",
                scientific_origin: "computed",
                note: null,
                review: { status: "reviewed" },
            },
            evidence_summary: {
                calculation_count: 2,
                geometry_count: 1,
                has_opt: true,
                has_freq: true,
                has_sp: false,
                levels_of_theory: {},
            },
            calculations: [
                {
                    calculation_ref: "calc_opt",
                    type: "opt",
                    quality: "ok",
                    review: { status: "reviewed" },
                    level_of_theory: { method: "b3lyp", basis: "def2tzvp" },
                    software_release: { software: "Gaussian" },
                    workflow_tool_release: { workflow_tool: "ARC" },
                },
                {
                    calculation_ref: "calc_sp",
                    type: "sp",
                    review: { status: "approved" },
                    level_of_theory: { method: "ccsd(t)", basis: "cc-pvtz" },
                    software_release: { software: "Molpro", version: "2024" },
                },
                {
                    calculation_ref: "calc_freq",
                    type: "freq",
                    quality: "ok",
                    review: { status: "not_reviewed" },
                    level_of_theory: { method: "wb97xd", basis: "def2tzvp" },
                    software_release: { software: "Gaussian" },
                },
            ],
            geometries: [{
                calculation_ref: "calc_opt",
                geometry: { geometry_ref: "geo_one", natoms: 4 },
            }],
        }],
        calculations: [],
        geometries: [
            { calculation_ref: "calc_opt", geometry: { geometry_ref: "geo_one", natoms: 4 } },
            { calculation_ref: "calc_sp", geometry: { geometry_ref: "geo_one", natoms: 4 } },
            { calculation_ref: "calc_freq", geometry: { geometry_ref: "geo_two", natoms: 4 } },
        ],
    },
}

describe("ConformerGroupPage", () => {
    const SP_RECORD = {
    calculation: { calculation_ref: "calc_sp", calculation_type: "sp", review: { status: "approved" } },
    energy: { energy_hartree: -40.123456789, energy_kind: "electronic" },
    conformer: { conformer_observation_ref: "co_one", conformer_group_ref: "cg_demo" },
    level_of_theory: { method: "ccsd(t)", basis: "cc-pvtz" },
    software_release: { software: "Molpro", version: "2024" },
}

    function serve(record: JsonBodyType = payload, energies: { sp?: unknown[]; opt?: unknown[] } = { sp: [SP_RECORD] }) {
        server.use(
            ...conformerReadHandlers({ entryRef: "spe_demo", energies }),
            http.get("/api/v1/scientific/conformer-groups/cg_demo", ({ request }) => {
                // `observation_details` expands each observation's calculations and geometries inline.
                expect(new URL(request.url).searchParams.getAll("include"))
                    .toEqual(["observations", "observation_details", "calculations", "geometries"])
                return HttpResponse.json(record)
            }),
        )
    }

    it("shows where this record sits in the side column, with the drawing hidden and a list twin", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const side = document.querySelector(".page-shell-side") as HTMLElement
        const map = within(side).getByRole("region", { name: "Where this record sits" })
        expect(map.querySelector("svg.rel-map-figure")).toHaveAttribute("aria-hidden", "true")
        const list = within(map).getByRole("list", { name: "Connected records" })
        const items = within(list).getAllByRole("listitem").map((item) => item.textContent ?? "")
        expect(items[0]).toContain("Molecule version")
        expect(items.find((item) => item.includes("Conformer observations"))).toContain("2 found")
        expect(items.find((item) => item.includes("Calculations"))).toContain("3 found")
        expect(items.find((item) => item.includes("Geometries"))).toContain("2 found")
    })

    it("counts a rate record once when the usage read lists it for several assignments", async () => {
        serve()
        server.use(http.get("/api/v1/scientific/species-entries/spe_demo/conformer-usage", () => HttpResponse.json({
            groups: [{
                conformer_group_ref: "cg_demo",
                thermo: [],
                kinetics: [
                    { kinetics_ref: "kin_1", reaction_entry_ref: "rxe_1", role: "reactant" },
                    { kinetics_ref: "kin_1", reaction_entry_ref: "rxe_1", role: "reactant" },
                ],
            }],
            ensemble_thermo_refs: [],
        })))
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const list = within(document.querySelector(".page-shell-side") as HTMLElement).getByRole("list", { name: "Connected records" })
        await waitFor(() => expect(within(list).getByText(/Rate record/)).toBeInTheDocument())
        expect(within(list).getByText(/Rate record/).closest("li")).toHaveTextContent("1 found")
    })

    it("titles the record by what it is, never by a ref or the producer's label", async () => {
        serve()
        page()
        const h1 = await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1 of CH3/ })
        expect(h1.textContent).toBe("Conformer Group 1 of CH3")
        expect(h1.textContent).not.toMatch(/cg_|conformer_1/)
    })

    it("titles an un-numbered group by its kind and formula, and prints the depositor's label only as theirs", async () => {
        const record = structuredClone(payload)
        record.record.conformer_group.label = "my anti conformer"
        ;(record.record.conformer_group as { note: string | null }).note = "Found by a manual scan."
        serve(record)
        page()
        const h1 = await screen.findByRole("heading", { level: 1, name: /^Conformer of CH3/ })
        expect(h1.textContent).not.toContain("my anti")
        const notes = document.querySelectorAll(".depositor-note blockquote")
        expect(Array.from(notes).map((note) => note.textContent)).toEqual(["my anti conformer", "Found by a manual scan."])
        expectDepositorTextOnlyInNotes()
    })

    it("says what the page is in one sentence under the title, from the counts", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        expect(screen.getByText(/One 3D shape this molecule can take, deposited as 2 observations and 3 calculations/)).toBeVisible()
    })

    it("shows the review status as a word beside the glyph", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const header = document.querySelector(".record-identity-kicker-row") as HTMLElement
        expect(within(header).getByText("not reviewed")).toBeVisible()
    })

    it("carries Molecules, the molecule, its version and the conformer as breadcrumbs", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" })
        const links = within(breadcrumb).getAllByRole("link")
        expect(links.map((link) => link.getAttribute("href"))).toEqual(["/species", "/species/spc_demo", "/species-entries/spe_demo"])
        expect(links[0]).toHaveTextContent("Molecules")
        expect(within(breadcrumb).getByText("Conformer Group 1")).toHaveAttribute("aria-current", "page")
    })

    it("draws the group's first final geometry with a Show in 3D button", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const figure = await screen.findByRole("figure", { name: "Picture of this conformer" })
        expect(await within(figure).findByRole("img", { name: /Ball-and-stick picture of CH3/ })).toBeVisible()
        expect(within(figure).getByRole("button", { name: "Show in 3D" })).toBeVisible()
        expect(within(figure).getByText(/First deposited final geometry\./)).toBeVisible()
        expect(within(figure).getByRole("link", { name: "Open the geometry" })).toHaveAttribute("href", "/geometries/geo_one")
    })

    it("lists each observation in deposit order with its energy, level, program and review beneath it", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const section = screen.getByRole("heading", { name: "Observations" }).closest("section") as HTMLElement
        expect(within(section).getByText("Listed in deposit order.", { exact: false })).toBeVisible()
        expect(within(section).getByText("This order is not a recommendation.", { exact: false })).toBeVisible()
        expect(within(section).getByRole("link", { name: "Observation 1" })).toHaveAttribute("href", "/conformer-observations/co_one")
        // The energy is a link to its calculation, shown to six places with the stored value on hover.
        const energy = await within(section).findByText("−40.123457")
        expect(energy.closest("a")).toHaveAttribute("href", "/calculations/calc_sp")
        expect(energy).toHaveAttribute("title", "-40.123456789")
        const source = energy.closest("li") as HTMLElement
        expect(source).toHaveTextContent(/Source calc_sp · single-point · at ccsd\(t\)\/cc-pvtz · Molpro 2024 · Review of this record: approved/)
        // The calculations that found the geometry keep their own levels.
        expect(within(section).getAllByText("b3lyp/def2tzvp").length).toBeGreaterThan(0)
        expect(within(section).getAllByText("wb97xd/def2tzvp").length).toBeGreaterThan(0)
    })

    it("says an energy is not recorded, rather than leaving it blank, when no energy came back for a single-point calculation", async () => {
        serve(payload, { sp: [] })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        expect((await screen.findAllByText("Energy not recorded")).length).toBeGreaterThan(0)
    })

    it("says the energies could not be read when that read fails, never that they are absent", async () => {
        server.use(
            http.get("/api/v1/scientific/species-calculations/search", () => HttpResponse.json({}, { status: 500 })),
            ...conformerReadHandlers({ entryRef: "spe_demo" }),
            http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)),
        )
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        expect(await screen.findByText("The single-point energies could not be read.")).toBeVisible()
        expect(screen.getAllByText("Could not be read").length).toBeGreaterThan(0)
    })

    const sp = (ref: string, hartree: number, program: string) => ({
        calculation: { calculation_ref: ref, calculation_type: "sp", review: { status: "approved" } },
        energy: { energy_hartree: hartree, energy_kind: "electronic" },
        conformer: { conformer_observation_ref: "co_one", conformer_group_ref: "cg_demo" },
        level_of_theory: { method: "ccsd(t)", basis: "cc-pvtz", level_of_theory_ref: "lot_cc" },
        software_release: { software: program, version: "1" },
    })
    const withSps = (...programs: string[]) => {
        const record = structuredClone(payload)
        for (const [index] of programs.entries()) {
            record.record.observations[0].calculations.push({
                calculation_ref: `calc_sp_${index}`, type: "sp", review: { status: "approved" },
                level_of_theory: { method: "ccsd(t)", basis: "cc-pvtz", level_of_theory_ref: "lot_cc" },
                software_release: { software: programs[index], version: "1" },
            } as never)
        }
        return record
    }

    it("states no spread between energies of one level computed by different programs", async () => {
        serve(withSps("Molpro", "Psi4"), { sp: [sp("calc_sp_0", -40.123, "Molpro"), sp("calc_sp_1", -40.1, "Psi4")] })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        await screen.findByText("−40.100000")
        expect(screen.getByText("−40.123000")).toBeVisible()
        expect(screen.queryByText(/differ by up to/)).not.toBeInTheDocument()
    })

    it("states the spread between energies of one level and one program", async () => {
        serve(withSps("Molpro", "Molpro"), { sp: [sp("calc_sp_0", -40.123, "Molpro"), sp("calc_sp_1", -40.1, "Molpro")] })
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const spread = await screen.findByText(/differ by up to/)
        expect(spread).toHaveTextContent(/at ccsd\(t\)\/cc-pvtz with Molpro 1 differ by up to 60\.39 kJ\/mol across 2 calculations/)
    })

    it("links each level, names the workflow tool and each found-by review, and gives the origin, ref and chain count", async () => {
        const record = structuredClone(payload)
        ;(record.record.observations[0].calculations[0].level_of_theory as { level_of_theory_ref?: string }).level_of_theory_ref = "lot_b3"
        serve(record)
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const section = screen.getByRole("heading", { name: "Observations" }).closest("section") as HTMLElement
        const levelLink = within(section).getAllByRole("link", { name: "b3lyp/def2tzvp" })[0]
        expect(levelLink.getAttribute("href")).toContain("lot_b3")
        expect(within(section).getAllByText(/run by/)[0]).toHaveTextContent("run by ARC")
        const found = within(section).getAllByRole("link", { name: "calc_opt" })[0].closest("li") as HTMLElement
        expect(within(found).getByText("reviewed")).toBeVisible()
        const head = within(section).getByRole("link", { name: "Observation 1" }).closest("p") as HTMLElement
        expect(head).toHaveTextContent("co_one")
        expect(head).toHaveTextContent("Origin: computed")
        expect(within(section).getByText(/1 optimisation chain\./)).toBeVisible()
    })

    it("names the thermochemistry and rate coefficients that name this conformer, and where it was merged", async () => {
        server.use(
            ...conformerReadHandlers({
                entryRef: "spe_demo",
                usage: {
                    species_entry_ref: "spe_demo",
                    groups: [{
                        conformer_group_ref: "cg_demo",
                        merged_into_conformer_group_ref: "cg_other",
                        thermo: [{ thermo_ref: "thm_one", declared_by: "depositor" }],
                        kinetics: [{ kinetics_ref: "kin_one", reaction_entry_ref: "rxe_one", role: "reactant" }],
                    }],
                    ensemble_thermo_refs: [],
                },
            }),
            http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)),
        )
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const section = screen.getByRole("heading", { name: "Used by" }).closest("section") as HTMLElement
        expect(await within(section).findByRole("link", { name: "thm_one" })).toHaveAttribute("href", "/species-entries/spe_demo/thermo?conformer=cg_demo#thermo-heading-thm_one")
        expect(within(section).getByRole("link", { name: "kin_one" })).toHaveAttribute("href", "/reaction-entries/rxe_one#kinetics-heading")
        expect(within(section).getByText(/was merged into/)).toBeVisible()
        expect(within(section).getByRole("link", { name: "another conformer" })).toHaveAttribute("href", "/conformer-groups/cg_other")
    })

    it("says no thermochemistry or rate coefficient names it when none does, never inferring one", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        expect(await screen.findByText("No thermochemistry or rate coefficient names this shape.")).toBeVisible()
    })

    it("says so when which records use it cannot be read", async () => {
        server.use(
            http.get("/api/v1/scientific/species-entries/spe_demo/conformer-usage", () => HttpResponse.json({}, { status: 500 })),
            ...conformerReadHandlers({ entryRef: "spe_demo" }),
            http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)),
        )
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        expect(await screen.findByText("Which records name this conformer could not be read.")).toBeVisible()
    })

    it("counts coverage per stage in plain text, collapsed behind a true roll-up", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const checklist = screen.getByLabelText("Conformer evidence checklist")
        expect(within(checklist).getByText("1 of 3 stages covered")).toBeInTheDocument()
        fireEvent.click(checklist.querySelector("summary")!)
        expect(within(checklist).getByText("2 of 2 observations")).toBeVisible()
        expect(within(checklist).getAllByText("1 of 2 observations")).toHaveLength(2)
    })

    it("links the calculation that produced each observation's geometry output", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const output = document.querySelector(".observation-geometries") as HTMLElement
        expect(within(output).getByRole("link", { name: "calc_opt" })).toHaveAttribute("href", "/calculations/calc_opt")
        expect(within(output).getByRole("link", { name: "geo_one" })).toHaveAttribute("href", "/geometries/geo_one")
    })

    it("lists the stored geometries with the calculations that produced them", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const row = document.querySelector(".geometry-link") as HTMLElement
        expect(within(row).getByRole("link", { name: "calc_opt" })).toHaveAttribute("href", "/calculations/calc_opt")
        expect(within(row).getByRole("link", { name: "calc_sp" })).toHaveAttribute("href", "/calculations/calc_sp")
        expect(screen.getAllByRole("link", { name: "geo_one" })[0]).toHaveAttribute("href", "/geometries/geo_one")
        expect(screen.getByText(/This is not a count of conformers/)).toBeVisible()
    })

    it("shows a plain empty state when the group has no deposited observations", async () => {
        const record = structuredClone(payload)
        record.record.observations = []
        record.record.observations_summary.total = 0
        serve(record)
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        expect(screen.getByText("No deposited observations were returned for this conformer.")).toBeVisible()
        expect(screen.queryByText("Listed in deposit order.", { exact: false })).not.toBeInTheDocument()
    })

    it("registers each section in the table of contents", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        const toc = await screen.findByRole("navigation", { name: "Sections on this page" })
        await waitFor(() => expect(within(toc).getAllByRole("link").map((link) => link.textContent)).toEqual(["Observations", "Used by", "Geometries"]))
    })

    it("never puts a heading inside a summary", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        for (const summary of document.querySelectorAll("summary")) {
            expect(summary.querySelector("h1, h2, h3, h4")).toBeNull()
        }
    })

    it("uses the one word conformer, and says basin nowhere", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        expect(document.body.textContent).not.toMatch(/basin/i)
        expect(document.title).not.toMatch(/basin/i)
    })

    it("ranks nothing: no best, lowest or recommended wording", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        await screen.findByText("−40.123457")
        expect(rankingWords(textOf(document.body))).toEqual([])
    })

    it("titles the tab with the kind Conformer", async () => {
        serve()
        page()
        await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1/ })
        expect(document.title).toBe("CH3 · Conformer · TCKDB")
    })

    it("shows a specific not-found state", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => {
            return HttpResponse.json({}, { status: 404 })
        }))
        page()
        expect(await screen.findByRole("heading", { name: "Conformer not found" })).toBeVisible()
    })

    // Regression test for the requestCache StrictMode bug (see the module
    // docstring on `strictPage`): the SAME 404 handler as the test above,
    // under StrictMode, still has to reach the real "not found"
    // classification -- not the generic "unavailable" a misattributed
    // AbortError used to produce (the discarded probe mount's own cleanup
    // aborted the ONE real request before the surviving mount's effect
    // ever received the 404).
    it("shows the same specific not-found state under React.StrictMode's double-invoked effects", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => {
            return HttpResponse.json({}, { status: 404 })
        }))
        strictPage()
        expect(await screen.findByRole("heading", { name: "Conformer not found" })).toBeVisible()
        expect(screen.queryByRole("heading", { name: "Conformer unavailable" })).not.toBeInTheDocument()
    })

    // Same bug, the OTHER failure mode: a page that renders successfully
    // never resolves at all under StrictMode -- stuck on its loading state
    // forever, because the discarded probe mount's cleanup aborted the one
    // real (successful) request before the surviving mount could observe
    // it.
    it("renders real data under React.StrictMode's double-invoked effects, never stuck loading", async () => {
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(payload)))
        strictPage()
        expect(await screen.findByRole("heading", { level: 1, name: /^Conformer Group 1 of CH3/ })).toBeVisible()
        expect(screen.queryByText("Loading conformer…")).not.toBeInTheDocument()
    })

    it("gives a malformed-ref 422 (code invalid_handle) its own non-retryable state", async () => {
        // This surface previously had no 422 coverage at all. `invalid_handle`
        // (distinct from `handle_type_mismatch`, the only code every other
        // page's 422 test exercised) is what live traffic actually returns
        // for a malformed-but-right-prefix ref — pinning it here guards the
        // `INVALID_HANDLE_CODES` classification in `useScientificRecord`,
        // shared machinery this page depends on but does not own.
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json({
            code: "invalid_handle",
            detail: "invalid_handle: 'cg_' not a recognised conformer_group handle",
            context: {},
        }, { status: 422 })))
        page()
        expect(await screen.findByRole("heading", { name: "Not a conformer reference" })).toBeVisible()
        expect(screen.getByText(/not a recognised conformer_group handle/)).toBeVisible()
        expect(screen.getByRole("alert")).toBeVisible()
    })

    // Review follow-up (SHOULD-FIX #1): the automatic `Retry-After` wait
    // (`requestScientificJson`) can run up to a minute
    // (`rate_limit_anon_read_per_minute`, `backend/app/api/config.py`) --
    // during it, the page must say "the archive is busy, retrying
    // automatically", not sit on a plain, indefinite "Loading …" a reader
    // has no way to tell apart from a stuck page.
    it("shows a distinct 'archive is busy' state during the automatic Retry-After wait, then renders real data once the retry succeeds", async () => {
        vi.useFakeTimers()
        let attempt = 0
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => {
            attempt += 1
            if (attempt === 1) {
                return HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "8" } })
            }
            return HttpResponse.json(payload)
        }))
        page()

        // Let the first (429) response and its synchronous onRateLimited
        // notification land, without yet advancing past the retry delay.
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        expect(screen.getByRole("heading", { name: "Loading conformer…" })).toBeVisible()
        const waitingMessage = () => screen.getByText(/receiving too many requests right now/)
        expect(waitingMessage()).toBeVisible()
        expect(waitingMessage().textContent).toMatch(/retrying automatically in about 8 seconds…/i)
        // Never the terminal states while still waiting on the automatic retry.
        expect(screen.queryByRole("heading", { name: "Conformer unavailable" })).not.toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "Archive is busy" })).not.toBeInTheDocument()

        // The countdown ticks down locally (RetryCountdown), independent
        // of the retry itself actually firing.
        await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
        expect(waitingMessage().textContent).toMatch(/retrying automatically in about 5 seconds…/i)

        // The retry itself fires at the full Retry-After delay and succeeds.
        await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
        expect(screen.getByRole("heading", { level: 1, name: /^Conformer Group 1 of CH3/ })).toBeVisible()
        expect(attempt).toBe(2)
    })

    // Wording pin (SHOULD-FIX #3): plain language, not operator vocabulary
    // -- "about 30 seconds", never the abbreviated "30s" the pre-fix copy
    // used ("Wait about 2s and try again").
    it("uses plain-language wording for the terminal rate-limited state, spelling out the wait", async () => {
        vi.useFakeTimers()
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => (
            HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "30" } })
        )))
        page()
        await act(async () => { await vi.advanceTimersByTimeAsync(0) }) // first attempt lands, retry scheduled
        await act(async () => { await vi.advanceTimersByTimeAsync(30_000) }) // the retry itself lands, also 429

        expect(screen.getByRole("heading", { name: "Archive is busy" })).toBeVisible()
        const message = screen.getByText(/receiving too many requests right now/)
        expect(message).toHaveTextContent(
            "The archive is receiving too many requests right now. Wait about 30 seconds and reload the page.",
        )
        // Never the abbreviated, operator-vocabulary form.
        expect(message.textContent).not.toMatch(/\d+s\b/)
    })
})

describe("ConformerGroupPage -- depositor text", () => {
    it("prints a depositor's typed fields only inside a note from the depositor", async () => {
        const planted = plantSentinels(payload, ["label", "note"])
        expectPlanted(planted)
        const { value } = planted
        ;(value.record.species as { species_entry_label?: string }).species_entry_label = `${ADVERSARIAL.joinedLabel} ${ADVERSARIAL.smilesWord}`
        server.use(http.get("/api/v1/scientific/conformer-groups/cg_demo", () => HttpResponse.json(value)))

        page()
        expect(await screen.findByRole("heading", { level: 1, name: /^Conformer of CH3/ })).toBeVisible()
        expect(screen.getAllByText("cg_demo").length).toBeGreaterThan(0)

        // The group's label, the group's note and the observation's note, each in its own labelled note.
        await waitFor(() => expect(document.querySelectorAll(".depositor-note blockquote")).toHaveLength(3))
        expectDepositorTextOnlyInNotes()
        expectNoAdversarialText()
    })
})
