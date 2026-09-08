import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import type { ReactionEntrySpeciesParticipant, ReactionTransitionStateInFull } from "../api/reactionEntryApi"
import { ReactionTransitionStatesSection } from "./ReactionTransitionStatesSection"

afterEach(cleanup)

/**
 * Owner complaint (2026-09): "in reaction we see Optimisation but no idea
 * what the optimisation is of? and its not clickable link to the calc?
 * also i think the graph needs the reactants and products". This file
 * exercises the three fixes in `ReactionTransitionStatesSection.tsx`'s own
 * docstring: the centre node becomes a real link (`centreLinked`), a
 * subject caption names what the centre node's optimisation is OF, and an
 * (optional, since `species` is itself a nullable §3A field) reaction
 * equation caption gives the graph its chemistry context -- plus the
 * honest note on what the graph does/doesn't show.
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

const REACTANTS: ReactionEntrySpeciesParticipant[] = [
    { species_entry_ref: "spe_water", smiles: "O", formula: "H2O", stoichiometry: 1, participant_index: 0, review: { status: "not_reviewed" } },
]
const PRODUCTS: ReactionEntrySpeciesParticipant[] = [
    { species_entry_ref: "spe_oh", smiles: "[OH]", formula: "HO", stoichiometry: 1, participant_index: 0, review: { status: "not_reviewed" } },
]

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
        const link = within(centreNode).getByRole("link")
        expect(link).toHaveAttribute("href", "/calculations/calc_opt1")
    })
})

describe("ReactionTransitionStatesSection — subject caption", () => {
    it("names the optimisation as this TS entry's own, linking to the TS entry (mutation table item (c))", () => {
        renderSection()
        const caption = screen.getByTestId("dep-graph-subject-caption")
        expect(caption).toHaveTextContent("geometry optimisation")
        const link = within(caption).getByRole("link", { name: "tse_test1" })
        expect(link).toHaveAttribute("href", "/transition-state-entries/tse_test1")
    })

    it("always renders the honest note on what the graph does/doesn't show", () => {
        renderSection()
        const note = screen.getByTestId("dep-graph-context-note")
        expect(note).toHaveTextContent(/does not record which of the reaction's species the IRC/)
    })
})

describe("ReactionTransitionStatesSection — reaction equation caption", () => {
    it("renders the equation, with participant links, when reactants/products are provided (mutation table item (d))", () => {
        renderSection({ reactants: REACTANTS, products: PRODUCTS, reversible: true })
        const caption = screen.getByTestId("dep-graph-equation-caption")
        const reactantLink = within(caption).getByRole("link", { name: "H2O" })
        expect(reactantLink).toHaveAttribute("href", "/species-entries/spe_water")
        const productLink = within(caption).getByRole("link", { name: "HO" })
        expect(productLink).toHaveAttribute("href", "/species-entries/spe_oh")
    })

    it("omits the equation caption entirely (no broken/empty equation) when reactants/products are not provided", () => {
        renderSection()
        expect(screen.queryByTestId("dep-graph-equation-caption")).toBeNull()
        // The subject caption and honest note still render regardless.
        expect(screen.getByTestId("dep-graph-subject-caption")).toBeInTheDocument()
    })
})

describe("ReactionTransitionStatesSection — no dependency edges", () => {
    it("renders neither the context block nor the graph when the TS entry has no dependencies", () => {
        renderSection({ transitionStates: [TS_NO_DEPENDENCIES], reactants: REACTANTS, products: PRODUCTS })
        expect(screen.queryByTestId("dep-graph-equation-caption")).toBeNull()
        expect(screen.queryByTestId("dep-graph-subject-caption")).toBeNull()
        expect(screen.getByText("No dependency edges are served for this TS entry's calculations.")).toBeInTheDocument()
    })
})
