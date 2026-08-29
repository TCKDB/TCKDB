import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { IdentifierSearch } from "./IdentifierSearch"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function page() {
    return render(<MemoryRouter><IdentifierSearch /></MemoryRouter>)
}

/**
 * Two matches that differ in *every* chemistry field -- formula, SMILES,
 * charge, and multiplicity -- plus a distinct entry count. This is a
 * deliberate departure from a "realistic" fixture: two species that only
 * differed by name (e.g. two waters) would let a row-mixing bug hide
 * behind two identical-looking fields. See the file-level comment on the
 * mutation coverage below for what this specifically guards against.
 */
const methylRadical = {
    species_ref: "spc_methyl00000000000000000radi",
    formula: "CH3",
    canonical_smiles: "[CH3]",
    charge: 0,
    multiplicity: 2,
    entries: [{ species_entry_ref: "spe_methylentry00000000000ground" }],
}

const hydroxideIon = {
    species_ref: "spc_hydroxide0000000000000anion",
    formula: "HO",
    canonical_smiles: "[OH-]",
    charge: -1,
    multiplicity: 1,
    entries: [
        { species_entry_ref: "spe_hydroxide0000000000000000a" },
        { species_entry_ref: "spe_hydroxide0000000000000000b" },
        { species_entry_ref: "spe_hydroxide0000000000000000c" },
    ],
}

async function searchFormula(user: ReturnType<typeof userEvent.setup>, value: string) {
    await user.type(await screen.findByLabelText("Exact species identifier"), value)
    await user.click(screen.getByRole("button", { name: "Search" }))
}

describe("IdentifierSearch chemistry-first results", () => {
    it("renders each match's own formula, SMILES, charge, spin, entry count, and ref -- not a neighbour's", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({
            records: [methylRadical, hydroxideIon],
        })))
        const user = userEvent.setup(); page()
        await searchFormula(user, "CH3")

        const rows = screen.getAllByRole("listitem")
        expect(rows).toHaveLength(2)

        // Scoped with `within(...)`: each assertion is about ONE row, not
        // "this text exists somewhere on the page" (a formula that leaked
        // onto the wrong row would still make an unscoped `getByText` pass).
        const methylRow = within(rows[0])
        expect(methylRow.getByRole("link", { name: /^CH3 \[CH3\]/ })).toHaveAttribute(
            "href", `/species/${methylRadical.species_ref}`,
        )
        expect(methylRow.getByText("charge 0 · spin doublet (2) · 1 entry", { exact: false })).toBeVisible()
        expect(methylRow.getByText(methylRadical.species_ref)).toBeVisible()

        const hydroxideRow = within(rows[1])
        expect(hydroxideRow.getByRole("link", { name: /^HO \[OH-\]/ })).toHaveAttribute(
            "href", `/species/${hydroxideIon.species_ref}`,
        )
        expect(hydroxideRow.getByText("charge −1 · spin singlet (1) · 3 entries", { exact: false })).toBeVisible()
        expect(hydroxideRow.getByText(hydroxideIon.species_ref)).toBeVisible()

        // Neither row's SMILES bleeds into the other's link.
        expect(methylRow.queryByText("[OH-]")).not.toBeInTheDocument()
        expect(hydroxideRow.queryByText("[CH3]")).not.toBeInTheDocument()
    })

    it("keeps the ref visible and copyable even though it is demoted below the chemistry", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [methylRadical] })))
        const user = userEvent.setup(); page()
        await searchFormula(user, "CH3")

        const ref = await screen.findByText(methylRadical.species_ref)
        expect(ref).toBeVisible()
        expect(ref.tagName).toBe("CODE")
        // Demoted, not deleted: the ref must not be the accessible name of
        // the result link (that is the exact defect this component fixes).
        expect(screen.queryByRole("link", { name: methylRadical.species_ref })).not.toBeInTheDocument()
    })

    it("leads with SMILES and says so honestly when the archive has no formula for a match", async () => {
        // The structure-search endpoint never returns `formula` at all
        // (see `scientificApi.ts`); this is the shape a SMILES/InChI/
        // InChIKey search actually returns.
        server.use(http.get("/api/v1/scientific/species/structure-search", () => HttpResponse.json({
            records: [{
                species_ref: "spc_ethanol0000000000000000000",
                species_entry_ref: "spe_ethanol0000000000000000000",
                smiles: "CCO",
                charge: 0,
                multiplicity: 1,
            }],
        })))
        const user = userEvent.setup(); page()
        await searchFormula(user, "smiles:CCO")

        const row = within(screen.getByRole("listitem"))
        // Not a blank, and not the ref standing in for the missing formula.
        expect(row.getByText("formula not available")).toBeVisible()
        expect(row.getByRole("link", { name: /^CCO formula not available/ })).toHaveAttribute(
            "href", "/species-entries/spe_ethanol0000000000000000000",
        )
        expect(row.getByText("spe_ethanol0000000000000000000")).toBeVisible()
    })
})
