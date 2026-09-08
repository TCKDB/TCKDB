import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
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

// The row must render the SHARED `ReactionEquation` component (PR 2), not a
// forked plain-text rendering: formulas carry subscripts, and the arrow
// reflects `reversible`. Each assertion here is precise enough that
// mutating the corresponding field in `record()` (or swapping which prop
// the row passes to `ReactionEquation`) makes the assertion fail -- not a
// loose substring check that would still pass on a differently-wrong row.
//
// Owner-reported-defect fix: participants no longer render as individual
// `/species-entries/:ref` links on this row (that was the bug -- almost
// every pixel of the equation was a species link, crowding out the
// reaction link). `ReactionBrowseRow` now passes `linkParticipants=false`,
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
// reading `has_kinetics`, per the PR 2 review finding on this exact trap
// ("not reviewed" satisfying a loose `toContain("no")`). Every assertion
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

// Ref + link target: the row links `/reaction-entries/:reaction_entry_ref`
// (the ENTRY page, per the plan's route key -- never `/reactions/:ref`,
// the chooser), with the ref itself visible in the data face and kept
// OUTSIDE the anchor so a drag-select over it does not start a link drag.
describe("ReactionBrowseRow: ref, link target, and the stretched-link/selectable-ref split", () => {
    it("renders the ref in a code.data element", () => {
        renderRow(record({ reaction_entry_ref: "rxe_specific" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const ref = within(row).getByText("rxe_specific")
        expect(ref.tagName).toBe("CODE")
        expect(ref).toHaveClass("browse-ref")
        expect(ref).toHaveClass("data")
    })

    it("links to /reaction-entries/:ref, not /reactions/:ref", () => {
        renderRow(record({ reaction_entry_ref: "rxe_specific", reaction_ref: "rxn_other" }))
        const links = screen.getAllByRole("link")
        const rowLink = links.find((el) => el.getAttribute("href") === "/reaction-entries/rxe_specific")
        expect(rowLink).toBeTruthy()
        expect(links.some((el) => el.getAttribute("href")?.startsWith("/reactions/"))).toBe(false)
    })

    it("the ref text does NOT sit inside any anchor -- a drag-select over it cannot start a link drag", () => {
        renderRow(record({ reaction_entry_ref: "rxe_specific" }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const ref = within(row).getByText("rxe_specific")
        expect(ref.closest("a")).toBeNull()
    })

    it("the row's single link carries an accessible name built from the served equation text", () => {
        renderRow(record({ reaction_entry_ref: "rxe_specific", equation: "O + [CH3] <=> C + [OH]" }))
        const link = screen.getByRole("link", { name: "O + [CH3] <=> C + [OH]" })
        expect(link).toHaveAttribute("href", "/reaction-entries/rxe_specific")
    })

    it("falls back to the browser's own content-derived accessible name when `equation` is absent (older API)", () => {
        const withoutEquation: ReactionBrowseRecord = record({ reaction_entry_ref: "rxe_specific" })
        delete withoutEquation.equation
        renderRow(withoutEquation)
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const link = within(row).getByRole("link")
        expect(link).toHaveAttribute("href", "/reaction-entries/rxe_specific")
        // No explicit aria-label was set, so the accessible name falls
        // back to the link's own visible text content (the rendered
        // equation) -- it is non-empty and mentions the formula.
        expect(link).not.toHaveAttribute("aria-label")
        expect(link.textContent).toContain("CH3")
    })

    // MUTATION CHECK (invariant: "a reaction browse row contains EXACTLY
    // ONE link"): catches (b) participant links re-enabled -- which would
    // add four more `<a>`s -- and (d) the row link deleted entirely --
    // which would drop this to zero. Neither survives this assertion.
    it("the row contains EXACTLY ONE link, and it is the reaction-entries link", () => {
        renderRow(record())
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const links = within(row).getAllByRole("link")
        expect(links).toHaveLength(1)
        expect(links[0]).toHaveAttribute("href", "/reaction-entries/rxe_one")
    })

    it("the row's link wraps the equation content -- no nested <a> inside it", () => {
        renderRow(record())
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const link = within(row).getByRole("link")
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
        const link = within(row).getByRole("link")
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

    // Review follow-up (round 2): the row re-created the exact stereo-chip
    // defect `reaction-entry.css`'s `.record-identity-title
    // .reaction-equation-chip` rule already fixed on the entry/chooser
    // pages -- that fix is scoped to `.record-identity-title` and does not
    // reach this row (`reaction-entry.css` is not even in this page's CSS
    // chunk). MEASURED before the fix (real Chrome, not jsdom): chip and
    // formula both rendered at the row title's own 20px step, a 1.00
    // ratio; after the fix, 13px chip / 20px formula (see the PR body's
    // measurement table for the exact numbers, both themes, 1920 and 680).
    //
    // This suite does NOT assert that ratio via `getComputedStyle` here --
    // confirmed empirically (see the investigation this comment survives
    // from) that this project's jsdom/cssstyle version does not resolve
    // `var(...)` custom properties AT ALL, in either shorthand (`font:
    // var(--type-note-font)`) or plain longhand (`font-size: var(--x)`)
    // declarations: `getComputedStyle(...).fontSize` returns the literal
    // string `"var(--type-note-font)"`, not a pixel value, for both the
    // BROKEN and the FIXED rule alike. No test anywhere in this codebase
    // asserts a computed pixel font-size for a token-driven rule for the
    // same reason -- the established pattern for exactly this class of
    // regression (`reaction-entry.css.test.ts`'s own two tests for the
    // IDENTICAL entry-page defect) is a source-text regex against the
    // stylesheet, in `browse.css.test.ts`. That is the load-bearing
    // regression guard for the actual font-size/color values; the real,
    // rendered ratio is verified in a genuine browser as part of the PR's
    // manual verification pass (screenshot + measurement in the PR body),
    // not reproducible as a computed-style assertion in this test runner.
    //
    // What CAN be asserted here, and is: the two elements resolve to
    // DIFFERENT (unresolved) `var()` references, proving the chip rule
    // does not simply inherit the title's own token -- a regression where
    // someone pointed `.reaction-equation-chip` back at
    // `--type-heading-2-font` would flip this from failing to passing on
    // the identical string, so it still catches the "same token" class of
    // mistake even without resolving either to a pixel value.
    it("the stereo-chip's font-size declaration references a DIFFERENT (unresolved) custom property than the title's own", () => {
        renderRow(record({
            reactants: [
                { species_entry_ref: "spe_nn", species_entry_label: "S", smiles: "N=N", formula: "H2N2", stoichiometry: 1, participant_index: 1 },
            ],
        }))
        const row = document.querySelector(".reaction-browse-row") as HTMLElement
        const chip = row.querySelector(".reaction-equation-chip") as HTMLElement
        const title = row.querySelector(".reaction-browse-row-title") as HTMLElement
        expect(chip).toBeTruthy()
        const chipFont = window.getComputedStyle(chip).font
        const titleFont = window.getComputedStyle(title).font
        expect(chipFont).not.toBe("")
        expect(chipFont).not.toBe(titleFont)
    })
})

// Review follow-up (round 2): `matched_direction` is served on every row
// (measured live -- present even on an unfiltered query, where it is
// "forward") but was previously dropped on the floor entirely. Live
// example: `product_smiles=O` returns `rxe_ed66mj3ohtyien5rm2x3sb3rdu`
// ("O + [CH3] <=> C + [OH]", water on the REACTANT side of the served
// equation) with `matched_direction: "reverse"` -- without rendering that
// fact, a reader searching for water-as-product sees water on the wrong
// side with nothing explaining why the filter "misfired". Tested in BOTH
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
