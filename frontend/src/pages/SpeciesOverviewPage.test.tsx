import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { act, cleanup, render, screen, within, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "../App"
import { ADVERSARIAL, DEPOSITOR_SENTINEL, expectDepositorTextOnlyInNotes, expectNoAdversarialText, expectNoDepositorText, expectPlanted, plantSentinels, setAt } from "../test/depositorSentinel"
import { resetAllRequestCaches } from "../api/requestCache"
import { titleHasKind } from "../test/routeTitleKinds"

const speciesRef = "spc_atp56uqux2ajao7hvckx7gx7ca"
const entryRef = "spe_bcbdjwkip75yoziblpntwzblzu"
const excitedEntryRef = "spe_abcdefghijklmnopqrstuvwxyz"
const lotA = { method: "wb97xd", basis: "def2tzvp", display: "wb97xd/def2tzvp", level_of_theory_ref: "lot_a" }
const lotB = { method: "ccsdt", basis: "ccpvtz", display: "ccsdt/ccpvtz", level_of_theory_ref: "lot_b" }
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
// `App` mounts `AuthProvider`, which probes `GET /auth/me` regardless of route.
const server = setupServer(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))

function groundEntry() {
    return {
        species_entry_ref: entryRef,
        species_entry_kind: "minimum",
        electronic_state_kind: "ground",
        review: { status: "not_reviewed" },
        availability: { has_thermo: true, has_statmech: true, has_transport: false, has_conformers: true, calculation_count: 14 },
    }
}

function excitedEntry() {
    return {
        ...groundEntry(),
        species_entry_ref: excitedEntryRef,
        electronic_state_kind: "excited",
        term_symbol: "3B1",
        review: { status: "approved" },
        availability: { ...groundEntry().availability, calculation_count: 3 },
    }
}

function secondGroundEntry() {
    return {
        ...groundEntry(),
        species_entry_ref: "spe_secondgroundentryrecordabcdefgh",
        availability: { ...groundEntry().availability, calculation_count: 7 },
    }
}

function speciesPayload(entries: unknown[] = [groundEntry(), excitedEntry()]) {
    return {
        records: [{
            species_ref: speciesRef,
            canonical_smiles: "[CH3]",
            inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
            formula: "CH3",
            charge: 0,
            multiplicity: 2,
            stereo_kind: "achiral",
            entries,
        }],
    }
}

function thermo(ref: string, lot: typeof lotA | null, h298: number | null, review = "not_reviewed") {
    return {
        thermo_ref: ref, scientific_origin: "computed", model_kind: "nasa", review: { status: review },
        h298_kj_mol: h298, s298_j_mol_k: h298 === null ? null : 200.5,
        enthalpy_reference_kind: "formation_298k", thermodynamic_target: null, phase: "gas", reference_pressure_bar: 1,
        levels: lot ? { geometry: lot, frequency: lot, energy: lot, energy_source: "opt" } : null,
        provenance: { software_release: { software_release_ref: "srel", software: "Arkane", version: "1.1.0" } },
        note: "typed by the depositor",
    }
}

function thermoRow(ref: string, entry: string, lot: typeof lotA | null, h298: number | null, review?: string) {
    return { species: { species_ref: speciesRef, species_entry_ref: entry }, thermo: thermo(ref, lot, h298, review) }
}

function statmech(ref: string, entry: string | null) {
    return {
        statmech: {
            statmech_ref: ref, scientific_origin: "computed", statmech_treatment: "rrho", rigid_rotor_kind: "asymmetric_top",
            point_group: "C2v", external_symmetry: 6, created_at: "2026-07-29T08:26:29", review: { status: "not_reviewed" },
            note: "typed by the depositor",
        },
        species: entry ? { species_ref: speciesRef, species_entry_ref: entry } : null,
        software_release: { software_release_ref: "srel_g", software: "Gaussian", version: "16" },
        levels: { geometry: lotA, frequency: lotA, energy: lotB, energy_source: "sp" },
        evidence_summary: { source_calculation_count: 3, has_opt_calculation: true, has_freq_calculation: true, has_sp_calculation: true, has_rotor_scans: false, torsion_count: 0, has_frequency_scale_factor: false, has_conformer_context: false },
        available_sections: { has_source_calculations: true, has_torsions: false, has_electronic_levels: false, has_frequencies: true, has_conformers: false, has_review: true },
    }
}

type Values = { thermo?: unknown[]; statmech?: unknown[]; echo?: boolean; calls?: URL[] }

function valueHandlers(values: Values = {}) {
    const thermoRows = values.thermo ?? [
        thermoRow("thm_g1", entryRef, lotA, 143.9),
        thermoRow("thm_e1", excitedEntryRef, lotB, -12.34, "approved"),
        thermoRow("thm_g2", entryRef, null, null),
    ]
    const statmechRows = values.statmech ?? [statmech("stm_g1", entryRef)]
    const echo = values.echo === false ? {} : sortEcho
    const page = (rows: unknown[], request: Request) => {
        const url = new URL(request.url)
        values.calls?.push(url)
        const offset = Number(url.searchParams.get("offset") ?? 0)
        const limit = Number(url.searchParams.get("limit") ?? 50)
        const slice = rows.slice(offset, offset + limit)
        return { review_summary: summary, records: slice, pagination: { offset, limit, returned: slice.length, total: rows.length }, request: echo }
    }
    return [
        http.get("/api/v1/scientific/thermo/search", ({ request }) => HttpResponse.json(page(thermoRows, request))),
        http.get("/api/v1/scientific/statmech/search", ({ request }) => HttpResponse.json(page(statmechRows, request))),
    ]
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); resetAllRequestCaches(); window.history.replaceState({}, "", "/"); vi.useRealTimers() })
afterAll(() => server.close())

async function open(payload: object = speciesPayload(), values: Values = {}) {
    server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json(payload)), ...valueHandlers(values))
    window.history.replaceState({}, "", `/species/${speciesRef}`)
    render(<App />)
    await screen.findByRole("heading", { name: "Versions of this molecule" })
}

async function versionsSection(): Promise<HTMLElement> {
    return (await screen.findByRole("heading", { name: "Versions of this molecule" })).closest("section") as HTMLElement
}

async function valuesRegion(): Promise<HTMLElement> {
    return screen.findByRole("region", { name: "Table of values for this molecule" })
}

function rowIds(region: HTMLElement): string[] {
    return Array.from(region.querySelectorAll("tbody tr[id]")).map((row) => row.id)
}

describe("molecule overview: header", () => {
    it("heads the page with Molecules > formula breadcrumbs, a formula title, a code-built lede and a Molecule tab title", async () => {
        await open()
        const identity = document.querySelector(".page-shell-identity") as HTMLElement
        const crumbs = within(within(identity).getByRole("navigation", { name: "Breadcrumb" }))
        expect(crumbs.getByRole("link", { name: "Molecules" })).toHaveAttribute("href", "/species")
        expect(identity.querySelector(".breadcrumbs [aria-current='page']")).toHaveTextContent("CH3")
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("CH3")
        expect(screen.getByRole("heading", { level: 1 })).not.toHaveTextContent(speciesRef)
        expect(document.querySelector(".record-lede")).toHaveTextContent("A molecule with 2 versions.")
        await waitFor(() => expect(titleHasKind(document.title, "/species/:ref")).toBe(true))
        expect(document.title).toBe("CH3 · Molecule · TCKDB")
        expect(within(identity).getByText("Molecule ref")).toBeVisible()
        expect(within(identity).getByText("[CH3]")).toBeVisible()
        expect(within(identity).getByText("WCYWZMWISLQXQU-UHFFFAOYSA-N")).toBeVisible()
        expect(within(identity).getByText("0 / doublet (2)")).toBeVisible()
    })

    it("says listed versions when some were hidden by default", async () => {
        await open({ ...speciesPayload(), request: { hidden_by_default: { counts: { rejected: 1 }, include_with: {}, counted: "species_entries" } } })
        expect(document.querySelector(".record-lede")).toHaveTextContent("A molecule with 2 listed versions.")
    })

    it("says one version in the singular", async () => {
        await open(speciesPayload([groundEntry()]))
        expect(document.querySelector(".record-lede")).toHaveTextContent("A molecule with 1 version.")
    })

    it("has one h1, a contents list for its two sections, and says nothing for the empty names slot", async () => {
        await open()
        expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
        const toc = await screen.findByRole("navigation", { name: /sections on this page/i })
        expect(within(toc).getByRole("link", { name: "Versions of this molecule" })).toBeVisible()
        expect(within(toc).getByRole("link", { name: "All values for this molecule" })).toBeVisible()
        expect(screen.queryByText(/also known as/i)).not.toBeInTheDocument()
        expect(document.body.textContent).not.toMatch(/\bSpecies\b/)
    })
})

describe("molecule overview: versions", () => {
    it("lists every version with a code-built label, what it holds, its review status and a link", async () => {
        await open()
        const section = await versionsSection()
        const items = within(section).getAllByRole("listitem")
        expect(items).toHaveLength(2)
        const ground = within(items[0]).getByRole("link", { name: "Version 1 · minimum · ground state" })
        expect(ground).toHaveAttribute("href", `/species-entries/${entryRef}`)
        expect(items[0]).toHaveTextContent("14 calculations; conformers, thermochemistry, statistical mechanics")
        expect(within(items[0]).getByText("not reviewed")).toBeVisible()
        expect(section.textContent).not.toContain(entryRef)
        expect(within(items[1]).getByRole("link", { name: "Version 2 · minimum · excited state · 3B1" })).toHaveAttribute("href", `/species-entries/${excitedEntryRef}`)
        expect(within(items[1]).getByText("approved")).toBeVisible()
        expect(within(items[0]).getAllByRole("link")).toHaveLength(1)
    })

    it("lists repeated ground-state versions as separate links to their own entries, printing no ref", async () => {
        await open(speciesPayload([groundEntry(), secondGroundEntry(), excitedEntry()]))
        const section = await versionsSection()
        const ground = [
            within(section).getByRole("link", { name: "Version 1 · minimum · ground state" }),
            within(section).getByRole("link", { name: "Version 2 · minimum · ground state" }),
        ]
        expect(ground.map((link) => link.getAttribute("href"))).toEqual([`/species-entries/${entryRef}`, "/species-entries/spe_secondgroundentryrecordabcdefgh"])
        expect(new Set(ground.map((link) => link.textContent)).size).toBe(2)
        expect(section.textContent).not.toContain("spe_secondgroundentryrecordabcdefgh")
        expect(within(section).getByText("7 calculations; conformers, thermochemistry, statistical mechanics")).toBeVisible()
    })

    it("words a stereo version as a readable phrase, never a bare R", async () => {
        const entry = { ...groundEntry(), stereo_label: "R", species_entry_label: "R" }
        await open(speciesPayload([entry]))
        const link = within(await versionsSection()).getByRole("link", { name: /minimum/ })
        expect(link).toHaveAccessibleName("Version 1 · minimum · ground state · R enantiomer")
        expect(link).not.toHaveTextContent(/^R$/)
    })

    it("tells excited states apart by their own state and term-symbol words, and hides what is outside the grammar", async () => {
        const stateEntry = (ref: string, label: string, term: string) => ({ ...excitedEntry(), species_entry_ref: ref, electronic_state_label: label, term_symbol: term })
        await open(speciesPayload([groundEntry(), stateEntry("spe_singletfirstexcitedabcdefghij", "S1", "¹B₁"), stateEntry("spe_tripletfirstexcitedabcdefghij", "T1", "³B₁")]))
        const versions = within(await versionsSection())
        expect(versions.getByRole("link", { name: /state S1 · ¹B₁/ })).toBeVisible()
        expect(versions.getByRole("link", { name: /state T1 · ³B₁/ })).toBeVisible()
        cleanup()
        resetAllRequestCaches()
        await open(speciesPayload([groundEntry(), stateEntry("spe_legacyexcitedabcdefghijklmn", "R", `${DEPOSITOR_SENTINEL}_term`)]))
        expect(await within(await versionsSection()).findAllByRole("link", { name: /minimum/ })).toHaveLength(2)
        expect(document.body.textContent).not.toMatch(/state R|enantiomer/)
        expectNoDepositorText()
    })

    it("explains when no versions are listed", async () => {
        await open(speciesPayload([]))
        expect(await screen.findByText("No versions are currently listed for this molecule.")).toBeVisible()
    })

    it("says how many versions were not listed, from the server's own count, and nothing when none were hidden", async () => {
        await open({
            ...speciesPayload(),
            request: { hidden_by_default: { counts: { rejected: 2, deprecated: 1 }, include_with: {}, counted: "species_entries" } },
        })
        expect(await screen.findByText("Not listed by default: 2 rejected and 1 deprecated versions.")).toBeVisible()
        cleanup()
        resetAllRequestCaches()
        await open({ ...speciesPayload(), request: { hidden_by_default: { counts: { rejected: 1 }, include_with: {}, counted: "species_entries" } } })
        expect(await screen.findByText("Not listed by default: 1 rejected version.")).toBeVisible()
        cleanup()
        resetAllRequestCaches()
        await open()
        expect(screen.queryByText(/Not listed by default/)).not.toBeInTheDocument()
    })

    it("draws the map from the versions, in the side column, with no note line", async () => {
        await open(speciesPayload([groundEntry(), secondGroundEntry(), excitedEntry()]))
        const rail = document.querySelector(".page-shell-side") as HTMLElement
        expect(rail.querySelector(".rel-map-note:not(svg *)")).toBeNull()
        expect(within(rail).queryByText(/This map shows/)).toBeNull()
        const list = within(rail).getByRole("list", { name: "Connected records" })
        const named = within(rail).getAllByRole("link", { name: "Version 1 · minimum · ground state, A version of this molecule", hidden: true })
        expect(named.map((link) => link.getAttribute("href"))).toContain(`/species-entries/${entryRef}`)
        expect(within(list).getAllByRole("listitem")[1]).toHaveTextContent("A version of this molecule")
        expect(rail.textContent).not.toMatch(/spe_/)
        const same = [
            within(list).getByRole("link", { name: "Version 1 · minimum · ground state" }),
            within(list).getByRole("link", { name: "Version 2 · minimum · ground state" }),
        ]
        expect(new Set(same.map((link) => link.textContent)).size).toBe(2)
        expect(same[0]).toHaveAccessibleDescription("A version of this molecule")
        expect(same[1]).toHaveAccessibleDescription("A version of this molecule")
        expect(same.map((link) => link.getAttribute("href"))).toEqual([`/species-entries/${entryRef}`, "/species-entries/spe_secondgroundentryrecordabcdefgh"])
        expect(within(rail).getByRole("link", { name: "This molecule (you are here)", hidden: true })).toBeInTheDocument()
    })

    it("draws at most 8 versions in the map and says how many more are under Versions", async () => {
        const many = Array.from({ length: 10 }, (_, index) => ({ ...groundEntry(), species_entry_ref: `spe_version${index}` }))
        await open(speciesPayload(many))
        const rail = document.querySelector(".page-shell-side") as HTMLElement
        const items = within(within(rail).getByRole("list", { name: "Connected records" })).getAllByRole("listitem")
        expect(items).toHaveLength(9)
        const more = within(items[8]).getByRole("link", { name: "2 more under Versions" })
        expect(more.getAttribute("href")).toMatch(/#species-versions$/)
        expect(rail.querySelectorAll("svg a.rel-map-node:not(.rel-map-node--self)")).toHaveLength(9)
        expect(within(rail).queryByText("spe_version9", { exact: false })).toBeNull()
    })

    it("gives version links no ref description, in the list and in the table", async () => {
        await open(speciesPayload([groundEntry(), secondGroundEntry(), excitedEntry()]), { thermo: [thermoRow("thm_c1", "spe_secondgroundentryrecordabcdefgh", lotA, 1)], statmech: [] })
        const versions = within(await versionsSection())
        expect(versions.getAllByRole("link")[1]).toHaveAccessibleDescription("")
        expect(versions.getAllByRole("link").map((link) => link.textContent)).toEqual([
            "Version 1 · minimum · ground state", "Version 2 · minimum · ground state", "Version 3 · minimum · excited state · 3B1",
        ])
        expect(versions.getAllByRole("link")[1]).toHaveAttribute("href", "/species-entries/spe_secondgroundentryrecordabcdefgh")
        const region = await valuesRegion()
        await waitFor(() => expect(rowIds(region)).toHaveLength(1))
        const row = region.querySelector("#thermo-thm_c1") as HTMLElement
        const link = within(row).getByRole("link", { name: "Version 2 · minimum · ground state" })
        expect(link).toHaveAccessibleDescription("")
        expect(link).toHaveAttribute("href", "/species-entries/spe_secondgroundentryrecordabcdefgh")
        expect(row.textContent).not.toContain("spe_secondgroundentryrecordabcdefgh")
    })
})

describe("molecule overview: all values for this molecule", () => {
    it("asks both searches by the molecule and lists every record, thermochemistry first, each naming its version", async () => {
        const calls: URL[] = []
        await open(speciesPayload(), { calls })
        const region = await valuesRegion()
        await waitFor(() => expect(rowIds(region)).toEqual(["thermo-thm_g1", "thermo-thm_e1", "thermo-thm_g2", "statmech-stm_g1"]))
        expect(calls.length).toBeGreaterThanOrEqual(2)
        for (const call of calls) expect(call.searchParams.get("species_ref")).toBe(speciesRef)
        const ground = region.querySelector("#thermo-thm_g1") as HTMLElement
        const link = within(ground).getByRole("link", { name: "Version 1 · minimum · ground state" })
        expect(link).toHaveAttribute("href", `/species-entries/${entryRef}`)
        expect(ground).toHaveTextContent("Version: Version 1 · minimum · ground state")
        expect(within(region.querySelector("#thermo-thm_e1") as HTMLElement).getByRole("link", { name: /excited state/ })).toHaveAttribute("href", `/species-entries/${excitedEntryRef}`)
        expect(within(region.querySelector("#statmech-stm_g1") as HTMLElement).getByRole("link", { name: /ground state/ })).toBeVisible()
        expect(within(ground).getByText("143.90")).toBeVisible()
        expect(within(region.querySelector("#thermo-thm_e1") as HTMLElement).getByText("−12.34")).toBeVisible()
    })

    it("links each value to its record on its own version's page", async () => {
        await open()
        const region = await valuesRegion()
        await waitFor(() => expect(rowIds(region)).toHaveLength(4))
        expect(within(region.querySelector("#thermo-thm_g1") as HTMLElement).getByRole("link", { name: /143\.90/ }))
            .toHaveAttribute("href", `/species-entries/${entryRef}/thermo#thermo-heading-thm_g1`)
        expect(within(region.querySelector("#thermo-thm_e1") as HTMLElement).getByRole("link", { name: /12\.34/ }))
            .toHaveAttribute("href", `/species-entries/${excitedEntryRef}/thermo#thermo-heading-thm_e1`)
        expect(within(region.querySelector("#statmech-stm_g1") as HTMLElement).getByRole("link", { name: /^6/ }))
            .toHaveAttribute("href", `/species-entries/${entryRef}/statmech#statmech-heading-stm_g1`)
    })

    it("shows the full source line on every row: levels, program and review status", async () => {
        await open()
        const region = await valuesRegion()
        await waitFor(() => expect(rowIds(region)).toHaveLength(4))
        const source = (id: string) => (region.querySelector(`#${id} .value-source`) as HTMLElement)
        expect(source("thermo-thm_g1")).toHaveTextContent("geometry, frequency, energy at wb97xd/def2tzvp")
        expect(source("thermo-thm_g1")).toHaveTextContent("Arkane")
        expect(source("statmech-stm_g1")).toHaveTextContent("energy ccsdt/ccpvtz")
        expect(source("statmech-stm_g1")).toHaveTextContent("Gaussian")
        expect(within(region.querySelector("#thermo-thm_e1 th") as HTMLElement).getByText("approved")).toBeVisible()
        expect(within(region.querySelector("#thermo-thm_g1 th") as HTMLElement).getByText("not reviewed")).toBeVisible()
        expect(Array.from(region.querySelectorAll("thead th")).map((cell) => cell.textContent)).toEqual(["Record", "Values"])
    })

    it("states the order the archive served, once, and says so when it stated none", async () => {
        await open()
        await valuesRegion()
        const note = await waitFor(() => {
            const found = document.querySelector(".entry-numbers .order-note") as HTMLElement
            expect(found).not.toBeNull()
            return found
        })
        expect(note).toHaveTextContent("Thermochemistry, then statistical mechanics; approved first, then newest deposited first.")
        expect(note).toHaveTextContent("This order is not a recommendation.")
        cleanup()
        resetAllRequestCaches()
        server.resetHandlers()
        await open(speciesPayload(), { echo: false })
        await waitFor(() => expect(document.querySelector(".entry-numbers .order-note")).toHaveTextContent("the archive did not state an order"))
    })

    it("says Not recorded for a missing value and never shows zero or another version's number", async () => {
        await open()
        const region = await valuesRegion()
        await waitFor(() => expect(rowIds(region)).toHaveLength(4))
        const row = region.querySelector("#thermo-thm_g2") as HTMLElement
        expect(within(row).getAllByText("Not recorded").length).toBeGreaterThanOrEqual(3)
        expect(row).not.toHaveTextContent("0.00")
        expect(row).not.toHaveTextContent("143.90")
    })

    it("does not give a record the archive attributes to no version a version of its own", async () => {
        await open(speciesPayload(), { statmech: [statmech("stm_none", null)] })
        const region = await valuesRegion()
        const row = await waitFor(() => {
            const found = region.querySelector("#statmech-stm_none") as HTMLElement
            expect(found).not.toBeNull()
            return found
        })
        expect(row).toHaveTextContent("Version not stated by the archive")
        expect(within(row).queryByRole("link", { name: /ground state|excited state/ })).toBeNull()
        expect(row.querySelector("a[href*='species-entries']")).toBeNull()
    })

    it("names a version missing from the listing without inventing a label", async () => {
        await open(speciesPayload([groundEntry()]), { thermo: [thermoRow("thm_x", excitedEntryRef, lotA, 5)], statmech: [] })
        const region = await valuesRegion()
        const row = await waitFor(() => {
            const found = region.querySelector("#thermo-thm_x") as HTMLElement
            expect(found).not.toBeNull()
            return found
        })
        expect(within(row).getByRole("link", { name: "Another version" })).toHaveAttribute("href", `/species-entries/${excitedEntryRef}`)
        expect(row.textContent).not.toContain(excitedEntryRef)
    })

    it("walks later pages and offers the level toggle for two or more levels", async () => {
        const user = userEvent.setup()
        const many = Array.from({ length: 120 }, (_, index) => thermoRow(`thm_${index}`, index % 2 ? entryRef : excitedEntryRef, index % 3 ? lotA : lotB, index))
        const calls: URL[] = []
        await open(speciesPayload(), { thermo: many, statmech: [], calls })
        const region = await valuesRegion()
        await waitFor(() => expect(rowIds(region)).toHaveLength(120))
        expect(calls.some((call) => Number(call.searchParams.get("offset")) > 0)).toBe(true)
        await user.click(screen.getByRole("button", { name: "Group by level of theory" }))
        expect(screen.getByRole("button", { name: "Group by level of theory" })).toHaveAttribute("aria-pressed", "true")
        expect(region.querySelectorAll(".entry-numbers-group")).toHaveLength(2)
    })

    it("says so when there are no values, and when the values could not be loaded", async () => {
        await open(speciesPayload(), { thermo: [], statmech: [] })
        expect(await screen.findByText("No thermochemistry or statistical mechanics records are deposited for this molecule.")).toBeVisible()
        cleanup()
        resetAllRequestCaches()
        server.use(http.get("/api/v1/scientific/thermo/search", () => HttpResponse.json({ detail: "down" }, { status: 503 })))
        window.history.replaceState({}, "", `/species/${speciesRef}`)
        render(<App />)
        expect(await screen.findByText("The values for this molecule could not be loaded. Each version's page still shows its own records.")).toBeVisible()
        expect(screen.getByRole("heading", { name: "Versions of this molecule" })).toBeVisible()
    })

    it("tabs through the version links in the versions list, then the version and value links in the table", async () => {
        const user = userEvent.setup()
        await open()
        const region = await valuesRegion()
        await waitFor(() => expect(rowIds(region)).toHaveLength(4))
        const versions = await versionsSection()
        const [first, second] = within(versions).getAllByRole("link")
        first.focus()
        expect(first).toHaveFocus()
        await user.tab()
        expect(second).toHaveFocus()
        const firstRow = region.querySelector("#thermo-thm_g1") as HTMLElement
        const stops: string[] = []
        for (let index = 0; index < 40 && !stops.includes("thm_g1 value"); index++) {
            await user.tab()
            const active = document.activeElement as HTMLElement
            if (firstRow.contains(active) && active.getAttribute("href")?.includes("#thermo-heading-thm_g1")) stops.push("thm_g1 value")
            if (firstRow.contains(active) && active.getAttribute("href") === `/species-entries/${entryRef}`) stops.push("thm_g1 version")
        }
        expect(stops).toEqual(["thm_g1 version", "thm_g1 value"])
    })
})

describe("molecule overview: states", () => {
    it("distinguishes absent, malformed, and unavailable projections", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [] })))
        window.history.replaceState({}, "", `/species/${speciesRef}`)
        const first = render(<App />)
        expect(await screen.findByRole("heading", { name: "Molecule not found" })).toBeVisible()
        await waitFor(() => expect(titleHasKind(document.title, "/species/:ref")).toBe(true))
        first.unmount()
        resetAllRequestCaches()

        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [{}] })))
        const second = render(<App />)
        expect(await screen.findByRole("alert")).toHaveTextContent("Molecule data could not be read")
        second.unmount()
        resetAllRequestCaches()

        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ detail: "archive unavailable" }, { status: 503 })))
        render(<App />)
        expect(await screen.findByRole("alert")).toHaveTextContent("Molecule unavailable")
    })

    it("gives a double 429 its own rate-limited state, in plain language, distinct from 'unavailable'", async () => {
        // Load the lazy page module before faking timers: fake timers cannot advance a module load.
        await import("./SpeciesOverviewPage")
        vi.useFakeTimers()
        server.use(http.get("/api/v1/scientific/species/search", () => (
            HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "45" } })
        )))
        window.history.replaceState({}, "", `/species/${speciesRef}`)
        render(<App />)
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        await act(async () => { await vi.advanceTimersByTimeAsync(45_000) })

        expect(screen.getByRole("heading", { name: "Archive is busy" })).toBeVisible()
        expect(screen.queryByRole("heading", { name: "Molecule unavailable" })).not.toBeInTheDocument()
        const message = screen.getByRole("alert")
        expect(message).toHaveTextContent("The archive is receiving too many requests right now. Wait about 45 seconds and reload the page.")
        expect(message.textContent).not.toMatch(/\d+s\b/)
    })
})

describe("molecule overview: depositor text", () => {
    it("prints none of the typed state label, term symbol, joined label or record note outside a depositor note", async () => {
        const planted = plantSentinels(speciesPayload([
            { ...groundEntry(), electronic_state_label: "x", term_symbol: "x", species_entry_label: "x" } as Record<string, unknown>,
            { ...excitedEntry(), electronic_state_label: "x", term_symbol: "x", species_entry_label: "x" } as Record<string, unknown>,
        ] as never), ["electronic_state_label", "term_symbol", "species_entry_label"])
        expectPlanted(planted)
        const { value } = planted
        setAt(value, "records.0.entries.0.species_entry_label", `${ADVERSARIAL.stereoMark} ${ADVERSARIAL.smilesWord}`)
        const thermoPlanted = plantSentinels({ records: [{ ...thermoRow("thm_g1", entryRef, lotA, 143.9), thermo: { ...thermo("thm_g1", lotA, 143.9), note: "x" } }] } as never, ["note"])
        expectPlanted(thermoPlanted)
        const statmechPlanted = plantSentinels({ records: [statmech("stm_g1", entryRef)] } as never, ["note"])
        expectPlanted(statmechPlanted)
        await open(value, {
            thermo: (thermoPlanted.value as { records: unknown[] }).records,
            statmech: (statmechPlanted.value as { records: unknown[] }).records,
        })
        const region = await valuesRegion()
        await waitFor(() => expect(rowIds(region)).toEqual(["thermo-thm_g1", "statmech-stm_g1"]))
        expect(await within(await versionsSection()).findByRole("link", { name: "Version 1 · minimum · ground state" })).toBeVisible()
        expect(document.querySelector(".entry-versions-meta code")).toBeNull()
        expectNoDepositorText()
        expectDepositorTextOnlyInNotes()
        expectNoAdversarialText()
    })
})
