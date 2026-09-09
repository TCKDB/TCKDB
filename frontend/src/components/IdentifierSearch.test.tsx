import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { IdentifierSearch } from "./IdentifierSearch"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
    vi.useRealTimers()
})
afterAll(() => server.close())

/**
 * Real `<Route>`s for every destination this component can `navigate()`
 * to (a reference-shaped input routes directly, see `runReferenceLookup`),
 * each rendering a distinct marker -- so a navigation test can assert
 * WHERE the app landed, not just that no error was thrown.
 */
function page() {
    return render(
        <MemoryRouter initialEntries={["/"]}>
            <Routes>
                <Route path="/" element={<IdentifierSearch />} />
                <Route path="/reactions/:ref" element={<div>landed on reaction record</div>} />
                <Route path="/reaction-entries/:ref" element={<div>landed on reaction-entry record</div>} />
                <Route path="/species/:ref" element={<div>landed on species record</div>} />
                <Route path="/species-entries/:ref" element={<div>landed on species-entry record</div>} />
            </Routes>
        </MemoryRouter>,
    )
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

async function switchToReactions(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("radio", { name: "Reactions" }))
}

async function searchSpecies(user: ReturnType<typeof userEvent.setup>, value: string) {
    await user.type(await screen.findByLabelText("Exact species identifier"), value)
    await user.click(screen.getByRole("button", { name: "Search" }))
}

/**
 * `fireEvent.change`, not `user.type`: an equation routinely contains `[`
 * and `]` (`[NH2]`, `[NH4+]`), which `userEvent`'s keyboard parser reads as
 * special key-descriptor syntax rather than literal SMILES characters --
 * setting the value directly sidesteps that entirely, the same escape
 * hatch this file's own pre-existing 429 tests already use for a reason
 * unrelated to brackets (fake timers).
 */
async function searchReactions(user: ReturnType<typeof userEvent.setup>, value: string) {
    await switchToReactions(user)
    fireEvent.change(await screen.findByLabelText("Exact reaction equation"), { target: { value } })
    await user.click(screen.getByRole("button", { name: "Search" }))
}

/**
 * A minimal, schema-valid `/scientific/reactions/browse` row --
 * `reactionBrowseRecordSchema` (`api/browseApi.ts`) requires `review` and
 * `availability`, which the old participation-only fixtures never needed.
 */
function reactionRecord(entryRef: string, reversible = true, matchedDirection: string | null = null) {
    return {
        reaction_ref: `rxn_${entryRef.slice(4)}`,
        reaction_entry_ref: entryRef,
        reversible,
        matched_direction: matchedDirection,
        review: { status: "not_reviewed" },
        reactants: [{ species_entry_ref: "spe_h2n_a", smiles: "[NH2]", stoichiometry: 2, participant_index: 0 }],
        products: [{ species_entry_ref: "spe_nn_a", smiles: "NN", stoichiometry: 1, participant_index: 0 }],
        availability: { has_kinetics: false, has_transition_state: false },
    }
}

function reactionsBrowseHandler(records: unknown[] = [], total = records.length) {
    let capturedUrl: URL | undefined
    const handler = http.get("/api/v1/scientific/reactions/browse", ({ request }) => {
        capturedUrl = new URL(request.url)
        return HttpResponse.json({ records, pagination: { offset: 0, limit: 5, returned: records.length, total } })
    })
    return { handler, capturedUrl: () => capturedUrl }
}

describe("Species mode is the default and is labelled/placeholder-ed for species only", () => {
    it("shows the species-only label, placeholder, and help text by default", async () => {
        page()
        const input = await screen.findByLabelText("Exact species identifier")
        expect(input.getAttribute("placeholder")).toContain("spc_/spe_ ref")
        expect(input.getAttribute("placeholder")).not.toMatch(/rxn_|rxe_/)
        expect(screen.getByText(/Exact only/)).toBeVisible()
    })
})

describe("IdentifierSearch chemistry-first results (species mode)", () => {
    it("renders each match's own formula, SMILES, charge, spin, entry count, and ref -- not a neighbour's", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({
            records: [methylRadical, hydroxideIon],
        })))
        const user = userEvent.setup(); page()
        await searchSpecies(user, "CH3")

        const rows = screen.getAllByRole("listitem")
        expect(rows).toHaveLength(2)

        const methylRow = within(rows[0])
        expect(methylRow.getByRole("link", { name: /^\[CH3\] \(CH3\)/ })).toHaveAttribute(
            "href", `/species/${methylRadical.species_ref}`,
        )
        expect(methylRow.getByText("charge 0 · spin doublet (2) · 1 entry", { exact: false })).toBeVisible()
        expect(methylRow.getByText(methylRadical.species_ref)).toBeVisible()

        const hydroxideRow = within(rows[1])
        expect(hydroxideRow.getByRole("link", { name: /^\[OH-\] \(HO\)/ })).toHaveAttribute(
            "href", `/species/${hydroxideIon.species_ref}`,
        )
        expect(hydroxideRow.getByText("charge −1 · spin singlet (1) · 3 entries", { exact: false })).toBeVisible()
        expect(hydroxideRow.getByText(hydroxideIon.species_ref)).toBeVisible()

        expect(methylRow.queryByText("[OH-]")).not.toBeInTheDocument()
        expect(hydroxideRow.queryByText("[CH3]")).not.toBeInTheDocument()
    })

    it("keeps the ref visible and copyable even though it is demoted below the chemistry", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [methylRadical] })))
        const user = userEvent.setup(); page()
        await searchSpecies(user, "CH3")

        const ref = await screen.findByText(methylRadical.species_ref)
        expect(ref).toBeVisible()
        expect(ref.tagName).toBe("CODE")
        expect(screen.queryByRole("link", { name: methylRadical.species_ref })).not.toBeInTheDocument()
    })

    it("leads with SMILES and says so honestly when the archive has no formula for a match", async () => {
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
        await searchSpecies(user, "smiles:CCO")

        const row = within(screen.getByRole("listitem"))
        expect(row.getByText("formula not available")).toBeVisible()
        expect(row.getByRole("link", { name: /^CCO formula not available/ })).toHaveAttribute(
            "href", "/species-entries/spe_ethanol0000000000000000000",
        )
        expect(row.getByText("spe_ethanol0000000000000000000")).toBeVisible()
    })
})

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
        server.use(http.get("/api/v1/scientific/species/structure-search", ({ request }) => {
            capturedUrl = new URL(request.url)
            return HttpResponse.json({ records: [propanoicAcid] })
        }))
        const user = userEvent.setup(); page()
        await searchSpecies(user, nonCanonical)

        expect(await screen.findByText(propanoicAcid.species_entry_ref)).toBeVisible()
        expect(screen.queryByText(/No exact/)).not.toBeInTheDocument()
        expect(capturedUrl?.pathname).toBe("/api/v1/scientific/species/structure-search")
        expect(capturedUrl?.searchParams.get("query_smiles")).toBe(nonCanonical)
        expect(capturedUrl?.searchParams.get("mode")).toBe("exact")
    })

    it("routes InChI the same way -- through structure-search mode=exact, not string equality", async () => {
        let capturedUrl: URL | undefined
        server.use(http.get("/api/v1/scientific/species/structure-search", ({ request }) => {
            capturedUrl = new URL(request.url)
            return HttpResponse.json({ records: [propanoicAcid] })
        }))
        const user = userEvent.setup(); page()
        const inchi = "InChI=1S/C3H6O2/c1-2-3(4)5/h2H2,1H3,(H,4,5)"
        await searchSpecies(user, inchi)

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
        const user = userEvent.setup(); page()
        await searchSpecies(user, "CH3")

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
        await searchSpecies(user, "smiles:not((a valid smiles")

        const message = await screen.findByText(/could not be parsed/i)
        expect(message).toBeVisible()
        expect(screen.queryByText(/No exact/)).not.toBeInTheDocument()
        expect(screen.queryByText(/archive could not complete that search/i)).not.toBeInTheDocument()
    })

    it("reports a double 429 in the same plain-language wording as everywhere else, not the generic failure message", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => (
            HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "20" } })
        )))
        page()
        fireEvent.change(await screen.findByLabelText("Exact species identifier"), { target: { value: "CH3" } })
        fireEvent.click(screen.getByRole("button", { name: "Search" }))

        vi.useFakeTimers()
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })

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
        const user = userEvent.setup(); page()
        await searchSpecies(user, "smiles:CCO")

        const message = await screen.findByText(/No exact SMILES record was found/i)
        expect(message).toBeVisible()
        expect(screen.queryByText(/could not be parsed/i)).not.toBeInTheDocument()
    })
})

/**
 * The old "Reactions involving …" group that used to fire automatically
 * for every structure query is GONE from species mode -- species mode
 * means "search species", full stop (the owner's core complaint about the
 * old field was inferred intent). What replaced it: a small cross-link
 * that switches to reaction mode and runs the participation search only
 * when the reader asks for it.
 */
describe("species mode no longer auto-searches reactions -- a cross-link replaces it", () => {
    const nn = {
        species_ref: "spc_hydrazine00000000000000nn",
        species_entry_ref: "spe_hydrazine00000000000000nn",
        smiles: "NN",
        charge: 0,
        multiplicity: 1,
    }

    it("never calls the reactions/browse endpoint for a species search, structure or formula", async () => {
        server.use(
            http.get("/api/v1/scientific/species/structure-search", () => HttpResponse.json({ records: [nn] })),
            // Registered and would fail the test (`onUnhandledRequest: "error"`
            // is NOT set for a handler that exists -- but any actual request
            // here would prove the exact regression this test guards against).
            http.get("/api/v1/scientific/reactions/browse", () => {
                throw new Error("species mode must never call reactions/browse automatically")
            }),
        )
        const user = userEvent.setup(); page()
        await searchSpecies(user, "smiles:NN")

        await screen.findByText(nn.species_entry_ref)
        expect(screen.queryByRole("region", { name: "Reactions found" })).not.toBeInTheDocument()
    })

    it("offers a cross-link into reaction mode for a structure match, which switches mode and runs the search", async () => {
        server.use(
            http.get("/api/v1/scientific/species/structure-search", () => HttpResponse.json({ records: [nn] })),
            reactionsBrowseHandler([reactionRecord("rxe_a0000000000000000000000001")], 1).handler,
        )
        const user = userEvent.setup(); page()
        await searchSpecies(user, "smiles:NN")
        await screen.findByText(nn.species_entry_ref)

        const link = screen.getByRole("button", { name: /Also search reactions involving/ })
        await user.click(link)

        // Switched to reaction mode -- the label proves it, not just the result.
        expect(await screen.findByLabelText("Exact reaction equation")).toBeInTheDocument()
        expect(screen.getByRole("radio", { name: "Reactions" })).toHaveAttribute("aria-checked", "true")
        const region = await screen.findByRole("region", { name: "Reactions found" })
        expect(within(region).getByText("rxe_a0000000000000000000000001")).toBeVisible()
        expect(within(within(region).getByRole("listitem")).getByRole("link")).toHaveAttribute(
            "href", "/reaction-entries/rxe_a0000000000000000000000001",
        )
    })

    it("offers no cross-link for a formula query -- a reaction has no formula", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json({ records: [methylRadical] })))
        const user = userEvent.setup(); page()
        await searchSpecies(user, "CH3")

        await screen.findByText(methylRadical.species_ref)
        expect(screen.queryByRole("button", { name: /Also search reactions/ })).not.toBeInTheDocument()
    })
})

describe("recognized public references route directly, regardless of which mode is selected", () => {
    it("gives a clear message for a recognized-but-unrouted reference, without attempting a structure search", async () => {
        const user = userEvent.setup(); page()
        await searchSpecies(user, "thm_aaaaaaaaaaaaaaaaaaaaaaaaaa")

        const message = await screen.findByText(/does not have a page for that record type yet/)
        expect(message).toBeVisible()
        expect(screen.queryByText(/could not be parsed/i)).not.toBeInTheDocument()
    })

    // Mutation-table item (c): the exact defect fixed earlier today for the
    // single-field search (a reaction ref fed to structure search and
    // reported unparseable) must not reappear in the new mode-switch shape.
    it("a rxe_ reference typed while SPECIES mode is selected still routes to the reaction-entry page, not a structure-search failure", async () => {
        // No species-search handler registered at all, and no
        // reactions/browse handler either -- `onUnhandledRequest: "error"`
        // means a regression that funnelled this through EITHER search
        // endpoint (instead of navigating directly, as an `rxe_` reference
        // always should) would fail here for that reason alone.
        const user = userEvent.setup(); page()
        expect(screen.getByRole("radio", { name: "Species" })).toHaveAttribute("aria-checked", "true")
        await searchSpecies(user, "rxe_aaaaaaaaaaaaaaaaaaaaaaaaaa")

        expect(await screen.findByText("landed on reaction-entry record")).toBeVisible()
    })

    it("a spc_ reference typed while REACTION mode is selected still resolves and navigates to the species page, not a parse failure", async () => {
        // A properly-shaped 26-char body (`REF_BODY` convention,
        // `recordModel.test.ts`) -- `methylRadical.species_ref` above is a
        // realistic-LOOKING fixture but is one character too long to match
        // the ref shape itself, which is not what this test is about.
        const validSpeciesRef = "spc_aaaaaaaaaaaaaaaaaaaaaaaaaa"
        server.use(http.get("/api/v1/scientific/species/search", ({ request }) => {
            const url = new URL(request.url)
            expect(url.searchParams.get("species_ref")).toBe(validSpeciesRef)
            return HttpResponse.json({ records: [{ ...methylRadical, species_ref: validSpeciesRef }] })
        }))
        const user = userEvent.setup(); page()
        await searchReactions(user, validSpeciesRef)

        expect(await screen.findByText("landed on species record")).toBeVisible()
        expect(screen.queryByText(/more than one reaction arrow|both sides|Enter a structure/i)).not.toBeInTheDocument()
    })
})

describe("identifier option ordering (species mode)", () => {
    it("offers SMILES before Formula when a value is ambiguous", async () => {
        const user = userEvent.setup(); page()
        await searchSpecies(user, "CCO")
        const choices = await screen.findAllByRole("button", { name: /^(SMILES|Formula)$/ })
        expect(choices.map((button) => button.textContent)).toEqual(["SMILES", "Formula"])
    })

    it("names SMILES ahead of formula in the input placeholder", async () => {
        page()
        const placeholder = (await screen.findByLabelText("Exact species identifier"))
            .getAttribute("placeholder") ?? ""
        expect(placeholder).toContain("SMILES")
        expect(placeholder.indexOf("SMILES")).toBeLessThan(placeholder.indexOf("formula"))
    })

    it("the ambiguity picker never appears in reaction mode -- reactions have no formula concept", async () => {
        const user = userEvent.setup(); page()
        await searchReactions(user, "CCO")
        expect(screen.queryByRole("button", { name: "Formula" })).not.toBeInTheDocument()
    })
})

// ---------------------------------------------------------------------------
// Reaction mode
// ---------------------------------------------------------------------------

describe("reaction mode: label, placeholder, and help text are reaction-specific", () => {
    it("updates the accessible label, placeholder, and help text the moment reaction mode is selected", async () => {
        const user = userEvent.setup(); page()
        expect(screen.getByLabelText("Exact species identifier")).toBeInTheDocument()

        await switchToReactions(user)

        // Mutation-table item (b): the label must not stay on the species
        // wording once reaction mode is active.
        expect(screen.queryByLabelText("Exact species identifier")).not.toBeInTheDocument()
        const input = screen.getByLabelText("Exact reaction equation")
        expect(input.getAttribute("placeholder")).toMatch(/rxn_|rxe_/)
        expect(input.getAttribute("placeholder")).not.toContain("formula")
        expect(screen.getByText(/searches both directions/i)).toBeVisible()
        // The help text no longer explains arrow semantics at all -- every
        // arrow means the same thing now (owner correction), so there is
        // no per-arrow behaviour left to document here.
        expect(screen.queryByText(/is forward/i)).not.toBeInTheDocument()
    })
})

describe("reaction mode: a bare structure is a participation search (either side)", () => {
    it("finds reactions for a bare SMILES with no arrow", async () => {
        const { handler, capturedUrl } = reactionsBrowseHandler(
            [reactionRecord("rxe_a0000000000000000000000001")], 20,
        )
        server.use(handler)
        const user = userEvent.setup(); page()
        await searchReactions(user, "NN")

        const region = await screen.findByRole("region", { name: "Reactions found" })
        expect(within(region).getByRole("heading", { name: /Reactions matching NN/ })).toBeVisible()
        expect(within(within(region).getByRole("listitem")).getByRole("link")).toHaveAttribute(
            "href", "/reaction-entries/rxe_a0000000000000000000000001",
        )
        expect(capturedUrl()?.searchParams.getAll("reactant_smiles")).toEqual(["NN"])
        expect(capturedUrl()?.searchParams.get("direction")).toBe("either")
        expect(capturedUrl()?.searchParams.getAll("product_smiles")).toEqual([])
    })

    it("truncates to a handful of rows and links through to /reactions with the matching filter", async () => {
        const shown = ["a", "b", "c", "d", "e"].map((letter) => reactionRecord(`rxe_${letter}0000000000000000000000${letter}`))
        server.use(reactionsBrowseHandler(shown, 20).handler)
        const user = userEvent.setup(); page()
        await searchReactions(user, "NN")

        const region = await screen.findByRole("region", { name: "Reactions found" })
        expect(within(region).getAllByRole("listitem")).toHaveLength(5)
        const seeAll = within(region).getByRole("link", { name: /See all 20 reactions/ })
        expect(seeAll).toHaveAttribute("href", `/reactions?reactant_smiles=${encodeURIComponent("NN")}&direction=either`)
    })

    // Mutation-table item (f): commas -- never `+` -- separate species on
    // one side. A charged SMILES like `[NH4+]` must reach the request
    // intact, as ONE `reactant_smiles` value, not split at the `+`.
    it("sends a comma-list as several reactant_smiles values, leaving a `+`-bearing SMILES intact", async () => {
        const { handler, capturedUrl } = reactionsBrowseHandler([], 0)
        server.use(handler)
        const user = userEvent.setup(); page()
        await searchReactions(user, "NN,[NH4+]")

        await screen.findByText(/No reaction in this archive lists/)
        expect(capturedUrl()?.searchParams.getAll("reactant_smiles")).toEqual(["NN", "[NH4+]"])
    })

    it("states a genuine zero-reaction result honestly, distinguishing 'together on one side' for a multi-structure query", async () => {
        server.use(reactionsBrowseHandler([], 0).handler)
        const user = userEvent.setup(); page()
        await searchReactions(user, "NN,[H]")

        expect(await screen.findByText("No reaction in this archive lists NN and [H] together on one side.")).toBeVisible()
        expect(screen.queryByRole("region", { name: "Reactions found" })).not.toBeInTheDocument()
    })
})

/**
 * Owner correction: an earlier version of this component mapped `<>`/
 * `<=>`/`<->` to "either direction" and `=>`/`->` to "forward only" -- a
 * hidden mode a reader typing `<>` had no way to discover `->` even
 * existed. "every arrow means the same thing" replaces that: all five
 * recognised arrows split reactants from products identically and the
 * request is ALWAYS `direction=either`, regardless of which one was
 * typed. Which individual rows only matched because the archive checked
 * the reverse orientation is stated per-row instead (next describe block).
 */
describe("reaction mode: every arrow splits reactants/products the same way and always searches direction=either", () => {
    it.each(["<=>", "<->", "<>", "=>", "->"])("arrow %s sends the identical request", async (arrow) => {
        const { handler, capturedUrl } = reactionsBrowseHandler([reactionRecord("rxe_a0000000000000000000000001")], 1)
        server.use(handler)
        const user = userEvent.setup(); page()
        await searchReactions(user, `NN,[H] ${arrow} N,[NH2]`)

        await screen.findByRole("region", { name: "Reactions found" })
        expect(capturedUrl()?.searchParams.getAll("reactant_smiles")).toEqual(["NN", "[H]"])
        expect(capturedUrl()?.searchParams.getAll("product_smiles")).toEqual(["N", "[NH2]"])
        expect(capturedUrl()?.searchParams.get("direction")).toBe("either")
    })

    // Mutation-table item (e'): if the request silently narrowed to
    // `direction=forward`, a reaction the archive holds only in the
    // opposite orientation would be lost from the result entirely --
    // not mislabelled, GONE. This is the regression the always-either
    // rule exists to prevent.
    it("finds a reaction that only matches in reverse -- proving direction=either actually reaches the request, not a narrower default", async () => {
        const { handler, capturedUrl } = reactionsBrowseHandler(
            [reactionRecord("rxe_a0000000000000000000000001", true, "reverse")], 1,
        )
        server.use(handler)
        const user = userEvent.setup(); page()
        await searchReactions(user, "NN,[H] -> N,[NH2]")

        const region = await screen.findByRole("region", { name: "Reactions found" })
        expect(within(region).getByRole("heading")).toBeVisible()
        expect(capturedUrl()?.searchParams.get("direction")).toBe("either")
    })
})

/**
 * Mutation-table item (h): a reverse match that renders with no
 * indication of HOW it matched leaves a reader unable to tell a row that
 * matched as written from one that only matched because the archive also
 * checked the opposite orientation -- exactly the information the arrow
 * used to (badly) imply and now must be stated per row instead.
 */
describe("reaction mode: a reverse match is labelled on the row itself, the same wording /reactions uses", () => {
    it("renders 'Matched on the reverse direction' only for a row whose matched_direction is reverse", async () => {
        server.use(reactionsBrowseHandler(
            [
                reactionRecord("rxe_a0000000000000000000000001", true, "reverse"),
                reactionRecord("rxe_b0000000000000000000000002", true, "forward"),
            ],
            2,
        ).handler)
        const user = userEvent.setup(); page()
        await searchReactions(user, "NN")

        const region = await screen.findByRole("region", { name: "Reactions found" })
        const rows = within(region).getAllByRole("listitem")
        expect(within(rows[0]).getByText("Matched on the reverse direction")).toBeVisible()
        expect(within(rows[1]).queryByText("Matched on the reverse direction")).not.toBeInTheDocument()
    })

    it("renders no note at all when matched_direction is absent (an older API) -- never asserts a fact the archive did not send", async () => {
        server.use(reactionsBrowseHandler([reactionRecord("rxe_a0000000000000000000000001", true, null)], 1).handler)
        const user = userEvent.setup(); page()
        await searchReactions(user, "NN")

        await screen.findByRole("region", { name: "Reactions found" })
        expect(screen.queryByText("Matched on the reverse direction")).not.toBeInTheDocument()
    })
})

describe("reaction mode: a malformed equation is a parse failure, distinct from an empty result", () => {
    it("rejects two arrows without ever calling the archive", async () => {
        server.use(http.get("/api/v1/scientific/reactions/browse", () => {
            throw new Error("a parse failure must never reach the network")
        }))
        const user = userEvent.setup(); page()
        await searchReactions(user, "NN => N => [H]")

        const message = await screen.findByText(/more than one reaction arrow/i)
        expect(message).toBeVisible()
        expect(screen.queryByText(/No reaction in this archive/)).not.toBeInTheDocument()
    })

    it("rejects an equation missing one side without ever calling the archive", async () => {
        server.use(http.get("/api/v1/scientific/reactions/browse", () => {
            throw new Error("a parse failure must never reach the network")
        }))
        const user = userEvent.setup(); page()
        await searchReactions(user, "NN,[H] <> ")

        const message = await screen.findByText(/both sides/i)
        expect(message).toBeVisible()
        expect(screen.queryByText(/No reaction in this archive/)).not.toBeInTheDocument()
    })
})

describe("reaction mode: rate limiting and generic failures are reported plainly", () => {
    it("reports a 429 in the same plain-language wording as species mode", async () => {
        server.use(http.get("/api/v1/scientific/reactions/browse", () => (
            HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "15" } })
        )))
        const user = userEvent.setup(); page()
        await switchToReactions(user)
        fireEvent.change(screen.getByLabelText("Exact reaction equation"), { target: { value: "NN" } })
        fireEvent.click(screen.getByRole("button", { name: "Search" }))

        vi.useFakeTimers()
        await act(async () => { await vi.advanceTimersByTimeAsync(0) })
        await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })

        expect(screen.getByText(/receiving too many requests right now/)).toBeVisible()
    })
})
