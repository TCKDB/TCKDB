import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "../App"
import { resetAllRequestCaches } from "../api/requestCache"
import { DEPOSITOR_SENTINEL, expectDepositorTextOnlyInNotes, expectPlanted, plantSentinels } from "../test/depositorSentinel"

const entryRef = "spe_numbers"
const otherEntryRef = "spe_excited"
const speciesRef = "spc_numbers"
const lotA = { method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp", level_of_theory_ref: "lot_a" }
const lotB = { method: "ccsdt", basis: "ccpvtz", display: "ccsdt/ccpvtz", level_of_theory_ref: "lot_b" }
const server = setupServer(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))

const summary = { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 }
const sortEcho = {
    sort: {
        keys: [
            { key: "review_rank", direction: "asc", meaning: "review status" },
            { key: "created_at", direction: "desc", meaning: "newest deposited" },
            { key: "id", direction: "desc", meaning: "unique tie-breaker" },
        ],
    },
}

function thermo(ref: string, lot: typeof lotA | null, h298: number | null, target: unknown, review = "not_reviewed") {
    return {
        thermo_ref: ref,
        scientific_origin: "computed", model_kind: "nasa",
        review: { status: review },
        h298_kj_mol: h298, s298_j_mol_k: h298 === null ? null : 200.5,
        enthalpy_reference_kind: "formation_298k",
        thermodynamic_target: target,
        levels: lot ? { geometry: lot, frequency: lot, energy: lot, energy_source: "opt" } : null,
        provenance: {
            software_release: { software_release_ref: "srel", software: "Arkane", version: "1.1.0" },
            conformer_group_ref: null,
        },
        note: "typed by the depositor",
    }
}

function records() {
    return [
        thermo("thm_a1", lotA, 143.9, { kind: "equilibrium_ensemble", conformer_group_ref: null, declared_by: "depositor" }),
        thermo("thm_b1", lotB, -12.34, { kind: "single_conformer", conformer_group_ref: "cg_1", declared_by: "depositor" }),
        thermo("thm_a2", lotA, null, null, "approved"),
    ]
}

function statmech(ref: string) {
    return {
        statmech: {
            statmech_ref: ref, scientific_origin: "computed", statmech_treatment: "rrho", point_group: "D3h",
            external_symmetry: 6, optical_isomers: 1, frequency_scale_factor_value: 0.9876,
            note: "typed by the depositor", created_at: "2026-07-29T08:26:29",
            review: { status: "not_reviewed" },
        },
        species: { species_ref: speciesRef, species_entry_ref: entryRef },
        software_release: { software_release_ref: "srel_g", software: "Gaussian", version: "16" },
        levels: { geometry: lotA, frequency: lotA, energy: lotB, energy_source: "sp" },
        evidence_summary: { source_calculation_count: 3, has_opt_calculation: true, has_freq_calculation: true, has_sp_calculation: true, has_rotor_scans: false, torsion_count: 0, has_frequency_scale_factor: true, has_conformer_context: false },
        available_sections: { has_source_calculations: true, has_torsions: false, has_electronic_levels: false, has_frequencies: true, has_conformers: false, has_review: true },
    }
}

type Options = {
    echo?: boolean
    entries?: unknown[]
    thermoPages?: boolean
    endless?: boolean
    thermoRecords?: unknown[]
    species?: unknown
}

function entrySummary(ref: string, state: string, calculations: number) {
    return {
        species_entry_ref: ref, species_entry_kind: "minimum", electronic_state_kind: state,
        species_entry_label: "typed label",
        review: { status: "not_reviewed" },
        availability: { has_thermo: true, has_statmech: true, has_transport: false, has_conformers: true, calculation_count: calculations },
    }
}

function handlers(options: Options = {}) {
    const all = options.thermoRecords ?? records()
    return [
        http.get("/api/v1/scientific/species/search", () => HttpResponse.json(options.species ?? { records: [{
            species_ref: speciesRef, canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", formula: "CH3",
            charge: 0, multiplicity: 2,
            entries: options.entries ?? [entrySummary(entryRef, "ground", 10), entrySummary(otherEntryRef, "excited", 4)],
        }] })),
        http.get("/api/v1/scientific/conformers/search", () => HttpResponse.json({ records: [{
            conformer_group: { conformer_group_ref: "cg_1", label: "conformer_1" },
            observations_summary: { total: 1 },
            evidence_summary: { calculation_count: 1, optimization_chain_count: 1, geometry_count: 1, evidence_coverage: { opt: 1, freq: 0, sp: 0 }, levels_of_theory: {} },
        }] })),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/thermo`, ({ request }) => {
            const query = new URL(request.url).searchParams
            const offset = Number(query.get("offset") ?? 0)
            if (options.endless) {
                return HttpResponse.json({ species_entry_ref: entryRef, review_summary: summary, records: [all[0]], pagination: { offset, limit: 1, returned: 1, total: 5000 } })
            }
            const size = options.thermoPages ? 2 : 50
            const page = all.slice(offset, offset + size)
            return HttpResponse.json({
                species_entry_ref: entryRef,
                review_summary: summary,
                records: page,
                pagination: { offset, limit: size, returned: page.length, total: all.length },
                request: options.echo === false ? {} : sortEcho,
            })
        }),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/statmech`, () => HttpResponse.json({
            review_summary: summary, records: [statmech("stm_1")],
            pagination: { offset: 0, limit: 50, returned: 1, total: 1 },
            request: options.echo === false ? {} : sortEcho,
        })),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/transport`, () => HttpResponse.json({
            review_summary: summary, records: [], pagination: { offset: 0, limit: 50, returned: 0, total: 0 },
        })),
        http.get("/api/v1/scientific/species-calculations/search", () => HttpResponse.json({ records: [] })),
    ]
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

async function open(options: Options = {}) {
    server.use(...handlers(options))
    window.history.replaceState({}, "", `/species-entries/${entryRef}`)
    render(<App />)
    await screen.findByRole("heading", { name: "Numbers for this entry" })
    return screen.findByRole("region", { name: "Numbers for this entry" })
}

function rowRefs(region: HTMLElement): string[] {
    return Array.from(region.querySelectorAll("tbody tr[id]")).map((row) => row.id)
}

describe("molecule page: numbers for this entry", () => {
    it("heads the page with real parents, a title that is not the ref, and a lede built from codes", async () => {
        await open()
        const crumbs = within(screen.getByRole("navigation", { name: "Breadcrumb" }))
        expect(crumbs.getByRole("link", { name: "Molecules" })).toHaveAttribute("href", "/species")
        expect(crumbs.getByRole("link", { name: "CH3" })).toHaveAttribute("href", `/species/${speciesRef}`)
        expect(crumbs.getByText("minimum · ground state")).toHaveAttribute("aria-current", "page")
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("CH3")
        expect(document.querySelector(".record-lede")).toHaveTextContent(
            "One version of this molecule: charge 0, doublet, ground state, an energy minimum. A molecule can have several versions.",
        )
        expect(document.title).toBe("CH3 · Molecule · TCKDB")
    })

    it("lists every record across pages, thermochemistry first, then statistical mechanics", async () => {
        const region = await open({ thermoPages: true })
        await waitFor(() => expect(rowRefs(region)).toEqual(["thermo-thm_a1", "thermo-thm_b1", "thermo-thm_a2", "statmech-stm_1"]))
        expect(within(region).getByText("143.90")).toBeVisible()
        expect(within(region).getByText("−12.34")).toBeVisible()
    })

    it("states the order the archive served, and says so when it stated none", async () => {
        await open()
        const note = document.querySelector(".entry-numbers .order-note") as HTMLElement
        expect(note).toHaveTextContent("Thermochemistry: approved first, then newest deposited first.")
        expect(note).toHaveTextContent("Statistical mechanics: approved first, then newest deposited first.")
        expect(note).toHaveTextContent("Transport: the archive did not state an order.")
        cleanup()
        server.resetHandlers()
        resetAllRequestCaches()
        await open({ echo: false })
        expect(document.querySelector(".entry-numbers .order-note")).toHaveTextContent("Thermochemistry: the archive did not state an order.")
    })

    it("groups by level of theory without re-ordering inside a group", async () => {
        const user = userEvent.setup()
        const region = await open()
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        await user.click(screen.getByRole("button", { name: "Group by level of theory" }))
        expect(screen.getByRole("button", { name: "Group by level of theory" })).toHaveAttribute("aria-pressed", "true")
        const bodies = Array.from(region.querySelectorAll("tbody"))
        const groups = bodies.map((body) => ({
            title: body.querySelector(".entry-numbers-group th")?.textContent ?? "",
            ids: Array.from(body.querySelectorAll("tr[id]")).map((row) => row.id),
        }))
        expect(groups.map((group) => group.ids)).toEqual([
            ["thermo-thm_a1", "thermo-thm_a2"],
            ["thermo-thm_b1"],
            ["statmech-stm_1"],
        ])
        expect(groups[0].title).toContain("wb97xd/def2tzvp")
        expect(groups[0].title).toContain("2 records")
        expect(groups[2].title).toContain("geometry wb97xd/def2tzvp")
        expect(groups[2].title).toContain("energy ccsdt/ccpvtz")
    })

    it("words what each thermo record describes from its declared target only", async () => {
        const region = await open()
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        const describes = (id: string) => (region.querySelector(`#${id} .entry-numbers-describes`) as HTMLElement)
        expect(describes("thermo-thm_a1")).toHaveTextContent("Describes All shapes together")
        expect(describes("thermo-thm_a2")).toHaveTextContent("Describes Not stated by the depositor")
        expect(describes("thermo-thm_b1")).toHaveTextContent("Describes One shape: Conformer Group 1")
        expect(within(describes("thermo-thm_b1")).getByRole("link")).toHaveAttribute("href", expect.stringContaining("conformer=cg_1"))
        expect(region.querySelector("#statmech-stm_1 .entry-numbers-describes")).toBeNull()
    })

    it("says Not recorded for a missing value and never shows zero", async () => {
        const region = await open()
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        const row = region.querySelector("#thermo-thm_a2") as HTMLElement
        expect(within(row).getAllByText("Not recorded").length).toBeGreaterThanOrEqual(3)
        expect(row).not.toHaveTextContent("0.00")
        expect(within(row).getAllByText("approved").length).toBeGreaterThanOrEqual(1)
    })

    it("gives a thermo value its full source line, linked to the record's own row on this page", async () => {
        const region = await open()
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        const source = region.querySelector("#thermo-thm_b1 .value-source") as HTMLElement
        expect(source).toHaveTextContent("Source thm_b1")
        expect(source).toHaveTextContent("geometry ccsdt/ccpvtz")
        expect(within(source).getByRole("link")).toHaveAttribute("href", expect.stringMatching(new RegExp(`^/species-entries/${entryRef}\\?conformer=cg_1#thermo-thm_b1$`)))
    })

    it("says when a list was longer than the cap", async () => {
        await open({ endless: true })
        expect(await screen.findByText(/Showing the first 5 of 5000 thermochemistry records/)).toBeVisible()
    })

    it("lists the other versions of the molecule, marking this one", async () => {
        await open()
        const section = (await screen.findByRole("heading", { name: "Other versions of this molecule" })).closest("section") as HTMLElement
        expect(await within(section).findByText("This molecule has 2 entries; this page shows one of them.")).toBeVisible()
        const items = Array.from(section.querySelectorAll(".entry-versions-item"))
        expect(items).toHaveLength(2)
        expect(items[0]).toHaveAttribute("aria-current", "true")
        expect(items[0]).toHaveTextContent("(this page)")
        expect(items[1]).toHaveTextContent("excited state")
        expect(items[1]).toHaveTextContent("4 calculations")
        expect(within(items[1] as HTMLElement).getByRole("link")).toHaveAttribute("href", `/species-entries/${otherEntryRef}`)
    })

    it("keeps depositor text out of everything but a depositor note", async () => {
        const planted = plantSentinels(
            { thermo: records(), statmech: [statmech("stm_1")], species: { records: [{ entries: [entrySummary(entryRef, "ground", 1)] }] } },
            ["note", "species_entry_label"],
        )
        expectPlanted(planted)
        const region = await open({
            thermoRecords: planted.value.thermo,
            species: { records: [{
                species_ref: speciesRef, canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", formula: "CH3",
                charge: 0, multiplicity: 2, entries: planted.value.species.records[0].entries,
            }] },
        })
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        await screen.findByText("This is the only entry listed for this molecule.")
        expect(document.body.innerHTML).not.toContain(DEPOSITOR_SENTINEL)
        expectDepositorTextOnlyInNotes()
    })

    it("tabs from the group toggle to the numbers box and on to the first value link", async () => {
        const user = userEvent.setup()
        const region = await open()
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        const toggle = screen.getByRole("button", { name: "Group by level of theory" })
        for (let step = 0; step < 120 && document.activeElement !== toggle; step += 1) await user.tab()
        expect(toggle).toHaveFocus()
        await user.tab()
        expect(within(region).getByRole("region", { name: "Table of numbers" })).toHaveFocus()
        await user.tab()
        const first = region.querySelector(".value-link") as HTMLElement
        expect(first).toHaveFocus()
        expect(first).toHaveTextContent("143.90")
    })
})
