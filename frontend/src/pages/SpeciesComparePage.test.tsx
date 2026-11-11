import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "../App"
import { resetAllRequestCaches } from "../api/requestCache"
import { rankingWords, textOf } from "../test/rankingWords"
import { ADVERSARIAL, expectDepositorTextOnlyInNotes, expectNoDepositorText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
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

function statmech(ref: string, entry: string, symmetry: number, core: Record<string, unknown> = {}) {
    return {
        statmech: {
            statmech_ref: ref, scientific_origin: "computed", statmech_treatment: "rrho", rigid_rotor_kind: "asymmetric_top",
            point_group: "C2v", external_symmetry: symmetry, ...core, created_at: "2026-07-29T08:26:29", review: { status: "approved" }, note: "typed by the depositor",
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


const thermoTable = () => region("Thermochemistry compared")
const statmechTable = () => region("Statistical mechanics compared")
const ids = (list: HTMLElement) => within(list).getAllByRole("rowheader").map((cell) => cell.textContent)

describe("molecule compare page", () => {
    it("titles the tab, heads the page and says what the page does not do", async () => {
        await open()
        await thermoTable()
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
        expect(document.body.textContent).toContain("Columns are grouped by version; within a version, the archive's display order.")
    })

    it("groups the records by version, in the molecule's version order, each column headed by its levels, programs, review word and link", async () => {
        await open()
        const table = await thermoTable()
        const groups = within(table).getAllByRole("columnheader").filter((header) => header.getAttribute("scope") === "colgroup")
        expect(groups.map((group) => group.getAttribute("colspan"))).toEqual(["2", "1"])
        expect(groups[0]).toHaveTextContent("minimum · ground state")
        expect(groups[0]).toHaveTextContent(entryA)
        expect(groups[1]).toHaveTextContent("minimum · excited state · 3B1")
        const headers = within(table).getAllByRole("columnheader").filter((header) => header.className.includes("compare-record"))
        expect(headers.map((header) => header.textContent?.match(/thm_(?:one|two|three)/)?.[0])).toEqual(["thm_one", "thm_two", "thm_three"])
        expect(headers[0]).toHaveTextContent("wb97xd/def2tzvp (geometry, frequency, energy)")
        expect(headers[1]).toHaveTextContent("ccsdt/ccpvtz (geometry, frequency, energy)")
        expect(headers[0]).toHaveTextContent("Arkane 1.1.0")
        expect(headers[1]).toHaveTextContent("approved")
        expect(headers[0]).toHaveTextContent("not reviewed")
        expect(within(headers[0]).getByRole("link", { name: "thm_one" })).toHaveAttribute("href", `/species-entries/${entryA}/thermo#thermo-heading-thm_one`)
    })

    it("lists enthalpy, entropy and uncertainties with units, and marks no measured row", async () => {
        await open()
        const table = await thermoTable()
        const h = rowCells(table, /Enthalpy of formation, 298/)
        expect(h[0]).toHaveTextContent("kJ/mol")
        expect(h.slice(1).map((cell) => cell.textContent)).toEqual(["143.90 ± 0.50", "141.20 ± 0.50", "141.20 ± 0.50"])
        expect(within(table).getByRole("link", { name: "143.90 ± 0.50" }).getAttribute("href")).toContain(`/species-entries/${entryA}/thermo`)
        expect(within(table).getAllByRole("rowheader").some((header) => /Not the same/.test(header.textContent ?? ""))).toBe(false)
        expect(within(table).getByRole("row", { name: /Phase/ })).toBeVisible()
        expect(within(table).getByRole("row", { name: /Reference pressure/ })).toBeVisible()
        expect(within(table).getByRole("row", { name: /Temperature range covered/ })).toBeVisible()
    })

    it("shows heat capacity only at shared temperatures, inside each record's own range, never borrowed", async () => {
        await open()
        const table = await thermoTable()
        expect(within(table).queryByRole("row", { name: /Heat capacity at 350/ })).not.toBeInTheDocument()
        expect(rowCells(table, /Heat capacity at 300/).slice(1).map((cell) => cell.textContent)).toEqual(["30", "31", "32"])
        const at500 = rowCells(table, /Heat capacity at 500/)
        expect(at500.slice(1).map((cell) => cell.textContent)).toEqual(["40", "41", "Outside its range"])
    })

    it("marks a setting that is not the same in every record of a version, in the row header, and leaves a measured row alone", async () => {
        await open({ statmech: [statmech("stm_one", entryA, 6), statmech("stm_two", entryA, 12), statmech("stm_three", entryB, 6, { rotational_constant_a_cm1: 0.00123 })] })
        const table = await statmechTable()
        const symmetry = within(within(table).getByRole("row", { name: /External symmetry number/ })).getByRole("rowheader")
        expect(symmetry).toHaveTextContent("≠ Not the same within a version")
        expect(within(within(table).getByRole("row", { name: /Treatment/ })).getByRole("rowheader")).not.toHaveTextContent("Not the same")
        expect(within(within(table).getByRole("row", { name: /Rotational constant A/ })).getByRole("rowheader")).not.toHaveTextContent("Not the same")
        expect(within(table.parentElement as HTMLElement).getByText(/a setting that is not the same within one version; not a verdict\./)).toBeVisible()
    })

    it("subscripts the point group and keeps exponents in both the comparison and the list, and sorts the list by the true value", async () => {
        const user = userEvent.setup()
        await open({ statmech: [statmech("stm_one", entryA, 6, { rotational_constant_a_cm1: 9.5 }), statmech("stm_two", entryA, 6, { rotational_constant_a_cm1: 0.00123 })] })
        const table = await statmechTable()
        const list = await region("Statistical mechanics records as a list")
        for (const box of [table, list]) {
            expect(box.querySelectorAll("sub")).toHaveLength(2)
            expect(box.querySelector("sub")).toHaveTextContent("2v")
            expect(box.querySelector("sup")).toHaveTextContent("−3")
            expect(box).toHaveTextContent("1.23 × 10−3")
        }
        await user.click(within(list).getByRole("button", { name: /^Rotational constant A/ }))
        expect(ids(list)).toEqual(["stm_two", "stm_one"])
        await user.click(within(list).getByRole("button", { name: /^Rotational constant A/ }))
        expect(ids(list)).toEqual(["stm_one", "stm_two"])
    })

    it("lists the records grouped by version with one row each, headers carrying their units, and never sorts by value by default", async () => {
        const user = userEvent.setup()
        await open()
        const list = await region("Thermochemistry records as a list")
        expect(ids(list)).toEqual(["thm_one", "thm_two", "thm_three"])
        expect(within(list).queryByText(/sorted by you/i)).not.toBeInTheDocument()
        const header = within(list).getByRole("button", { name: /^Enthalpy of formation, 298.K kJ\/mol sort/ })
        expect(header.querySelector(".data-table-unit")).toHaveTextContent("kJ/mol")
        await user.click(header)
        expect(screen.getAllByText(/Sorted by you: Enthalpy of formation, 298 K, ascending\. This is not a recommendation\./)).toHaveLength(1)
        expect(ids(list)).toEqual(["thm_two", "thm_three", "thm_one"])
        await user.click(screen.getByRole("button", { name: "Back to the archive's order" }))
        expect(header).toHaveFocus()
        expect(ids(list)).toEqual(["thm_one", "thm_two", "thm_three"])
    })

    it("says per table when a kind has one record, and what it is when none is deposited", async () => {
        await open({ thermo: thermoRows(THERMO()).slice(0, 1), statmech: [] })
        expect(await screen.findByText("Only one thermochemistry record is deposited, so there is nothing to compare it with.")).toBeVisible()
        expect(screen.getByText("No statistical mechanics records are deposited for this molecule.")).toBeVisible()
        cleanup()
        resetAllRequestCaches()
        await open({ thermo: [], statmech: [] })
        expect(await screen.findByText("No thermochemistry or statistical mechanics records are deposited for this molecule.")).toBeVisible()
    })

    it("shows at most 12 columns per version, says so, and keeps every record in the list", async () => {
        const many = Array.from({ length: 14 }, (_unused, index) => ({ species: { species_ref: speciesRef, species_entry_ref: entryA }, thermo: thermo(`thm_m${String(index).padStart(2, "0")}`, lotA, 100 + index, [[300, 30]], "not_reviewed", [300, 500]) }))
        await open({ thermo: many, statmech: [] })
        const table = await thermoTable()
        expect(within(table).getAllByRole("columnheader").filter((header) => header.className.includes("compare-record"))).toHaveLength(12)
        expect(screen.getByText("Showing the first 12 records of each version here. The list below has every record.")).toBeVisible()
        expect(ids(await region("Thermochemistry records as a list"))).toHaveLength(14)
    })

    it("uses no ranking word as a judgement, no literal double hyphen, and the guard does catch one", async () => {
        await open()
        await thermoTable()
        await statmechTable()
        const text = textOf(document.body)
        expect(rankingWords(text)).toEqual([])
        expect(text).not.toContain("--")
        const planted = document.createElement("p")
        planted.textContent = "Best"
        document.body.append(planted)
        expect(rankingWords(textOf(document.body))).toEqual(["Best"])
    })

    it("prints depositor text nowhere, and a point group that is not one as Not shown without dropping the row", async () => {
        const thermoPlanted = plantSentinels({ records: thermoRows(THERMO()) } as never, ["note"])
        const statmechPlanted = plantSentinels({ records: [statmech("stm_one", entryA, 6, { point_group: ADVERSARIAL.pointGroup }), statmech("stm_two", entryA, 6)] } as never, ["note"])
        expectPlanted(thermoPlanted)
        expectPlanted(statmechPlanted)
        await open({ thermo: (thermoPlanted.value as { records: unknown[] }).records, statmech: (statmechPlanted.value as { records: unknown[] }).records })
        const table = await statmechTable()
        expect(within(within(table).getByRole("row", { name: /Point group/ })).getByText("Not shown")).toBeVisible()
        expectNoDepositorText()
        expectDepositorTextOnlyInNotes()
        expect(document.body.innerHTML).not.toContain("PISS")
    })
})

describe("links to the compare page", () => {
    it("is offered on the molecule page when either kind has two or more records, and not otherwise", async () => {
        await open({ thermo: thermoRows(THERMO()).slice(0, 1), statmech: STATMECH() }, `/species/${speciesRef}`)
        expect(await screen.findByRole("link", { name: "Compare these values" })).toHaveAttribute("href", `/species/${speciesRef}/compare`)
        cleanup()
        resetAllRequestCaches()
        await open({ thermo: thermoRows(THERMO()).slice(0, 1), statmech: STATMECH().slice(0, 1) }, `/species/${speciesRef}`)
        await screen.findByRole("region", { name: "Table of values for this molecule" })
        expect(screen.queryByRole("link", { name: "Compare these values" })).not.toBeInTheDocument()
    })
})
