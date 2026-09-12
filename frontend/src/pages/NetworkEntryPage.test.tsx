import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
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

// ---------------------------------------------------------------------------
// k(T,P) evaluate -- POST /networks/{ref}/kinetics/evaluate (PR 4)
// ---------------------------------------------------------------------------

function ktpPoint(temperatureK: number, pressureBar: number, k: number, inRange = true) {
    return { temperature_k: temperatureK, pressure_bar: pressureBar, k, in_range: inRange }
}

function ktpFitFixture(overrides: Record<string, unknown> = {}) {
    return {
        network_kinetics_ref: "nk_test1",
        channel_key: "channel_1",
        channel_kind: "isomerization",
        source_state_composition_hash: "hash_bim",
        sink_state_composition_hash: "hash_well",
        network_solve_ref: "nsolve_test1",
        model_kind: "chebyshev",
        k_units: "per_s",
        tmin_k: 300,
        tmax_k: 2000,
        pmin_bar: 0.01,
        pmax_bar: 100,
        points: [],
        ...overrides,
    }
}

/** Two fits (Chebyshev + PLOG) for `channel_1`, one point per requested
 * `(T, P)` combination -- built from whatever grid the component actually
 * requested, so this fixture works regardless of the exact T/P counts
 * `networkKtpChartLayout.ts` picks. */
function defaultKtpFits(temperaturesK: number[], pressuresBar: number[]) {
    const gridPoints = (kAtOneKelvinOneBar: number) => temperaturesK.flatMap((t) => pressuresBar.map((p) => ktpPoint(t, p, kAtOneKelvinOneBar * t)))
    return [
        ktpFitFixture({ network_kinetics_ref: "nk_cheb1", model_kind: "chebyshev", points: gridPoints(2) }),
        ktpFitFixture({ network_kinetics_ref: "nk_plog1", model_kind: "plog", points: gridPoints(3) }),
    ]
}

/** Call count is tracked on the object itself (not a module-level `let`)
 * so each test's own counter is trivially inspectable without resetting
 * shared state between tests -- `server.resetHandlers()` in `afterEach`
 * already drops the handler (and its counter) between tests. */
function handleKtpEvaluate(buildFits: (temperaturesK: number[], pressuresBar: number[]) => object[] = defaultKtpFits) {
    const calls: { temperature_k: number[]; pressure_bar: number[] }[] = []
    server.use(http.post(`/api/v1/scientific/networks/${NETWORK_REF}/kinetics/evaluate`, async ({ request }) => {
        const body = await request.json() as { temperature_k: number[]; pressure_bar: number[] }
        calls.push(body)
        return HttpResponse.json({ network_ref: NETWORK_REF, fits: buildFits(body.temperature_k, body.pressure_bar) })
    }))
    return calls
}

/** Every fetch this page's default fixture needs, wired at once. */
function handleEverything(overrides: {
    stateEnergies?: object[]
    channelBarriers?: object[]
    thermoCounts?: Record<string, number>
    /** Passed straight through to `networkDetailFixture`'s own `overrides`
     *  -- e.g. `{ network: { review: { status: "under_review", note: "…" } } }`
     *  to exercise the review-note wiring against the network's own review
     *  record. */
    record?: Record<string, unknown>
} = {}) {
    handleNetworkDetail(networkDetailFixture(overrides.record))
    handleSolveDetail("nsolve_test1", overrides.stateEnergies ?? [], overrides.channelBarriers ?? [])
    handleThermo(overrides.thermoCounts ?? { spe_well: 0, spe_h2: 0 })
    handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
    handleKtpEvaluate()
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

    it("renders the potential-energy surface section and the k(T,P) section", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "hydrazine" })
        expect(screen.getByRole("heading", { name: "Potential-energy surface" })).toBeVisible()
        expect(await screen.findByRole("heading", { name: "k(T,P)" })).toBeVisible()
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

// ---------------------------------------------------------------------------
// Review note -- the curator's stated reason for a status (`RecordReview.note`).
// The live archive's one real note is on a `network_solve` record (a
// hydrazine network, `under_review`): barriers are sound, bond-fission
// asymptotes are ~27.4 kJ/mol low against the ATcT. See `components/
// ReviewNote.tsx`.
// ---------------------------------------------------------------------------

const NETWORK_REVIEW_NOTE = "The overall network topology and rate ordering are consistent with prior mechanisms."
const SOLVE_REVIEW_NOTE =
    "Barriers are sound; bond-fission asymptotes are systematically low. " +
    "N2H4 -> 2 NH2 is 27.4 kJ/mol below the Active Thermochemical Tables value."

function networkWithReview(review: { status: string; note?: string | null }) {
    return {
        network_ref: NETWORK_REF, name: "hydrazine", description: "A dual-form pressure-dependent network deposit.",
        solve_temperature_min_k: 300, solve_temperature_max_k: 2000, solve_pressure_min_bar: 0.01, solve_pressure_max_bar: 100,
        review,
    }
}

function solvesWithReview(review: { status: string; note?: string | null }) {
    return [{
        network_solve_ref: "nsolve_test1", kind: "computed", me_method: "modified strong collision", interpolation_model: "chebyshev",
        tmin_k: 300, tmax_k: 2000, pmin_bar: 0.01, pmax_bar: 100, review,
    }]
}

describe("NetworkEntryPage -- review note", () => {
    // MUTATION TABLE (required, brief item 3): a record with no note must
    // render an empty container nowhere -- there must be no review-note
    // disclosure at all when neither the network's nor the solve's review
    // carries one. The default fixture's reviews are both `{ status:
    // "not_reviewed" }`, with no `note` key at all.
    it("renders no review-note disclosure when neither review carries a note", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "hydrazine" })
        expect(screen.queryByText("Network review note", { exact: false })).toBeNull()
        expect(screen.queryByText("Network solve review note", { exact: false })).toBeNull()
    })

    // MUTATION TABLE (required, brief item 2): if the page rendered the
    // review status pill but dropped the note, this goes red.
    it("shows the solve's review note -- the real one on the live archive -- once its disclosure is opened", async () => {
        const user = userEvent.setup()
        handleEverything({ record: { solves: solvesWithReview({ status: "under_review", note: SOLVE_REVIEW_NOTE }) } })
        page()
        await screen.findByRole("heading", { name: "hydrazine" })
        const summary = await screen.findByText("Network solve review note", { exact: false })
        expect(screen.queryByText(SOLVE_REVIEW_NOTE)).not.toBeVisible()
        await user.click(summary)
        expect(screen.getByText(SOLVE_REVIEW_NOTE)).toBeVisible()
    })

    it("shows the network's own review note independently of the solve's", async () => {
        const user = userEvent.setup()
        handleEverything({ record: { network: networkWithReview({ status: "approved", note: NETWORK_REVIEW_NOTE } ) } })
        page()
        await screen.findByRole("heading", { name: "hydrazine" })
        // Present regardless of status -- this fixture's network review is
        // `approved`, not `under_review`, pinning the "not gated on status"
        // invariant on the page itself, not just the component in isolation.
        const summary = await screen.findByText("Network review note", { exact: false })
        await user.click(summary)
        expect(screen.getByText(NETWORK_REVIEW_NOTE)).toBeVisible()
        // The solve's own review still carries no note in this fixture.
        expect(screen.queryByText("Network solve review note", { exact: false })).toBeNull()
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
        // guard checks). `findAllByText`, not `findByText`: PR 3's network
        // diagram section (below) legitimately renders the SAME
        // `composition.state_label` a second time in its own accessible
        // states table -- two honest, independent renderings of the same
        // safe label, not a duplicate-content bug.
        const matches = await screen.findAllByText("NN + [H][H]")
        expect(matches.length).toBeGreaterThanOrEqual(1)
        for (const match of matches) expect(match).toBeInTheDocument()
        expect(container.textContent ?? "").not.toContain("hash_well")
        expect(container.textContent ?? "").not.toContain("hash_bim")
    })
})

describe("NetworkEntryPage -- channel_key never renders as a bare label", () => {
    it("channel_key (channel_1) appears only inside code.data cells, never as a heading/dt/caption or an SVG <text>", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "hydrazine" })
        const channelCells = Array.from(container.querySelectorAll('td[data-label="Channel"]'))
        expect(channelCells.some((cell) => cell.textContent === "channel_1")).toBe(true)
        for (const cell of channelCells) {
            if (cell.textContent === "channel_1") expect(cell.querySelector("code.data")!.textContent).toBe("channel_1")
        }
        // Never inside a heading, a <dt>, a <caption>, or an SVG <text>
        // anywhere on the page (invariant 1 of PR 3's diagram brief --
        // grepping the rendered DOM, not just trusting the component).
        for (const el of Array.from(container.querySelectorAll("h1, h2, h3, dt, caption, svg text"))) {
            expect(el.textContent).not.toContain("channel_1")
        }
    })
})

describe("NetworkEntryPage -- potential-energy surface", () => {
    it("renders one level per state with a deposited energy, and one saddle point for the channel with a deposited barrier", async () => {
        handleEverything({
            stateEnergies: [
                { state_composition_hash: "hash_well", energy_kj_mol: 0, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
                { state_composition_hash: "hash_bim", energy_kj_mol: 50, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
            ],
            channelBarriers: [
                { channel_key: "channel_1", reaction_entry_ref: "rxe_test1", transition_state_entry_ref: "tse_test1", forward_barrier_kj_mol: 20, reverse_barrier_kj_mol: 70, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
            ],
        })
        const { container } = page()
        await screen.findByRole("heading", { name: "hydrazine" })
        expect(container.querySelectorAll(".net-pes-level-link")).toHaveLength(2)
        expect(container.querySelectorAll(".net-pes-saddle-link")).toHaveLength(1)
    })

    it("renders every visible level label from composition.state_label, never the composition_hash", async () => {
        handleEverything({
            stateEnergies: [
                { state_composition_hash: "hash_well", energy_kj_mol: 0, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
                { state_composition_hash: "hash_bim", energy_kj_mol: 50, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
            ],
        })
        const { container } = page()
        await screen.findByRole("heading", { name: "hydrazine" })
        const svgTexts = Array.from(container.querySelectorAll("svg text")).map((t) => t.textContent)
        expect(svgTexts).toContain("NN")
        // The raw hash legitimately appears in a `data-composition-hash`
        // attribute (a programmatic join key, invisible to a reader) --
        // this checks rendered TEXT, the same distinction the existing
        // composition_hash guard above draws with `container.textContent`.
        expect(container.textContent ?? "").not.toContain("hash_well")
        expect(container.textContent ?? "").not.toContain("hash_bim")
    })

    it("the accessible states/channels tables always render, alongside the SVG", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "hydrazine" })
        expect(screen.getByRole("table", { name: "States in this network" })).toBeInTheDocument()
        expect(screen.getByRole("table", { name: "Channels in this network" })).toBeInTheDocument()
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
        handleKtpEvaluate()
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
        handleKtpEvaluate()
        page()
        const link = await screen.findByRole("link", { name: "rxe_test1" })
        expect(link).toHaveAttribute("href", "/reaction-entries/rxe_test1")
    })

    it("the transition state entry ref links to /transition-state-entries/:ref", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate()
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
        handleKtpEvaluate()
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
        handleKtpEvaluate()
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
        handleKtpEvaluate()
        page()
        expect(await screen.findByRole("heading", { name: "hydrazine" })).toBeVisible()
        expect(await screen.findByText("NN <=> [H][H] + N=N")).toBeInTheDocument()
    })

    it("says the energies were unreadable rather than rendering as though none exist", async () => {
        handleNetworkDetail(networkDetailFixture())
        malformedSolve()
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate()
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

describe("NetworkEntryPage -- k(T,P) chart (PR 4)", () => {
    it("renders BOTH fits for the default channel -- a Chebyshev and a PLOG series, never collapsed to one", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        // `nk_cheb1` and `nk_plog1` are `defaultKtpFits`' two fits for
        // `channel_1` -- both must render as their own line group.
        await waitFor(() => {
            expect(container.querySelectorAll('[data-testid="ktp-line-nk_cheb1"]').length).toBeGreaterThan(0)
            expect(container.querySelectorAll('[data-testid="ktp-line-nk_plog1"]').length).toBeGreaterThan(0)
        })
        // Distinguishable, not just present: a chebyshev and a plog series
        // on the SAME channel must not share a stroke colour.
        const chebyshevLine = container.querySelector('[data-testid="ktp-line-nk_cheb1"] polyline')
        const plogLine = container.querySelector('[data-testid="ktp-line-nk_plog1"] polyline')
        expect(chebyshevLine).not.toBeNull()
        expect(plogLine).not.toBeNull()
        expect(chebyshevLine!.getAttribute("stroke")).not.toBe(plogLine!.getAttribute("stroke"))
    })

    it("issues exactly ONE POST to the batch evaluate endpoint per page load -- not one per channel, not one per fit", async () => {
        // TWO channels carrying kinetics, not one -- a per-channel-loop
        // regression (call the endpoint once per channel instead of once
        // for the whole page) is indistinguishable from the correct
        // behaviour on a one-channel fixture, since both produce exactly
        // one call. This is the fixture that actually exercises the
        // distinction (confirmed via mutation -- see this PR's report).
        handleNetworkDetail(networkDetailFixture({
            channels: [
                {
                    channel_key: "channel_1", kind: "isomerization", mechanism: "elementary",
                    source_state_composition_hash: "hash_bim", sink_state_composition_hash: "hash_well", has_kinetics: true,
                    microreactions: [{ reaction_entry_ref: "rxe_test1", transition_state_entry_ref: "tse_test1", path_kind: "saddle_point" }],
                },
                {
                    channel_key: "channel_2", kind: "association", mechanism: "elementary",
                    source_state_composition_hash: "hash_well", sink_state_composition_hash: "hash_bim", has_kinetics: true,
                    microreactions: [],
                },
            ],
        }))
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        const calls = handleKtpEvaluate()
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_cheb1"]').length).toBeGreaterThan(0))
        expect(calls.length).toBe(1)

        // Client-side interaction -- toggling which channel is shown, and
        // switching the shared pressure selector -- must never fire a
        // second request. The batch endpoint already returned every
        // channel's every fit in the one call above; selection is a pure
        // client-side filter over that one response.
        const pressureSelect = screen.getByLabelText("Pressure") as HTMLSelectElement
        fireEvent.change(pressureSelect, { target: { value: pressureSelect.options[pressureSelect.options.length - 1].value } })
        expect(calls.length).toBe(1)
    })

    it("a point outside a fit's own stated validity range renders dashed, never presented as interpolated", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate((temperaturesK, pressuresBar) => [
            ktpFitFixture({
                network_kinetics_ref: "nk_cheb1",
                model_kind: "chebyshev",
                points: temperaturesK.flatMap((t, index) => pressuresBar.map((p) => ktpPoint(t, p, 2 * t, index < temperaturesK.length - 2))),
            }),
            ktpFitFixture({
                network_kinetics_ref: "nk_plog1",
                model_kind: "plog",
                points: temperaturesK.flatMap((t) => pressuresBar.map((p) => ktpPoint(t, p, 3 * t))),
            }),
        ])
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        // Checks the ACTUAL rendered `stroke-dasharray`, not just the
        // `data-in-range` bookkeeping attribute alongside it -- a mutation
        // that stops applying the dasharray while leaving `data-in-range`
        // untouched must still fail this test.
        await waitFor(() => {
            const dashed = container.querySelectorAll('[data-testid="ktp-line-nk_cheb1"] polyline[stroke-dasharray="4 3"]')
            expect(dashed.length).toBeGreaterThan(0)
        })
        const chebyshevSolid = container.querySelectorAll('[data-testid="ktp-line-nk_cheb1"] polyline:not([stroke-dasharray])')
        expect(chebyshevSolid.length).toBeGreaterThan(0)
        const plogDashed = container.querySelectorAll('[data-testid="ktp-line-nk_plog1"] polyline[stroke-dasharray="4 3"]')
        expect(plogDashed.length).toBe(0)
    })

    it("channel_key never appears as visible text in the k(T,P) section -- chemistry labels the channel instead", async () => {
        handleEverything()
        const { container } = page()
        const heading = await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_cheb1"]').length).toBeGreaterThan(0))
        const section = heading.closest("section")!
        expect(section.textContent ?? "").not.toContain("channel_1")
        // Confirms the section actually rendered channel-scoped content
        // (so the assertion above is not vacuously true on empty markup)
        // -- `channel_key` is present as a `data-*` attribute, per
        // invariant 4, just never as rendered text.
        expect(section.querySelector('[data-channel-key="channel_1"]')).not.toBeNull()
    })

    it("degrades honestly when no channel on this network carries kinetics -- no empty chart, no request fired", async () => {
        handleNetworkDetail(networkDetailFixture({
            evidence_summary: {
                species_count: 2, reaction_count: 1, state_count: 2, channel_count: 1, solve_count: 1,
                kinetics_count: 0, source_calculation_count: 4, has_chebyshev: false, has_plog: false, has_point_kinetics: false,
            },
            channels: [{
                channel_key: "channel_1", kind: "isomerization", mechanism: "elementary",
                source_state_composition_hash: "hash_bim", sink_state_composition_hash: "hash_well", has_kinetics: false,
                microreactions: [],
            }],
        }))
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        // Deliberately NOT calling handleKtpEvaluate() -- MSW's
        // `onUnhandledRequest: "error"` fails this test if the component
        // fires the batch request anyway when no channel has kinetics.
        page()
        const heading = await screen.findByRole("heading", { name: "k(T,P)" })
        const section = heading.closest("section")!
        await waitFor(() => expect(section.textContent ?? "").toContain("No k(T,P) fits are deposited on this network."))
    })

    it("degrades honestly when the solve carries no temperature/pressure range -- no request fired", async () => {
        handleNetworkDetail(networkDetailFixture({
            network: {
                network_ref: NETWORK_REF, name: "hydrazine", description: null,
                solve_temperature_min_k: null, solve_temperature_max_k: null, solve_pressure_min_bar: null, solve_pressure_max_bar: null,
                review: { status: "not_reviewed" },
            },
        }))
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        // Same "no unmocked request" guard as above.
        page()
        const heading = await screen.findByRole("heading", { name: "k(T,P)" })
        const section = heading.closest("section")!
        await waitFor(() => expect(section.textContent ?? "").toContain("does not record a temperature/pressure range"))
    })
})

// ---------------------------------------------------------------------------
// k(T,P) x-axis mode (owner: "And why can't I change temp to 1/temp" -- the
// Arrhenius chart already has this control, this chart did not). Mirrors
// `ArrheniusChart.test.tsx`'s own "the x-axis mode control" describe block,
// same fixture-independent assertions: default mode, switching re-projects
// and reverses, switching back restores, and the axis title follows the
// mode. `networkDetailFixture()`'s own solve range (300 K .. 2000 K) is the
// grid `buildKtpRequestGrid` samples, so the plotted line's own first/last
// points are exactly that range's endpoints (`evenTicks` samples inclusive
// of both ends) -- pinned against directly, not assumed.
// ---------------------------------------------------------------------------

describe("NetworkEntryPage -- k(T,P) chart x-axis mode (temperature vs 1000/T)", () => {
    it("defaults to Temperature (K) -- today's view, unchanged", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_cheb1"]').length).toBeGreaterThan(0))

        const select = screen.getByRole("combobox", { name: "X-axis" }) as HTMLSelectElement
        expect(select.value).toBe("temperature")
        const title = container.querySelector(".arrhenius-chart-axis-title--x")
        expect(title?.textContent).toBe("Temperature (K)")
        const svg = screen.getByRole("img", { name: /k\(T,P\) evaluated/ })
        expect(svg.getAttribute("aria-label")).toMatch(/versus temperature in kelvin/)
    })

    // MUTATION TARGET (required): x mapping stays linear in T while the
    // mode says inverse -- pinned against hand-computed 1000/300 and
    // 1000/2000, the fixture's own T range endpoints. Also covers the
    // required "axis direction not reversed" target: the 2000 K point (last
    // in temperature order) must land at a SMALLER pixel x than the 300 K
    // point once the axis reverses.
    it("switching to 1000/T re-projects every plotted point and reverses the axis, high temperature at the left", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_cheb1"]').length).toBeGreaterThan(0))

        const line = container.querySelector('[data-testid="ktp-line-nk_cheb1"] polyline:not([stroke-dasharray])')!
        const pointsBefore = line.getAttribute("points")!.split(" ").map((pair) => pair.split(",").map(Number))
        const [firstXBefore] = pointsBefore[0] // 300 K
        const [lastXBefore] = pointsBefore[pointsBefore.length - 1] // 2000 K
        expect(firstXBefore).toBeLessThan(lastXBefore) // today's view: low T on the left

        fireEvent.change(screen.getByRole("combobox", { name: "X-axis" }), { target: { value: "inverse_temperature" } })

        const title = container.querySelector(".arrhenius-chart-axis-title--x")
        expect(title?.textContent).toBe("1000 / T (K⁻¹)")
        const svg = screen.getByRole("img", { name: /k\(T,P\) evaluated/ })
        expect(svg.getAttribute("aria-label")).toMatch(/1000 divided by temperature/)
        expect(svg.getAttribute("aria-label")).toMatch(/high temperature at the left/)

        const lineAfter = container.querySelector('[data-testid="ktp-line-nk_cheb1"] polyline:not([stroke-dasharray])')!
        const pointsAfter = lineAfter.getAttribute("points")!.split(" ").map((pair) => pair.split(",").map(Number))
        const [firstXAfter] = pointsAfter[0] // still 300 K, now plotted at 1000/300
        const [lastXAfter] = pointsAfter[pointsAfter.length - 1] // still 2000 K, now plotted at 1000/2000

        // The two x pixel positions actually changed -- proves the mapping
        // is not still plain T (the required "x mapping stays linear"
        // mutation target: a regression that keeps plotting raw T while
        // merely relabeling the axis would leave these two values
        // unchanged from `pointsBefore`).
        expect(firstXAfter).not.toBeCloseTo(firstXBefore, 0)
        expect(lastXAfter).not.toBeCloseTo(lastXBefore, 0)
        // And the axis genuinely reversed: 2000 K (1000/2000, the SMALLER
        // transformed value) is now on the left.
        expect(lastXAfter).toBeLessThan(firstXAfter)
    })

    it("switching back to Temperature (K) restores the un-reversed axis", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_cheb1"]').length).toBeGreaterThan(0))

        const select = screen.getByRole("combobox", { name: "X-axis" })
        fireEvent.change(select, { target: { value: "inverse_temperature" } })
        fireEvent.change(select, { target: { value: "temperature" } })

        const title = container.querySelector(".arrhenius-chart-axis-title--x")
        expect(title?.textContent).toBe("Temperature (K)")
        const line = container.querySelector('[data-testid="ktp-line-nk_cheb1"] polyline:not([stroke-dasharray])')!
        const points = line.getAttribute("points")!.split(" ").map((pair) => pair.split(",").map(Number))
        const [firstX] = points[0]
        const [lastX] = points[points.length - 1]
        expect(firstX).toBeLessThan(lastX) // 300 K back on the left
    })

    it("is ONE chart-wide control, not one per family panel", async () => {
        handleNetworkDetail(stackedFamilyNetworkFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate(stackedFamilyKtpFits)
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        // The channel fieldset only renders once the batch evaluate fetch
        // resolves -- wait for the default channel's own line before
        // selecting more (same ordering the stacked-family describe block
        // below this one already relies on).
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_iso_cheb"]').length).toBeGreaterThan(0))

        // Select the two extra channels so both unit families (per_s and
        // cm3_mol_s) end up with at least one panel each.
        selectChannel(container, "channel_2")
        selectChannel(container, "channel_3")
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_iso_cheb"]').length).toBeGreaterThan(0))
        await waitFor(() => expect(container.querySelectorAll('.arrhenius-chart-panel-wrap').length).toBeGreaterThan(1))

        expect(screen.getAllByRole("combobox", { name: "X-axis" })).toHaveLength(1)

        // The single control still governs every panel: switching it moves
        // every panel's own axis title, not just one.
        fireEvent.change(screen.getByRole("combobox", { name: "X-axis" }), { target: { value: "inverse_temperature" } })
        const titles = container.querySelectorAll(".arrhenius-chart-axis-title--x")
        expect(titles.length).toBeGreaterThan(1)
        titles.forEach((title) => expect(title.textContent).toBe("1000 / T (K⁻¹)"))
    })

    // Invariant 8: the k(T) table behind the Disclosure must never disagree
    // with the chart about what is plotted. `ArrheniusChart.tsx`'s own k(T)
    // table always tabulates T in kelvin regardless of its x-axis mode --
    // this table does the same, so switching the chart's axis must not
    // change the table's own T column.
    it("the k(T) table's T column stays in kelvin under 1000/T mode, matching the Arrhenius chart's own table", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_cheb1"]').length).toBeGreaterThan(0))

        // A closed `<details>` still renders its children into the DOM
        // (only visibility is affected) -- `container.querySelector` reads
        // them directly, same as `ArrheniusChart.test.tsx`'s own table
        // assertions, no need to open the disclosure first.
        const firstRowBefore = container.querySelector(".kinetics-k-table tbody tr td[data-label='T (K)']")?.textContent

        fireEvent.change(screen.getByRole("combobox", { name: "X-axis" }), { target: { value: "inverse_temperature" } })
        const firstRowAfter = container.querySelector(".kinetics-k-table tbody tr td[data-label='T (K)']")?.textContent

        expect(firstRowBefore).toBe("300.00")
        expect(firstRowAfter).toBe(firstRowBefore)
    })
})

describe("NetworkEntryPage — the k(T,P) y-axis title sits in the grid track built for it", () => {
    it("is a direct child of .arrhenius-chart-panel, not a sibling before it", async () => {
        // `.arrhenius-chart-axis-title--y` is placed with `grid-column: 1`,
        // so it only lands beside the axis when its parent is the grid.
        // Shipped as a sibling BEFORE the panel, it fell into normal flow and
        // rendered the rotated title as a vertical run of characters floating
        // above the plot. jsdom computes no layout, so the structural parent
        // relationship is the testable part of that defect.
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        const title = await waitFor(() => {
            const el = container.querySelector(".arrhenius-chart-axis-title--y")
            expect(el).not.toBeNull()
            return el!
        })
        expect(title.parentElement).not.toBeNull()
        // `classList.contains`, NOT `className.toContain`: the wrapper one
        // level out is `arrhenius-chart-panel-wrap`, whose name CONTAINS
        // "arrhenius-chart-panel" as a substring, so a substring assertion
        // passes against the exact broken layout it is meant to catch.
        expect(title.parentElement!.classList.contains("arrhenius-chart-panel")).toBe(true)
    })
})

// ---------------------------------------------------------------------------
// k(T,P) stacked-panel / unit-control follow-up (owner: "shouldn't they all
// stack on one graph... why is there no control for changing the y axis").
// A network with THREE channels across the two families actually observed
// on the live archive: `channel_1` isomerization/`per_s`, `channel_2`
// association/`cm3_mol_s`, `channel_3` exchange/`cm3_mol_s` -- exercises
// both "same family stacks" and "different families never mix" at once.
// ---------------------------------------------------------------------------

function stackedFamilyNetworkFixture() {
    return networkDetailFixture({
        channels: [
            {
                channel_key: "channel_1", kind: "isomerization", mechanism: "elementary",
                source_state_composition_hash: "hash_bim", sink_state_composition_hash: "hash_well", has_kinetics: true, microreactions: [],
            },
            {
                channel_key: "channel_2", kind: "association", mechanism: "elementary",
                source_state_composition_hash: "hash_well", sink_state_composition_hash: "hash_bim", has_kinetics: true, microreactions: [],
            },
            {
                channel_key: "channel_3", kind: "exchange", mechanism: "elementary",
                source_state_composition_hash: "hash_bim", sink_state_composition_hash: "hash_well", has_kinetics: true, microreactions: [],
            },
        ],
    })
}

function stackedFamilyKtpFits(temperaturesK: number[], pressuresBar: number[]) {
    const gridPoints = (kAtOneKelvinOneBar: number) => temperaturesK.flatMap((t) => pressuresBar.map((p) => ktpPoint(t, p, kAtOneKelvinOneBar * t)))
    return [
        ktpFitFixture({
            network_kinetics_ref: "nk_iso_cheb", channel_key: "channel_1", channel_kind: "isomerization",
            model_kind: "chebyshev", k_units: "per_s", points: gridPoints(2),
        }),
        ktpFitFixture({
            network_kinetics_ref: "nk_iso_plog", channel_key: "channel_1", channel_kind: "isomerization",
            model_kind: "plog", k_units: "per_s", points: gridPoints(3),
        }),
        ktpFitFixture({
            network_kinetics_ref: "nk_assoc_cheb", channel_key: "channel_2", channel_kind: "association",
            model_kind: "chebyshev", k_units: "cm3_mol_s", points: gridPoints(5e12),
        }),
        ktpFitFixture({
            network_kinetics_ref: "nk_assoc_plog", channel_key: "channel_2", channel_kind: "association",
            model_kind: "plog", k_units: "cm3_mol_s", points: gridPoints(7e12),
        }),
        ktpFitFixture({
            network_kinetics_ref: "nk_exch_cheb", channel_key: "channel_3", channel_kind: "exchange",
            model_kind: "chebyshev", k_units: "cm3_mol_s", points: gridPoints(11e12),
        }),
        ktpFitFixture({
            network_kinetics_ref: "nk_exch_plog", channel_key: "channel_3", channel_kind: "exchange",
            model_kind: "plog", k_units: "cm3_mol_s", points: gridPoints(13e12),
        }),
    ]
}

/** Selects (checks) a channel's own checkbox in the multi-select fieldset --
 *  every checkbox's `<label>` carries `data-channel-key`, unchanged from PR
 *  4. */
function selectChannel(container: HTMLElement, channelKey: string) {
    const checkbox = container.querySelector(`.network-ktp-channel-option[data-channel-key="${channelKey}"] input`) as HTMLInputElement
    fireEvent.click(checkbox)
}

describe("NetworkEntryPage -- k(T,P) chart stacks selected channels by unit family, never mixing families", () => {
    it("MUTATION TARGET: a per_s channel and a cm3_mol_s channel never share one panel -- two panels, not one", async () => {
        handleNetworkDetail(stackedFamilyNetworkFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate(stackedFamilyKtpFits)
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        // Only channel_1 selected by default -- one panel.
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_iso_cheb"]').length).toBeGreaterThan(0))
        expect(container.querySelectorAll(".arrhenius-chart-panel-wrap")).toHaveLength(1)

        selectChannel(container, "channel_2")
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_assoc_cheb"]').length).toBeGreaterThan(0))

        const wraps = Array.from(container.querySelectorAll(".arrhenius-chart-panel-wrap"))
        expect(wraps).toHaveLength(2)
        // Every wrap holds exactly one of the two families' channel(s) --
        // never both `channel_1` and `channel_2` inside the SAME wrap. A
        // mutation that merges the two into one shared panel (the exact
        // defect this PR's brief warns against) makes this assertion fail:
        // one wrap would then carry both data-testids at once.
        for (const wrap of wraps) {
            const hasIso = wrap.querySelector('[data-testid="ktp-line-nk_iso_cheb"]') !== null
            const hasAssoc = wrap.querySelector('[data-testid="ktp-line-nk_assoc_cheb"]') !== null
            expect(hasIso && hasAssoc).toBe(false)
        }
    })

    it("overlays TWO channels of the SAME family on one shared panel, both distinguishable by colour", async () => {
        handleNetworkDetail(stackedFamilyNetworkFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate(stackedFamilyKtpFits)
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_iso_cheb"]').length).toBeGreaterThan(0))

        selectChannel(container, "channel_2")
        selectChannel(container, "channel_3")
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_exch_cheb"]').length).toBeGreaterThan(0))

        // channel_2 and channel_3 are BOTH cm3_mol_s (association/exchange)
        // -- exactly one shared wrap holds both, not two separate ones.
        const wraps = Array.from(container.querySelectorAll(".arrhenius-chart-panel-wrap"))
        expect(wraps).toHaveLength(2) // channel_1's own per_s panel, plus the shared cm3_mol_s panel
        const sharedWrap = wraps.find((wrap) => wrap.querySelector('[data-testid="ktp-line-nk_assoc_cheb"]') && wrap.querySelector('[data-testid="ktp-line-nk_exch_cheb"]'))
        expect(sharedWrap).not.toBeUndefined()

        // Distinguishable: channel_2's Chebyshev line and channel_3's
        // Chebyshev line share a model kind (so tint/width are IDENTICAL
        // between them) but must still carry different `stroke` colours --
        // that difference is entirely channel-identity colour, proving
        // colour actually varies per channel and not just per model kind.
        const assocLine = sharedWrap!.querySelector('[data-testid="ktp-line-nk_assoc_cheb"] polyline')
        const exchLine = sharedWrap!.querySelector('[data-testid="ktp-line-nk_exch_cheb"] polyline')
        expect(assocLine).not.toBeNull()
        expect(exchLine).not.toBeNull()
        expect(assocLine!.getAttribute("stroke")).not.toBe(exchLine!.getAttribute("stroke"))
    })

    it("familyUnits(per_s) has no interchangeable sibling -- that panel renders NO Y-axis control", async () => {
        handleNetworkDetail(stackedFamilyNetworkFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate(stackedFamilyKtpFits)
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_iso_cheb"]').length).toBeGreaterThan(0))
        // Only channel_1 (per_s) selected -- no Y-axis control anywhere on
        // the page yet (per_s's own family has exactly one member).
        expect(screen.queryByLabelText(/Y-axis/)).toBeNull()
    })

    it("MUTATION TARGET: selecting a different unit converts the PLOTTED VALUES and the axis title together, never one without the other", async () => {
        handleNetworkDetail(stackedFamilyNetworkFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate(stackedFamilyKtpFits)
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_iso_cheb"]').length).toBeGreaterThan(0))

        selectChannel(container, "channel_2")
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_assoc_cheb"]').length).toBeGreaterThan(0))

        // cm3_mol_s's own family (bimolecular) is the one with a real
        // choice: cm3_mol_s / m3_mol_s / cm3_molecule_s.
        const unitSelect = screen.getByLabelText("Y-axis (bimolecular)") as HTMLSelectElement
        const assocWrap = container.querySelector('[data-testid="ktp-line-nk_assoc_cheb"]')!.closest(".arrhenius-chart-panel-wrap")!
        const axisTitleBefore = assocWrap.querySelector(".arrhenius-chart-axis-title--y")!.textContent
        const lineBefore = assocWrap.querySelector('[data-testid="ktp-line-nk_assoc_cheb"] polyline')!.getAttribute("points")
        expect(axisTitleBefore).toBe("log₁₀ [k / cm³ mol⁻¹ s⁻¹]")

        fireEvent.change(unitSelect, { target: { value: "m3_mol_s" } })

        const axisTitleAfter = assocWrap.querySelector(".arrhenius-chart-axis-title--y")!.textContent
        const lineAfter = assocWrap.querySelector('[data-testid="ktp-line-nk_assoc_cheb"] polyline')!.getAttribute("points")
        // The axis title names the NEW unit -- a mutation that converts the
        // plotted values but leaves the title reading the old unit (the
        // exact defect the Arrhenius chart's own owner caught once already)
        // fails this line.
        expect(axisTitleAfter).toBe("log₁₀ [k / m³ mol⁻¹ s⁻¹]")
        expect(axisTitleAfter).not.toBe(axisTitleBefore)
        // The plotted line itself actually moved (m3_mol_s = cm3_mol_s *
        // 1e-6, six full log10 decades down the y-axis) -- a mutation that
        // relabels the axis WITHOUT touching the drawn points fails this
        // line instead.
        expect(lineAfter).not.toBe(lineBefore)

        // The table behind the Disclosure must agree with the chart
        // (invariant 8) -- its caption and the k(T) values it prints both
        // follow the SAME unit selection, not a stale one.
        const caption = assocWrap.querySelector(".kinetics-k-table caption")
        expect(caption).not.toBeNull()
        expect(caption!.textContent ?? "").toContain("m³ mol⁻¹ s⁻¹")
        expect(caption!.textContent ?? "").not.toContain("cm³ mol⁻¹ s⁻¹")
    })

    it("does not collapse a channel's two fits even when overlaid with another channel -- both Chebyshev AND PLOG still render for every selected channel", async () => {
        handleNetworkDetail(stackedFamilyNetworkFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate(stackedFamilyKtpFits)
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_iso_cheb"]').length).toBeGreaterThan(0))

        selectChannel(container, "channel_2")
        selectChannel(container, "channel_3")
        await waitFor(() => expect(container.querySelectorAll('[data-testid="ktp-line-nk_exch_plog"]').length).toBeGreaterThan(0))

        // Every one of the six served fits still renders its own line group.
        for (const ref of ["nk_iso_cheb", "nk_iso_plog", "nk_assoc_cheb", "nk_assoc_plog", "nk_exch_cheb", "nk_exch_plog"]) {
            expect(container.querySelectorAll(`[data-testid="ktp-line-${ref}"]`).length).toBeGreaterThan(0)
        }
    })
})
