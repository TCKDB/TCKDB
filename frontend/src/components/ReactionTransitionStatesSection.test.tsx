import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { ReactionTransitionStateInFull } from "../api/reactionEntryApi"
import { ReactionTransitionStatesSection } from "./ReactionTransitionStatesSection"

afterEach(cleanup)

/**
 * Owner complaint, round 1 (2026-09): "in reaction we see Optimisation but
 * no idea what the optimisation is of? and its not clickable link to the
 * calc?". Round 2, on an earlier version of this fix that answered "what
 * is it" with a caption block ABOVE the graph (the equation, plus a
 * subject sentence): "This does need repeating what reaction since the top
 * of the page says which reaction" -- the equation caption is gone
 * entirely, and the subject now lives ON the centre node itself
 * (`CalculationDependencyGraph.tsx`'s `centreSubject`), not in prose above
 * it. See `ReactionTransitionStatesSection.tsx`'s own docstring for the
 * full history.
 *
 * `TS` has an `ts_irc` slot (17 of 34 live TS entries do not -- see
 * `TS_NO_IRC` below for that case, which the honest note must stay silent
 * on rather than naming an IRC that was never run).
 */

const TS: ReactionTransitionStateInFull = {
    transition_state_ref: "ts_test1",
    transition_state_entry_ref: "tse_test1",
    status: "optimized",
    review: { status: "not_reviewed" },
    evidence_summary: {
        calculation_count: 4, has_opt: true, has_freq: true, has_sp: true, has_irc: true,
        has_path_search: false, has_geometry_validation: false, has_scf_stability: false,
        levels_of_theory: {},
    },
    calculations: {
        ts_opt: { calculation_ref: "calc_opt1", type: "opt" },
        ts_freq: { calculation_ref: "calc_freq1", type: "freq" },
        ts_irc: { calculation_ref: "calc_irc1", type: "irc" },
    },
    dependencies: [
        { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_freq1", role: "freq_on" },
        { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_irc1", role: "irc_start" },
    ],
}

// Same shape as `TS`, but with no `ts_irc` slot at all -- the opt/freq/sp
// -only case the honest note must not name an IRC for.
const TS_NO_IRC: ReactionTransitionStateInFull = {
    ...TS,
    transition_state_entry_ref: "tse_test_no_irc",
    evidence_summary: { ...TS.evidence_summary, has_irc: false },
    calculations: {
        ts_opt: { calculation_ref: "calc_opt1", type: "opt" },
        ts_freq: { calculation_ref: "calc_freq1", type: "freq" },
    },
    dependencies: [
        { parent_calculation_ref: "calc_opt1", child_calculation_ref: "calc_freq1", role: "freq_on" },
    ],
}

const TS_NO_DEPENDENCIES: ReactionTransitionStateInFull = {
    ...TS,
    transition_state_entry_ref: "tse_test2",
    calculations: { ts_opt: { calculation_ref: "calc_opt2", type: "opt" } },
    dependencies: [],
}

function renderSection(props: Partial<Parameters<typeof ReactionTransitionStatesSection>[0]> = {}) {
    return render(
        <MemoryRouter>
            <ReactionTransitionStatesSection
                transitionStates={[TS]}
                calculations={[]}
                {...props}
            />
        </MemoryRouter>,
    )
}

describe("ReactionTransitionStatesSection — centre node link", () => {
    it("links the dependency graph's centre node to /calculations/<ts_opt ref> (mutation table item (a))", () => {
        renderSection()
        const centreNode = screen.getByTestId("dep-node-centre-calc_opt1")
        const calcLink = within(centreNode).getByRole("link", { name: /Optimisation calc_opt1/ })
        expect(calcLink).toHaveAttribute("href", "/calculations/calc_opt1")
    })
})

describe("ReactionTransitionStatesSection — subject lives on the node, not in a caption", () => {
    it("names the optimisation's own subject ON the centre node, linking to the TS entry (mutation table item (a))", () => {
        renderSection()
        const centreNode = screen.getByTestId("dep-node-centre-calc_opt1")
        const subjectLink = within(centreNode).getByTestId("dep-node-subject-calc_opt1")
        expect(subjectLink).toHaveTextContent("Transition state")
        expect(subjectLink).toHaveAttribute("href", "/transition-state-entries/tse_test1")
        expect(subjectLink).toHaveAccessibleName("Transition state tse_test1")
    })

    // Round-2 owner complaint: no caption above the graph should repeat
    // the reaction (the page's own <h1> already states it) or restate the
    // subject a second time in prose now that it lives on the node.
    it("renders no equation caption and no separate subject caption above the graph", () => {
        renderSection()
        expect(screen.queryByTestId("dep-graph-equation-caption")).toBeNull()
        expect(screen.queryByTestId("dep-graph-subject-caption")).toBeNull()
        expect(screen.queryByText(/^Reaction:/)).toBeNull()
    })
})

describe("ReactionTransitionStatesSection — IRC-conditional honest note", () => {
    it("renders the honest note, naming the IRC, when this TS entry has an ts_irc slot", () => {
        renderSection()
        const note = screen.getByTestId("dep-graph-context-note")
        expect(note).toHaveTextContent(/does not show which species the IRC connects/)
        // The narrower claim from review: this VIEW lacks it, not "the
        // archive" -- the evidence can exist elsewhere (the TS entry).
        expect(note.textContent).not.toMatch(/archive does not record/)
    })

    it("does NOT render the honest note for a TS entry with no ts_irc slot -- no IRC was run, so nothing IRC-shaped should be named", () => {
        renderSection({ transitionStates: [TS_NO_IRC] })
        expect(screen.queryByTestId("dep-graph-context-note")).toBeNull()
        // The subject still renders on the node regardless -- only the
        // IRC-specific note is conditional.
        expect(screen.getByTestId("dep-node-subject-calc_opt1")).toBeInTheDocument()
    })
})

describe("ReactionTransitionStatesSection — no dependency edges", () => {
    it("renders neither the IRC note nor the graph when the TS entry has no dependencies", () => {
        renderSection({ transitionStates: [TS_NO_DEPENDENCIES] })
        expect(screen.queryByTestId("dep-graph-context-note")).toBeNull()
        expect(screen.queryByTestId("dep-node-centre-calc_opt2")).toBeNull()
        expect(screen.getByText("No dependency edges are served for this TS entry's calculations.")).toBeInTheDocument()
    })
})

// Owner complaint, round 3 (2026-09): "whats this whole thing about not
// served by /full?" -- `/full` is internal API vocabulary, meaningless to
// a scientist-facing reader. The Energy/Review columns that used to print
// it (`TransitionStateCalculationSlot` never carries either field -- see
// `backend/app/schemas/reads/scientific_provenance.py:227`) are gone
// entirely, not replaced with softer wording for the same non-answer.
describe("ReactionTransitionStatesSection — calculations-by-stage table has no unservable columns", () => {
    it("renders only Stage / Level of theory / Software-workflow / Record -- no Energy or Review column, no /full jargon anywhere (mutation table item (b))", () => {
        renderSection()
        const table = screen.getByRole("table", { name: "Calculations for tse_test1" })
        const headers = within(table).getAllByRole("columnheader").map((h) => h.textContent)
        expect(headers).toEqual(["Stage", "Level of theory", "Software / workflow", "Record"])
        expect(table.textContent).not.toMatch(/\/full/)
        expect(screen.queryByText(/not served by/)).toBeNull()
        expect(screen.queryByText(/structurally absent from this view/)).toBeNull()
    })
})
