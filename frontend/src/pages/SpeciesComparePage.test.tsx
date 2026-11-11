import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "../App"
import { resetAllRequestCaches } from "../api/requestCache"
import { expectDepositorTextOnlyInNotes, expectNoDepositorText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
import { titleHasKind } from "../test/routeTitleKinds"

const speciesRef = "spc_atp56uqux2ajao7hvckx7gx7ca"
const entryA = "spe_bcbdjwkip75yoziblpntwzblzu"
const entryB = "spe_abcdefghijklmnopqrstuvwxyz"
const lotA = { method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp", level_of_theory_ref: "lot_a" }
const lotB = { method: "ccsdt", basis: "ccpvtz", display: "ccsdt/ccpvtz", level_of_theory_ref: "lot_b" }
const summary = { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 }
const sortEcho = { sort: { keys: [{ key: "review_rank", direction: "asc", meaning: "review status" }, { key: "created_at", direction: "desc", meaning: "newest deposited" }] } }
const server = setupServer(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))

const entry = (ref: string, calculations: number) => ({
    species_entry_ref: ref, species_entry_kind: "minimum", electronic_state_kind: "ground", review: { status: "not_reviewed" },
    availability: { has_thermo: true, has_statmech: true, has_transport: false, has_conformers: true, calculation_count: calculations },
})

const speciesPayload = () => ({
    records: [{
        species_ref: speciesRef, canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", formula: "CH3", charge: 0, multiplicity: 2,
        stereo_kind: "achiral", entries: [entry(entryA, 14), { ...entry(entryB, 3), electronic_state_kind: "excited", term_symbol: "3B1" }],
    }],
})

function thermo(ref: string, lot: typeof lotA, h298: number | null, points: [number, number][], review = "not_reviewed", range?: [number, number]) {
    return {
        thermo_ref: ref, scientific_origin: "computed", model_kind: "nasa", review: { status: review },
        h298_kj_mol: h298, h298_uncertainty_kj_mol: 0.5, s298_j_mol_k: 190.25, enthalpy_reference_kind: "formation_298k",
        thermodynamic_target: null, phase: "gas", reference_pressure_bar: 1,
        levels: { geometry: lot, frequency: lot, energy: lot, energy_source: "opt" },
        provenance: { software_release: { software_release_ref: "srel", software: "Arkane", version: "1.1.0" } },
        points: points.map(([temperature_k, cp_j_mol_k]) => ({ temperature_k, cp_j_mol_k })),
        temperature_coverage: range ? { record_min_k: range[0], record_max_k: range[1], covers_requested_range: true, extrapolation_distance_k: 0 } : null,
        note: "typed by the depositor",
    }
}

const thermoRows = (rows: ReturnType<typeof thermo>[]) => rows.map((record, index) => ({ species: { species_ref: speciesRef, species_entry_ref: index === 2 ? entryB : entryA }, thermo: record }))

function statmech(ref: string, entry: string, symmetry: number) {
    return {
        statmech: {
            statmech_ref: ref, scientific_origin: "computed", statmech_treatment: "rrho", rigid_rotor_kind: "asymmetric_top",
            point_group: "C2v", external_symmetry: symmetry, created_at: "2026-07-29T08:26:29", review: { status: "approved" }, note: "typed by the depositor",
        },
        species: { species_ref: speciesRef, species_entry_ref: entry },
        software_release: { software_release_ref: "srel_g", software: "Gaussian", version: "16" },
        levels: { geometry: lotA, frequency: lotA, energy: lotB, energy_source: "sp" },
        evidence_summary: { source_calculation_count: 3, has_opt_calculation: true, has_freq_calculation: true, has_sp_calculation: true, has_rotor_scans: false, torsion_count: 0, has_frequency_scale_factor: false, has_conformer_context: false },
        available_sections: { has_source_calculations: true, has_torsions: false, has_electronic_levels: false, has_frequencies: true, has_conformers: false, has_review: true },
    }
}

const THERMO = () => [
    thermo("thm_one", lotA, 143.9, [[300, 30], [500, 40]], "not_reviewed", [300, 500]),
    thermo("thm_two", lotB, 141.2, [[300, 31], [500, 41]], "approved", [300, 500]),
    thermo("thm_three", lotB, 141.2, [[300, 32], [350, 33]], "not_reviewed", [300, 400]),
]
const STATMECH = () => [statmech("stm_one", entryA, 6), statmech("stm_two", entryA, 6)]

type Values = { thermo?: unknown[]; statmech?: unknown[]; entries?: object }
function handlers(values: Values = {}) {
    const thermoList = values.thermo ?? thermoRows(THERMO())
    const statmechList = values.statmech ?? STATMECH()
    const page = (rows: unknown[], request: Request) => {
        const url = new URL(request.url)
        const offset = Number(url.searchParams.get("offset") ?? 0)
        const limit = Number(url.searchParams.get("limit") ?? 50)
        const slice = rows.slice(offset, offset + limit)
        return { review_summary: summary, records: slice, pagination: { offset, limit, returned: slice.length, total: rows.length }, request: sortEcho }
    }
    return [
        http.get("/api/v1/scientific/species/search", () => HttpResponse.json(values.entries ?? speciesPayload())),
        http.get("/api/v1/scientific/thermo/search", ({ request }) => HttpResponse.json(page(thermoList, request))),
        http.get("/api/v1/scientific/statmech/search", ({ request }) => HttpResponse.json(page(statmechList, request))),
    ]
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); resetAllRequestCaches(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

async function open(values: Values = {}, path = `/species/${speciesRef}/compare`) {
    server.use(...handlers(values))
    window.history.replaceState({}, "", path)
    render(<App />)
}

const region = (name: string) => screen.findByRole("region", { name })
const rowCells = (table: HTMLElement, label: RegExp) => within(within(table).getByRole("row", { name: label })).getAllByRole("cell")

describe("molecule compare page", () => {
    it("titles the tab, heads the page and says what the page does not do", async () => {
        await open()
        await region("Thermochemistry compared")
        expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Compare values for CH3")
        await waitFor(() => expect(document.title).toBe("CH3 · Compare · TCKDB"))
        expect(titleHasKind(document.title, "/species/:ref/compare")).toBe(true)
        const crumbs = within(screen.getByRole("navigation", { name: "Breadcrumb" }))
        expect(crumbs.getByRole("link", { name: "Molecules" })).toHaveAttribute("href", "/species")
        expect(crumbs.getByRole("link", { name: "CH3" })).toHaveAttribute("href", `/species/${speciesRef}`)
        expect(document.querySelector(".breadcrumbs [aria-current='page']")).toHaveTextContent("Compare")
        expect(screen.getByRole("note")).toHaveTextContent("listed side by side for reading, not ranked")
        expect(screen.getByRole("note")).toHaveTextContent("never subtracts values across levels or programs")
    })

    it("gives each record a column headed by its version, levels, programs, review word and a link", async () => {
        await open()
        const table = await region("Thermochemistry compared")
        const headers = within(table).getAllByRole("columnheader")
        expect(headers.map((header) => header.textContent?.slice(0, 7))).toEqual(["Quantit", "Unit", "minimum", "minimum", "minimum"])
        expect(headers[2]).toHaveTextContent("minimum · ground state")
        expect(headers[4]).toHaveTextContent("minimum · excited state · 3B1")
        expect(headers[2]).toHaveTextContent("wb97xd/def2tzvp (geometry, frequency, energy)")
        expect(headers[3]).toHaveTextContent("ccsdt/ccpvtz (geometry, frequency, energy)")
        expect(headers[2]).toHaveTextContent("Arkane 1.1.0")
        expect(headers[3]).toHaveTextContent("approved")
        expect(headers[2]).toHaveTextContent("not reviewed")
        expect(within(headers[2]).getByRole("link", { name: "thm_one" })).toHaveAttribute("href", `/species-entries/${entryA}/thermo#thermo-heading-thm_one`)
    })

    it("lists enthalpy, entropy and uncertainties with units, marks the differing value and names no best", async () => {
        await open()
        const table = await region("Thermochemistry compared")
        const h = rowCells(table, /Enthalpy of formation, 298 K/)
        expect(h[0]).toHaveTextContent("kJ/mol")
        expect(h[1]).toHaveTextContent("143.90 ± 0.50")
        expect(h[1].className).toContain("compare-cell--differs")
        expect(h[2]).toHaveTextContent("141.20 ± 0.50")
        expect(h[3]).toHaveTextContent("141.20 ± 0.50")
        expect(h[1]).toHaveTextContent("differs from the others in this row")
        expect(h[2].className).not.toContain("differs")
        expect(h[3].className).not.toContain("differs")
        expect(within(table).getByRole("link", { name: "143.90 ± 0.50" }).getAttribute("href")).toContain(`/species-entries/${entryA}/thermo`)
        expect(rowCells(table, /Entropy, 298 K/)[1].className).not.toContain("differs")
        expect(screen.getAllByText(/differs from the others in this row; not a verdict\./).length).toBeGreaterThan(0)
    })

    it("shows heat capacity only at shared temperatures, inside each record's own range, never borrowed", async () => {
        await open()
        const table = await region("Thermochemistry compared")
        expect(within(table).queryByRole("row", { name: /Heat capacity at 350 K/ })).not.toBeInTheDocument()
        const at300 = rowCells(table, /Heat capacity at 300 K/)
        expect(at300.slice(1).map((cell) => cell.textContent?.slice(0, 5))).toEqual(["30.00", "31.00", "32.00"])
        const at500 = rowCells(table, /Heat capacity at 500 K/)
        expect(at500[1]).toHaveTextContent("40.00")
        expect(at500[2]).toHaveTextContent("41.00")
        expect(at500[3]).toHaveTextContent("Outside its range")
    })

    it("compares the statistical mechanics scalars and lists the records with one row each", async () => {
        await open()
        const table = await region("Statistical mechanics compared")
        expect(rowCells(table, /External symmetry number/)[1]).toHaveTextContent("6")
        expect(within(table).getAllByRole("columnheader", { name: /Gaussian 16/ })).toHaveLength(2)
        const list = await region("Thermochemistry records as a list")
        expect(within(list).getAllByRole("row")).toHaveLength(4)
    })

    it("never sorts the columns by value, and sorting the list is the reader's own act", async () => {
        const user = userEvent.setup()
        await open()
        const table = await region("Thermochemistry compared")
        expect(within(table).getAllByRole("columnheader").slice(2).map((header) => header.textContent?.match(/thm_(?:one|two|three)/)?.[0])).toEqual(["thm_one", "thm_two", "thm_three"])
        const list = await region("Thermochemistry records as a list")
        expect(within(list).queryByText(/sorted by you/i)).not.toBeInTheDocument()
        await user.click(within(list).getByRole("button", { name: "Sort by Enthalpy of formation, 298 K" }))
        expect(screen.getAllByText(/Sorted by you: Enthalpy of formation, 298 K, ascending\. This is not a recommendation\./)).toHaveLength(1)
        const first = within(list).getAllByRole("row")[1]
        expect(within(first).getByRole("rowheader")).toHaveTextContent("thm_two")
    })

    it("says what it is when one record is deposited and when none is", async () => {
        await open({ thermo: thermoRows(THERMO()).slice(0, 1), statmech: [] })
        expect(await screen.findByText("Only one record is deposited, so there is nothing to compare it with.")).toBeVisible()
        expect(document.querySelector(".compare-cell--differs")).toBeNull()
        cleanup()
        resetAllRequestCaches()
        await open({ thermo: [], statmech: [] })
        expect(await screen.findByText("No thermochemistry or statistical mechanics records are deposited for this molecule.")).toBeVisible()
    })

    it("uses no ranking word as a judgement and no literal double hyphen", async () => {
        await open()
        await region("Thermochemistry compared")
        await region("Statistical mechanics compared")
        const text = document.body.textContent ?? ""
        expect(text).not.toMatch(/\b(best|top|recommended|winner|preferred|lowest|highest)\b/i)
        expect(text).not.toContain("--")
    })

    it("prints depositor text nowhere", async () => {
        const thermoPlanted = plantSentinels({ records: thermoRows(THERMO()) } as never, ["note"])
        const statmechPlanted = plantSentinels({ records: STATMECH() } as never, ["note"])
        expectPlanted(thermoPlanted)
        expectPlanted(statmechPlanted)
        await open({ thermo: (thermoPlanted.value as { records: unknown[] }).records, statmech: (statmechPlanted.value as { records: unknown[] }).records })
        await region("Thermochemistry compared")
        expectNoDepositorText()
        expectDepositorTextOnlyInNotes()
    })
})

describe("links to the compare page", () => {
    it("is offered on the molecule page with two or more records and not with fewer", async () => {
        await open({}, `/species/${speciesRef}`)
        const link = await screen.findByRole("link", { name: "Compare these values" })
        expect(link).toHaveAttribute("href", `/species/${speciesRef}/compare`)
        cleanup()
        resetAllRequestCaches()
        await open({ thermo: thermoRows(THERMO()).slice(0, 1), statmech: [] }, `/species/${speciesRef}`)
        await screen.findByRole("region", { name: "Table of values for this molecule" })
        expect(screen.queryByRole("link", { name: "Compare these values" })).not.toBeInTheDocument()
    })
})
