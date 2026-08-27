import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadEntryConformers, loadSpeciesEntry } from "./speciesEntryApi"
import { ScientificApiError } from "./scientificApi"

const entryRef = "spe_bcbdjwkip75yoziblpntwzblzu"
const server = setupServer()

const entryPayload = {
    records: [{
        species_ref: "spc_atp56uqux2ajao7hvckx7gx7ca",
        canonical_smiles: "[CH3]",
        inchi_key: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
        formula: "CH3",
        charge: 0,
        multiplicity: 2,
        entries: [{
            species_entry_ref: entryRef,
            species_entry_kind: "minimum",
            electronic_state_kind: "ground",
            review: { status: "not_reviewed" },
            availability: {
                has_thermo: true,
                has_statmech: true,
                has_transport: false,
                has_conformers: true,
                calculation_count: 14,
            },
        }],
    }],
}

const conformerPayload = {
    records: [{
        conformer_group: {
            conformer_group_ref: "cg_rsoqvj37biuvkucdr6dpaba6iy",
            label: "conformer_1",
        },
        observations_summary: { total: 4 },
        evidence_summary: {
            calculation_count: 14,
            optimization_chain_count: 4,
            geometry_count: 2,
            evidence_coverage: { opt: 4, freq: 4, sp: 3 },
            levels_of_theory: {
                opt: [{ method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp" }],
            },
        },
        observations: [],
        calculations: [],
        geometries: [],
    }],
}

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

describe("species-entry API", () => {
    it("requests the entry and conformer projections with ordered repeated includes", async () => {
        server.use(
            http.get("/api/v1/scientific/species/search", ({ request }) => {
                const query = new URL(request.url).searchParams
                expect(query.get("species_entry_ref")).toBe(entryRef)
                expect(query.getAll("include")).toEqual(["thermo", "statmech", "transport", "conformers"])
                return HttpResponse.json(entryPayload)
            }),
            http.get("/api/v1/scientific/conformers/search", ({ request }) => {
                const query = new URL(request.url).searchParams
                expect(query.get("species_entry_ref")).toBe(entryRef); expect(query.get("limit")).toBe("50")
                expect(query.getAll("include")).toEqual(["observations", "calculations", "geometries"])
                return HttpResponse.json(conformerPayload)
            }),
        )
        await expect(loadSpeciesEntry(entryRef)).resolves.toMatchObject({
            speciesRef: "spc_atp56uqux2ajao7hvckx7gx7ca",
            multiplicity: 2,
        })
        await expect(loadEntryConformers(entryRef)).resolves.toHaveLength(1)
    })

    it("returns null when a valid entry search is empty", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [] })))
        await expect(loadSpeciesEntry(entryRef)).resolves.toBeNull()
    })

    it("rejects malformed successful entry and conformer responses", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => (
            HttpResponse.json({ records: [{ species_ref: "spc_bad" }] })
        )))
        await expect(loadSpeciesEntry(entryRef)).rejects.toEqual(
            expect.objectContaining<Partial<ScientificApiError>>({ status: 200 }),
        )
        server.use(http.get("/api/v1/scientific/conformers/search", () => (
            HttpResponse.json({ records: [{ conformer_group: {} }] })
        )))
        await expect(loadEntryConformers(entryRef)).rejects.toEqual(
            expect.objectContaining<Partial<ScientificApiError>>({ status: 200 }),
        )
    })

    it("surfaces HTTP failure details", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => (
            HttpResponse.json({ detail: "archive unavailable" }, { status: 503 })
        )))
        await expect(loadSpeciesEntry(entryRef)).rejects.toEqual(
            expect.objectContaining<Partial<ScientificApiError>>({
                status: 503,
                message: "archive unavailable",
            }),
        )
    })
})
