import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import App from "../App"
import { resetAllRequestCaches } from "../api/requestCache"
import { ADVERSARIAL, DEPOSITOR_SENTINEL, expectDepositorTextOnlyInNotes, expectNoAdversarialText, expectPlanted, plantSentinels, setAt } from "../test/depositorSentinel"

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

function thermo(ref: string, lot: typeof lotA | null, h298: number | null, target: unknown, review = "not_reviewed", group: string | null = null, kind = "nasa") {
    return {
        thermo_ref: ref,
        scientific_origin: "computed", model_kind: kind,
        review: { status: review },
        h298_kj_mol: h298, s298_j_mol_k: h298 === null ? null : 200.5,
        enthalpy_reference_kind: "formation_298k",
        thermodynamic_target: target,
        phase: "gas", reference_pressure_bar: 1,
        temperature_coverage: { record_min_k: 300, record_max_k: 2000, covers_requested_range: true, extrapolation_distance_k: 0 },
        levels: lot ? { geometry: lot, frequency: lot, energy: lot, energy_source: "opt" } : null,
        provenance: {
            software_release: { software_release_ref: "srel", software: "Arkane", version: "1.1.0" },
            conformer_group_ref: group,
        },
        note: "typed by the depositor",
    }
}

const ensemble = { kind: "equilibrium_ensemble", conformer_group_ref: null, declared_by: "depositor" }

function records() {
    return [
        thermo("thm_a1", lotA, 143.9, ensemble),
        thermo("thm_b1", lotB, -12.34, { kind: "single_conformer", conformer_group_ref: "cg_1", declared_by: "depositor" }),
        thermo("thm_a2", lotA, null, null, "approved"),
    ]
}

function statmech(ref: string, core: Record<string, unknown> = {}) {
    return {
        statmech: {
            statmech_ref: ref, scientific_origin: "computed", statmech_treatment: "rrho_1d", rigid_rotor_kind: "asymmetric_top",
            point_group: "C2v", external_symmetry: 6, optical_isomers: 1, is_linear: false, uses_projected_frequencies: null,
            rotational_constant_a_cm1: 9.5, rotational_constant_b_cm1: 0.00123,
            frequency_scale_factor_value: 0.9876,
            note: "typed by the depositor", created_at: "2026-07-29T08:26:29",
            review: { status: "not_reviewed" },
            ...core,
        },
        species: { species_ref: speciesRef, species_entry_ref: entryRef },
        software_release: { software_release_ref: "srel_g", software: "Gaussian", version: "16" },
        levels: { geometry: lotA, frequency: lotA, energy: lotB, energy_source: "sp" },
        evidence_summary: { source_calculation_count: 3, has_opt_calculation: true, has_freq_calculation: true, has_sp_calculation: true, has_rotor_scans: false, torsion_count: 0, has_frequency_scale_factor: true, has_conformer_context: false },
        available_sections: { has_source_calculations: true, has_torsions: false, has_electronic_levels: false, has_frequencies: true, has_conformers: false, has_review: true },
    }
}

const transportRecord = {
    transport: {
        transport_ref: "trn_1", scientific_origin: "computed", sigma_angstrom: 3.8, epsilon_over_k_k: 144, dipole_debye: 0,
        polarizability_angstrom3: 2.60, rotational_relaxation: 1.5, created_at: "2026-07-29T08:26:29", review: { status: "not_reviewed" },
    },
    species: { species_ref: speciesRef, species_entry_ref: entryRef },
    evidence_summary: { source_calculation_count: 1, has_source_calculations: true, has_lj_parameters: true, has_dipole_moment: true, has_polarizability: true, has_rotational_relaxation: true, has_literature_source: false },
    available_sections: { has_source_calculations: true, has_review: false },
    source_calculations: [{
        role: "dipole", calculation_ref: "calc_t1", calculation_type: "sp", quality: "ok", created_at: "2026-07-29T08:26:29",
        review: { status: "not_reviewed" }, level_of_theory: lotB,
    }],
}

type Options = {
    echo?: boolean
    entries?: unknown[]
    /** What the by-molecule listing returns, when it differs from the entry lookup. */
    listing?: unknown[]
    thermoPages?: boolean
    endless?: boolean
    thermoRecords?: unknown[]
    statmechRecords?: unknown[]
    transportRecords?: unknown[]
    species?: unknown
    counts?: { thermo: number; statmech: number; transport: number; thermoLimits?: string[] }
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
    const stat = options.statmechRecords ?? [statmech("stm_1")]
    const transport = options.transportRecords ?? []
    const echo = options.echo === false ? {} : sortEcho
    return [
        http.get("/api/v1/scientific/species/search", ({ request }) => {
            const byMolecule = new URL(request.url).searchParams.has("species_ref")
            return HttpResponse.json(options.species ?? { records: [{
                species_ref: speciesRef, canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", formula: "CH3",
                charge: 0, multiplicity: 2,
                entries: (byMolecule ? options.listing : undefined) ?? options.entries ?? [entrySummary(entryRef, "ground", 10), entrySummary(otherEntryRef, "excited", 4)],
            }] })
        }),
        http.get("/api/v1/scientific/conformers/search", () => HttpResponse.json({ records: [{
            conformer_group: { conformer_group_ref: "cg_1", label: "conformer_1" },
            observations_summary: { total: 1 },
            evidence_summary: { calculation_count: 1, optimization_chain_count: 1, geometry_count: 1, evidence_coverage: { opt: 1, freq: 0, sp: 0 }, levels_of_theory: {} },
        }] })),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/thermo`, ({ request }) => {
            if (options.counts) options.counts.thermo += 1
            const query = new URL(request.url).searchParams
            const offset = Number(query.get("offset") ?? 0)
            if (options.endless) {
                const size = Number(query.get("limit") ?? 50)
                const page = Array.from({ length: size }, (_, index) => thermo(`thm_${offset + index}`, lotA, offset + index, ensemble))
                options.counts?.thermoLimits?.push(String(size))
                return HttpResponse.json({ species_entry_ref: entryRef, review_summary: summary, records: page, pagination: { offset, limit: size, returned: size, total: 5000 } })
            }
            const size = options.thermoPages ? 2 : 50
            const page = all.slice(offset, offset + size)
            return HttpResponse.json({
                species_entry_ref: entryRef, review_summary: summary, records: page,
                pagination: { offset, limit: size, returned: page.length, total: all.length },
                request: echo,
            })
        }),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/statmech`, ({ request }) => {
            // Only the plain list counts: a record's on-demand sections are their own reads.
            if (options.counts && !new URL(request.url).searchParams.has("include")) options.counts.statmech += 1
            return HttpResponse.json({
                review_summary: summary, records: stat,
                pagination: { offset: 0, limit: 50, returned: stat.length, total: stat.length },
                request: echo,
            })
        }),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/transport`, ({ request }) => {
            // The numbers table asks for the source calculations so it can show the levels the record ran at.
            const withSources = new URL(request.url).searchParams.getAll("include").includes("source_calculations")
            if (options.counts) options.counts.transport += 1
            return HttpResponse.json({
                review_summary: summary,
                records: transport.map((record) => ({ ...(record as object), ...(withSources ? {} : { source_calculations: null }) })),
                pagination: { offset: 0, limit: 50, returned: transport.length, total: transport.length },
                request: echo,
            })
        }),
        http.get("/api/v1/scientific/species-calculations/search", () => HttpResponse.json({ records: [] })),
        http.get(`/api/v1/scientific/species-entries/${entryRef}/conformer-usage`, () => HttpResponse.json({ species_entry_ref: entryRef, groups: [], ensemble_thermo_refs: [] })),
        http.get("/api/v1/scientific/geometries/:ref", () => HttpResponse.json({ detail: "none" }, { status: 404 })),
        http.get("/api/v1/scientific/networks/search", () => HttpResponse.json({ records: [] })),
        http.get("/api/v1/scientific/reactions/search", () => HttpResponse.json({ records: [] })),
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

describe("molecule page: header", () => {
    it("heads the page with real parents in the identity slot, a title that is not the ref, and a lede built from codes", async () => {
        await open()
        const identity = document.querySelector(".page-shell-identity") as HTMLElement
        const crumbs = within(within(identity).getByRole("navigation", { name: "Breadcrumb" }))
        expect(identity.firstElementChild).toBe(screen.getByRole("navigation", { name: "Breadcrumb" }))
        expect(crumbs.getByRole("link", { name: "Molecules" })).toHaveAttribute("href", "/species")
        expect(crumbs.getByRole("link", { name: "CH3" })).toHaveAttribute("href", `/species/${speciesRef}`)
        expect(crumbs.getByText("minimum · ground state")).toHaveAttribute("aria-current", "page")
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("CH3")
        expect(screen.getByText("One version of a molecule")).toBeVisible()
        expect(within(identity).getByText("Entry ref")).toBeVisible()
        expect(screen.getByText("Conformers · Thermochemistry · Statistical mechanics")).toBeVisible()
        expect(screen.queryByText("Entry kind / state")).toBeNull()
        expect(document.querySelector(".record-lede")).toHaveTextContent(
            "One version of this molecule: charge 0, doublet, ground state, an energy minimum.",
        )
        expect(document.title).toBe("CH3 · Molecule · TCKDB")
    })
})

describe("molecule page: numbers for this entry", () => {
    it("lists every record across pages, thermochemistry first, then statistical mechanics, then transport", async () => {
        const region = await open({ thermoPages: true, transportRecords: [transportRecord] })
        await waitFor(() => expect(rowRefs(region)).toEqual(["thermo-thm_a1", "thermo-thm_b1", "thermo-thm_a2", "statmech-stm_1", "transport-trn_1"]))
        expect(within(region).getByText("143.90")).toBeVisible()
        expect(within(region).getByText("−12.34")).toBeVisible()
    })

    it("offers the molecule's compare page only when the entry holds two or more records", async () => {
        const region = await open()
        await waitFor(() => expect(rowRefs(region).length).toBeGreaterThan(1))
        expect(within(region).getByRole("link", { name: "Compare these values" })).toHaveAttribute("href", `/species/${speciesRef}/compare`)
    })

    it("offers the compare link for a single record only when another version has that kind of record", async () => {
        const bare = { availability: { has_thermo: false, has_statmech: false, has_transport: false, has_conformers: false, calculation_count: 1 } }
        const none = await open({ thermoRecords: [records()[0]], statmechRecords: [], entries: [entrySummary(entryRef, "ground", 10), { ...entrySummary(otherEntryRef, "excited", 4), ...bare }] })
        await waitFor(() => expect(rowRefs(none)).toEqual(["thermo-thm_a1"]))
        await screen.findByRole("heading", { name: "Other versions of this molecule" })
        await waitFor(() => expect(screen.getByText(/This molecule has 2 versions listed/)).toBeVisible())
        expect(within(none).queryByRole("link", { name: "Compare these values" })).not.toBeInTheDocument()
    })

    it("offers the compare link for a single record when another version has thermochemistry", async () => {
        const single = await open({ thermoRecords: [records()[0]], statmechRecords: [] })
        await waitFor(() => expect(within(single).getByRole("link", { name: "Compare these values" })).toHaveAttribute("href", `/species/${speciesRef}/compare`))
    })

    it("states the order the archive served once, naming only lists that have rows, and says so when it stated none", async () => {
        const orderNote = () => document.querySelector(".entry-numbers .order-note")
        await open()
        await waitFor(() => expect(orderNote()).toHaveTextContent("Thermochemistry, then statistical mechanics; approved first, then newest deposited first."))
        expect(orderNote()).not.toHaveTextContent("Transport")
        cleanup()
        server.resetHandlers()
        resetAllRequestCaches()
        await open({ echo: false })
        await waitFor(() => expect(orderNote()).toHaveTextContent("Thermochemistry: the archive did not state an order."))
    })

    it("lays each record out as a record column and a values column, with review under the ref", async () => {
        const region = await open()
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        expect(Array.from(region.querySelectorAll("thead th")).map((cell) => cell.textContent)).toEqual(["Record", "Values"])
        const row = region.querySelector("#thermo-thm_a2") as HTMLElement
        expect(within(row.querySelector("th") as HTMLElement).getByText("approved")).toBeVisible()
        expect(region.querySelector("caption")).toHaveTextContent("Numbers for this entry")
    })

    it("offers the level toggle only for two or more levels, and groups without re-ordering inside a group", async () => {
        const user = userEvent.setup()
        const region = await open()
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        expect(screen.getByText("2 levels of theory.")).toBeVisible()
        await user.click(screen.getByRole("button", { name: "Group by level of theory" }))
        expect(screen.getByRole("button", { name: "Group by level of theory" })).toHaveAttribute("aria-pressed", "true")
        const groups = Array.from(region.querySelectorAll("tbody")).map((body) => ({
            title: body.querySelector(".entry-numbers-group th")?.textContent ?? "",
            ids: Array.from(body.querySelectorAll("tr[id]")).map((row) => row.id),
        }))
        expect(groups.map((group) => group.ids)).toEqual([["thermo-thm_a1", "thermo-thm_a2"], ["thermo-thm_b1"], ["statmech-stm_1"]])
        expect(groups[0].title).toContain("wb97xd/def2tzvp (geometry, frequency, energy)")
        expect(groups[0].title).toContain("2 records")
        expect(groups[2].title).toContain("wb97xd/def2tzvp (geometry, frequency) · ccsdt/ccpvtz (energy)")
        expect(region.querySelector(".entry-numbers-group th")).toHaveAttribute("scope", "rowgroup")
        cleanup()
        resetAllRequestCaches()
        await open({ thermoRecords: [thermo("thm_x", lotA, 1, ensemble)], statmechRecords: [] })
        expect(screen.queryByRole("button", { name: "Group by level of theory" })).toBeNull()
    })

    it("words what each thermo record describes from its declared target only", async () => {
        const region = await open({
            thermoRecords: [
                ...records(),
                thermo("thm_u", lotA, 1, { kind: "single_conformer", conformer_group_ref: null, declared_by: "depositor" }),
                thermo("thm_k", lotA, 1, { kind: "mystery_kind", conformer_group_ref: null, declared_by: "depositor" }),
                thermo("thm_c", lotA, 1, { kind: "equilibrium_ensemble", conformer_group_ref: null, declared_by: "chemkin_format" }),
            ],
        })
        await waitFor(() => expect(rowRefs(region)).toHaveLength(7))
        const describes = (id: string) => (region.querySelector(`#${id} .entry-numbers-describes`) as HTMLElement)
        expect(describes("thermo-thm_a1")).toHaveTextContent("Describes: all shapes together")
        expect(describes("thermo-thm_a2")).toHaveTextContent("Describes: not stated by the depositor")
        expect(describes("thermo-thm_b1")).toHaveTextContent("Describes: one shape, Conformer Group 1")
        expect(within(describes("thermo-thm_b1")).getByRole("link")).toHaveAttribute("href", expect.stringContaining("conformer=cg_1"))
        expect(describes("thermo-thm_u")).toHaveTextContent("Describes: one shape (not named)")
        expect(describes("thermo-thm_k")).toHaveTextContent("Describes: mystery_kind")
        expect(describes("thermo-thm_c")).toHaveTextContent("Describes: all shapes together (implied by the CHEMKIN format)")
        expect(region.querySelector("#statmech-stm_1 .entry-numbers-describes")).toBeNull()
    })

    it("says Not recorded for a missing value and never shows zero", async () => {
        const region = await open()
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        const row = region.querySelector("#thermo-thm_a2") as HTMLElement
        expect(within(row).getAllByText("Not recorded").length).toBeGreaterThanOrEqual(3)
        expect(row).not.toHaveTextContent("0.00")
    })

    it("lists the single values the API sends: phase, pressure, range, model form, treatment, rotor kind and the flags", async () => {
        const region = await open()
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        const thermoRow = region.querySelector("#thermo-thm_a1") as HTMLElement
        for (const label of ["Phase", "Reference pressure", "Temperature range covered", "Form of the model"]) expect(within(thermoRow).getByText(label)).toBeVisible()
        expect(within(thermoRow).getByText("Gas")).toBeVisible()
        expect(thermoRow).toHaveTextContent("300–2000 K")
        const row = region.querySelector("#statmech-stm_1") as HTMLElement
        expect(row).toHaveTextContent("Rigid rotor, harmonic oscillator, one-dimensional hindered rotors")
        expect(row).toHaveTextContent("Asymmetric top")
        expect(within(row).getByText("Linear molecule").nextElementSibling).toHaveTextContent("No")
        expect(within(row).getByText("Projected frequencies used").nextElementSibling).toHaveTextContent("Not recorded")
    })

    it("types the point group with a subscript, never as a link, and shows Not shown for anything else", async () => {
        const region = await open({ statmechRecords: [statmech("stm_1"), statmech("stm_2", { point_group: "PISS", rigid_rotor_kind: "odd_top", statmech_treatment: "rrho_ad" })] })
        await waitFor(() => expect(rowRefs(region)).toContain("statmech-stm_2"))
        const good = within(region.querySelector("#statmech-stm_1") as HTMLElement).getByText("Point group").nextElementSibling as HTMLElement
        expect(good.querySelector("sub")).toHaveTextContent("2v")
        expect(good).toHaveTextContent("C2v")
        expect(good.querySelector("a")).toBeNull()
        const bad = region.querySelector("#statmech-stm_2") as HTMLElement
        expect(within(bad).getByText("Point group").nextElementSibling).toHaveTextContent("Not shown")
        expect(bad).not.toHaveTextContent("PISS")
        expect(within(bad).getByText("rrho_ad").tagName).toBe("CODE")
        expect(within(bad).getByText("odd_top").tagName).toBe("CODE")
        expect(within(bad).getByText("External symmetry number").nextElementSibling?.querySelector("a")).not.toBeNull()
    })

    it("formats numbers through their specs: significant figures for constants, as deposited for transport", async () => {
        const region = await open({ transportRecords: [transportRecord] })
        await waitFor(() => expect(rowRefs(region)).toContain("transport-trn_1"))
        const statmechRow = region.querySelector("#statmech-stm_1") as HTMLElement
        expect(within(statmechRow).getByText("Rotational constant A").nextElementSibling).toHaveTextContent("9.5 cm⁻¹")
        expect(within(statmechRow).getByText("Rotational constant B").nextElementSibling).toHaveTextContent("1.23 × 10−3 cm⁻¹")
        const transportRow = region.querySelector("#transport-trn_1") as HTMLElement
        expect(within(transportRow).getByText("Polarizability").nextElementSibling).toHaveTextContent("2.6 Å³")
        expect(within(transportRow).getByText("Rotational relaxation").nextElementSibling).toHaveTextContent("1.5")
    })

    it("takes transport levels from the record's own source calculations", async () => {
        const region = await open({ transportRecords: [transportRecord, { ...transportRecord, transport: { ...transportRecord.transport, transport_ref: "trn_2" }, source_calculations: [] }] })
        await waitFor(() => expect(rowRefs(region)).toContain("transport-trn_2"))
        expect(region.querySelector("#transport-trn_1 .value-source")).toHaveTextContent("dipole ccsdt/ccpvtz")
        expect(region.querySelector("#transport-trn_2 .value-source")).not.toHaveTextContent("ccsdt")
    })

    it("gives every record the full source line, linked to the record's card in its tab and keeping its row anchor", async () => {
        const region = await open({ transportRecords: [transportRecord] })
        await waitFor(() => expect(rowRefs(region)).toHaveLength(5))
        const source = region.querySelector("#thermo-thm_b1 .value-source") as HTMLElement
        expect(source).toHaveTextContent("Source Thermo record")
        expect(source).toHaveTextContent("geometry, frequency, energy at ccsdt/ccpvtz")
        expect(source).toHaveTextContent("Arkane 1.1.0")
        expect(source).not.toHaveTextContent("Review of this record")
        expect(within(source).getByRole("link", { name: "Source: Thermo record" })).toHaveAttribute("href", `/species-entries/${entryRef}/thermo?conformer=cg_1#thermo-heading-thm_b1`)
        const value = region.querySelector("#thermo-thm_b1 .value-link") as HTMLElement
        expect(value).toHaveAttribute("href", `/species-entries/${entryRef}/thermo?conformer=cg_1#thermo-heading-thm_b1`)
        expect(region.querySelector("#statmech-stm_1 .value-source")).toHaveTextContent("Gaussian 16")
        expect(region.querySelector("#transport-trn_1 .value-source")).toHaveTextContent("Source Transport record")
    })

    it("links each record to its own card, including two identical records on different shapes", async () => {
        const user = userEvent.setup()
        const region = await open({ thermoRecords: [thermo("thm_p", lotA, 1, ensemble, "not_reviewed", "cg_1"), thermo("thm_q", lotA, 1, ensemble, "not_reviewed", "cg_2")] })
        await waitFor(() => expect(region.querySelector("#thermo-thm_q")).not.toBeNull())
        expect(region.querySelector("#thermo-thm_p .value-link")).toHaveAttribute("href", `/species-entries/${entryRef}/thermo?conformer=cg_1#thermo-heading-thm_p`)
        expect(region.querySelector("#thermo-thm_q .value-link")).toHaveAttribute("href", `/species-entries/${entryRef}/thermo?conformer=cg_2#thermo-heading-thm_q`)
        await user.click(screen.getByRole("tab", { name: "Thermochemistry" }))
        const panel = await screen.findByRole("tabpanel")
        await waitFor(() => expect(panel.querySelector("#thermo-heading-thm_q")).not.toBeNull())
        expect(panel.querySelector("#thermo-heading-thm_p")).not.toBeNull()
    })

    it("words the model form and shows a code it has no wording for as a code", async () => {
        const region = await open({ thermoRecords: [thermo("thm_w", lotA, 1, ensemble, "not_reviewed", null, "wilhoit"), thermo("thm_z", lotA, 1, ensemble, "not_reviewed", null, "mystery_form")] })
        await waitFor(() => expect(region.querySelector("#thermo-thm_z")).not.toBeNull())
        expect(within(region.querySelector("#thermo-thm_w") as HTMLElement).getByText("Form of the model").nextElementSibling).toHaveTextContent("Wilhoit polynomial")
        expect(within(region.querySelector("#thermo-thm_z") as HTMLElement).getByText("mystery_form").tagName).toBe("CODE")
    })

    it("stops at the cap, asking for 200 at a time after the shared first page", async () => {
        const counts = { thermo: 0, statmech: 0, transport: 0, thermoLimits: [] as string[] }
        await open({ endless: true, counts })
        expect(await screen.findByText(/Showing the first 1,000 of 5,000 thermochemistry records\. All in the Thermochemistry tab\./)).toBeVisible()
        expect(counts.thermoLimits).toEqual(["50", "200", "200", "200", "200", "150"])
    })

    it("does not fetch the lists again when a tab opens", async () => {
        const user = userEvent.setup()
        const counts = { thermo: 0, statmech: 0, transport: 0 }
        const region = await open({ counts, transportRecords: [transportRecord] })
        await waitFor(() => expect(rowRefs(region)).toHaveLength(5))
        expect(counts).toEqual({ thermo: 1, statmech: 1, transport: 1 })
        await user.click(screen.getByRole("tab", { name: "Thermochemistry" }))
        await within(await screen.findByRole("tabpanel")).findByText("thm_a1")
        await user.click(screen.getByRole("tab", { name: "Statistical mechanics" }))
        await within(await screen.findByRole("tabpanel")).findAllByText("stm_1")
        await user.click(screen.getByRole("tab", { name: /^Transport/ }))
        await within(await screen.findByRole("tabpanel")).findAllByText("trn_1")
        expect(counts).toEqual({ thermo: 1, statmech: 1, transport: 1 })
    })

    it("lists the versions of the molecule, marking this one, with charge in words", async () => {
        await open()
        const section = (await screen.findByRole("heading", { name: "Other versions of this molecule" })).closest("section") as HTMLElement
        expect(await within(section).findByText("This molecule has 2 versions listed; this page shows one of them.")).toBeVisible()
        expect(section).not.toHaveTextContent("levels of theory behind their values")
        const items = Array.from(section.querySelectorAll(".entry-versions-item"))
        expect(items).toHaveLength(2)
        expect(items[0]).toHaveAttribute("aria-current", "true")
        expect(items[0]).toHaveTextContent("charge 0 · doublet (2) · minimum · ground state (this page)")
        expect(items[1]).toHaveTextContent("excited state")
        expect(items[1]).toHaveTextContent("4 calculations")
        expect(within(items[1] as HTMLElement).getByRole("link")).toHaveAttribute("href", `/species-entries/${otherEntryRef}`)
    })

    it("says how many versions were not listed, as versions, in the singular for one", async () => {
        const species = (counts: Record<string, number>) => ({
            records: [{
                species_ref: speciesRef, canonical_smiles: "[CH3]", inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N", formula: "CH3", charge: 0, multiplicity: 2,
                entries: [entrySummary(entryRef, "ground", 10), entrySummary(otherEntryRef, "excited", 4)],
            }],
            request: { hidden_by_default: { counts, include_with: {}, counted: "species_entries" } },
        })
        await open({ species: species({ rejected: 2, deprecated: 1 }) })
        expect(await screen.findByText("Not listed by default: 2 rejected and 1 deprecated versions.")).toBeVisible()
        cleanup()
        resetAllRequestCaches()
        await open({ species: species({ rejected: 1 }) })
        expect(await screen.findByText("Not listed by default: 1 rejected version.")).toBeVisible()
    })

    it("marks this version even when the listing leaves it out, and pluralises", async () => {
        await open({ listing: [entrySummary(otherEntryRef, "excited", 4)] })
        const section = (await screen.findByRole("heading", { name: "Other versions of this molecule" })).closest("section") as HTMLElement
        expect(await within(section).findByText("This molecule has 1 other version listed, plus this one.")).toBeVisible()
        expect(section.querySelectorAll(".entry-versions-item")).toHaveLength(2)
        expect(section.querySelector(".entry-versions-item[aria-current='true']")).toHaveTextContent("(this page)")
        cleanup()
        resetAllRequestCaches()
        await open({ listing: [entrySummary(entryRef, "ground", 4)] })
        expect(await screen.findByText("This is the only version listed for this molecule.")).toBeVisible()
    })

    it("keeps depositor text, and a point group that is not one, out of everything but a depositor note", async () => {
        const planted = plantSentinels(
            { thermo: records(), statmech: [statmech("stm_1")], entries: [entrySummary(entryRef, "ground", 1)] },
            ["note", "species_entry_label"],
        )
        expectPlanted(planted)
        setAt(planted.value, "statmech.0.statmech.point_group", ADVERSARIAL.pointGroup)
        const region = await open({ thermoRecords: planted.value.thermo, statmechRecords: planted.value.statmech, entries: planted.value.entries })
        await waitFor(() => expect(rowRefs(region)).toHaveLength(4))
        await screen.findByText("This is the only version listed for this molecule.")
        expect(within(region.querySelector("#statmech-stm_1") as HTMLElement).getByText("Point group").nextElementSibling).toHaveTextContent("Not shown")
        expect(document.body.innerHTML).not.toContain(DEPOSITOR_SENTINEL)
        expectDepositorTextOnlyInNotes()
        expectNoAdversarialText()
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
