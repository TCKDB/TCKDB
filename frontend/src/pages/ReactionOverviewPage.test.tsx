import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
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

const participant = (ref: string, formula: string, smiles: string, label?: string) => ({
    species_entry_ref: ref, formula, smiles, participant_index: 0, species_entry_label: label ?? null,
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
                    participant("spe_cft35qrkqphdifcfqlcenqgdau", "H2N2", "N=N", "Z"),
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
                // Deliberately the OPPOSITE of row 0's flags (has_kinetics
                // true here, false there) -- a swap between the two columns
                // (or between the two rows) is only distinguishable from a
                // correct render when the two rows/columns actually differ.
                availability: { has_kinetics: true, has_transition_state: false, has_path_search: false, kinetics_count: 3 },
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

        // Precise per-cell assertions (not a loose `.toContain("no"/"yes")`
        // on the whole row, which "not reviewed" alone already satisfies
        // for "no") -- and the two rows are DELIBERATELY given opposite
        // flags in the fixture above, so a swap between the has-kinetics
        // and has-TS columns (or between the two rows) is caught.
        expect(rows[0].querySelector('td[data-label="Has kinetics"]')?.textContent).toBe("no")
        expect(rows[0].querySelector('td[data-label="Has TS"]')?.textContent).toBe("yes")
        expect(rows[0].querySelector('td[data-label="Kinetics count"]')?.textContent).toBe("0")
        expect(rows[1].querySelector('td[data-label="Has kinetics"]')?.textContent).toBe("yes")
        expect(rows[1].querySelector('td[data-label="Has TS"]')?.textContent).toBe("no")
        expect(rows[1].querySelector('td[data-label="Kinetics count"]')?.textContent).toBe("3")

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

    // Post-review fix: every participant cell used to render ONLY the
    // formula link, dropping the ref/copy-button/stereo-label chip the
    // mock's own chooser table carried for all four facts.
    it("each participant cell carries its own spe_ ref, a copy button, and its served stereo-label chip", async () => {
        server.use(http.get("/api/v1/scientific/reactions/search", () => HttpResponse.json(searchResponse())))
        page(REACTION_REF)
        await screen.findByRole("link", { name: "rxe_tku6xu2lt3girf2rsiwl5uds4e" })

        // Appears in BOTH rows -- the fixture's two entries share the same
        // products -- so the first occurrence (row 0, where the "Z" label
        // is set) is what this test targets.
        const refCode = screen.getAllByText("spe_cft35qrkqphdifcfqlcenqgdau")[0]
        expect(refCode.tagName).toBe("CODE")
        const cell = refCode.closest("td")!
        expect(cell.querySelector(".copy-button")).not.toBeNull()
        expect(cell.textContent).toContain("(Z isomer)")
    })

    // Round-2 review finding: the between-participant separator ("·") and
    // the stereo-chip's own separator were the SAME glyph, so a two-
    // product cell read "H2 ... · H2N2 ... · Z isomer" -- "Z isomer"
    // looked like a THIRD product rather than a label on H2N2. Asserting
    // the exact cell text (not just `.toContain`) proves the chip is
    // parenthesised and therefore distinguishable from the participant
    // separator, which uses the same "·" character either way.
    it("the products cell text is unambiguous: the stereo chip never reads as a third product", async () => {
        server.use(http.get("/api/v1/scientific/reactions/search", () => HttpResponse.json(searchResponse())))
        page(REACTION_REF)
        await screen.findByRole("link", { name: "rxe_tku6xu2lt3girf2rsiwl5uds4e" })
        const rows = screen.getAllByRole("row").slice(1)
        const productsCell = rows[0].querySelector('td[data-label="Products"]')!
        // Exactly TWO "·" characters would appear if the chip used the same
        // separator as a genuine third participant; there must be only ONE
        // (the real reactant/product separator), with the label parenthesised.
        const dotCount = (productsCell.textContent!.match(/·/g) ?? []).length
        expect(dotCount).toBe(1)
        expect(productsCell.textContent).toContain("(Z isomer)")
    })

    it("the identity header carries the record's own Equation (as deposited) fact", async () => {
        server.use(http.get("/api/v1/scientific/reactions/search", () => HttpResponse.json(searchResponse())))
        page(REACTION_REF)
        await screen.findByRole("link", { name: "rxe_tku6xu2lt3girf2rsiwl5uds4e" })
        const dt = Array.from(document.querySelectorAll(".record-identity-header dt")).find((el) => el.textContent === "Equation (as deposited)")
        expect(dt).not.toBeUndefined()
        expect(dt!.nextElementSibling?.textContent).toBe("NN <=> [H][H] + N=N")
    })

    // Independent review: `EvidenceChecklist`'s old `"N rows"` fallback
    // collapsed this card behind its own fixed 6-row count regardless of
    // the real total. This fixture's `review_summary.total: 4` (distinct
    // from the fixed 6-row count) proves the collapsed summary is wired to
    // the real total, not the row list's own length -- and that opening
    // the card reaches the same rows a reader could always see.
    it("the review card's collapsed summary is the TRUE total, never the fixed 6-row count", async () => {
        server.use(http.get("/api/v1/scientific/reactions/search", () => HttpResponse.json(searchResponse())))
        page(REACTION_REF)
        await screen.findByRole("link", { name: "rxe_tku6xu2lt3girf2rsiwl5uds4e" })

        const reviewSection = document.querySelector('section[aria-labelledby="review-heading"]')!
        const card = reviewSection.querySelector(".card.card--derived.coverage-card") as HTMLElement
        const rollup = card.querySelector(".coverage-checklist-summary") as HTMLElement
        expect(rollup).toHaveTextContent("4 joined records")
        expect(rollup.textContent).not.toContain("6 rows")

        const details = card.querySelector("details") as HTMLDetailsElement
        const checklist = card.querySelector(".coverage-checklist") as HTMLElement
        expect(details.open).toBe(false)
        expect(checklist).not.toBeVisible()
        fireEvent.click(details.querySelector("summary")!)
        expect(checklist).toBeVisible()
        const dt = Array.from(checklist.querySelectorAll("dt")).find((el) => el.textContent === "Total")
        expect(dt?.nextElementSibling?.textContent).toBe("4")
    })
})
