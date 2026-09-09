import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
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
function handleEverything(overrides: { stateEnergies?: object[]; channelBarriers?: object[]; thermoCounts?: Record<string, number> } = {}) {
    handleNetworkDetail(networkDetailFixture())
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

    it("renders the network diagram section (PR 3) and the k(T,P) section (PR 4)", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "hydrazine" })
        expect(screen.getByRole("heading", { name: "Network diagram" })).toBeVisible()
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

describe("NetworkEntryPage -- network diagram (PR 3)", () => {
    it("renders one node link per state and one edge link per channel, matching evidence_summary", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "hydrazine" })
        expect(container.querySelectorAll(".net-node-link")).toHaveLength(2)
        expect(container.querySelectorAll(".net-edge-link")).toHaveLength(1)
    })

    it("renders every visible node label from composition.state_label, never the composition_hash", async () => {
        handleEverything()
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
