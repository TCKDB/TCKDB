import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { TransitionStateBrowseRecord } from "../api/browseApi"
import { TransitionStateBrowseRow } from "./TransitionStateBrowseRow"

afterEach(cleanup)

function renderRow(record: TransitionStateBrowseRecord) {
    return render(
        <MemoryRouter>
            <ul>
                <TransitionStateBrowseRow record={record} />
            </ul>
        </MemoryRouter>,
    )
}

function record(overrides: Partial<TransitionStateBrowseRecord> = {}): TransitionStateBrowseRecord {
    return {
        transition_state_entry: {
            transition_state_entry_ref: "tse_one",
            charge: 0,
            multiplicity: 2,
            status: "optimized",
            unmapped_smiles: null,
            review: { status: "not_reviewed" },
        },
        transition_state: {
            transition_state_ref: "ts_one",
            label: "TS0",
            note: null,
            review: { status: "not_reviewed" },
        },
        reaction: {
            reaction_ref: "rxn_one",
            reaction_entry_ref: "rxe_one",
            equation: "A <=> B",
            reversible: true,
            family: "R_Addition_MultipleBond",
        },
        evidence_summary: {
            calculation_count: 4,
            has_opt: true,
            has_freq: true,
            has_sp: true,
            has_irc: true,
            has_path_search: false,
            has_geometry_validation: false,
            has_scf_stability: false,
        },
        ...overrides,
    } as TransitionStateBrowseRecord
}

// The owner's report, reproduced: "TS0 · optimized · review not reviewed"
// rendered as one run of plain text where the species row already uses
// pills for the equivalent facts. Fixed by reusing the species row's own
// pill classes -- asserted here as a POSITIVE check on the pill markup
// itself, not just on the text content (a fixture where the text alone
// matches but the markup regressed back to a plain `<p>` would still pass
// a text-only check).
describe("TransitionStateBrowseRow: pills, not plain text", () => {
    it("renders label+status as one value pill, and review status as its own separate pill", () => {
        renderRow(record())
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(row).toBeTruthy()

        const kindPill = within(row).getByText("TS0 · optimized").closest(".value-pill")
        expect(kindPill).toBeTruthy()
        expect(kindPill).toHaveClass("browse-entry-kind-pill")

        const reviewPill = within(row).getByText("not reviewed").closest(".value-pill")
        expect(reviewPill).toBeTruthy()
        expect(reviewPill).toHaveClass("browse-entry-review")
        expect(reviewPill).toHaveClass("value-pill--muted")

        // Two distinct pills, never one shared box -- same shape the
        // species row's own review-status fix already established.
        expect(kindPill).not.toBe(reviewPill)

        // The awkward literal word "review" glued onto the status is gone.
        expect(within(row).queryByText(/review not reviewed/)).not.toBeInTheDocument()
    })

    it("falls back to 'Unlabeled transition state' inside the pill when no label was deposited", () => {
        renderRow(record({ transition_state: { transition_state_ref: "ts_one", label: null, note: null, review: { status: "not_reviewed" } } }))
        const row = document.querySelector(".ts-browse-row") as HTMLElement
        expect(within(row).getByText("Unlabeled transition state · optimized")).toBeVisible()
    })
})

// Only the equation text used to be clickable; every other element in the
// row (pills, evidence, ref) sat outside any link. Reproduced here by
// checking that content OUTSIDE the old headline -- the pill and the ref
// code -- is now inside the SAME link as the equation.
describe("TransitionStateBrowseRow: the whole row is the click target", () => {
    it("wraps the entire row's content in one link to the transition-state entry, not just the equation", () => {
        renderRow(record())
        // The link has NO aria-label, so its accessible name is its full text
        // content. Pin the pieces an aria-label once silenced: charge and the
        // ref (a re-review found family, charge, spin and the evidence line
        // unannounced). Restoring any aria-label that omits these fails here.
        const link = screen.getByRole("link", { name: /tse_one/ })
        expect(link).toHaveAccessibleName(expect.stringContaining("charge 0"))
        expect(link).toHaveAccessibleName(expect.stringContaining("tse_one"))
        expect(link).not.toHaveAttribute("aria-label")
        expect(link).toHaveAttribute("href", "/transition-state-entries/tse_one")

        // Content far from the headline -- the review pill and the stable
        // ref code -- are inside the SAME link element, not sitting
        // outside it as dead space.
        expect(within(link).getByText("not reviewed")).toBeVisible()
        expect(within(link).getByText("tse_one")).toBeVisible()
    })

    it("does not change WHERE the row links -- exactly transition_state_entry.transition_state_entry_ref, not the reaction ref", () => {
        renderRow(record({
            transition_state_entry: {
                transition_state_entry_ref: "tse_specific",
                charge: 0,
                multiplicity: 2,
                status: "optimized",
                unmapped_smiles: null,
                review: { status: "not_reviewed" },
            },
            reaction: { reaction_ref: "rxn_specific", reaction_entry_ref: "rxe_x", equation: "X <=> Y", reversible: null, family: null },
        }))
        expect(screen.getByRole("link", { name: /tse_specific/ }))
            .toHaveAttribute("href", "/transition-state-entries/tse_specific")
    })

    it("keeps the label, status and ref inside the link, so they are announced as part of its content", () => {
        renderRow(record())
        const link = screen.getByRole("link", { name: /tse_one/ })
        // The visible pill text and ref are unaffected by the aria-label --
        // both are still real, visible text content inside the link.
        expect(within(link).getByText("TS0 · optimized")).toBeVisible()
        expect(within(link).getByText("tse_one")).toBeVisible()
    })

    it("renders no link at all when the archive gave no transition-state entry ref -- the row is inert, matching the prior fallback", () => {
        // The schema marks transition_state_entry_ref as always-present, but
        // the component still guards against a falsy ref defensively -- this
        // exercises that guard the same way an untyped/malformed payload would.
        renderRow(record({
            transition_state_entry: {
                transition_state_entry_ref: null as unknown as string,
                charge: 0,
                multiplicity: 2,
                status: "optimized",
                unmapped_smiles: null,
                review: { status: "not_reviewed" },
            },
            reaction: { reaction_ref: "rxn_one", reaction_entry_ref: "rxe_one", equation: "Z <=> W", reversible: null, family: null },
        }))
        expect(screen.queryByRole("link")).not.toBeInTheDocument()
        expect(screen.getByText("Z <=> W")).toBeVisible()
    })
})

describe("TransitionStateBrowseRow: unchanged behaviour", () => {
    it("still renders the family, charge/spin, evidence summary and 'Equation not recorded' fallback", () => {
        renderRow(record({ reaction: { reaction_ref: null, reaction_entry_ref: null, equation: null, reversible: null, family: null } }))
        expect(screen.getByText("Equation not recorded")).toBeVisible()
        expect(screen.getByText(/family not recorded/)).toBeVisible()
        expect(screen.getByText(/charge 0 · spin doublet/)).toBeVisible()
        expect(screen.getByText(/Evidence: opt · freq · sp · irc \(4 calculations\)/)).toBeVisible()
    })
})
