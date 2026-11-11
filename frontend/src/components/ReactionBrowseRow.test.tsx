import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { ReactionBrowseRecord } from "../api/browseApi"
// Needed for the computed-style assertions below (the stretched-link/
// selectable-ref mechanic, and the stereo-chip sizing fix): without the
// real stylesheets loaded, `getComputedStyle` would fall back to each
// property's browser-initial value regardless of what `.reaction-browse-
// row`'s own rules say, passing those assertions without ever having
// exercised the rule at all (same reasoning as `Disclosure.test.tsx`'s own
// import of `design-system.css`). `design-system.css` is imported
// separately from `browse.css` (which does not itself `@import` it) --
// `--type-heading-2-font`/`--type-note-font` are DEFINED there, and
// `browse.css`'s own rules only reference them via `var(...)`.
import "../design-system.css"
import "../browse.css"
import { ReactionBrowseRow } from "./ReactionBrowseRow"

afterEach(cleanup)

function renderRow(record: ReactionBrowseRecord) {
    return render(
        <MemoryRouter>
            <ul>
                <ReactionBrowseRow record={record} />
            </ul>
        </MemoryRouter>,
    )
}

function record(overrides: Partial<ReactionBrowseRecord> = {}): ReactionBrowseRecord {
    return {
        reaction_ref: "rxn_one",
        reaction_entry_ref: "rxe_one",
        equation: "O + [CH3] <=> C + [OH]",
        reversible: true,
        family: "R_Addition_MultipleBond",
        review: { status: "not_reviewed" },
        reactants: [
            { species_entry_ref: "spe_o", species_entry_label: null, smiles: "[O]", formula: "O", stoichiometry: 1, participant_index: 1 },
            { species_entry_ref: "spe_ch3", species_entry_label: null, smiles: "[CH3]", formula: "CH3", stoichiometry: 1, participant_index: 2 },
        ],
        products: [
            { species_entry_ref: "spe_ch4", species_entry_label: null, smiles: "C", formula: "CH4", stoichiometry: 1, participant_index: 1 },
            { species_entry_ref: "spe_oh", species_entry_label: null, smiles: "[OH]", formula: "OH", stoichiometry: 1, participant_index: 2 },
        ],
        availability: { has_kinetics: true, has_transition_state: true, has_path_search: false, has_atom_map: false, kinetics_count: 1 },
        ...overrides,
    } as ReactionBrowseRecord
}

// The row must render the SHARED `ReactionEquation` component, not a
// forked plain-text rendering: formulas carry subscripts, and the arrow
// reflects `reversible`. Each assertion here is precise enough that
// mutating the corresponding field in `record()` (or swapping which prop
// the row passes to `ReactionEquation`) makes the assertion fail -- not a
// loose substring check that would still pass on a differently-wrong row.
//
// Participants do not render as individual
// `/species-entries/:ref` links on this row (almost
// every pixel of the equation would be a species link, crowding out the
// reaction link). `ReactionBrowseRow` passes `linkParticipants=false`,
// so the formula/subscript/chip content still renders, just as plain text.
describe("ReactionBrowseRow: equation rendering (via the shared ReactionEquation component, participants UNLINKED)", () => {
    it("renders each participant's formula WITH subscripts, not the bare formula string -- but as plain text, no per-participant <a>", () => {
        const { container } = renderRow(record())
        expect(container.querySelector('a[href="/species-entries/spe_ch3"]')).toBeNull()
        const title = container.querySelector(".reaction-browse-row-title") as HTMLElement
        expect(title.querySelector("sub")?.textContent).toBe("3")
        expect(title.textContent).toContain("CH3")
    })

    it("renders the reversible arrow (⇌) with its aria-label when reversible=true", () => {
        renderRow(record({ reversible: true }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(row.textContent).toContain("⇌")
        expect(row.querySelector('[aria-label="reacts reversibly with"]')).not.toBeNull()
        expect(row.textContent).not.toContain("→")
    })

    it("renders the one-way arrow (→) with its OWN aria-label when reversible=false -- not the reversible one left over", () => {
        renderRow(record({ reversible: false }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(row.textContent).toContain("→")
        expect(row.querySelector('[aria-label="reacts to form"]')).not.toBeNull()
        expect(row.querySelector('[aria-label="reacts reversibly with"]')).toBeNull()
    })

    // MUTATION CHECK: if the row re-enabled participant links (dropped
    // `linkParticipants={false}` or flipped it to `true`), every one of
    // these four hrefs would reappear, and the row would carry FIVE links
    // instead of one (see the "EXACTLY ONE link" describe block below,
    // which is the sharper version of this same check).
    it("no participant renders as a link to its OWN species entry -- none of the four species hrefs exist on this row", () => {
        const { container } = renderRow(record())
        expect(container.querySelector('a[href="/species-entries/spe_o"]')).toBeNull()
        expect(container.querySelector('a[href="/species-entries/spe_ch3"]')).toBeNull()
        expect(container.querySelector('a[href="/species-entries/spe_ch4"]')).toBeNull()
        expect(container.querySelector('a[href="/species-entries/spe_oh"]')).toBeNull()
    })
})

// Family: rendered plainly when present, in the `.absent` register (never
// indistinguishable plain text) when null -- the archive's own "never
// assert from absence" rule ("not recorded", not a claim the family does
// not exist).
describe("ReactionBrowseRow: family", () => {
    it("renders the family, token-formatted, as plain text", () => {
        renderRow(record({ family: "R_Addition_MultipleBond" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(within(row).getByText("R Addition MultipleBond")).toBeVisible()
        expect(row.querySelector(".absent")).toBeNull()
    })

    it("renders 'family not recorded' in the .absent register when family is null", () => {
        renderRow(record({ family: null }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const absent = within(row).getByText("family not recorded")
        expect(absent.className).toMatch(/absent/)
    })
})

// Review pill: the ENTRY's own review badge (`record.review.status`),
// distinguishable from the availability pills below -- swapping which
// field feeds this pill would satisfy a loose "some pill says something"
// check, so the assertion pins the exact class AND exact text.
describe("ReactionBrowseRow: review pill", () => {
    it("renders the review status, token-formatted, in its own pill", () => {
        renderRow(record({ review: { status: "under_review" } }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const pill = within(row).getByText("under review")
        expect(pill).toHaveClass("value-pill")
        expect(pill).toHaveClass("browse-entry-review")
    })

    it("renders a DIFFERENT review status when the record says a different one -- not a hardcoded 'not reviewed'", () => {
        renderRow(record({ review: { status: "approved" } }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(within(row).getByText("approved")).toBeVisible()
        expect(within(row).queryByText("not reviewed")).not.toBeInTheDocument()
    })
})

// Availability pills: two independent facts, `has_kinetics` and
// `has_transition_state`. Each of the four assertions below flips exactly
// ONE boolean and checks BOTH the true-state text and the false-state text
// disappear/appear correctly -- a test that only checked `toContain("no")`
// would also pass if the row rendered "no transition state" while actually
// reading `has_kinetics` ("not reviewed" satisfying a loose `toContain("no")`). Every assertion
// here is an exact string, not a substring.
describe("ReactionBrowseRow: has-kinetics / has-transition-state pills, read from their OWN field", () => {
    it("has_kinetics=true, has_transition_state=true: both positive pills render, neither negative one does", () => {
        renderRow(record({ availability: { has_kinetics: true, has_transition_state: true } as ReactionBrowseRecord["availability"] }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(within(row).getByText("has kinetics")).toBeVisible()
        expect(within(row).getByText("has transition state")).toBeVisible()
        expect(within(row).queryByText("no kinetics deposited")).not.toBeInTheDocument()
        expect(within(row).queryByText("no transition state deposited")).not.toBeInTheDocument()
    })

    it("has_kinetics=false, has_transition_state=false: both negative pills render, neither positive one does", () => {
        renderRow(record({ availability: { has_kinetics: false, has_transition_state: false } as ReactionBrowseRecord["availability"] }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(within(row).getByText("no kinetics deposited")).toBeVisible()
        expect(within(row).getByText("no transition state deposited")).toBeVisible()
        expect(within(row).queryByText("has kinetics")).not.toBeInTheDocument()
        expect(within(row).queryByText("has transition state")).not.toBeInTheDocument()
    })

    // MUTATION CHECK: has_kinetics true but has_transition_state false --
    // this is the case that catches the two pills being fed from the SAME
    // field (or swapped with each other). If `has kinetics` were derived
    // from `has_transition_state` (or vice versa), this fixture would flip
    // exactly one of the two expectations below.
    it("has_kinetics=true, has_transition_state=false: kinetics pill positive, TS pill negative -- the two fields are read independently", () => {
        renderRow(record({ availability: { has_kinetics: true, has_transition_state: false } as ReactionBrowseRecord["availability"] }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(within(row).getByText("has kinetics")).toBeVisible()
        expect(within(row).getByText("no transition state deposited")).toBeVisible()
        expect(within(row).queryByText("no kinetics deposited")).not.toBeInTheDocument()
        expect(within(row).queryByText("has transition state")).not.toBeInTheDocument()
    })

    it("has_kinetics=false, has_transition_state=true: the inverse split", () => {
        renderRow(record({ availability: { has_kinetics: false, has_transition_state: true } as ReactionBrowseRecord["availability"] }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(within(row).getByText("no kinetics deposited")).toBeVisible()
        expect(within(row).getByText("has transition state")).toBeVisible()
        expect(within(row).queryByText("has kinetics")).not.toBeInTheDocument()
        expect(within(row).queryByText("no transition state deposited")).not.toBeInTheDocument()
    })

    it("a positive availability pill carries `value-pill` without `value-pill--muted`; a negative one carries both", () => {
        renderRow(record({ availability: { has_kinetics: true, has_transition_state: false } as ReactionBrowseRecord["availability"] }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const positive = within(row).getByText("has kinetics")
        const negative = within(row).getByText("no transition state deposited")
        expect(positive).toHaveClass("value-pill")
        expect(positive).not.toHaveClass("value-pill--muted")
        expect(negative).toHaveClass("value-pill")
        expect(negative).toHaveClass("value-pill--muted")
    })
})

// Ref + link target: the row's
// TITLE links `/reactions/:reaction_ref` (the CHOOSER, where the reader
// selects the relevant entry, as with species), never
// `/reaction-entries/:reaction_entry_ref` directly. The one deposit this row
// itself represents is still reachable, as a SECOND, sibling link in the
// footer (`.browse-row-entry-link`) -- `SpeciesBrowseRow`'s own
// identity-link/entry-link shape. The identity ref
// (`reaction_ref`) sits in the data face, outside any anchor, so a
// drag-select over it does not start a link drag.
describe("ReactionBrowseRow: ref, link targets, and the stretched-link/selectable-ref split", () => {
    it("renders the reaction (identity) ref in a code.data element", () => {
        renderRow(record({ reaction_ref: "rxn_specific" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const ref = within(row).getByText("rxn_specific")
        expect(ref.tagName).toBe("CODE")
        expect(ref).toHaveClass("browse-ref")
        expect(ref).toHaveClass("data")
    })

    // MUTATION CHECK (mutation table item a): pointing the title back at
    // `/reaction-entries/:ref` directly is what this test
    // guards against.
    it("the title links to /reactions/:reaction_ref (the chooser), never /reaction-entries/:ref directly", () => {
        renderRow(record({ reaction_entry_ref: "rxe_specific", reaction_ref: "rxn_specific" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const title = within(row).getByRole("link", { name: /reacts reversibly with/ })
        expect(title).toHaveAttribute("href", "/reactions/rxn_specific")
        expect(title).not.toHaveAttribute("href", "/reaction-entries/rxe_specific")
    })

    it("a second link still reaches the one deposit this row represents, at /reaction-entries/:reaction_entry_ref", () => {
        renderRow(record({ reaction_entry_ref: "rxe_specific", reaction_ref: "rxn_other" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const entryLink = within(row).getByRole("link", { name: "View this deposit" })
        expect(entryLink).toHaveAttribute("href", "/reaction-entries/rxe_specific")
    })

    // Mirrors `SpeciesBrowseRow`'s own shape: the entry link is a SIBLING
    // of the title link, never nested inside it -- so there is no nested
    // `<a>` in `<a>` hazard even though the title link is also present.
    it("the entry link is NOT nested inside the title link", () => {
        renderRow(record())
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const title = within(row).getByRole("link", { name: /reacts reversibly with/ })
        const entryLink = within(row).getByRole("link", { name: "View this deposit" })
        expect(title.contains(entryLink)).toBe(false)
        expect(entryLink.contains(title)).toBe(false)
    })

    it("the reaction ref text does NOT sit inside any anchor -- a drag-select over it cannot start a link drag", () => {
        renderRow(record({ reaction_ref: "rxn_specific" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const ref = within(row).getByText("rxn_specific")
        expect(ref.closest("a")).toBeNull()
    })

    // `aria-label={record.equation ?? undefined}` on the link would be
    // a WCAG 2.5.3 "Label in Name" failure -- `record.equation` is the raw
    // SMILES-joined served string (e.g. "[NH2] + [NH2] <=> NN"), which
    // shares no words with the rendered formula text a sighted user sees
    // ("H2N + H2N ⇌ H4N2"), so a voice-control user saying "click H2N"
    // could not activate the row, and a screen reader spelled out SMILES
    // letter by letter instead of reading the formula. The fix is no
    // `aria-label` at all: the browser's own accname computation already
    // reads as a sentence, because the arrow's own nested `aria-label`
    // ("reacts reversibly with"/"reacts to form") is picked up by that
    // computation same as any other descendant text.
    it("the title link carries NO aria-label -- its accessible name is computed from its own visible content, not `record.equation`", () => {
        renderRow(record({ reaction_ref: "rxn_specific", equation: "O + [CH3] <=> C + [OH]" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const link = within(row).getByRole("link", { name: /reacts reversibly with/ })
        expect(link).toHaveAttribute("href", "/reactions/rxn_specific")
        expect(link).not.toHaveAttribute("aria-label")
        // MUTATION CHECK: the raw served `equation` string must NOT be
        // (part of) the accessible name -- reintroducing the old
        // `aria-label={record.equation}` override would make this match.
        expect(within(row).queryByRole("link", { name: "O + [CH3] <=> C + [OH]" })).toBeNull()
    })

    it("behaves identically when `equation` is absent (older API) -- there was never a code path that used it for aria-label", () => {
        const withoutEquation: ReactionBrowseRecord = record({ reaction_ref: "rxn_specific" })
        delete withoutEquation.equation
        renderRow(withoutEquation)
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const link = within(row).getByRole("link", { name: /reacts reversibly with/ })
        expect(link).toHaveAttribute("href", "/reactions/rxn_specific")
        expect(link).not.toHaveAttribute("aria-label")
        expect(link.textContent).toContain("CH3")
    })

    // MUTATION CHECK (invariant: "a reaction browse row contains EXACTLY
    // TWO links -- the reaction title and the one entry it represents"):
    // catches (b) participant links re-enabled -- which would add four more
    // `<a>`s -- and the row link (either one) deleted entirely -- which
    // would drop this below two.
    it("the row contains EXACTLY TWO links: the reaction title and the entry link", () => {
        renderRow(record())
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const links = within(row).getAllByRole("link")
        expect(links).toHaveLength(2)
        const hrefs = links.map((el) => el.getAttribute("href"))
        expect(hrefs).toContain("/reactions/rxn_one")
        expect(hrefs).toContain("/reaction-entries/rxe_one")
    })

    it("the title link wraps the equation content -- no nested <a> inside it", () => {
        renderRow(record())
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const link = within(row).getByRole("link", { name: /reacts reversibly with/ })
        expect(link.textContent).toContain("CH3")
        expect(link.querySelector("a")).toBeNull()
    })
})

// Computed-style coverage for the stretched-link mechanic itself: the row
// must be positioned (so the overlay can stretch to fill it), the overlay
// must be absolutely positioned, and the headline/footer must ALSO be
// positioned (so they paint above the overlay per DOM order -- see
// `browse.css`'s comment beside these rules for the full mechanic). A pure
// class-presence check cannot catch a typo'd property value (e.g.
// `position: static` surviving under a correctly-named rule); reading the
// actual computed value can.
describe("ReactionBrowseRow: stretched-link CSS mechanic (computed styles)", () => {
    it("the row itself is positioned, so the overlay's inset:0 resolves against IT, not an ancestor further up the page", () => {
        renderRow(record())
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(window.getComputedStyle(row).position).toBe("relative")
    })

    it("the stretched-link overlay (the title link's own ::after) is absolutely positioned -- same mechanic as the TS row's .browse-row-title::after", () => {
        renderRow(record())
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const link = within(row).getByRole("link", { name: /reacts reversibly with/ })
        expect(link).toHaveClass("browse-row-title")
        // jsdom does not compute pseudo-element styles, so the overlay
        // rule itself is covered by source-text assertions in
        // `browse.css.test.ts` (`.reaction-browse-row .browse-row-title
        // ::after`); this asserts the anchor it is scoped from carries the
        // right class, and that the row (the positioning ancestor the
        // overlay's `inset: 0` resolves against) is itself positioned.
        expect(window.getComputedStyle(row).position).toBe("relative")
    })

    it("the footer (ref) is positioned, so it paints ABOVE the overlay and stays selectable", () => {
        renderRow(record())
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const footer = row.querySelector(".browse-row-footer") as HTMLElement
        expect(window.getComputedStyle(footer).position).toBe("relative")
    })

    it("never prints a participant's species_entry_label (it joins in depositor-typed parts)", () => {
        renderRow(record({
            reactants: [
                { species_entry_ref: "spe_nn", species_entry_label: "R DEPOSITOR_SENTINEL_label", smiles: "N=N", formula: "H2N2", stoichiometry: 1, participant_index: 1 },
            ],
        }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(row.innerHTML).not.toContain("DEPOSITOR_SENTINEL")
        expect(row.textContent).not.toContain("enantiomer")
    })
})

// `matched_direction` is served on every row (present even on an
// unfiltered query, where it is "forward"). Example: `product_smiles=O`
// returns a reaction ("O + [CH3] <=> C + [OH]", water on the REACTANT side
// of the served equation) with `matched_direction: "reverse"` -- without
// rendering that fact, a reader searching for water-as-product sees water on
// the wrong side with nothing explaining why the filter "misfired". Tested in BOTH
// directions, per the finding: "forward" (and its absence/null) must stay
// SILENT, not just "reverse" must speak -- a component that rendered the
// note unconditionally, or read the wrong field, would still pass a
// reverse-only test suite.
describe("ReactionBrowseRow: matched_direction (reverse-match note)", () => {
    it('matched_direction="reverse": renders the note, exact text, in the footer', () => {
        renderRow(record({ matched_direction: "reverse" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const note = within(row).getByText("Matched on the reverse direction")
        expect(note).toBeVisible()
        expect(note).toHaveClass("browse-row-evidence")
        expect(note.closest(".browse-row-footer")).toBeTruthy()
    })

    it('matched_direction="forward": renders NO note -- an ordinary match is not itself news', () => {
        renderRow(record({ matched_direction: "forward" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(within(row).queryByText(/[Mm]atched on the reverse direction/)).not.toBeInTheDocument()
    })

    it("matched_direction absent (older API, key never served): renders NO note -- an absent field is not a claim of \"forward\"", () => {
        const withoutField: ReactionBrowseRecord = record()
        delete withoutField.matched_direction
        renderRow(withoutField)
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(within(row).queryByText(/[Mm]atched on the reverse direction/)).not.toBeInTheDocument()
    })

    it("matched_direction=null: renders NO note", () => {
        renderRow(record({ matched_direction: null }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(within(row).queryByText(/[Mm]atched on the reverse direction/)).not.toBeInTheDocument()
    })

    it("the reverse note is a plain note, not a .value-pill -- distinct from the review/availability facts", () => {
        renderRow(record({ matched_direction: "reverse" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const note = within(row).getByText("Matched on the reverse direction")
        expect(note).not.toHaveClass("value-pill")
    })
})

describe("ReactionBrowseRow: design-system primitive adoption", () => {
    it("the row carries the shared .card and .browse-row primitives alongside .reaction-browse-row", () => {
        renderRow(record())
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        expect(row).toHaveClass("card")
        expect(row).toHaveClass("browse-row")
    })
})

describe("ReactionBrowseRow: electronic-state facets reach the equation chips", () => {
    it("shows a participant's grammar-valid state label and term symbol, and hides one outside the grammar", () => {
        const base = record()
        const { container } = renderRow(record({
            reactants: [
                { ...base.reactants[0], species_entry_kind: "minimum", electronic_state_kind: "excited", electronic_state_label: "T1", term_symbol: "\u00b3B\u2081" },
                { ...base.reactants[1], species_entry_kind: "minimum", electronic_state_kind: "excited", electronic_state_label: "R", term_symbol: "DEPOSITOR_SENTINEL" },
            ],
        }))
        const text = container.textContent ?? ""
        expect(text).toContain("excited state, state T1, \u00b3B\u2081")
        expect(text).not.toMatch(/state R|DEPOSITOR_SENTINEL/)
    })
})
