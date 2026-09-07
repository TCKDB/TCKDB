import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ReactionOverviewPage from "./ReactionOverviewPage"

const REACTION_REF = "rxn_naeqmg4l5wyqex5cl5tir2vt2y"
const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function page(ref: string) {
    return render(
        <MemoryRouter initialEntries={[`/reactions/${ref}`]}>
            <Routes>
                <Route path="/reactions/:reactionRef" element={<ReactionOverviewPage />} />
                <Route path="/reaction-entries/:entryRef" element={<div>reaction entry page for {ref}</div>} />
            </Routes>
        </MemoryRouter>,
    )
}

const participant = (ref: string, formula: string, smiles: string) => ({
    species_entry_ref: ref, formula, smiles, participant_index: 0,
})

function searchResponse() {
    return {
        review_summary: { total: 4, not_reviewed: 4, approved: 0, under_review: 0, deprecated: 0, rejected: 0 },
        records: [
            {
                reaction_ref: REACTION_REF,
                reaction_entry_ref: "rxe_tku6xu2lt3girf2rsiwl5uds4e",
                equation: "NN <=> [H][H] + N=N",
                reversible: true,
                family: null,
                review: { status: "not_reviewed" },
                reactants: [participant("spe_wi6mz65sb47vzsyop3tqrqrcai", "H4N2", "NN")],
                products: [
                    participant("spe_7ioiqvdqm6cyumgnrammbhefum", "H2", "[H][H]"),
                    participant("spe_cft35qrkqphdifcfqlcenqgdau", "H2N2", "N=N"),
                ],
                availability: { has_kinetics: false, has_transition_state: true, has_path_search: false, kinetics_count: 0 },
            },
            {
                reaction_ref: REACTION_REF,
                reaction_entry_ref: "rxe_kftcgjn7zalusaouojwi23z3gy",
                equation: "NN <=> [H][H] + N=N",
                reversible: true,
                family: null,
                review: { status: "not_reviewed" },
                reactants: [participant("spe_wi6mz65sb47vzsyop3tqrqrcai", "H4N2", "NN")],
                products: [
                    participant("spe_7ioiqvdqm6cyumgnrammbhefum", "H2", "[H][H]"),
                    participant("spe_cft35qrkqphdifcfqlcenqgdau", "H2N2", "N=N"),
                ],
                availability: { has_kinetics: false, has_transition_state: true, has_path_search: false, kinetics_count: 0 },
            },
        ],
    }
}

describe("ReactionOverviewPage", () => {
    it("an rxe_ ref redirects to /reaction-entries/:ref by prefix check alone -- no request issued", async () => {
        // No handler is registered for reactions/search at all; `server.listen`
        // is configured `onUnhandledRequest: "error"`, so any request this
        // page fired would fail the test outright.
        page("rxe_ed66mj3ohtyien5rm2x3sb3rdu")
        expect(await screen.findByText("reaction entry page for rxe_ed66mj3ohtyien5rm2x3sb3rdu")).toBeVisible()
    })

    it("lists every deposited entry with its own ref, review pill, and has-kinetics/has-TS/kinetics-count flags", async () => {
        server.use(http.get("/api/v1/scientific/reactions/search", () => HttpResponse.json(searchResponse())))
        page(REACTION_REF)

        expect(await screen.findByRole("link", { name: "rxe_tku6xu2lt3girf2rsiwl5uds4e" }))
            .toHaveAttribute("href", "/reaction-entries/rxe_tku6xu2lt3girf2rsiwl5uds4e")
        expect(screen.getByRole("link", { name: "rxe_kftcgjn7zalusaouojwi23z3gy" }))
            .toHaveAttribute("href", "/reaction-entries/rxe_kftcgjn7zalusaouojwi23z3gy")

        const rows = screen.getAllByRole("row").slice(1) // drop header row
        expect(rows).toHaveLength(2)
        expect(rows[0].textContent).toContain("not reviewed")
        expect(rows[0].textContent).toContain("no") // has_kinetics: false
        expect(rows[0].textContent).toContain("yes") // has_transition_state: true
        expect(rows[0].textContent).toContain("0") // kinetics_count

        // Review, across all entries.
        expect(screen.getByText("Not reviewed").closest("div")?.textContent).toContain("4")
    })

    it("renders the h1 from the first entry's participants and reversible flag", async () => {
        server.use(http.get("/api/v1/scientific/reactions/search", () => HttpResponse.json(searchResponse())))
        page(REACTION_REF)
        // `findByRole` retries until a match appears -- the LOADING state
        // also renders an `<h1>` ("Loading reaction…"), so this specifically
        // waits for the one carrying the equation, not just any h1.
        const h1 = await screen.findByRole("heading", { level: 1, name: /H4N2/ })
        expect(h1.textContent).toContain("⇌")
    })
})
