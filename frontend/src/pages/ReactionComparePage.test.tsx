import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "../App"
import { resetAllRequestCaches } from "../api/requestCache"
import { arrheniusTermK } from "../domain/kineticsTable"
import { rankingWords, textOf } from "../test/rankingWords"
import { expectDepositorTextOnlyInNotes, expectNoDepositorText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
import { titleHasKind } from "../test/routeTitleKinds"

const reactionRef = "rxn_abcdefghijklmnopqrstuvwxyz"
const lotA = { method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp", level_of_theory_ref: "lot_a" }
const lotB = { method: "ccsdt", basis: "ccpvtz", display: "ccsdt/ccpvtz", level_of_theory_ref: "lot_b" }
const gaussian = { software_release_ref: "srel_g", software: "Gaussian", version: "16" }
const orca = { software_release_ref: "srel_o", software: "Orca", version: "6.0" }
const summary = { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 }
const sortEcho = { sort: { keys: [{ key: "review_rank", direction: "asc", meaning: "review status" }, { key: "created_at", direction: "desc", meaning: "newest deposited" }] } }
const server = setupServer(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))

type Spec = { ref: string; level: (typeof lotA & Record<string, unknown>) | null; software: typeof gaussian | null; A: number; n: number; Ea: number; range: [number, number]; tunnelling: string; review?: string; units?: string; spRef?: string }

function kinetics(spec: Spec) {
    return {
        kinetics_ref: spec.ref, scientific_origin: "computed", model_kind: "modified_arrhenius", direction: "forward", review: { status: spec.review ?? "not_reviewed" },
        parameters: { A: spec.A, A_units: spec.units ?? "cm3_mol_s", n: spec.n, Ea_kj_mol: spec.Ea },
        tunneling_model: spec.tunnelling, is_third_body: false, uncertainty: {},
        temperature_coverage: { record_min_k: spec.range[0], record_max_k: spec.range[1] },
        evidence_completeness: { score: 3, max: 9, checklist: {} },
        levels: { geometry: spec.level, frequency: spec.level, energy: spec.level, energy_source: "sp" },
        provenance: { primary_software: spec.software, ts_sp_calculation_ref: spec.spRef ?? null, literature: { title: "typed by the depositor" } },
        note: "typed by the depositor",
    }
}

const K1: Spec = { ref: "kin_one", level: lotA, software: gaussian, A: 1e10, n: 1.5, Ea: 40, range: [300, 2000], tunnelling: "wigner", review: "approved" }
const K2: Spec = { ref: "kin_two", level: lotA, software: gaussian, A: 2e10, n: 1.4, Ea: 42.5, range: [300, 1000], tunnelling: "eckart" }
const K3: Spec = { ref: "kin_three", level: lotB, software: orca, A: 3e9, n: 0, Ea: 35, range: [500, 3000], tunnelling: "eckart" }
const K5: Spec = { ...K1, ref: "kin_five", level: lotB, software: gaussian, tunnelling: "wigner" }
const K6: Spec = { ...K1, ref: "kin_six", software: orca }
const K4: Spec = { ref: "kin_four", level: null, software: null, A: 5e9, n: 0.5, Ea: 20, range: [300, 3000], tunnelling: "none" }

const ENTRY_OF: Record<string, string> = { kin_one: "rxe_one", kin_two: "rxe_one", kin_three: "rxe_two", kin_four: "rxe_two", kin_five: "rxe_one", kin_six: "rxe_one" }

const POSITION_OF: Record<string, number> = { rxe_one: 1, rxe_two: 2 }

const participant = (ref: string, smiles: string, formula: string, index: number) => ({ species_entry_ref: ref, smiles, formula, stoichiometry: 1, participant_index: index })
const overview = () => ({
    review_summary: summary,
    records: [{
        reaction_ref: reactionRef, reaction_entry_ref: "rxe_one", deposit_position: 1, equation: "CH4 + OH <=> CH3 + H2O", reversible: true, family: null, review: { status: "not_reviewed" },
        reactants: [participant("spe_a", "C", "CH4", 0), participant("spe_b", "[OH]", "HO", 1)],
        products: [participant("spe_c", "[CH3]", "CH3", 0), participant("spe_d", "O", "H2O", 1)],
        availability: { has_kinetics: true, has_transition_state: true, kinetics_count: 4 },
    }],
    pagination: { offset: 0, limit: 50, returned: 1, total: 1 },
})

function profile(level: typeof lotA, program: string, tse: string, version = program === "Gaussian" ? "16" : "6.0") {
    const point = (calc: string, hartree: number) => ({ calculation_ref: calc, energy_type: "single_point", electronic_energy_hartree: hartree, software: { program, version }, review_status: "not_reviewed", convergence_unrecorded: false })
    return {
        transition_state_entry_ref: tse, transition_state_entry_review_status: "not_reviewed", level_of_theory: level, program, energy_types: ["single_point"], versions: [version],
        energy_kind: "electronic", definition: "Each molecule's energy at this level from this program.",
        reactants: [{ species_entry_ref: "spe_a", count: 1, ...point("calc_ra", -40.1) }],
        transition_state: { transition_state_entry_ref: tse, ...point("calc_ts", -40.0) },
        products: [{ species_entry_ref: "spe_c", count: 1, ...point("calc_pc", -40.2) }],
        energy_hartree: { reactants: -40.1, transition_state: -40.0, products: -40.2 },
        relative_kj_mol: { reactants: 0, transition_state: 262.5, products: -262.5 },
    }
}

const profileRead = (ref: string, profiles: object[]) => ({ request: { hidden_by_default: null, hidden_by_default_by_scope: {} }, reaction_entry_ref: ref, profiles, unavailable: [], cited_by_kinetics: [] })

type Options = { records?: object[]; overviewPayload?: object; profiles?: Record<string, object[]>; entryOf?: Record<string, string>; positionOf?: Record<string, number>; onProfile?: (ref: string) => void }

function handlers(options: Options = {}) {
    const records = options.records ?? [K1, K2, K3, K4].map(kinetics)
    const rows = records.map((record) => {
        const entry = (options.entryOf ?? ENTRY_OF)[(record as { kinetics_ref: string }).kinetics_ref]
        return { reaction: { reaction_ref: reactionRef, reaction_entry_ref: entry, deposit_position: (options.positionOf ?? POSITION_OF)[entry] ?? 1 }, kinetics: record }
    })
    const profiles = options.profiles ?? { rxe_one: [profile(lotA, "Gaussian", "tse_one"), profile(lotB, "Orca", "tse_one"), profile(lotB, "Gaussian", "tse_one"), profile(lotA, "Orca", "tse_one"), profile(lotA, "Gaussian", "tse_one", "09")], rxe_two: [profile(lotB, "Orca", "tse_two")] }
    return [
        http.get("/api/v1/scientific/reactions/search", () => HttpResponse.json(options.overviewPayload ?? overview())),
        http.get("/api/v1/scientific/kinetics/search", ({ request }) => {
            const url = new URL(request.url)
            expect(url.searchParams.get("reaction_ref")).toBe(reactionRef)
            const offset = Number(url.searchParams.get("offset") ?? 0)
            const limit = Number(url.searchParams.get("limit") ?? 50)
            const slice = rows.slice(offset, offset + limit)
            return HttpResponse.json({ review_summary: summary, records: slice, pagination: { offset, limit, returned: slice.length, total: rows.length }, request: sortEcho })
        }),
        http.get("/api/v1/scientific/reaction-entries/:ref/energy-profile", ({ params }) => { options.onProfile?.(String(params.ref)); return HttpResponse.json(profileRead(String(params.ref), profiles[String(params.ref)] ?? [])) }),
    ]
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); resetAllRequestCaches(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

async function open(options: Options = {}, path = `/reactions/${reactionRef}/compare`) {
    server.use(...handlers(options))
    window.history.replaceState({}, "", path)
    render(<App />)
}

const table = () => screen.findByRole("region", { name: "Rate records compared" })
/** The record cells of a row, without its unit cell. */
const rowCells = (grid: HTMLElement, label: RegExp) => within(within(grid).getByRole("row", { name: label })).getAllByRole("cell").slice(1)
const records = (...specs: Spec[]) => specs.map(kinetics)

describe("reaction compare page", () => {
    it("titles the tab, heads the page and states what it does not do", async () => {
        await open()
        await table()
        expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/Compare rate coefficients for/)
        expect(titleHasKind(document.title, "/reactions/:ref/compare")).toBe(true)
        expect(document.title).toMatch(/Compare · TCKDB$/)
        expect(screen.getByText(/nothing is filled in from another record/)).toBeInTheDocument()
        expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", { name: "Reactions" })).toBeInTheDocument()
    })

    it("groups columns by energy level of theory and program, each group header a colgroup th", async () => {
        await open()
        const grid = await table()
        const heads = within(grid).getAllByRole("columnheader").filter((head) => head.getAttribute("scope") === "colgroup")
        expect(heads).toHaveLength(3)
        expect(heads[0]).toHaveTextContent("wb97xd/def2tzvp")
        expect(heads[0]).toHaveTextContent("Gaussian 16")
        expect(heads[1]).toHaveTextContent("ccsdt/ccpvtz")
        expect(heads[1]).toHaveTextContent("Orca 6.0")
        expect(heads[2]).toHaveTextContent("Energy level of theory not recorded")
        expect(heads[2]).toHaveTextContent("Program not recorded")
        expect(heads.map((head) => head.getAttribute("colspan"))).toEqual(["3", "1", "1"])
        expect(grid.querySelectorAll("colgroup")).toHaveLength(4)
    })

    it("says the groups differ in method and program when there are two or more", async () => {
        await open()
        await table()
        expect(screen.getByText("These use different methods and programs. Differences mix both effects; neither is an error.")).toBeInTheDocument()
    })

    it("says what differs: methods only, programs only", async () => {
        await open({ records: records(K1, K5) })
        await table()
        expect(screen.getByText("These use different methods. Differences reflect the method; neither is an error.")).toBeInTheDocument()
        cleanup(); resetAllRequestCaches(); server.resetHandlers()
        await open({ records: records(K1, K6) })
        await table()
        expect(screen.getByText("These use different programs. Differences reflect the program; neither is an error.")).toBeInTheDocument()
    })

    it("keeps one level run in two programs as two groups", async () => {
        await open({ records: records(K1, K6) })
        const grid = await table()
        const heads = within(grid).getAllByRole("columnheader").filter((head) => head.getAttribute("scope") === "colgroup")
        expect(heads).toHaveLength(2)
        expect(heads[0]).toHaveTextContent("Gaussian 16")
        expect(heads[1]).toHaveTextContent("Orca 6.0")
    })

    it("says nothing of the kind for a single group", async () => {
        await open({ records: records(K1, K2) })
        await table()
        expect(screen.queryByText(/These use different methods and programs/)).not.toBeInTheDocument()
    })

    it("forms a difference only inside a group of exactly two records", async () => {
        await open()
        const grid = await table()
        const heads = within(grid).getAllByRole("columnheader").filter((head) => head.getAttribute("scope") === "col")
        const deltas = heads.filter((head) => head.textContent?.startsWith("Δ"))
        expect(deltas).toHaveLength(1)
        const at = heads.indexOf(deltas[0])
        expect(deltas[0]).toHaveTextContent("Record 2 − Record 1")
        expect(heads[at - 1]).toHaveTextContent("Record 2")
        expect(heads[at - 2]).toHaveTextContent("Record 1")
        expect(heads[at + 1]).toHaveTextContent("Record 3")
        expect(grid.textContent).not.toMatch(/kin_/)
        expect(heads[at + 1].className).toContain("compare-group-start")
        const ea = rowCells(grid, /Activation energy Ea/)
        expect(ea).toHaveLength(5)
        expect(ea[0]).toHaveTextContent("40.00")
        expect(ea[1]).toHaveTextContent("42.50")
        expect(ea[2]).toHaveTextContent("+2.50")
        expect(screen.getByText(/second record minus first record of a group of two/)).toBeInTheDocument()
    })

    it.each([
        ["every group has one record", [K1, K3, K4]],
        ["a group has more than two", [K1, K2, { ...K2, ref: "kin_five" }]],
    ])("forms no difference when %s", async (_name, specs) => {
        await open({ records: records(...(specs as Spec[])) })
        const grid = await table()
        expect(within(grid).queryAllByRole("columnheader").filter((head) => head.textContent?.startsWith("Δ"))).toHaveLength(0)
        expect(screen.queryByText(/second record minus first record of a group of two/)).not.toBeInTheDocument()
    })

    it("shows k only inside each record's own fitted range and never extrapolates", async () => {
        await open()
        const grid = await table()
        const at500 = rowCells(grid, /k at 500 K/)
        const k1 = arrheniusTermK(1e10, 1.5, 40, 500, 1)
        const [rawMantissa, exponent] = k1.toExponential(5).split("e")
        const mantissa = String(Number(rawMantissa))
        expect(at500[0].textContent).toContain(mantissa)
        expect(at500[0].textContent).toContain(`10${exponent.replace("+", "")}`)
        expect(at500[0].textContent).toContain("cm³ mol⁻¹ s⁻¹")
        expect(rowCells(grid, /k at 1500 K/)[1]).toHaveTextContent("Outside this fit's range")
        expect(rowCells(grid, /k at 300 K/)[3]).toHaveTextContent("Outside this fit's range")
        for (const label of [/k at 300 K/, /k at 1500 K/, /k at 2000 K/]) {
            for (const cell of rowCells(grid, label)) {
                if (/Outside/.test(cell.textContent ?? "")) expect(cell.textContent).not.toMatch(/\d/)
            }
        }
        expect(within(grid).queryByRole("row", { name: /k at 1000 K/ })).not.toBeNull()
    })

    it("states origin and review in each record's header and links each record", async () => {
        await open()
        const grid = await table()
        const head = within(grid).getAllByRole("columnheader").find((cell) => cell.textContent?.startsWith("Record 1")) as HTMLElement
        expect(head).toHaveTextContent("Origin: computed")
        expect(head).toHaveTextContent(/approved/i)
        expect(within(head).getByRole("link", { name: "Record 1" })).toHaveAttribute("href", "/reaction-entries/rxe_one#kinetics-kin_one")
        expect(within(head).getByRole("link", { name: "Reaction entry: Version 1" })).toHaveAttribute("href", "/reaction-entries/rxe_one")
        expect(within(rowCells(grid, /Tunnelling model/)[0]).getByText("Wigner")).toBeInTheDocument()
        expect(within(grid).getByRole("row", { name: /Tunnelling model/ })).toHaveTextContent("Not the same within a group")
    })

    it("draws one energy profile per group, from that group's own level and program, never overlaid", async () => {
        await open()
        await table()
        await waitFor(() => expect(document.querySelectorAll("svg.energy-profile-svg").length).toBe(2))
        const sections = screen.getAllByRole("heading", { level: 3 })
        expect(sections.map((heading) => heading.textContent)).toEqual([
            "wb97xd/def2tzvp · Gaussian 16",
            "ccsdt/ccpvtz · Orca 6.0",
            "Energy level of theory not recorded",
        ])
        expect(document.querySelectorAll(".energy-profile-view")).toHaveLength(2)
        expect(document.querySelectorAll(".energy-profile-picker")).toHaveLength(0)
        const first = sections[0].parentElement as HTMLElement
        const groupOne = Array.from(document.querySelectorAll(".energy-profile-view"))[0] as HTMLElement
        const second = Array.from(document.querySelectorAll(".energy-profile-view"))[1] as HTMLElement
        // Each profile sits under its own group's heading, which is where its level and program are stated.
        expect(sections[0].compareDocumentPosition(groupOne) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
        expect(groupOne.compareDocumentPosition(sections[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
        expect(sections[1].compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
        expect(second.compareDocumentPosition(sections[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
        expect(groupOne).not.toHaveTextContent("ccsdt/ccpvtz")
        expect(second).not.toHaveTextContent("wb97xd/def2tzvp")
        expect(first).toBeTruthy()
        expect(screen.getByText(/do not state both an energy level of theory and a program/)).toBeInTheDocument()
        expect(screen.getByText("Each group's profile has its own axis. Profiles of different groups are never drawn together.")).toBeInTheDocument()
        expect(screen.getByText("As deposited; k only within each fitted range, tunnelling only if the fit had it.")).toBeInTheDocument()
    })

    it("makes each profile heading plain words that the contents list carries, with the level's disclosure beneath it", async () => {
        await open()
        await table()
        await waitFor(() => expect(document.querySelectorAll("svg.energy-profile-svg").length).toBe(2))
        const headings = screen.getAllByRole("heading", { level: 3 })
        for (const heading of headings) {
            expect(heading.querySelector("details, summary, a")).toBeNull()
            expect(heading.id).toMatch(/^compare-profile-\d$/)
        }
        const contents = await screen.findByRole("navigation", { name: "Sections on this page" })
        await waitFor(() => expect(contents).toHaveTextContent("wb97xd/def2tzvp · Gaussian 16"))
        expect(contents).toHaveTextContent("ccsdt/ccpvtz · Orca 6.0")
        expect(contents.textContent).not.toMatch(/compare-profile/)
        const section = headings[0].parentElement as HTMLElement
        // The heading already says the level and program: the profile does not say them again.
        expect(within(section).queryByText("Level of theory", { selector: "dt" })).toBeNull()
        expect(section.textContent?.match(/wb97xd\/def2tzvp/g)).toHaveLength(1)
    })

    it("puts a group's qualifier disclosure beside its heading, never inside a paragraph, without repeating the level", async () => {
        const errors = vi.spyOn(console, "error").mockImplementation(() => undefined)
        try {
            const qualified = { ...lotA, pno_truncation: "tight" }
            const kinetic = records({ ...K1, level: qualified }, { ...K2, level: qualified })
            await open({ records: kinetic, profiles: { rxe_one: [profile(qualified, "Gaussian", "tse_one")] } })
            await table()
            await waitFor(() => expect(document.querySelectorAll("svg.energy-profile-svg").length).toBe(1))
            const heading = screen.getAllByRole("heading", { level: 3 })[0]
            const details = heading.parentElement?.querySelector(".level-label-details") as HTMLElement
            expect(details).not.toBeNull()
            expect(details.closest("p")).toBeNull()
            expect(within(details).getByText("tight")).toBeInTheDocument()
            expect(heading.parentElement?.querySelector(".energy-profile-facts")?.textContent).not.toContain("Level of theory")
            const warnings = errors.mock.calls.map((call) => call.map(String).join(" ")).filter((message) => /validateDOMNesting|cannot be a descendant|cannot contain/.test(message))
            expect(warnings).toEqual([])
        } finally {
            errors.mockRestore()
        }
    })

    it("gives the list's level cell room, and wraps long labels at word breaks only", async () => {
        await open()
        const list = await screen.findByRole("region", { name: "Rate records as a list" })
        const cells = list.querySelectorAll("td .level-label-cell")
        expect(cells.length).toBeGreaterThan(0)
        expect(getComputedStyle(cells[0]).minWidth).toBe("16ch")
        expect(getComputedStyle(list.querySelector(".level-label-text") as Element).overflowWrap).toBe("break-word")
    })

    it("prints the qualifier that tells two groups of one short label apart, in the heading and the contents list", async () => {
        const frozen = { ...lotA, level_of_theory_ref: "lot_fc", core_treatment: "frozen_core" }
        const all = { ...lotA, level_of_theory_ref: "lot_ae", core_treatment: "all_electron" }
        await open({ records: [{ ...K1, level: frozen }, { ...K2, level: all }].map(kinetics), entryOf: ENTRY_OF, profiles: { rxe_one: [] } })
        await table()
        await waitFor(() => expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(2))
        expect(screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual([
            "wb97xd/def2tzvp · frozen core · Gaussian 16",
            "wb97xd/def2tzvp · all electron · Gaussian 16",
        ])
        await waitFor(() => expect(screen.getByRole("navigation", { name: "Sections on this page" })).toHaveTextContent("wb97xd/def2tzvp · all electron · Gaussian 16"))
    })

    it("says where the program came from only when it is not the energy calculation's", async () => {
        await open({ records: [{ ...K1, spRef: "calc_sp1" }, { ...K2, spRef: "calc_sp2" }, K3].map(kinetics) })
        const grid = await table()
        const heads = within(grid).getAllByRole("columnheader").filter((head) => head.getAttribute("scope") === "colgroup")
        expect(heads[0]).not.toHaveTextContent(/Program of the/)
        expect(heads[1]).toHaveTextContent("Program of the source calculation")
        expect(document.body.textContent).not.toContain("Program of the energy calculation")
    })

    it("names each version by the deposit position its kinetics records carry, not by the overview page", async () => {
        const payload = overview()
        await open({
            overviewPayload: { ...payload, pagination: { offset: 0, limit: 50, returned: 1, total: 61 } },
            positionOf: { rxe_one: 61, rxe_two: 60 },
        })
        const grid = await table()
        const head = within(grid).getAllByRole("columnheader").find((cell) => cell.textContent?.startsWith("Record 1")) as HTMLElement
        expect(within(head).getByRole("link", { name: "Reaction entry: Version 61" })).toBeInTheDocument()
        const third = within(grid).getAllByRole("columnheader").find((cell) => cell.textContent?.startsWith("Record 3")) as HTMLElement
        expect(within(third).getByRole("link", { name: "Reaction entry: Version 60" })).toBeInTheDocument()
        const list = await screen.findByRole("region", { name: "Rate records as a list" })
        expect(within(list).getByRole("link", { name: "Record 1, Version 61" })).toBeInTheDocument()
        expect(within(list).getByRole("link", { name: "Record 3, Version 60" })).toBeInTheDocument()
        expect(document.body.textContent).not.toContain("Another version")
    })

    it("names the order and offers the list view with sort buttons", async () => {
        await open()
        await table()
        expect(screen.getAllByText(/Grouped by level and program/).length).toBeGreaterThan(0)
        expect(screen.getAllByText(/approved first, then newest deposited first/).length).toBeGreaterThan(0)
        const list = await screen.findByRole("region", { name: "Rate records as a list" })
        const button = within(list).getAllByRole("button", { name: /Activation energy/ })[0]
        await userEvent.setup().click(button)
        expect(button.closest("th")).toHaveAttribute("aria-sort", "ascending")
        expect(screen.getAllByText(/sorted by you/i).length).toBeGreaterThan(0)
    })

    it("tabs through the group headers' links, then the record links, then the sort buttons", async () => {
        await open()
        const grid = await table()
        const groupLinks = within(grid).getAllByRole("columnheader").filter((head) => head.getAttribute("scope") === "colgroup").flatMap((head) => within(head).queryAllByRole("link"))
        expect(groupLinks.map((link) => link.textContent)).toEqual(["wb97xd/def2tzvp · Gaussian 16", "ccsdt/ccpvtz · Orca 6.0"])
        const user = userEvent.setup()
        groupLinks[0].focus()
        const seen: Element[] = [document.activeElement as Element]
        for (let step = 0; step < 80 && !(document.activeElement?.closest("th")?.hasAttribute("aria-sort")); step += 1) {
            await user.tab()
            seen.push(document.activeElement as Element)
        }
        const sortButton = seen[seen.length - 1]
        expect(sortButton.closest("th")).toHaveAttribute("aria-sort")
        expect(seen.indexOf(groupLinks[1])).toBe(1)
        const recordLink = seen.find((element) => element.closest("th[scope='col']") && element.tagName === "A") as Element
        expect(seen.indexOf(recordLink)).toBeGreaterThan(1)
        expect(seen.indexOf(recordLink)).toBeLessThan(seen.length - 1)
    })

    it("uses no ranking word anywhere on the page", async () => {
        await open()
        await table()
        await waitFor(() => expect(document.querySelectorAll("svg.energy-profile-svg").length).toBe(2))
        expect(rankingWords(textOf(document.body))).toEqual([])
    })

    it("shows no depositor-typed text", async () => {
        const planted = plantSentinels(records(K1, K2, K3, K4), ["note", "title"])
        expectPlanted(planted)
        await open({ records: planted.value })
        await table()
        await waitFor(() => expect(document.querySelectorAll("svg.energy-profile-svg").length).toBe(2))
        expectNoDepositorText()
        expectDepositorTextOnlyInNotes()
    })

    it("says so when the reaction has no rate records, and when only one", async () => {
        await open({ records: [] })
        expect(await screen.findByText("No rate records are deposited for this reaction.")).toBeInTheDocument()
        cleanup(); resetAllRequestCaches(); server.resetHandlers()
        await open({ records: records(K1) })
        expect(await screen.findByText(/Only one rate record is deposited/)).toBeInTheDocument()
    })

    it("reads at most six reaction entries' profiles and says so", async () => {
        const specs = Array.from({ length: 7 }, (_unused, index) => ({ ...K1, ref: `kin_n${index}` }))
        const entryOf = Object.fromEntries(specs.map((spec, index) => [spec.ref, `rxe_n${index}`]))
        const read: string[] = []
        await open({ records: records(...specs), entryOf, profiles: {}, onProfile: (ref) => read.push(ref) })
        await table()
        expect(await screen.findByText(/Profiles were read for the first 6 of 7 reaction entries/)).toBeInTheDocument()
        await waitFor(() => expect(read).toHaveLength(6))
        expect(new Set(read).size).toBe(6)
    })

    it("does not sort a list column whose values carry different units", async () => {
        await open({ records: records(K1, { ...K1, ref: "kin_m3", units: "m3_mol_s" }), entryOf: { ...ENTRY_OF, kin_m3: "rxe_one" } })
        const list = await screen.findByRole("region", { name: "Rate records as a list" })
        const head = within(list).getAllByRole("columnheader").find((cell) => cell.textContent?.includes("Pre-exponential")) as HTMLElement
        expect(head).toHaveTextContent("units differ in this row")
        expect(within(head).queryByRole("button")).toBeNull()
        const ea = within(list).getAllByRole("columnheader").find((cell) => cell.textContent?.includes("Activation energy")) as HTMLElement
        expect(within(ea).getByRole("button")).toBeInTheDocument()
    })

    it("offers no compare link from the reaction page below two records", async () => {
        const one = overview()
        one.records[0].availability.kinetics_count = 1
        await open({ overviewPayload: one }, `/reactions/${reactionRef}`)
        await screen.findByRole("heading", { level: 1 })
        await screen.findByText("Reaction entries")
        expect(screen.queryByRole("link", { name: "Compare these values" })).not.toBeInTheDocument()
    })

    it("offers the compare link from the reaction page when two records exist", async () => {
        await open({}, `/reactions/${reactionRef}`)
        expect(await screen.findByRole("link", { name: "Compare these values" })).toHaveAttribute("href", `/reactions/${reactionRef}/compare`)
    })
})
