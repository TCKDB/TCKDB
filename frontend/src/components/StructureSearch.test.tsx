import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { StructureSearch } from "./StructureSearch"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function page() {
    return render(<MemoryRouter><StructureSearch /></MemoryRouter>)
}

const ethanolEntry = {
    species_ref: "spc_ethanol0000000000000000000",
    species_entry_ref: "spe_ethanol0000000000000000000",
    smiles: "CCO",
    charge: 0,
    multiplicity: 1,
    match: { mode: "substructure", similarity_score: null },
    review: { status: "approved" },
}

const propanolEntry = {
    species_ref: "spc_propanol000000000000000000",
    species_entry_ref: "spe_propanol000000000000000000",
    smiles: "CCCO",
    charge: 0,
    multiplicity: 1,
    match: { mode: "similarity", similarity_score: 0.727 },
    review: { status: "not_reviewed" },
}

async function runSubstructureSearch(user: ReturnType<typeof userEvent.setup>, query: string) {
    await user.type(await screen.findByLabelText("SMILES"), query)
    await user.click(screen.getByRole("button", { name: "Search structures" }))
}

describe("StructureSearch", () => {
    it("runs a substructure search over query_smiles and labels the result with its mode", async () => {
        let capturedUrl: URL | undefined
        server.use(http.get("/api/v1/scientific/species/structure-search", ({ request }) => {
            capturedUrl = new URL(request.url)
            return HttpResponse.json({
                records: [ethanolEntry],
                pagination: { offset: 0, limit: 50, returned: 1, total: 8 },
            })
        }))
        const user = userEvent.setup(); page()
        await runSubstructureSearch(user, "CCO")

        // The mode a reader ran must be legible in the output, not just
        // implicit in which button they clicked -- a substructure hit and
        // a similarity hit mean different things about how close the
        // match is.
        expect(await screen.findByText(/Substructure search/)).toBeVisible()
        expect(screen.getByText("8", { exact: false })).toBeVisible()
        expect(screen.getByRole("link", { name: /CCO/ })).toHaveAttribute("href", "/species-entries/spe_ethanol0000000000000000000")

        expect(capturedUrl?.pathname).toBe("/api/v1/scientific/species/structure-search")
        expect(capturedUrl?.searchParams.get("mode")).toBe("substructure")
        expect(capturedUrl?.searchParams.get("query_smiles")).toBe("CCO")
        expect(capturedUrl?.searchParams.has("query_smarts")).toBe(false)
    })

    it("sends a SMARTS query as query_smarts, never as query_smiles", async () => {
        let capturedUrl: URL | undefined
        server.use(http.get("/api/v1/scientific/species/structure-search", ({ request }) => {
            capturedUrl = new URL(request.url)
            return HttpResponse.json({ records: [], pagination: { offset: 0, limit: 50, returned: 0, total: 0 } })
        }))
        const user = userEvent.setup(); page()
        await user.click(await screen.findByRole("checkbox", { name: /Treat the query as SMARTS/ }))
        // The label switches with the checkbox -- "SMARTS pattern" once checked.
        // `[` opens a userEvent key-descriptor (like `{enter}` for `{`);
        // `[[` is the literal escape for a real `[` character.
        await user.type(await screen.findByLabelText("SMARTS pattern"), "[[#6]-[[#8]")
        await user.click(screen.getByRole("button", { name: "Search structures" }))

        await screen.findByText(/Substructure search/)
        expect(capturedUrl?.searchParams.get("query_smarts")).toBe("[#6]-[#8]")
        expect(capturedUrl?.searchParams.has("query_smiles")).toBe(false)
    })

    it("shows the similarity threshold and each result's score", async () => {
        server.use(http.get("/api/v1/scientific/species/structure-search", ({ request }) => {
            const url = new URL(request.url)
            expect(url.searchParams.get("mode")).toBe("similarity")
            expect(url.searchParams.get("similarity_threshold")).toBe("0.6")
            return HttpResponse.json({
                records: [propanolEntry],
                pagination: { offset: 0, limit: 50, returned: 1, total: 1 },
            })
        }))
        const user = userEvent.setup(); page()
        await user.click(screen.getByRole("radio", { name: "Similarity" }))
        const thresholdInput = await screen.findByLabelText(/Similarity threshold/)
        await user.clear(thresholdInput)
        await user.type(thresholdInput, "0.6")
        await user.type(screen.getByLabelText("SMILES"), "CCCO")
        await user.click(screen.getByRole("button", { name: "Search structures" }))

        expect(await screen.findByText(/Similarity search/)).toBeVisible()
        // The threshold that was actually run must be legible in the
        // summary, not just set-and-forgotten in the form.
        expect(screen.getByText(/threshold ≥ 0\.60/)).toBeVisible()
        const row = within(screen.getByRole("listitem"))
        expect(row.getByText(/Tanimoto 0\.727/)).toBeVisible()
    })

    it("distinguishes a genuine zero-match result from a search that was never run", async () => {
        server.use(http.get("/api/v1/scientific/species/structure-search", () =>
            HttpResponse.json({ records: [], pagination: { offset: 0, limit: 50, returned: 0, total: 0 } })
        ))
        const user = userEvent.setup(); page()

        // Before any search: no result copy of any kind on the page.
        expect(screen.queryByText(/matches were found/)).not.toBeInTheDocument()
        expect(screen.queryByRole("status")).not.toBeInTheDocument()

        await runSubstructureSearch(user, "c1ccccc1")
        expect(await screen.findByText(/No substructure matches were found/)).toBeVisible()
    })

    it("reports an unparseable query as invalid, not as a zero-match result", async () => {
        server.use(http.get("/api/v1/scientific/species/structure-search", () =>
            HttpResponse.json(
                {
                    code: "invalid_structure_query",
                    detail: "invalid_structure_query: RDKit could not parse the SMILES supplied as query_smiles.",
                },
                { status: 422 },
            )
        ))
        const user = userEvent.setup(); page()
        await runSubstructureSearch(user, "not(((a valid smiles")

        const message = await screen.findByText(/could not be parsed/i)
        expect(message).toBeVisible()
        expect(screen.queryByText(/matches were found/)).not.toBeInTheDocument()
    })
})
