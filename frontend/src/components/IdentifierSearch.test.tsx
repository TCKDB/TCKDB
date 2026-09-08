import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { IdentifierSearch } from "./IdentifierSearch"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
    vi.useRealTimers()
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
    await user.type(await screen.findByLabelText("Exact species or reaction identifier"), value)
    await user.click(screen.getByRole("button", { name: "Search" }))
}

/**
 * A successful SMILES/InChI/InChIKey species match also fires the
 * reaction-participation lookup (the "Reactions involving …" group) --
 * this file's `server` is configured `onUnhandledRequest: "error"`, so
 * every test whose structure-search handler returns at least one record
 * needs this registered too, or the unmocked request fails the test for a
 * reason unrelated to what it actually asserts. Empty by default (no
 * reaction group renders); pass `records`/`total` to test the group
 * itself.
 */
function reactionsBrowseHandler(records: unknown[] = [], total = records.length) {
    return http.get("/api/v1/scientific/reactions/browse", () => HttpResponse.json({
        records, pagination: { offset: 0, limit: 5, returned: records.length, total },
    }))
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
        server.use(
            http.get("/api/v1/scientific/species/structure-search", () => HttpResponse.json({
                records: [{
                    species_ref: "spc_ethanol0000000000000000000",
                    species_entry_ref: "spe_ethanol0000000000000000000",
                    smiles: "CCO",
                    charge: 0,
                    multiplicity: 1,
                }],
            })),
            reactionsBrowseHandler(),
        )
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

/**
 * The bug this suite exists to catch: a SMILES search that compares
 * strings byte-for-byte against the stored canonical form instead of
 * chemical identity. A fixture built from the CANONICAL spelling
 * ("CCC(=O)O") cannot catch that bug -- a string-equality implementation
 * would pass it too. Both cases below use a spelling that is chemically
 * identical to, but textually different from, the archive's own
 * canonical SMILES for propanoic acid.
 */
describe("SMILES/InChI search routes through structure-search (chemical identity, not string equality)", () => {
    const propanoicAcid = {
        species_ref: "spc_propanoic00000000000000acid",
        species_entry_ref: "spe_propanoic00000000000000acid",
        smiles: "CCC(=O)O",
        charge: 0,
        multiplicity: 1,
    }

    it.each([
        ["OC(=O)CC"],
        ["C(CC)(=O)O"],
    ])("finds propanoic acid from the non-canonical spelling \"%s\"", async (nonCanonical) => {
        let capturedUrl: URL | undefined
        // Deliberately no `/scientific/species/search` handler is
        // registered here. This suite's `server` is configured
        // `onUnhandledRequest: "error"` (top of file) -- a regression to
        // string-equality search (routing through
        // `species/search?smiles=`, the exact defect this test guards
        // against) would hit that unhandled route and fail the test for
        // that reason alone, even before the assertions below run.
        server.use(
            http.get("/api/v1/scientific/species/structure-search", ({ request }) => {
                capturedUrl = new URL(request.url)
                return HttpResponse.json({ records: [propanoicAcid] })
            }),
            reactionsBrowseHandler(),
        )
        const user = userEvent.setup(); page()
        await searchFormula(user, nonCanonical)

        expect(await screen.findByText(propanoicAcid.species_entry_ref)).toBeVisible()
        expect(screen.queryByText(/No exact/)).not.toBeInTheDocument()
        expect(capturedUrl?.pathname).toBe("/api/v1/scientific/species/structure-search")
        expect(capturedUrl?.searchParams.get("query_smiles")).toBe(nonCanonical)
        expect(capturedUrl?.searchParams.get("mode")).toBe("exact")
    })

    it("routes InChI the same way -- through structure-search mode=exact, not string equality", async () => {
        let capturedUrl: URL | undefined
        server.use(
            http.get("/api/v1/scientific/species/structure-search", ({ request }) => {
                capturedUrl = new URL(request.url)
                return HttpResponse.json({ records: [propanoicAcid] })
            }),
            reactionsBrowseHandler(),
        )
        const user = userEvent.setup(); page()
        const inchi = "InChI=1S/C3H6O2/c1-2-3(4)5/h2H2,1H3,(H,4,5)"
        await searchFormula(user, inchi)

        expect(await screen.findByText(propanoicAcid.species_entry_ref)).toBeVisible()
        expect(capturedUrl?.searchParams.get("query_inchi")).toBe(inchi)
        expect(capturedUrl?.searchParams.get("mode")).toBe("exact")
    })

    it("a formula query still goes through the formula path, never through RDKit/structure-search", async () => {
        let capturedUrl: URL | undefined
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            capturedUrl = new URL(request.url)
            return HttpResponse.json({ records: [methylRadical] })
        }))
        // No structure-search handler registered -- an accidental funnel of
        // every identifier through one endpoint (the constraint this test
        // guards) would hit the unhandled route and fail here too.
        const user = userEvent.setup(); page()
        await searchFormula(user, "CH3")

        expect(await screen.findByText(methylRadical.species_ref)).toBeVisible()
        expect(capturedUrl?.pathname).toBe("/api/v1/scientific/species/search")
        expect(capturedUrl?.searchParams.get("formula")).toBe("CH3")
        expect(capturedUrl?.searchParams.has("query_smiles")).toBe(false)
    })

    it("reports an unparseable SMILES as invalid, distinct from 'not found' and from a generic failure", async () => {
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
        await searchFormula(user, "smiles:not((a valid smiles")

        const message = await screen.findByText(/could not be parsed/i)
        expect(message).toBeVisible()
        // Distinct wording from both other failure modes: a resolved
        // zero-record search, and an unclassified/network failure.
        expect(screen.queryByText(/No exact/)).not.toBeInTheDocument()
        expect(screen.queryByText(/archive could not complete that search/i)).not.toBeInTheDocument()
    })

    // Review follow-up (SHOULD-FIX #2): a double 429 (both automatic-retry
    // attempts exhausted) used to fall into the same generic "archive
    // could not complete that search" as an unclassified failure. Distinct
    // wording -- same plain-language message as every other rate-limited
    // surface (`RecordStatus`, `SpeciesEntryPage`, `SpeciesOverviewPage`,
    // `BrowsePage`).
    it("reports a double 429 in the same plain-language wording as everywhere else, not the generic failure message", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => (
            HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "20" } })
        )))
        page()
        // `fireEvent`, not `userEvent`: this test needs fake timers active
        // for `requestScientificJson`'s retry wait, and `userEvent`'s own
        // internal scheduling does not play well with fake timers even
        // with `delay: null` (measured: hangs to the test timeout). Fake
        // timers are switched on AFTER the form submits -- before that,
        // nothing here depends on timers at all.
        fireEvent.change(await screen.findByLabelText("Exact species or reaction identifier"), { target: { value: "CH3" } })
        fireEvent.click(screen.getByRole("button", { name: "Search" }))

        vi.useFakeTimers()
        await act(async () => { await vi.advanceTimersByTimeAsync(0) }) // first (429) attempt lands
        await act(async () => { await vi.advanceTimersByTimeAsync(20_000) }) // the retry, also 429

        const message = screen.getByText(/receiving too many requests right now/)
        expect(message).toHaveTextContent(
            "The archive is receiving too many requests right now. Wait about 20 seconds and reload the page.",
        )
        expect(message.textContent).not.toMatch(/\d+s\b/)
        expect(screen.queryByText(/archive could not complete that search/i)).not.toBeInTheDocument()
    })

    it("reports a genuine zero-record structure-search result as 'not found', not as invalid", async () => {
        server.use(http.get("/api/v1/scientific/species/structure-search", () =>
            HttpResponse.json({ records: [] })
        ))
        // "smiles:" prefix, not bare "CCO": a bare "CCO" is ambiguous
        // between formula and SMILES (`classifyIdentifier`) and this test
        // is about the resolved-empty-result message, not the ambiguity
        // picker.
        const user = userEvent.setup(); page()
        await searchFormula(user, "smiles:CCO")

        const message = await screen.findByText(/No exact SMILES record was found/i)
        expect(message).toBeVisible()
        expect(screen.queryByText(/could not be parsed/i)).not.toBeInTheDocument()
    })
})

/**
 * "A structure query must also find reactions" (owner brief). A SMILES/
 * InChI/InChIKey query that resolves at least one species also fires
 * `searchReactionParticipation` (`api/scientificApi.ts`) and renders a
 * second, clearly-labelled group beneath the species matches. A formula
 * query never does -- a reaction has no formula.
 */
describe("structure query also finds reactions", () => {
    const nn = {
        species_ref: "spc_hydrazine00000000000000nn",
        species_entry_ref: "spe_hydrazine00000000000000nn",
        smiles: "NN",
        charge: 0,
        multiplicity: 1,
    }

    function reactionRecord(entryRef: string) {
        return {
            reaction_ref: `rxn_${entryRef.slice(4)}`,
            reaction_entry_ref: entryRef,
            reversible: true,
            reactants: [{ species_entry_ref: "spe_h2n_a", smiles: "[NH2]", stoichiometry: 2, participant_index: 0 }],
            products: [{ species_entry_ref: nn.species_entry_ref, smiles: "NN", stoichiometry: 1, participant_index: 0 }],
        }
    }

    it("shows a labelled 'Reactions involving …' group under the species matches, linked to the reaction entry", async () => {
        server.use(
            http.get("/api/v1/scientific/species/structure-search", () => HttpResponse.json({ records: [nn] })),
            reactionsBrowseHandler([reactionRecord("rxe_a0000000000000000000000001")], 1),
        )
        const user = userEvent.setup(); page()
        await searchFormula(user, "smiles:NN")

        await screen.findByText(nn.species_entry_ref)
        const region = await screen.findByRole("region", { name: "Reactions found" })
        expect(within(region).getByRole("heading", { name: /Reactions involving/ })).toBeVisible()
        // Links to the REACTION ENTRY, never the reaction chooser
        // (`/reactions/:ref`) or anywhere else -- mutation-table item (d).
        const link = within(region).getByRole("link")
        expect(link).toHaveAttribute("href", "/reaction-entries/rxe_a0000000000000000000000001")
        expect(within(region).getByText("rxe_a0000000000000000000000001")).toBeVisible()
    })

    it("truncates to a handful of rows and links through to /reactions with the filter when more exist", async () => {
        const shown = ["a", "b", "c", "d", "e"].map((letter) => reactionRecord(`rxe_${letter}0000000000000000000000${letter}`))
        server.use(
            http.get("/api/v1/scientific/species/structure-search", () => HttpResponse.json({ records: [nn] })),
            // 20 reactions total (the real `NN` figure against the live
            // archive) -- only the first page (5, `REACTION_PARTICIPATION_
            // LIMIT`) is returned by the mock, matching what the real
            // endpoint would hand back for `limit=5`.
            reactionsBrowseHandler(shown, 20),
        )
        const user = userEvent.setup(); page()
        await searchFormula(user, "smiles:NN")

        const region = await screen.findByRole("region", { name: "Reactions found" })
        expect(within(region).getAllByRole("listitem")).toHaveLength(5)
        const seeAll = within(region).getByRole("link", { name: /See all 20 reactions/ })
        expect(seeAll).toHaveAttribute("href", `/reactions?reactant_smiles=${encodeURIComponent("NN")}`)
    })

    // Honesty rule: an absence is a real result and must be SAID, not left
    // implied by rendering nothing -- the same rule the species "No exact
    // … record was found" message already follows. Mutation-table item (c).
    it("states a genuine zero-reaction result honestly, rather than silently showing nothing", async () => {
        server.use(
            http.get("/api/v1/scientific/species/structure-search", () => HttpResponse.json({ records: [nn] })),
            reactionsBrowseHandler([], 0),
        )
        const user = userEvent.setup(); page()
        await searchFormula(user, "smiles:NN")

        await screen.findByText(nn.species_entry_ref)
        expect(await screen.findByText("No reactions in this archive list NN as a participant.")).toBeVisible()
        expect(screen.queryByRole("region", { name: "Reactions found" })).not.toBeInTheDocument()
    })

    // A reaction has no formula -- a formula query must stay species-only,
    // never asserting (or even attempting to check) reaction participation.
    // Mutation-table item (b).
    it("never renders a reaction group for a formula query, even though a reaction exists for the match", async () => {
        server.use(
            http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [methylRadical] })),
            // Registered and would return a real reaction if fetched -- this
            // proves the absence of the group is because the formula path
            // never calls this endpoint, not because the endpoint happened
            // to return nothing.
            reactionsBrowseHandler([reactionRecord("rxe_a0000000000000000000000001")], 1),
        )
        const user = userEvent.setup(); page()
        await searchFormula(user, "CH3")

        await screen.findByText(methylRadical.species_ref)
        expect(screen.queryByRole("region", { name: "Reactions found" })).not.toBeInTheDocument()
        expect(screen.queryByText(/Reactions involving/)).not.toBeInTheDocument()
        expect(screen.queryByText(/No reactions in this archive list/)).not.toBeInTheDocument()
    })
})

describe("recognized public references route directly, unrecognized ones never fall through to a structure search", () => {
    it("gives a clear message for a recognized-but-unrouted reference, without attempting a structure search", async () => {
        // No handler registered for either species endpoint -- this file's
        // `server` is `onUnhandledRequest: "error"`, so a regression that
        // sent this through structure-search (the exact bug this suite
        // guards against) would fail here even before the message assertion.
        const user = userEvent.setup(); page()
        await searchFormula(user, "thm_aaaaaaaaaaaaaaaaaaaaaaaaaa")

        const message = await screen.findByText(/does not have a page for that record type yet/)
        expect(message).toBeVisible()
        expect(screen.queryByText(/could not be parsed/i)).not.toBeInTheDocument()
    })
})

describe("identifier option ordering", () => {
    it("offers SMILES before Formula when a value is ambiguous", async () => {
        const user = userEvent.setup(); page()
        // `CCO` parses as both an elemental formula and a structure string,
        // so the component asks rather than guessing.
        await searchFormula(user, "CCO")
        const choices = await screen.findAllByRole("button", { name: /^(SMILES|Formula)$/ })
        // Order IS the assertion. A set-membership check would pass with the
        // buttons in either order, which is exactly the regression this guards.
        expect(choices.map((button) => button.textContent)).toEqual(["SMILES", "Formula"])
    })

    it("names SMILES ahead of formula in the input placeholder", async () => {
        page()
        const placeholder = (await screen.findByLabelText("Exact species or reaction identifier"))
            .getAttribute("placeholder") ?? ""
        expect(placeholder).toContain("SMILES")
        expect(placeholder.indexOf("SMILES")).toBeLessThan(placeholder.indexOf("formula"))
    })
})
