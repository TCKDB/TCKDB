import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import App from "../App"

const speciesRef = "spc_atp56uqux2ajao7hvckx7gx7ca"
const entryRef = "spe_bcbdjwkip75yoziblpntwzblzu"
const excitedEntryRef = "spe_abcdefghijklmnopqrstuvwxyz"
const server = setupServer()

function speciesPayload(entries = [groundEntry(), excitedEntry()]) {
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

function groundEntry() {
    return {
        species_entry_ref: entryRef,
        species_entry_kind: "minimum",
        electronic_state_kind: "ground",
        species_entry_label: "ground electronic state",
        review: { status: "not_reviewed" },
        availability: {
            has_thermo: true,
            has_statmech: true,
            has_transport: false,
            has_conformers: true,
            calculation_count: 14,
        },
    }
}

function excitedEntry() {
    return {
        ...groundEntry(),
        species_entry_ref: excitedEntryRef,
        electronic_state_kind: "excited",
        species_entry_label: "excited T1",
        term_symbol: "T1",
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

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); window.history.replaceState({}, "", "/") })
afterAll(() => server.close())

describe("species overview", () => {
    it("requires explicit electronic-state entry selection and supplies accessible hierarchy links", async () => {
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            const query = new URL(request.url).searchParams
            expect(query.get("species_ref")).toBe(speciesRef)
            expect(query.get("limit")).toBe("1")
            return HttpResponse.json(speciesPayload())
        }))
        window.history.replaceState({}, "", `/species/${speciesRef}`)
        render(<App />)

        expect(await screen.findByRole("heading", { name: "CH3" })).toBeVisible()
        expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeVisible()
        expect(screen.getByRole("heading", { name: "Electronic-state entries" })).toBeVisible()
        expect(screen.getByText("2 entries")).toBeVisible()
        expect(screen.getByRole("heading", { name: /ground electronic state.*1 entry/i })).toBeVisible()
        expect(screen.getByRole("heading", { name: /excited electronic state.*1 entry/i })).toBeVisible()
        expect(screen.getByRole("link", { name: "ground electronic state" }))
            .toHaveAttribute("href", `/species-entries/${entryRef}`)
        expect(screen.getByRole("link", { name: "excited T1" }))
            .toHaveAttribute("href", `/species-entries/${excitedEntryRef}`)
        expect(screen.getByText("14")).toBeVisible()
        expect(screen.getByText("3")).toBeVisible()
        expect(screen.getAllByText("Deposited records")).toHaveLength(2)
        expect(screen.getAllByText("Available data")).toHaveLength(2)
    })

    it("groups repeated ground-state entries without merging their stable records", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => (
            HttpResponse.json(speciesPayload([groundEntry(), secondGroundEntry(), excitedEntry()]))
        )))
        window.history.replaceState({}, "", `/species/${speciesRef}`)
        render(<App />)

        expect(await screen.findByRole("heading", { name: /ground electronic state.*2 entries/i })).toBeVisible()
        const groundLinks = screen.getAllByRole("link", { name: "ground electronic state" })
        expect(groundLinks).toHaveLength(2)
        expect(groundLinks[0]).toHaveAttribute("href", `/species-entries/${entryRef}`)
        expect(groundLinks[1]).toHaveAttribute("href", "/species-entries/spe_secondgroundentryrecordabcdefgh")
        expect(screen.getByText("spe_secondgroundentryrecordabcdefgh")).toBeVisible()
        expect(screen.getByText("7")).toBeVisible()
        expect(screen.getAllByText(/Each row is a separate record/)).toHaveLength(2)
    })

    it("explains when no state-specific entries are projected", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json(speciesPayload([]))))
        window.history.replaceState({}, "", `/species/${speciesRef}`)
        render(<App />)
        expect(await screen.findByText(
            "No electronic-state entries are currently projected for this species.",
        )).toBeVisible()
    })

    it("distinguishes absent, malformed, and unavailable projections", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [] })))
        window.history.replaceState({}, "", `/species/${speciesRef}`)
        const first = render(<App />)
        expect(await screen.findByRole("heading", { name: "Species not found" })).toBeVisible()
        first.unmount()

        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [{}] })))
        const second = render(<App />)
        expect(await screen.findByRole("alert")).toHaveTextContent("Species data could not be read")
        second.unmount()

        server.use(http.get("/api/v1/scientific/species/search", () => (
            HttpResponse.json({ detail: "archive unavailable" }, { status: 503 })
        )))
        render(<App />)
        expect(await screen.findByRole("alert")).toHaveTextContent("Species unavailable")
    })
})
