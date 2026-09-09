import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import "../design-system.css"
import NetworkEntryPage from "./NetworkEntryPage"

const NETWORK_REF = "net_test1"
const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

function page(ref = NETWORK_REF) {
    return render(
        <MemoryRouter initialEntries={[`/networks/${ref}`]}>
            <Routes>
                <Route path="/networks/:networkRef" element={<NetworkEntryPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

function stateFixture(hash: string, participants: { ref: string; smiles: string }[], label: string) {
    return {
        composition_hash: hash,
        kind: participants.length > 1 ? "bimolecular" : "well",
        label: null,
        participant_count: participants.length,
        composition: {
            participants: participants.map((p) => ({ species_entry_ref: p.ref, species_ref: `sp_${p.ref}`, canonical_smiles: p.smiles, stoichiometry: 1 })),
            participant_count_total: participants.length,
            participants_truncated: false,
            state_label: label,
        },
    }
}

function networkDetailFixture(overrides: Record<string, unknown> = {}) {
    return {
        review_summary: { approved: 0, under_review: 0, not_reviewed: 1, deprecated: 0, rejected: 0, total: 1 },
        record: {
            network: {
                network_ref: NETWORK_REF, name: "hydrazine", description: "A dual-form pressure-dependent network deposit.",
                solve_temperature_min_k: 300, solve_temperature_max_k: 2000, solve_pressure_min_bar: 0.01, solve_pressure_max_bar: 100,
                review: { status: "not_reviewed" },
            },
            software_release: null,
            workflow_tool_release: { workflow_tool_release_ref: "wfr_test1", workflow_tool: "Arkane", version: "3.2.0" },
            literature: null,
            evidence_summary: {
                species_count: 2, reaction_count: 1, state_count: 2, channel_count: 1, solve_count: 1,
                kinetics_count: 2, source_calculation_count: 4, has_chebyshev: true, has_plog: true, has_point_kinetics: false,
            },
            states: [
                stateFixture("hash_well", [{ ref: "spe_well", smiles: "NN" }], "NN"),
                stateFixture("hash_bim", [{ ref: "spe_well", smiles: "NN" }, { ref: "spe_h2", smiles: "[H][H]" }], "NN + [H][H]"),
            ],
            channels: [
                {
                    channel_key: "channel_1", kind: "isomerization", mechanism: "elementary",
                    source_state_composition_hash: "hash_bim", sink_state_composition_hash: "hash_well", has_kinetics: true,
                    microreactions: [{ reaction_entry_ref: "rxe_test1", transition_state_entry_ref: "tse_test1", path_kind: "saddle_point" }],
                },
            ],
            solves: [{
                network_solve_ref: "nsolve_test1", kind: "computed", me_method: "modified strong collision", interpolation_model: "chebyshev",
                tmin_k: 300, tmax_k: 2000, pmin_bar: 0.01, pmax_bar: 100, review: { status: "not_reviewed" },
            }],
            ...overrides,
        },
    }
}

function handleNetworkDetail(payload: object) {
    server.use(http.get(`/api/v1/scientific/networks/${NETWORK_REF}`, () => HttpResponse.json(payload)))
}

function handleSolveDetail(ref: string, stateEnergies: object[] = [], channelBarriers: object[] = []) {
    server.use(http.get(`/api/v1/scientific/network-solves/${ref}`, () => HttpResponse.json({
        record: { network_solve: { network_solve_ref: ref }, state_energies: stateEnergies, channel_barriers: channelBarriers },
    })))
}

function thermoListPayload(entryRef: string, count: number) {
    return {
        species_entry_ref: entryRef,
        review_summary: { approved: 0, under_review: 0, not_reviewed: count, deprecated: 0, rejected: 0, total: count },
        records: Array.from({ length: count }, (_unused, i) => ({
            thermo_ref: `thermo_${entryRef}_${i}`, scientific_origin: "computed", model_kind: "nasa", review: { status: "not_reviewed" },
        })),
        pagination: { offset: 0, limit: 50, returned: count, total: count, post_collapse_total: count },
    }
}

function handleThermo(refs: Record<string, number>) {
    for (const [ref, count] of Object.entries(refs)) {
        server.use(http.get(`/api/v1/scientific/species-entries/${ref}/thermo`, () => HttpResponse.json(thermoListPayload(ref, count))))
    }
}

function handleReactionEntry(ref: string, equation: string, status = "not_reviewed") {
    server.use(http.get(`/api/v1/scientific/reaction-entries/${ref}/full`, () => HttpResponse.json({
        reaction_entry: { reaction_entry_ref: ref, reaction_ref: `rxn_${ref}`, equation, reversible: true, review: { status }, atom_maps: [] },
    })))
}

/** Every fetch this page's default fixture needs, wired at once. */
function handleEverything(overrides: { stateEnergies?: object[]; channelBarriers?: object[]; thermoCounts?: Record<string, number> } = {}) {
    handleNetworkDetail(networkDetailFixture())
    handleSolveDetail("nsolve_test1", overrides.stateEnergies ?? [], overrides.channelBarriers ?? [])
    handleThermo(overrides.thermoCounts ?? { spe_well: 0, spe_h2: 0 })
    handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
}

describe("NetworkEntryPage -- identity, evidence, reactions, review", () => {
    it("renders the identity header, evidence checklist, reactions table and review section", async () => {
        handleEverything()
        page()
        expect(await screen.findByRole("heading", { name: "hydrazine" })).toBeVisible()
        expect(screen.getByText(NETWORK_REF)).toBeVisible()
        expect(screen.getByRole("heading", { name: "Evidence on this network" })).toBeVisible()
        expect(screen.getByRole("heading", { name: "Reactions" })).toBeVisible()
        expect(screen.getByRole("heading", { name: "Review" })).toBeVisible()
        expect(await screen.findByText("NN <=> [H][H] + N=N")).toBeVisible()
    })

    it("does NOT render a diagram or k(T,P) section -- PR 2 leaves those seams for PR 3/PR 4", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "hydrazine" })
        expect(screen.queryByRole("heading", { name: /network diagram/i })).not.toBeInTheDocument()
        expect(screen.queryByRole("heading", { name: "k(T,P)" })).not.toBeInTheDocument()
    })

    it("no rendered copy names an endpoint, an include= fragment, or a bare URL", async () => {
        handleEverything({ stateEnergies: [{ state_composition_hash: "hash_well", energy_kj_mol: 0, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" }] })
        const { container } = page()
        await screen.findByRole("heading", { name: "hydrazine" })
        const text = container.textContent ?? ""
        expect(text).not.toMatch(/GET \/scientific/)
        expect(text).not.toMatch(/include=/)
        expect(text).not.toMatch(/https?:\/\//)
    })
})

describe("NetworkEntryPage -- energy coverage is computed live, never hardcoded", () => {
    it("0 of 2 species entries have thermo -- worded from the served presence, not a literal string", async () => {
        handleEverything({ thermoCounts: { spe_well: 0, spe_h2: 0 } })
        page()
        expect(await screen.findByText(/none of the 2 participant species entries carry a thermo/)).toBeVisible()
    })

    // MUTATION TABLE (a): hardcode the energy-coverage figure instead of
    // computing it (e.g. always render "0 of 2" regardless of the fixture).
    // This test lands a DIFFERENT thermo-presence fixture (1 of 2 species
    // entries covered) and asserts the DIFFERENT resulting sentence -- a
    // hardcoded "none of the 2" would fail this test, not the one above it.
    it("1 of 2 species entries have thermo -- the sentence changes to match, proving it is not a fixed string", async () => {
        handleEverything({ thermoCounts: { spe_well: 1, spe_h2: 0 } })
        page()
        expect(await screen.findByText(/1 of the 2 participant species entries carry a thermo/)).toBeVisible()
        expect(screen.queryByText(/none of the 2 participant species entries carry a thermo/)).not.toBeInTheDocument()
    })

    it("the solve's own electronic-only coverage (states/channels) is also computed live, from state_energies/channel_barriers length", async () => {
        handleEverything({
            stateEnergies: [
                { state_composition_hash: "hash_well", energy_kj_mol: 0, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
                { state_composition_hash: "hash_bim", energy_kj_mol: 123.729, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
            ],
        })
        page()
        expect(await screen.findByText(/for 2 of 2 states, and a forward\/reverse electronic barrier for 0 of 1 channels/)).toBeVisible()
    })
})

describe("NetworkEntryPage -- composition_hash never renders where chemistry belongs", () => {
    it("the solve-internal state-energy table shows composition.state_label, never the raw composition_hash", async () => {
        handleEverything({
            stateEnergies: [
                { state_composition_hash: "hash_well", energy_kj_mol: 0, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
                { state_composition_hash: "hash_bim", energy_kj_mol: 123.729, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
            ],
        })
        const { container } = page()
        await screen.findByRole("heading", { name: "hydrazine" })
        // Open the disclosure (Testing Library renders `<details>` content
        // regardless of `open`, but assert both the label appears and the
        // raw hash never does, anywhere in the DOM).
        // Inside the (collapsed-by-default) "Solve-internal state & channel
        // energies" disclosure -- `toBeInTheDocument`, not `toBeVisible`
        // (jest-dom's `toBeVisible` treats a closed `<details>`'s content as
        // not visible by design; presence, not visibility, is what this
        // guard checks).
        expect(await screen.findByText("NN + [H][H]")).toBeInTheDocument()
        expect(container.textContent ?? "").not.toContain("hash_well")
        expect(container.textContent ?? "").not.toContain("hash_bim")
    })
})

describe("NetworkEntryPage -- channel_key never renders as a bare label", () => {
    it("channel_key (channel_1) appears only inside a code.data cell, in the Reactions table's Channel column", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "hydrazine" })
        const channelCell = container.querySelector('td[data-label="Channel"]')!
        expect(channelCell.textContent).toBe("channel_1")
        const code = channelCell.querySelector("code.data")
        expect(code).not.toBeNull()
        expect(code!.textContent).toBe("channel_1")
        // Never inside a heading, a <dt>, or a <caption> anywhere on the page.
        for (const el of Array.from(container.querySelectorAll("h1, h2, h3, dt, caption"))) {
            expect(el.textContent).not.toContain("channel_1")
        }
    })
})

describe("NetworkEntryPage -- reactions table", () => {
    it("a barrierless channel's Transition state cell reads 'none — barrierless', never a code ref", async () => {
        handleNetworkDetail(networkDetailFixture({
            channels: [{
                channel_key: "channel_5", kind: "association", mechanism: "elementary",
                source_state_composition_hash: "hash_bim", sink_state_composition_hash: "hash_well", has_kinetics: true,
                microreactions: [{ reaction_entry_ref: "rxe_barrierless", transition_state_entry_ref: null, path_kind: "barrierless" }],
            }],
        }))
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_barrierless", "2 [NH2] <=> NN")
        page()
        await screen.findByText("2 [NH2] <=> NN")
        expect(screen.getByText("none — barrierless")).toBeVisible()
    })
})

describe("NetworkEntryPage — every ref that has a record page is a link to it", () => {
    // The page originally rendered these as inert <code>, which is the same
    // dead-end this PR's own ReactionEntryPage change removed in the opposite
    // direction. Each assertion names the route the ref must reach, so a
    // wrong-route regression fails here rather than 404ing in a browser.
    it("the reaction entry ref links to /reaction-entries/:ref", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        page()
        const link = await screen.findByRole("link", { name: "rxe_test1" })
        expect(link).toHaveAttribute("href", "/reaction-entries/rxe_test1")
    })

    it("the transition state entry ref links to /transition-state-entries/:ref", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        page()
        const link = await screen.findByRole("link", { name: "tse_test1" })
        expect(link).toHaveAttribute("href", "/transition-state-entries/tse_test1")
    })
})

describe("NetworkEntryPage — solve-internal source calculations link to the calculation page", () => {
    // These two live inside the collapsed "Solve-internal state & channel
    // energies" disclosure, so they are queried by text and asserted through
    // the closest <a> -- a closed <details> is not role-queryable (see the
    // composition_hash guard above for the same constraint).
    it("a state energy's source calculation ref links to /calculations/:ref", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1", [{
            state_composition_hash: "hash_well",
            energy_kj_mol: 0,
            energy_zero_convention: "lowest_state",
            correction_convention: "electronic_only",
            source_calculation_ref: "calc_state1",
        }])
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        page()
        const cell = await screen.findByText("calc_state1")
        expect(cell.closest("a")).toHaveAttribute("href", "/calculations/calc_state1")
    })

    it("a channel barrier's source calculation ref links to /calculations/:ref", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1", [], [{
            channel_key: "channel_1",
            reaction_entry_ref: "rxe_test1",
            transition_state_entry_ref: "tse_test1",
            forward_barrier_kj_mol: 12.5,
            reverse_barrier_kj_mol: 30.25,
            energy_zero_convention: "lowest_state",
            correction_convention: "electronic_only",
            source_calculation_ref: "calc_barrier1",
        }])
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        page()
        const cell = await screen.findByText("calc_barrier1")
        expect(cell.closest("a")).toHaveAttribute("href", "/calculations/calc_barrier1")
    })
})

describe("NetworkEntryPage — an unreadable solve degrades, it does not blank the page", () => {
    // Before this, a single malformed channel_barrier row rejected the whole
    // load and replaced the page with "Pressure-dependent network data could
    // not be read" -- losing the identity header, the reactions table and the
    // review section, none of which come from the solve request.
    function malformedSolve() {
        server.use(http.get("/api/v1/scientific/network-solves/nsolve_test1", () => HttpResponse.json({
            record: {
                network_solve: { network_solve_ref: "nsolve_test1" },
                state_energies: [],
                // Missing reaction_entry_ref / transition_state_entry_ref /
                // both convention fields, all of which the served contract
                // marks required, so the schema rejects the row.
                channel_barriers: [{ channel_key: "channel_1", forward_barrier_kj_mol: 1, reverse_barrier_kj_mol: 2 }],
            },
        })))
    }

    it("keeps the identity header, the reactions table and the review section", async () => {
        handleNetworkDetail(networkDetailFixture())
        malformedSolve()
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        page()
        expect(await screen.findByRole("heading", { name: "hydrazine" })).toBeVisible()
        expect(await screen.findByText("NN <=> [H][H] + N=N")).toBeInTheDocument()
    })

    it("says the energies were unreadable rather than rendering as though none exist", async () => {
        handleNetworkDetail(networkDetailFixture())
        malformedSolve()
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        const { container } = page()
        await screen.findByRole("heading", { name: "hydrazine" })
        expect(container.textContent ?? "").toContain("could not read")
        expect(container.textContent ?? "").toContain("not a statement that no energies were deposited")
    })

    it("a network whose solve reads fine says no such thing", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "hydrazine" })
        expect(container.textContent ?? "").not.toContain("could not read")
    })
})
