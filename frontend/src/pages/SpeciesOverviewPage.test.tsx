import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
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

// `species_entry_label` is left unset here on purpose: the server's own
// discriminator (`species_identity.species_entry_label`) is `None` for a
// plain ground-state minimum with nothing else to distinguish it -- see
// `domain/recordFacets.ts`'s module docstring. Setting it to a hand-typed
// "ground electronic state" string, as this fixture used to, is not a
// shape the archive actually produces.
function groundEntry() {
    return {
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
    }
}

function excitedEntry() {
    return {
        ...groundEntry(),
        species_entry_ref: excitedEntryRef,
        electronic_state_kind: "excited",
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

/** Scopes queries to one entry card, identified by its stable `<code>` ref
 *  -- more robust than matching a link's accessible name, which is exactly
 *  the thing under test here. Async: waits for the record to finish
 *  loading, same as every other assertion on this page. */
async function cardFor(ref: string): Promise<HTMLElement> {
    const code = await screen.findByText(ref)
    const card = code.closest("article")
    if (!card) throw new Error(`No .entry-card ancestor found for ref "${ref}"`)
    return card
}

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

        // Each card sits inside an `EntryStateGroup` whose own heading
        // already says "ground electronic state"/"excited electronic
        // state" -- the card itself no longer repeats the bare state text
        // (that redundancy was the owner's own report; see
        // `domain/recordFacets.test.ts`'s `includeState` tests). A card's
        // heading still carries anything the group heading does NOT
        // establish -- here, the excited entry's own term symbol "T1".
        const groundCard = await cardFor(entryRef)
        expect(within(groundCard).getByRole("link", { name: "minimum" }))
            .toHaveAttribute("href", `/species-entries/${entryRef}`)

        const excitedCard = await cardFor(excitedEntryRef)
        expect(within(excitedCard).getByRole("link", { name: "minimum · T1" }))
            .toHaveAttribute("href", `/species-entries/${excitedEntryRef}`)

        // Scoped to `dd` -- the formula heading above ("CH3") now renders
        // through `Formula`, which types the "3" subscript as its own
        // `<sub>3</sub>` element, so an unscoped `getByText("3")` would
        // ambiguously match both that subscript and this calculation count.
        expect(screen.getByText("14", { selector: "dd" })).toBeVisible()
        expect(screen.getByText("3", { selector: "dd" })).toBeVisible()
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
        // Both ground entries carry the same axes (same kind, same state,
        // no stereo/isotope), so they render IDENTICAL heading text --
        // their stable refs, not their headings, are what tells them apart.
        const groundLinks = screen.getAllByRole("link", { name: "minimum" })
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

describe("species overview: card heading (the bare-'R' bug, no pill boxes)", () => {
    // The record measured against the live archive: spc_n7c5snosejeow4z2vr4aivmv34
    // has one entry whose heading rendered as a bare "R" where every
    // sibling species on the archive shows "minimum · ground". Its
    // `species_entry_label` really is `"R"` on the wire -- see
    // `domain/recordFacets.ts` for why that is the server's OWN compact
    // discriminator, not free text, and why the heading must not read that
    // field at all. This fixture reproduces the real shape rather than a
    // convenient stand-in.
    const rEnantiomerSpeciesRef = "spc_n7c5snosejeow4z2vr4aivmv34"
    const rEnantiomerEntryRef = "spe_n7c5rentry00000000000000000"

    function rEnantiomerPayload() {
        return {
            records: [{
                species_ref: rEnantiomerSpeciesRef,
                canonical_smiles: "C[C@H](N)C(=O)O",
                inchi_key: "QNAYBMKLOCPYGJ-REOHCLBHSA-N",
                formula: "C3H7NO2",
                charge: 0,
                multiplicity: 1,
                stereo_kind: "enantiomer",
                entries: [{
                    species_entry_ref: rEnantiomerEntryRef,
                    species_entry_kind: "minimum",
                    electronic_state_kind: "ground",
                    stereo_label: "R",
                    species_entry_label: "R",
                    review: { status: "not_reviewed" },
                    availability: {
                        has_thermo: false, has_statmech: false, has_transport: false,
                        has_conformers: true, calculation_count: 5,
                    },
                }],
            }],
        }
    }

    function headingOf(card: HTMLElement): HTMLElement {
        const heading = card.querySelector("h4")
        if (!heading) throw new Error("No <h4> heading found in entry card")
        return heading
    }

    it("renders the R record as a readable phrase, not a bare 'R' heading -- this is the bug", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json(rEnantiomerPayload())))
        window.history.replaceState({}, "", `/species/${rEnantiomerSpeciesRef}`)
        render(<App />)

        const heading = headingOf(await cardFor(rEnantiomerEntryRef))
        // The lone entry's own state group has 1 member, so the bare
        // "ground state" phrase is dropped as redundant with the group
        // heading -- but stereochemistry is NOT established by that group
        // heading, so it survives into the card: "minimum · R enantiomer".
        expect(heading).toHaveTextContent("minimum · R enantiomer")
        // The bug: the heading's own accessible name must never collapse
        // to a bare, unexplained "R".
        expect(heading).not.toHaveTextContent(/^R$/)
        expect(heading).toHaveAccessibleName("minimum · R enantiomer")
    })

    it("renders no pill boxes at all -- every fact is plain text", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json(rEnantiomerPayload())))
        window.history.replaceState({}, "", `/species/${rEnantiomerSpeciesRef}`)
        render(<App />)

        await cardFor(rEnantiomerEntryRef)
        expect(document.querySelector(".record-facet-chips")).not.toBeInTheDocument()
        expect(document.querySelector(".record-facet-chip")).not.toBeInTheDocument()
    })

    it("states the electronic state exactly once within its own state group -- the group heading, not the card, carries it", async () => {
        server.use(http.get("/api/v1/scientific/species/search", () => HttpResponse.json(rEnantiomerPayload())))
        window.history.replaceState({}, "", `/species/${rEnantiomerSpeciesRef}`)
        render(<App />)

        const card = await cardFor(rEnantiomerEntryRef)
        const group = card.closest("li.entry-state-group") as HTMLElement
        // Scoped to the group itself (not the whole page, which also
        // carries an unrelated static intro paragraph that happens to
        // mention "ground-state" in prose) -- within the group, the group
        // heading is the ONE place "ground" appears for this entry; a
        // mutation that re-adds the bare state phrase to the card's own
        // heading would make this find two.
        expect(within(group).getAllByText(/ground/i)).toHaveLength(1)
        expect(within(card).queryByText(/ground/i)).not.toBeInTheDocument()
    })
})
