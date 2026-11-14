import { describe, expect, it, afterEach, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ledeText } from "../domain/ledeText"
import { MemoryRouter } from "react-router-dom"
import { RecordIdentityHeader } from "./RecordIdentityHeader"
import type { RecordIdentity } from "../domain/recordIdentity"

afterEach(() => {
    cleanup()
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true })
})

// `kicker`/`title` are required props (the
// header owns the record's own kicker-row/h1, not just identity --
// see the component's own docstring). Every test below that does not
// care about their exact content gets a stand-in default here so the
// existing identity/facets/provenance assertions keep testing exactly
// what they always tested, without every call site having to repeat
// boilerplate the test isn't about.
function renderHeader(props: Partial<Parameters<typeof RecordIdentityHeader>[0]> & Pick<Parameters<typeof RecordIdentityHeader>[0], "identity">) {
    const merged = { kicker: "Test record · deposited evidence", title: "Test record", ...props }
    return render(<MemoryRouter><RecordIdentityHeader {...merged} /></MemoryRouter>)
}

const speciesIdentity: RecordIdentity = {
    kind: "species_entry",
    formula: "CH3",
    canonicalSmiles: "[CH3]",
    inchiKey: "WCYWZMWISLQXQU-UHFFFAOYSA-N",
    charge: 0,
    multiplicity: 2,
    speciesEntryRef: "spe_demo",
}

const tsIdentity: RecordIdentity = {
    kind: "transition_state_entry",
    formula: null,
    charge: 0,
    multiplicity: 2,
    transitionStateEntryRef: "tse_demo",
}

describe("RecordIdentityHeader", () => {
    it("renders a known species identity with SMILES and InChIKey", () => {
        renderHeader({ identity: speciesIdentity })
        // Scoped to the "SMILES" fact specifically: the species-entry link
        // just below it (`SpeciesEntryLink`, SMILES-leads-formula-in-
        // brackets) ALSO renders the bare identity SMILES as part of its
        // own text now, so an unscoped `getByText("[CH3]")` would match
        // two elements.
        expect(screen.getByText("SMILES").closest("div")).toHaveTextContent("[CH3]")
        expect(screen.getByText("WCYWZMWISLQXQU-UHFFFAOYSA-N")).toBeVisible()
    })

    it("renders the ambiguous case distinctly from the unambiguous case -- no SMILES/InChIKey shown, owners listed instead", () => {
        renderHeader({
            identity: { kind: "ambiguous", owners: [{ kind: "species_entry", ref: "spe_a" }, { kind: "species_entry", ref: "spe_b" }] },
        })
        expect(screen.getByTestId("record-identity-ambiguous")).toHaveTextContent(/more than one distinct owner/)
        expect(screen.getByRole("link", { name: "Species entry 1" })).toHaveAttribute("href", "/species-entries/spe_a")
        expect(screen.getByRole("link", { name: "Species entry 2" })).toHaveAttribute("href", "/species-entries/spe_b")
        expect(screen.getByTestId("record-identity-ambiguous").textContent).not.toMatch(/spe_/)
        expect(screen.queryByText("SMILES")).not.toBeInTheDocument()
        expect(screen.queryByText("InChIKey")).not.toBeInTheDocument()
    })

    it("renders the absent case distinctly from both known and ambiguous", () => {
        renderHeader({ identity: { kind: "absent" } })
        expect(screen.getByText(/No molecular identity is recorded/)).toBeVisible()
        expect(screen.queryByTestId("record-identity-ambiguous")).not.toBeInTheDocument()
    })

    it("a transition-state identity never renders a SMILES or InChIKey field, even an empty one", () => {
        renderHeader({ identity: tsIdentity })
        // No "SMILES" or "InChIKey" label anywhere -- not present with an
        // empty value, not present at all.
        expect(screen.queryByText("SMILES")).not.toBeInTheDocument()
        expect(screen.queryByText("InChIKey")).not.toBeInTheDocument()
        // The unmapped SMILES is depositor-typed and unparsed: no such fact at all.
        expect(screen.queryByText("Reaction SMILES (unmapped)")).not.toBeInTheDocument()
    })

    // No depositor-typed labels on public
    // pages at all. `TransitionStateIdentity` does not even carry a
    // `label` field (see that type's own comment), but this asserts the
    // RENDER side directly and defensively -- even if a future caller
    // passes an identity object that still happens to carry a stray
    // `label` property at runtime (TypeScript erases the type, not the
    // property), this header must never turn it into a "Label" fact or
    // show the string anywhere.
    it("never renders a 'Label' fact or a depositor-typed label string, even if the identity object carries one", () => {
        renderHeader({ identity: { ...tsIdentity, label: "TS0" } as unknown as RecordIdentity })
        expect(screen.queryByText("Label")).not.toBeInTheDocument()
        expect(screen.queryByText("TS0")).not.toBeInTheDocument()
    })

    it("never renders a transition-state's unmapped SMILES, whatever it holds", () => {
        for (const typed of ["[CH2]OO[CH2]", "PISS.ON.BOOBS", "DEPOSITOR_SENTINEL_smiles"]) {
            const { unmount } = renderHeader({ identity: { ...tsIdentity, unmappedSmiles: typed } as unknown as RecordIdentity })
            expect(document.body.innerHTML).not.toContain(typed)
            unmount()
        }
    })

    it("renders the identified-by-reaction note for a transition-state identity by default", () => {
        renderHeader({ identity: tsIdentity })
        expect(screen.getByText(/A transition state has no SMILES identity; it is identified by the reaction it connects/)).toBeVisible()
    })

    it("omits the identified-by-reaction note when explainTransitionStateIdentity=false", () => {
        renderHeader({ identity: tsIdentity, explainTransitionStateIdentity: false })
        expect(screen.queryByText(/identified by the reaction it connects/i)).not.toBeInTheDocument()
    })

    it("renders no submission row at all when the key is absent (anonymous caller)", () => {
        renderHeader({ identity: speciesIdentity, submissionRef: undefined })
        expect(screen.queryByText("Submission")).not.toBeInTheDocument()
    })

    it("renders 'not recorded' when the key is present but null (authenticated, no linked submission)", () => {
        renderHeader({ identity: speciesIdentity, submissionRef: null })
        expect(screen.getByText("Submission")).toBeVisible()
        expect(screen.getByText("not recorded")).toBeVisible()
    })

    it("renders the submission ref when the key is present and populated", () => {
        renderHeader({ identity: speciesIdentity, submissionRef: "sub_demo" })
        expect(screen.getByText("sub_demo")).toBeVisible()
    })

    it("renders classification facets as a plain readable phrase, only when facets are supplied -- no pill row", () => {
        const { rerender } = render(
            <MemoryRouter>
                <RecordIdentityHeader kicker="Test record · deposited evidence" title="Test record" identity={speciesIdentity} />
            </MemoryRouter>,
        )
        expect(document.querySelector(".record-identity-facets")).toBeNull()
        rerender(
            <MemoryRouter>
                <RecordIdentityHeader
                    kicker="Test record · deposited evidence"
                    title="Test record"
                    identity={speciesIdentity}
                    facets={{ species_entry_kind: "minimum", electronic_state_kind: "ground" }}
                />
            </MemoryRouter>,
        )
        const facets = document.querySelector(".record-identity-facets")
        expect(facets).not.toBeNull()
        expect(facets).toHaveTextContent("minimum · ground state")
        // No pill boxes: never a `.record-facet-chips` / `.record-facet-chip`
        // list, not even when facets are supplied.
        expect(document.querySelector(".record-facet-chips")).toBeNull()
    })

    // Mutation check: swap the kicker/h1/identity order in the component
    // (e.g. move the `<h1>` above the kicker row, or the identity `.kv-list`
    // above the `<h1>`) and this test fails -- it asserts the ACTUAL DOM
    // order top to bottom, not just presence of each piece.
    it("renders kicker row, then h1, then identity facts, then classification facets, then provenance -- in that order", () => {
        const { container } = renderHeader({
            kicker: "Optimisation calculation · deposited evidence",
            title: "Optimisation of CH3",
            identity: speciesIdentity,
            facets: { species_entry_kind: "minimum", electronic_state_kind: "ground" },
            submissionRef: "sub_demo",
        })
        const header = container.querySelector(".record-identity-header") as HTMLElement
        expect(header).not.toBeNull()
        const tags = Array.from(header.children).map((el) => el.className)
        expect(tags[0]).toBe("record-identity-kicker-row")
        expect(header.querySelector("h1")).not.toBeNull()
        expect(Array.from(header.children).findIndex((el) => el.tagName === "H1")).toBe(1)
        const known = header.querySelector(".record-identity-known") as HTMLElement
        expect(known).not.toBeNull()
        expect(known.querySelector("dl.kv-list.record-identity-facts")).not.toBeNull()
        const facets = header.querySelector(".record-identity-facets")
        const provenance = header.querySelector("dl.kv-list.record-identity-provenance")
        expect(facets).not.toBeNull()
        expect(provenance).not.toBeNull()
        // identity block precedes facets, which precedes provenance.
        const order = Array.from(header.children)
        expect(order.indexOf(known)).toBeLessThan(order.indexOf(facets as Element))
        expect(order.indexOf(facets as Element)).toBeLessThan(order.indexOf(provenance as Element))
    })

    it("renders the kicker text and an optional caller-supplied pill together in the kicker row", () => {
        const { container } = renderHeader({
            kicker: "Geometry · deposited evidence",
            title: "Geometry",
            identity: speciesIdentity,
            pill: <span className="value-pill" data-testid="test-pill">reviewed</span>,
        })
        const row = container.querySelector(".record-identity-kicker-row") as HTMLElement
        expect(row).not.toBeNull()
        expect(row.querySelector(".t-kicker")).toHaveTextContent("Geometry · deposited evidence")
        expect(row.querySelector('[data-testid="test-pill"]')).not.toBeNull()
    })

    it("sets the title in the one --type-display-2 step", () => {
        const { container } = renderHeader({ identity: speciesIdentity })
        expect(container.querySelector("h1.t-display-2")).not.toBeNull()
        expect(container.querySelector("h1.t-display-1")).toBeNull()
    })

    // Identity facts rendered as `.data` get a copy button. This reuses
    // `RefsDisclosure`'s own `CopyButton`, the EXACT component (clipboard
    // write, "Copied" feedback, aria-label pattern) rather than building a
    // second copy button, so every page composing this header (species
    // entry, calculation, geometry, conformer group, observation,
    // transition-state entry) gets the affordance back for free.
    describe("copy affordances on identity facts rendered as .data", () => {
        it("a header with a SMILES renders a copy button whose accessible name names the field, and clicking it writes the value to the clipboard", async () => {
            const writeText = vi.fn().mockResolvedValue(undefined)
            Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })

            renderHeader({ identity: speciesIdentity })
            const button = screen.getByRole("button", { name: /copy smiles/i })
            fireEvent.click(button)
            expect(writeText).toHaveBeenCalledWith(speciesIdentity.canonicalSmiles)
        })

        it("a header with an InChIKey renders a copy button whose accessible name names the field, and clicking it writes the value to the clipboard", async () => {
            const writeText = vi.fn().mockResolvedValue(undefined)
            Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })

            renderHeader({ identity: speciesIdentity })
            const button = screen.getByRole("button", { name: /copy inchikey/i })
            fireEvent.click(button)
            expect(writeText).toHaveBeenCalledWith(speciesIdentity.inchiKey)
        })

        it("a header without SMILES (a transition-state identity, which carries no such field at all) renders no SMILES copy button", () => {
            renderHeader({ identity: tsIdentity })
            expect(screen.queryByRole("button", { name: /copy smiles/i })).not.toBeInTheDocument()
            expect(screen.queryByRole("button", { name: /copy inchikey/i })).not.toBeInTheDocument()
        })

        it("a transition-state identity's entry-ref fact gets a copy button, and no unmapped SMILES is offered", () => {
            renderHeader({
                identity: { ...tsIdentity, unmappedSmiles: "[CH3].[OH2]", transitionStateEntryRef: "tse_demo" } as unknown as RecordIdentity,
            })
            expect(screen.queryByRole("button", { name: /copy reaction smiles/i })).not.toBeInTheDocument()
            expect(screen.getByRole("button", { name: /copy transition state entry/i })).toBeVisible()
            expect(screen.getByRole("link", { name: "Transition state" })).toHaveAttribute("href", "/transition-state-entries/tse_demo")
            expect(screen.getByText("Transition state entry").closest("div")?.textContent).not.toContain("tse_demo")
        })

        it("a transition-state identity with no unmapped SMILES deposited renders no copy button for that fact -- 'not recorded' isn't copyable", () => {
            renderHeader({ identity: tsIdentity })
            expect(screen.queryByRole("button", { name: /copy reaction smiles/i })).not.toBeInTheDocument()
        })
    })

    // The "Species entry" fact does not show the RAW
    // `identity.speciesEntryLabel` string as the link's own visible/
    // accessible text (a bare "R"). `species_entry_label` is NOT
    // depositor free text -- it is built server-side (`backend/app/
    // services/scientific_read/species_identity.py:42`'s
    // `species_entry_label()`) as a compact discriminator from the
    // identity columns that make one entry differ from its siblings
    // (stereo_label, electronic_state_kind/label, term_symbol,
    // isotope_key), omitting anything at the default -- the live "R" is
    // the entry's stereo label (R enantiomer). The link now shows
    // `SpeciesFace` (SMILES leads, formula in parentheses -- same
    // `<Formula>` rendering the h1 uses, so subscripts match) -- or the
    // entry ref as `<code className="data">` when NEITHER a SMILES nor a
    // formula was served -- followed by the EXPANDED label via
    // `recordFacets.ts`'s own `stereoChip` helper (documented at
    // `domain/recordFacets.ts:53-68`) when one was served, never the raw
    // discriminator string alone.
    //
    // This fact now delegates to `SpeciesEntryLink`
    // (`./SpeciesEntryLink.tsx`) rather than re-deriving the expression
    // here -- see that component's own test file for the exhaustive
    // SMILES/formula/label matrix. These cases stay here as an
    // integration check that this header actually wires
    // `identity.canonicalSmiles` / `identity.formula` /
    // `identity.speciesEntryLabel` through, and -- per the unified
    // fallback rule across all four species-entry-link call sites (this
    // header, `ConformerGroupPage`, `ConformerObservationPage`) -- that
    // the fallback is the entry REF, never the literal words "Species
    // entry" (the `<dt>` beside this `<dd>` already says that).
    describe("the species-entry link expands the served discriminator via the shared stereoChip helper", () => {
        // `getByRole(..., { name })` uses accessible-name computation, which
        // collapses the whitespace between the SMILES `code` and the
        // bracketed-formula `span` differently from raw `textContent`
        // (confirmed elsewhere via `.textContent` assertions) -- queried by
        // `href` and asserted on `textContent` here instead, to check the
        // actual rendered text rather than the accname algorithm's own
        // normalisation of it.
        it("shows SMILES-leads-formula-in-brackets and never the served species_entry_label", () => {
            renderHeader({
                identity: { ...speciesIdentity, formula: "CH3" },
            })
            const link = document.querySelector('a[href="/species-entries/spe_demo"]')!
            expect(link.textContent).toBe("[CH3] (CH3)")
        })

        it("shows SMILES-leads-formula-in-brackets alone when the identity carries no label", () => {
            renderHeader({ identity: { ...speciesIdentity, formula: "CH3" } })
            const link = document.querySelector('a[href="/species-entries/spe_demo"]')!
            expect(link.textContent).toBe("[CH3] (CH3)")
        })

        it("no formula served, SMILES still is: the SMILES stands alone (no bracketed formula)", () => {
            renderHeader({ identity: { ...speciesIdentity, formula: null } })
            const link = screen.getByRole("link", { name: "[CH3]" })
            expect(link).toHaveAttribute("href", "/species-entries/spe_demo")
            expect(link.querySelector("code.data")?.textContent).toBe("[CH3]")
        })

        it("falls back to the words 'Species entry', never the ref, when the identity carries no SMILES, no formula, and no label", () => {
            renderHeader({ identity: { ...speciesIdentity, canonicalSmiles: "", formula: null } })
            const link = document.querySelector('a[href="/species-entries/spe_demo"]') as HTMLElement
            expect(link).not.toBeNull()
            expect(link.textContent).toBe("Species entry")
            expect(link.querySelector("code")).toBeNull()
        })
    })
})

// `ownRef` -- the record's OWN public ref, shown inline first. Deliberately exercised across every
// `identity.kind` branch (`species_entry`, `transition_state_entry`,
// `ambiguous`, `absent`): a mutation that only prepends `ownRef` inside
// ONE branch's `dl` (e.g. only `species_entry`) would still pass a test
// that only checks the species-entry case, so each branch gets its own
// assertion here.
describe("ownRef -- the record's own ref, shown first, always", () => {
    it("omits the own-ref fact entirely when no ownRef is supplied -- existing callers without it are unaffected", () => {
        const { container } = renderHeader({ identity: speciesIdentity })
        expect(screen.queryByText("Calculation ref")).not.toBeInTheDocument()
        // No mystery leading fact: the first child of the facts dl is still
        // SMILES, exactly as it was before `ownRef` existed.
        const facts = container.querySelector("dl.kv-list.record-identity-facts") as HTMLElement
        expect(Array.from(facts.children)[0]).toHaveTextContent("SMILES")
    })

    it("renders the own ref as the FIRST fact in a species_entry identity's facts list, with the data face and a copy button", () => {
        const { container } = renderHeader({
            identity: speciesIdentity,
            ownRef: { label: "Calculation ref", value: "calc_demo" },
        })
        const facts = container.querySelector("dl.kv-list.record-identity-facts") as HTMLElement
        expect(facts).not.toBeNull()
        expect(Array.from(facts.children)[0]).toHaveTextContent("Calculation ref")
        const value = within(facts).getByText("calc_demo")
        expect(value.tagName).toBe("CODE")
        expect(value).toHaveClass("data")
        expect(within(facts).getByRole("button", { name: /copy calculation ref/i })).toBeVisible()
    })

    it("renders the own ref as the FIRST fact in a transition_state_entry identity's facts list", () => {
        const { container } = renderHeader({
            identity: tsIdentity,
            ownRef: { label: "Geometry ref", value: "geom_demo" },
        })
        const facts = container.querySelector("dl.kv-list.record-identity-facts") as HTMLElement
        expect(facts).not.toBeNull()
        expect(Array.from(facts.children)[0]).toHaveTextContent("Geometry ref")
        expect(within(facts).getByText("geom_demo")).toBeVisible()
    })

    it("renders the own ref even when the identity is ambiguous -- the owner list follows it, not instead of it", () => {
        renderHeader({
            identity: { kind: "ambiguous", owners: [{ kind: "species_entry", ref: "spe_a" }] },
            ownRef: { label: "Geometry ref", value: "geom_demo" },
        })
        const facts = screen.getByText("Geometry ref").closest(".record-identity-facts") as HTMLElement
        expect(facts).not.toBeNull()
        expect(within(facts).getByText("geom_demo")).toBeVisible()
        // Still renders the ambiguous-owner content alongside it.
        expect(screen.getByTestId("record-identity-ambiguous")).toBeVisible()
        expect(screen.getByRole("link", { name: "Species entry" })).toHaveAttribute("href", "/species-entries/spe_a")
        expect(screen.getByTestId("record-identity-ambiguous").textContent).not.toMatch(/spe_/)
    })

    it("renders the own ref even when the identity is absent -- a record can always name itself even with no known molecular owner", () => {
        renderHeader({
            identity: { kind: "absent" },
            ownRef: { label: "Geometry ref", value: "geom_demo" },
        })
        const facts = screen.getByText("Geometry ref").closest(".record-identity-facts") as HTMLElement
        expect(facts).not.toBeNull()
        expect(within(facts).getByText("geom_demo")).toBeVisible()
        expect(screen.getByText(/No molecular identity is recorded/)).toBeVisible()
    })

    it("clicking the own-ref copy button writes its value to the clipboard", () => {
        const writeText = vi.fn().mockResolvedValue(undefined)
        Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })

        renderHeader({ identity: speciesIdentity, ownRef: { label: "Calculation ref", value: "calc_demo" } })
        fireEvent.click(screen.getByRole("button", { name: /copy calculation ref/i }))
        expect(writeText).toHaveBeenCalledWith("calc_demo")
    })
})

describe("glyph, review chip and lede", () => {
    it("shows the kind glyph, the review status as a word and the lede under the h1", () => {
        const { container } = renderHeader({
            identity: speciesIdentity,
            glyph: "species-entry",
            review: "approved",
            lede: ledeText`A species entry with ${2} calculations.`,
        })
        const row = container.querySelector(".record-identity-kicker-row") as HTMLElement
        expect(row.querySelector('svg[data-kind="species-entry"]')).not.toBeNull()
        expect(row.querySelector(".review-chip")).toHaveTextContent("approved")
        const h1 = container.querySelector("h1") as HTMLElement
        expect(h1.nextElementSibling).toHaveTextContent("A species entry with 2 calculations.")
    })

    it("shows one sentence under the title: intro alone as before, lede alone, and only lede with a console error when both are given", () => {
        const { container, unmount } = renderHeader({ identity: speciesIdentity, intro: "An intro." })
        expect(container.querySelector("h1")?.nextElementSibling).toHaveTextContent("An intro.")
        unmount()

        const error = vi.spyOn(console, "error").mockImplementation(() => {})
        const both = renderHeader({ identity: speciesIdentity, intro: "An intro.", lede: ledeText`A lede.` })
        expect(both.container.querySelectorAll(".record-lede, .section-intro")).toHaveLength(1)
        expect(both.container.querySelector("h1")?.nextElementSibling).toHaveTextContent("A lede.")
        expect(both.container).not.toHaveTextContent("An intro.")
        expect(error).toHaveBeenCalledWith(expect.stringContaining("either `lede` or `intro`"))
        error.mockRestore()
    })

    it("keeps the ref out of the h1: it is a secondary code with a copy button", () => {
        const { container } = renderHeader({
            identity: speciesIdentity,
            title: "Methyl radical",
            ownRef: { label: "Species entry ref", value: "spe_demo" },
        })
        expect(container.querySelectorAll("h1")).toHaveLength(1)
        expect(container.querySelector("h1")).toHaveTextContent("Methyl radical")
        expect(container.querySelector("h1")).not.toHaveTextContent("spe_demo")
        expect(screen.getByText("spe_demo").tagName).toBe("CODE")
        expect(screen.getByRole("button", { name: /copy species entry ref/i })).toBeVisible()
    })

    it("renders no chip or glyph when none is given, and the pill slot still works", () => {
        const { container } = renderHeader({ identity: speciesIdentity, pill: <span data-testid="p">x</span> })
        expect(container.querySelector(".review-chip")).toBeNull()
        expect(container.querySelector("svg")).toBeNull()
        expect(screen.getByTestId("p")).toBeVisible()
    })

    it("Tab reaches the review term, then the copy button, and Esc closes the term", async () => {
        const user = userEvent.setup()
        renderHeader({ identity: speciesIdentity, review: "not_reviewed", ownRef: { label: "Calculation ref", value: "calc_demo" } })
        await user.tab()
        const term = screen.getByRole("button", { name: "What review status means" })
        expect(term).toHaveFocus()
        const popover = screen.getByRole("tooltip", { hidden: true })
        expect(popover).toHaveAttribute("data-open", "true")
        await user.keyboard("{Escape}")
        expect(popover).toHaveAttribute("data-open", "false")
        expect(term).toHaveFocus()
        await user.tab()
        expect(screen.getByRole("button", { name: /copy calculation ref/i })).toHaveFocus()
    })
})
