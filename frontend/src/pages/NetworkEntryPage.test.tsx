import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { Announcer } from "../components/Announcer"
import "../design-system.css"
import NetworkEntryPage from "./NetworkEntryPage"
import { channelHandle } from "../domain/publicText"
import { ADVERSARIAL, expectNoAdversarialText, expectNoDepositorText, expectPlanted, plantSentinels } from "../test/depositorSentinel"
import { titleHasKind } from "../test/routeTitleKinds"

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
        pagination: { offset: 0, limit: 50, returned: count, total: count },
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
// k(T,P) evaluate -- POST /networks/{ref}/kinetics/evaluate
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
        expect(await screen.findByRole("heading", { name: "Pressure-dependent network" })).toBeVisible()
        await waitFor(() => expect(titleHasKind(document.title, "/networks/:ref")).toBe(true))
        expect(screen.getByText(NETWORK_REF)).toBeVisible()
        expect(screen.getByRole("heading", { name: "Evidence on this network" })).toBeVisible()
        expect(screen.getByRole("heading", { name: "Reactions" })).toBeVisible()
        expect(screen.getByRole("heading", { name: "Review" })).toBeVisible()
        expect(await screen.findByText("NN <=> [H][H] + N=N")).toBeVisible()
    })

    it("renders the network map section and the k(T,P) section", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        expect(screen.getByRole("heading", { name: "Network map" })).toBeVisible()
        expect(await screen.findByRole("heading", { name: "k(T,P)" })).toBeVisible()
    })

    it("no rendered copy names an endpoint, an include= fragment, or a bare URL", async () => {
        handleEverything({ stateEnergies: [{ state_composition_hash: "hash_well", energy_kj_mol: 0, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" }] })
        const { container } = page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
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
    // Mutation check: a record with no note must
    // render an empty container nowhere -- there must be no review-note
    // disclosure at all when neither the network's nor the solve's review
    // carries one. The default fixture's reviews are both `{ status:
    // "not_reviewed" }`, with no `note` key at all.
    it("renders no review-note disclosure when neither review carries a note", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        expect(screen.queryByText("Network review note", { exact: false })).toBeNull()
        expect(screen.queryByText("Network solve review note", { exact: false })).toBeNull()
    })

    // Mutation check: if the page rendered the
    // review status pill but dropped the note, this goes red.
    it("shows the solve's review note -- the real one on the live archive -- once its disclosure is opened", async () => {
        const user = userEvent.setup()
        handleEverything({ record: { solves: solvesWithReview({ status: "under_review", note: SOLVE_REVIEW_NOTE }) } })
        page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        const summary = await screen.findByText("Network solve review note", { exact: false })
        expect(screen.queryByText(SOLVE_REVIEW_NOTE)).not.toBeVisible()
        await user.click(summary)
        expect(screen.getByText(SOLVE_REVIEW_NOTE)).toBeVisible()
    })

    it("shows the solve temperature range to 6 significant figures with the stored value as title", async () => {
        handleEverything({ record: { network: { ...networkWithReview({ status: "not_reviewed" }), solve_temperature_min_k: 300.0000001 } } })
        page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        const range = (await screen.findByText("Solve temperature range")).nextElementSibling as HTMLElement
        expect(range).toHaveTextContent("300–2000 K")
        expect(range.querySelector("data")).toHaveAttribute("title", "300.0000001")
    })

    it("shows the network's own review note independently of the solve's", async () => {
        const user = userEvent.setup()
        handleEverything({ record: { network: networkWithReview({ status: "approved", note: NETWORK_REVIEW_NOTE } ) } })
        page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
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
        expect(await screen.findByText(/none of the 2 participant species entries carry a thermo/)).toBeInTheDocument()
    })

    // Mutation check: hardcode the energy-coverage figure instead of
    // computing it (e.g. always render "0 of 2" regardless of the fixture).
    // This test lands a DIFFERENT thermo-presence fixture (1 of 2 species
    // entries covered) and asserts the DIFFERENT resulting sentence -- a
    // hardcoded "none of the 2" would fail this test, not the one above it.
    it("1 of 2 species entries have thermo -- the sentence changes to match, proving it is not a fixed string", async () => {
        handleEverything({ thermoCounts: { spe_well: 1, spe_h2: 0 } })
        page()
        expect(await screen.findByText(/1 of the 2 participant species entries carry a thermo/)).toBeInTheDocument()
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
        expect(await screen.findByText(/for 2 of 2 states and a forward and reverse barrier for 0 of 1 channels/)).toBeInTheDocument()
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
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        // Open the disclosure (Testing Library renders `<details>` content
        // regardless of `open`, but assert both the label appears and the
        // raw hash never does, anywhere in the DOM).
        // Inside the (collapsed-by-default) "Solve-internal state & channel
        // energies" disclosure -- `toBeInTheDocument`, not `toBeVisible`
        // (jest-dom's `toBeVisible` treats a closed `<details>`'s content as
        // not visible by design; presence, not visibility, is what this
        // guard checks). `findAllByText`, not `findByText`: the network
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

describe("NetworkEntryPage -- channel_key never reaches the DOM", () => {
    it("prints a channel's position, never the depositor-typed channel_key, anywhere on the page", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        const channelCells = Array.from(container.querySelectorAll('td[data-label="Channel"]'))
        expect(channelCells.length).toBeGreaterThan(0)
        expect(channelCells.every((cell) => /^\d+$/.test(cell.textContent ?? ""))).toBe(true)
        expect(container.innerHTML).not.toContain("channel_1")
    })
})

describe("NetworkEntryPage -- structured stereo facets tell isomeric states apart", () => {
    it("labels the Z and E diazene states from the participants' structured facets, not the joined label", async () => {
        const facets = { species_entry_kind: "minimum", electronic_state_kind: "ground", stereo_resolution: "resolved" }
        const state = (hash: string, stereo: string) => ({
            composition_hash: hash, kind: "well", label: null, participant_count: 1,
            composition: {
                participants: [{
                    species_entry_ref: `spe_${stereo}`, species_ref: "sp_nn", canonical_smiles: "N=N", stoichiometry: 1,
                    // The joined label carries a typed R as well; it is never read.
                    species_entry_label: `${stereo} R`, ...facets, stereo_label: stereo, stereo_descriptor: stereo,
                }],
                participant_count_total: 1, participants_truncated: false, state_label: `N=N (${stereo} R)`,
            },
        })
        handleEverything({ record: { states: [state("hash_z", "Z"), state("hash_e", "E")], channels: [] }, thermoCounts: { spe_Z: 0, spe_E: 0 } })
        const { container } = page()
        await screen.findByText(NETWORK_REF)
        const table = await screen.findByRole("table", { name: "States in this network" })
        const text = table.textContent ?? ""
        expect(text).toContain("N=N (Z isomer)")
        expect(text).toContain("N=N (E isomer)")
        expect(container.textContent).not.toContain("enantiomer")
    })
})

describe("NetworkEntryPage -- no depositor-typed text", () => {
    it("prints none of the fields a depositor typed, in text, SVG text, ids or any attribute", async () => {
        const detail = networkDetailFixture()
        // The server's state label embeds each participant's `species_entry_label`; plant the typed parts there.
        const typedLabel = `${ADVERSARIAL.joinedLabel} ${ADVERSARIAL.smilesWord}`
        for (const state of (detail.record as { states: { composition: { participants: Record<string, unknown>[]; state_label: string } }[] }).states) {
            for (const participant of state.composition.participants) participant.species_entry_label = typedLabel
            state.composition.state_label = `${state.composition.state_label} (${typedLabel})`
        }
        const planted = plantSentinels(detail, ["name", "description", "channel_key", "label"])
        expectPlanted(planted)
        const { value } = planted
        handleNetworkDetail(value)
        const barrier = plantSentinels(
            { channel_key: "x", reaction_entry_ref: "rxe_test1", transition_state_entry_ref: "tse_test1", forward_barrier_kj_mol: 20, reverse_barrier_kj_mol: 70, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
            ["channel_key"],
        )
        handleSolveDetail("nsolve_test1", [
            { state_composition_hash: "hash_well", energy_kj_mol: 0, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
            { state_composition_hash: "hash_bim", energy_kj_mol: 50, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
        ], [barrier.value])
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate((temperaturesK, pressuresBar) => plantSentinels(defaultKtpFits(temperaturesK, pressuresBar), ["channel_key"]).value)

        const { container } = page()
        // The fixture reached the page: ref, equation, and the energy diagram all rendered.
        expect(await screen.findByText(NETWORK_REF)).toBeVisible()
        expect(await screen.findByText("NN <=> [H][H] + N=N")).toBeVisible()
        await waitFor(() => expect(container.querySelectorAll(".net-map-link").length).toBeGreaterThan(0))
        await screen.findByRole("heading", { name: "k(T,P)" })

        expectNoDepositorText()
        expectNoAdversarialText()
    })
})

describe("NetworkEntryPage -- network map", () => {
    it("draws one circle and one hexagon for the two states, and a triangle for the channel with a deposited barrier", async () => {
        handleEverything({
            channelBarriers: [
                { channel_key: "channel_1", reaction_entry_ref: "rxe_test1", transition_state_entry_ref: "tse_test1", forward_barrier_kj_mol: 20, reverse_barrier_kj_mol: 70, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
            ],
        })
        const { container } = page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        expect(container.querySelectorAll("svg.net-map-svg circle.net-map-node")).toHaveLength(1)
        expect(container.querySelectorAll("svg.net-map-svg polygon.net-map-node--end")).toHaveLength(1)
        expect(container.querySelectorAll("svg.net-map-svg .net-map-ts")).toHaveLength(1)
    })

    it("labels every state from composition.state_label, never the composition_hash", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        expect(Array.from(container.querySelectorAll("svg text")).map((t) => t.textContent)).toContain("NN")
        expect(container.textContent ?? "").not.toContain("hash_well")
        expect(container.textContent ?? "").not.toContain("hash_bim")
    })

    it("renders the states and channels tables alongside the map", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        expect(screen.getByRole("table", { name: "States in this network" })).toBeInTheDocument()
        expect(screen.getByRole("table", { name: "Channels in this network" })).toBeInTheDocument()
    })

    it("says no energy sentence when the solve carries no energies", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "Energies" })
        expect(screen.queryByText(/Energies are electronic only/)).not.toBeInTheDocument()
    })

    it("says the energies in one sentence when the solve carries some, and keeps the rest closed", async () => {
        handleEverything({ stateEnergies: [{ state_composition_hash: "hash_well", energy_kj_mol: 0, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" }] })
        page()
        await screen.findByRole("heading", { name: "Energies" })
        expect(await screen.findByText("Energies are electronic only, relative to the lowest state; they are not free energies.")).toBeVisible()
        const summary = screen.getByText("Energy coverage", { selector: "summary" })
        expect(summary.closest("details")).not.toHaveAttribute("open")
    })
})

describe("NetworkEntryPage -- barrier table numbering", () => {
    it("numbers a barrier by its channel's position in the channel list, as the map and the other tables do", async () => {
        const channel = (key: string) => ({
            channel_key: key, kind: "isomerization", mechanism: "elementary",
            source_state_composition_hash: "hash_bim", sink_state_composition_hash: "hash_well", has_kinetics: false, microreactions: [],
        })
        handleEverything({
            record: { channels: [channel("channel_1"), channel("channel_2")] },
            channelBarriers: [
                { channel_key: "channel_2", reaction_entry_ref: "rxe_test1", transition_state_entry_ref: "tse_test1", forward_barrier_kj_mol: 20, reverse_barrier_kj_mol: 70, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
            ],
        })
        page()
        const table = await screen.findByRole("table", { name: "Solve-internal channel barriers" })
        expect(table.querySelector('td[data-label="Channel"]')!.textContent).toBe("2")
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
    // Each assertion names the route the ref must reach, so a
    // wrong-route regression fails here rather than 404ing in a browser.
    it("the reaction entry ref links to /reaction-entries/:ref", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate()
        page()
        const link = await screen.findByRole("link", { name: /^Reaction entry, channel \d+$/ })
        expect(link.textContent).toBe("Reaction entry")
        expect(link).toHaveAttribute("href", "/reaction-entries/rxe_test1")
    })

    it("the transition state entry ref links to /transition-state-entries/:ref", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate()
        page()
        const link = await screen.findByRole("link", { name: /^Transition state, channel \d+$/ })
        expect(link.textContent).toBe("Transition state")
        expect(link).toHaveAttribute("href", "/transition-state-entries/tse_test1")
    })
})

describe("NetworkEntryPage — solve-internal source calculations link to the calculation page", () => {
    // These two live inside the collapsed "Solve-internal state & channel
    // energies" disclosure, so they are found by their href -- a closed
    // <details> is not role-queryable (see the composition_hash guard above
    // for the same constraint).
    const waitForLink = (href: string) => waitFor(() => {
        const link = document.querySelector(`a[href="${href}"]`)
        expect(link).not.toBeNull()
        return link as HTMLAnchorElement
    })
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
        const link = await waitForLink("/calculations/calc_state1")
        expect(link.textContent).toBe("Calculation")
        expect(link).toHaveAccessibleName("Calculation")
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
        const link = await waitForLink("/calculations/calc_barrier1")
        expect(link.textContent).toBe("Calculation")
        expect(link).toHaveAccessibleName("Calculation")
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
        expect(await screen.findByRole("heading", { name: "Pressure-dependent network" })).toBeVisible()
        expect(await screen.findByText("NN <=> [H][H] + N=N")).toBeInTheDocument()
    })

    it("says the energies were unreadable rather than rendering as though none exist", async () => {
        handleNetworkDetail(networkDetailFixture())
        malformedSolve()
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate()
        const { container } = page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        expect(container.textContent ?? "").toContain("could not be read here")
        expect(container.textContent ?? "").toContain("not a statement that none were deposited")
    })

    it("a network whose solve reads fine says no such thing", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "Pressure-dependent network" })
        expect(container.textContent ?? "").not.toContain("could not be read here")
    })
})

describe("NetworkEntryPage -- k(T,P) heat map", () => {
    it("writes a value in every cell and offers each stored record by its own label", async () => {
        handleEverything()
        const { container } = page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll("svg.ktp-svg .ktp-value").length).toBeGreaterThan(0))
        const cells = container.querySelectorAll("svg.ktp-svg .ktp-cell")
        const values = container.querySelectorAll("svg.ktp-svg .ktp-value")
        expect(values).toHaveLength(cells.length)
        for (const value of Array.from(values)) expect(value.textContent).toMatch(/^\d\.\d×10[⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+$/)
        const select = screen.getByLabelText("Rate record") as HTMLSelectElement
        expect(Array.from(select.options).map((option) => option.textContent)).toEqual(["NN + [H][H] to NN, Chebyshev", "NN + [H][H] to NN, PLOG"])
    })

    it("asks the archive once, however the controls move", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        const requests = handleKtpEvaluate()
        page()
        const pressure = await screen.findByRole("slider", { name: "Pressure" })
        const user = userEvent.setup()
        pressure.focus()
        await user.keyboard("{ArrowRight}{ArrowRight}{ArrowLeft}")
        await user.selectOptions(screen.getByLabelText("Rate record"), "nk_plog1")
        expect(requests).toHaveLength(1)
        expect(requests[0].temperature_k).toHaveLength(8)
        expect(requests[0].pressure_bar).toHaveLength(5)
    })

    it("shows only values the archive returned: a cell equals its served point", async () => {
        handleEverything()
        const { container } = page()
        await waitFor(() => expect(container.querySelectorAll("svg.ktp-svg .ktp-value").length).toBeGreaterThan(0))
        // First record: k = 2 * T, and T = 300 K is the first column, at every pressure.
        const cell = container.querySelector('[data-testid="ktp-cell-0-4"]')!
        expect(cell.parentElement!.querySelector(".ktp-value")!.textContent).toBe("6.0×10²")
    })

    it("marks a point outside the record's stated range and never hides it", async () => {
        handleNetworkDetail(networkDetailFixture())
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "NN <=> [H][H] + N=N")
        handleKtpEvaluate((temperaturesK, pressuresBar) => [
            ktpFitFixture({
                network_kinetics_ref: "nk_cheb1",
                points: temperaturesK.flatMap((t, index) => pressuresBar.map((p) => ktpPoint(t, p, 2 * t, index < temperaturesK.length - 1))),
            }),
        ])
        const { container } = page()
        await waitFor(() => expect(container.querySelectorAll("svg.ktp-svg .ktp-value").length).toBeGreaterThan(0))
        const marked = Array.from(container.querySelectorAll("svg.ktp-svg .ktp-value")).filter((value) => value.textContent?.endsWith("†"))
        expect(marked).toHaveLength(5)
        expect(container.querySelector(".ktp-caption")!.textContent).toMatch(/† is outside the stated range \(300–2000 K, 0\.01–100 bar\)\. The table has the same values\./)
        expect(container.querySelector(".ktp-caption")!.textContent).not.toMatch(/nk_cheb1|nsolve_test1/)
    })

    it("keeps channel_key out of the visible text of the section", async () => {
        handleEverything()
        const { container } = page()
        const heading = await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(container.querySelectorAll("svg.ktp-svg .ktp-value").length).toBeGreaterThan(0))
        const section = heading.closest("section")!
        expect(section.textContent ?? "").not.toContain("channel_1")
        expect(section.querySelector(`[data-channel-key="${channelHandle("channel_1")}"]`)).not.toBeNull()
    })

    it("links the value to the channel's own row on this page", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        const link = await waitFor(() => {
            const found = document.querySelector(".ktp-readout a")
            expect(found).not.toBeNull()
            return found as HTMLAnchorElement
        })
        expect(link).toHaveAttribute("href", "#channel-row-0")
        expect(document.getElementById("channel-row-0")).not.toBeNull()
    })

    it("has a table with the same values", async () => {
        handleEverything()
        page()
        await screen.findByRole("heading", { name: "k(T,P)" })
        const table = await screen.findByRole("table", { name: "k at each pressure and temperature" })
        // 40 pressure and temperature pairs and a header; one column for each record of the channel.
        expect(within(table).getAllByRole("row")).toHaveLength(41)
        expect(within(table).getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
            "Pressure", "Temperature", "Chebyshev, s⁻¹", "PLOG, s⁻¹",
        ])
    })

    it("says so when no channel carries kinetics, and asks for nothing", async () => {
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
        page()
        const heading = await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(heading.closest("section")!.textContent ?? "").toContain("No rate records are deposited on this network."))
    })

    it("says so when the solve records no range, and asks for nothing", async () => {
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
        page()
        const heading = await screen.findByRole("heading", { name: "k(T,P)" })
        await waitFor(() => expect(heading.closest("section")!.textContent ?? "").toContain("records no temperature and pressure range"))
    })
})

describe("NetworkEntryPage -- keyboard path", () => {
    it("Tab reaches the map's channel link, then the pressure control; arrow keys change it and the new value is announced", async () => {
        handleEverything()
        render(
            <MemoryRouter initialEntries={[`/networks/${NETWORK_REF}`]}>
                <Announcer />
                <Routes>
                    <Route path="/networks/:networkRef" element={<NetworkEntryPage />} />
                </Routes>
            </MemoryRouter>,
        )
        const user = userEvent.setup()
        const channelLink = await screen.findByRole("link", { name: /^Channel 1, isomerization/ })
        const pressure = await screen.findByRole("slider", { name: "Pressure" })
        channelLink.focus()
        for (let presses = 0; presses < 40 && document.activeElement !== pressure; presses += 1) await user.tab()
        expect(pressure).toHaveFocus()
        expect(pressure).toHaveAttribute("aria-valuetext", "1 bar")

        await user.keyboard("{ArrowRight}")
        expect(pressure).toHaveAttribute("aria-valuetext", "10 bar")
        const live = document.querySelector(".announcer")!
        await waitFor(() => expect(live.textContent).toMatch(/^k at 1271 K and 10 bar: 2542\.86 s⁻¹$/))

        await user.keyboard("{ArrowLeft}{ArrowLeft}")
        expect(pressure).toHaveAttribute("aria-valuetext", "0.1 bar")
        await waitFor(() => expect(live.textContent).toMatch(/^k at 1271 K and 0\.1 bar: /))
    })
})

describe("NetworkEntryPage -- which solve the energies came from, and the order of the state table", () => {
    const stateEnergies = [
        { state_composition_hash: "hash_bim", energy_kj_mol: 123.729, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
        { state_composition_hash: "hash_well", energy_kj_mol: 0, energy_zero_convention: "lowest_state", correction_convention: "electronic_only" },
    ]

    it("names the one deposited solve the energies are read from", async () => {
        handleEverything({ stateEnergies })
        page()
        expect(await screen.findByText(/read from the network's one deposited solve, nsolve_test1/)).toBeInTheDocument()
    })

    it("with several solves deposited, says the energies are the first one's and that the first is not preferred", async () => {
        const second = { network_solve_ref: "nsolve_test2", kind: "computed", me_method: "reservoir state", interpolation_model: "plog", tmin_k: 300, tmax_k: 2000, pmin_bar: 0.01, pmax_bar: 100, review: { status: "not_reviewed" } }
        const first = { network_solve_ref: "nsolve_test1", kind: "computed", me_method: "modified strong collision", interpolation_model: "chebyshev", tmin_k: 300, tmax_k: 2000, pmin_bar: 0.01, pmax_bar: 100, review: { status: "not_reviewed" } }
        handleEverything({ stateEnergies, record: { solves: [first, second] } })
        page()
        expect(await screen.findByText(/read from solve nsolve_test1, the first of 2 deposited solves/)).toBeInTheDocument()
        expect(screen.getByText(/the first is not preferred/)).toBeInTheDocument()
    })

    it("says the state rows run lowest to highest, and that this ranks nothing", async () => {
        handleEverything({ stateEnergies })
        page()
        expect(await screen.findByText(/Rows are listed from the lowest relative energy to the highest/)).toBeInTheDocument()
        const table = screen.getByRole("table", { name: "Solve-internal state energies" })
        const cells = Array.from(table.querySelectorAll('td[data-label="Relative energy"]')).map((cell) => cell.textContent)
        // Served highest first; shown lowest first.
        expect(cells[0]).toMatch(/^0\b/)
    })

    it("the visible energy sentence names which solve it is about when several are deposited, and lists every solve ref", async () => {
        const make = (ref: string) => ({ network_solve_ref: ref, kind: "computed", me_method: "modified strong collision", interpolation_model: "chebyshev", tmin_k: 300, tmax_k: 2000, pmin_bar: 0.01, pmax_bar: 100, review: { status: "not_reviewed" } })
        handleEverything({ stateEnergies, record: { solves: [make("nsolve_test1"), make("nsolve_test2")] } })
        page()
        expect(await screen.findByText(/The first of this network's 2 solves \(nsolve_test1; none is preferred\) carries/)).toBeInTheDocument()
        expect(screen.queryByText(/This network's own solve carries/)).not.toBeInTheDocument()
        fireEvent.click(screen.getByText(/^References/))
        expect(screen.getByText("nsolve_test1")).toBeInTheDocument()
        expect(screen.getByText("nsolve_test2")).toBeInTheDocument()
        expect(screen.getByText("Network solve 2 of 2")).toBeInTheDocument()
    })

    it("with one solve the sentence stays as it was", async () => {
        handleEverything({ stateEnergies })
        page()
        expect(await screen.findByText(/This network's own solve carries/)).toBeInTheDocument()
    })

    it("states the master-equation method per solve when several are deposited, not the first solve's alone", async () => {
        const make = (ref: string, method: string) => ({ network_solve_ref: ref, kind: "computed", me_method: method, interpolation_model: "chebyshev", tmin_k: 300, tmax_k: 2000, pmin_bar: 0.01, pmax_bar: 100, review: { status: "not_reviewed" } })
        handleEverything({ stateEnergies, record: { solves: [make("nsolve_test1", "modified strong collision"), make("nsolve_test2", "reservoir state")] } })
        page()
        const term = await screen.findByText("ME method")
        const value = term.nextElementSibling as HTMLElement
        expect(value.textContent).toContain("modified strong collision (nsolve_test1)")
        expect(value.textContent).toContain("reservoir state (nsolve_test2)")
    })
})

describe("NetworkEntryPage -- literature title (depositor text guard)", () => {
    async function mount(literature: Record<string, unknown>) {
        handleNetworkDetail(networkDetailFixture({ literature }))
        handleSolveDetail("nsolve_test1")
        handleThermo({ spe_well: 0, spe_h2: 0 })
        handleReactionEntry("rxe_test1", "2 [NH2] <=> NN")
        handleKtpEvaluate()
        page()
        // The fixture reached the page: the Literature row rendered.
        await screen.findByText("Literature", { selector: "dt" })
    }

    it("withholds a typed title and a spam DOI; prints the public ref and says the title is not verified", async () => {
        await mount({
            literature_ref: "lit_net1", title: "DEPOSITOR_SENTINEL_literature_title", title_source: "depositor_supplied",
            doi: "10.1/ DEPOSITOR_SENTINEL_literature_doi spam.example",
        })
        expect(screen.getByText("Literature", { selector: "dt" }).nextElementSibling?.textContent).toBe("lit_net1 · Title not verified")
        expectNoDepositorText()
        expect(document.body.innerHTML).not.toContain("spam.example")
    })

    it("shows a registry-resolved title (the positive control)", async () => {
        await mount({ literature_ref: "lit_net1", title: "RESOLVED_TITLE", title_source: "resolved_metadata" })
        expect(screen.getByText("Literature", { selector: "dt" }).nextElementSibling?.textContent).toBe("RESOLVED_TITLE")
    })
})

describe("NetworkEntryPage -- electronic-state labels and term symbols tell excited states apart", () => {
    const stateWith = (hash: string, ref: string, facets: Record<string, unknown>) => ({
        composition_hash: hash, kind: "well", label: null, participant_count: 1,
        composition: {
            participants: [{
                species_entry_ref: ref, species_ref: "sp_ch2o", canonical_smiles: "C=O", stoichiometry: 1,
                species_entry_label: "joined DEPOSITOR_SENTINEL_joined", species_entry_kind: "minimum", electronic_state_kind: "excited", ...facets,
            }],
            participant_count_total: 1, participants_truncated: false, state_label: "C=O (excited DEPOSITOR_SENTINEL_joined)",
        },
    })

    it("labels the S1 and T1 states from the participants' structured facets", async () => {
        handleEverything({
            record: {
                states: [
                    stateWith("hash_s1", "spe_s1", { electronic_state_label: "S1", term_symbol: "¹A″" }),
                    stateWith("hash_t1", "spe_t1", { electronic_state_label: "T1", term_symbol: "³A″" }),
                ],
                channels: [],
            },
            thermoCounts: { spe_s1: 0, spe_t1: 0 },
        })
        page()
        await screen.findByText(NETWORK_REF)
        const table = await screen.findByRole("table", { name: "States in this network" })
        const text = table.textContent ?? ""
        expect(text).toContain("C=O (excited state, state S1, ¹A″)")
        expect(text).toContain("C=O (excited state, state T1, ³A″)")
        expectNoDepositorText()
    })

    it("hides a label or term symbol outside the grammar (the hide-invalid path)", async () => {
        handleEverything({
            record: {
                states: [stateWith("hash_bad", "spe_bad", { electronic_state_label: "R", term_symbol: "DEPOSITOR_SENTINEL_term" })],
                channels: [],
            },
            thermoCounts: { spe_bad: 0 },
        })
        page()
        await screen.findByText(NETWORK_REF)
        const table = await screen.findByRole("table", { name: "States in this network" })
        expect(table.textContent).toContain("C=O (excited state)")
        expect(table.textContent).not.toMatch(/state R/)
        expectNoDepositorText()
    })
})
