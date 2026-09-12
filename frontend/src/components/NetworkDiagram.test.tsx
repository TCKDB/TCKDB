import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import "../design-system.css"
import type { NetworkChannel, NetworkChannelBarrier, NetworkState, NetworkStateEnergy } from "../api/networkEntryApi"
import { NetworkDiagram } from "./NetworkDiagram"

afterEach(() => cleanup())

function state(hash: string, kind: string, label: string, participants: { ref: string; smiles: string; stoich?: number }[] = []): NetworkState {
    return {
        composition_hash: hash,
        kind,
        label: null,
        participant_count: participants.length,
        composition: {
            participants: participants.map((p) => ({
                species_entry_ref: p.ref,
                species_ref: `sp_${p.ref}`,
                canonical_smiles: p.smiles,
                stoichiometry: p.stoich ?? 1,
            })),
            participant_count_total: participants.length,
            participants_truncated: false,
            state_label: label,
        },
    }
}

function channel(key: string, kind: string, source: string, sink: string, hasKinetics = true, mechanism = "elementary"): NetworkChannel {
    return {
        channel_key: key,
        kind,
        mechanism,
        source_state_composition_hash: source,
        sink_state_composition_hash: sink,
        has_kinetics: hasKinetics,
        microreactions: [],
    }
}

function energy(hash: string, energyKjMol: number): NetworkStateEnergy {
    return {
        state_composition_hash: hash,
        energy_kj_mol: energyKjMol,
        energy_zero_convention: "lowest_state",
        correction_convention: "electronic_only",
    }
}

function barrier(channelKey: string, forward: number, reverse: number): NetworkChannelBarrier {
    return {
        channel_key: channelKey,
        reaction_entry_ref: `rxe_${channelKey}`,
        transition_state_entry_ref: `tse_${channelKey}`,
        forward_barrier_kj_mol: forward,
        reverse_barrier_kj_mol: reverse,
        energy_zero_convention: "lowest_state",
        correction_convention: "electronic_only",
    }
}

// A network shaped like the live hydrazine archive: 4 states, 3 channels,
// but only ONE channel (channel_1) carries a deposited barrier -- the
// other two (channel_2, channel_5) must produce no saddle point at all
// (invariant 1). hash_n6 deliberately carries no deposited energy, so it
// must be OMITTED from the surface (never guessed), counted in the
// "no deposited energy" note.
const HYDRAZINE_STATES: NetworkState[] = [
    state("hash_n1", "well", "NN", [{ ref: "spe_nn", smiles: "NN" }]),
    state("hash_n2", "well", "[NH-][NH3+]", [{ ref: "spe_zw", smiles: "[NH-][NH3+]" }]),
    state("hash_n3", "bimolecular", "N=N (Z) + [H][H]", [{ ref: "spe_z", smiles: "N=N" }, { ref: "spe_h2", smiles: "[H][H]" }]),
    state("hash_n6", "bimolecular", "2 [NH2]", [{ ref: "spe_nh2", smiles: "[NH2]", stoich: 2 }]),
]

const HYDRAZINE_CHANNELS: NetworkChannel[] = [
    channel("channel_1", "isomerization", "hash_n2", "hash_n1", true),
    channel("channel_2", "association", "hash_n3", "hash_n1", true),
    channel("channel_5", "association", "hash_n6", "hash_n1", false),
]

// source (hash_n2) = 180.1, sink (hash_n1) = 0.0, forward = 94.6 ->
// TS height = 180.1 + 94.6 = 274.7 (matches the brief's own verified
// figure for this exact channel). reverse chosen to agree: 274.7 - 0 = 274.7.
const HYDRAZINE_ENERGIES: NetworkStateEnergy[] = [
    energy("hash_n1", 0.0),
    energy("hash_n2", 180.1),
    energy("hash_n3", 380.9),
    // hash_n6: no deposited energy, deliberately.
]

const HYDRAZINE_BARRIERS: NetworkChannelBarrier[] = [
    barrier("channel_1", 94.6, 274.7),
]

function renderDiagram(
    states: NetworkState[],
    channels: NetworkChannel[],
    stateEnergies: NetworkStateEnergy[] | null = HYDRAZINE_ENERGIES,
    channelBarriers: NetworkChannelBarrier[] | null = HYDRAZINE_BARRIERS,
) {
    return render(
        <MemoryRouter>
            <NetworkDiagram states={states} channels={channels} stateEnergies={stateEnergies} channelBarriers={channelBarriers} />
        </MemoryRouter>,
    )
}

describe("NetworkDiagram -- invariant 1: a saddle point is drawn only for a channel with a deposited barrier", () => {
    it("draws exactly one saddle point (channel_1) though three channels exist", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        // Scoped to `.net-pes-saddle-link`, not `querySelector("svg")` --
        // the legend renders its own small illustrative <svg> icons first,
        // so "the first svg" would silently scope this to a 22x10 icon.
        const saddleLinks = container.querySelectorAll(".net-pes-saddle-link")
        expect(saddleLinks.length).toBeGreaterThan(0)
        expect(saddleLinks).toHaveLength(1)
    })

    it("draws no saddle point at all when NO channel carries a deposited barrier", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS, HYDRAZINE_ENERGIES, [])
        expect(container.querySelectorAll(".net-pes-saddle-link")).toHaveLength(0)
    })

    it("states the true deposited-barrier count in prose, live from the data", () => {
        renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(screen.getByText(/1 of 3 channels carries a deposited barrier/)).toBeVisible()
    })
})

describe("NetworkDiagram -- saddle height is source_energy + forward_barrier_kj_mol, rendered on the peak", () => {
    it("shows 274.7, never the bare forward barrier (94.6) or the reverse-derived figure alone", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const peakLabel = container.querySelector(".net-pes-peak-label")
        expect(peakLabel).not.toBeNull()
        expect(peakLabel!.textContent).toBe("274.7")
    })
})

describe("NetworkDiagram -- channel_key never reaches a reader, by any route", () => {
    it("appears in no user-facing text on the SVG: not a <text>, not an aria-label, not a title", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const labelled = Array.from(container.querySelectorAll("[aria-label], [title]"))
        expect(labelled.length).toBeGreaterThan(0)
        for (const el of labelled) {
            expect(el.getAttribute("aria-label") ?? "").not.toMatch(/channel_\d/)
            expect(el.getAttribute("title") ?? "").not.toMatch(/channel_\d/)
        }
        for (const el of Array.from(container.querySelectorAll("title, desc"))) {
            expect(el.textContent ?? "").not.toMatch(/channel_\d/)
        }
        for (const el of Array.from(container.querySelectorAll("svg text"))) {
            expect(el.textContent ?? "").not.toMatch(/channel_\d/)
        }
    })

    it("still describes the drawn saddle point by its chemistry, so the aria-label is not merely emptied", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const labels = Array.from(container.querySelectorAll("svg a.net-pes-saddle-link"))
            .map((el) => el.getAttribute("aria-label") ?? "")
        expect(labels.length).toBeGreaterThan(0)
        for (const label of labels) {
            expect(label).not.toBe("")
            expect(label).toMatch(/ to /)
        }
    })

    it("channel_1 appears as a data-channel-key attribute and in the table, never inside <text>", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const line = container.querySelector('polyline[data-channel-key="channel_1"]')
        expect(line).not.toBeNull()
        expect(Array.from(container.querySelectorAll('td[data-label="Channel"] code.data')).some((el) => el.textContent === "channel_1")).toBe(true)
    })
})

describe("NetworkDiagram -- every visible level label is composition.state_label", () => {
    it("renders each plotted state's state_label as SVG text, never the composition_hash", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const svgTexts = Array.from(container.querySelectorAll("svg text")).map((t) => t.textContent)
        expect(svgTexts).toContain("NN")
        expect(svgTexts).toContain("[NH-][NH3+]")
        expect(container.textContent ?? "").not.toContain("hash_n1")
    })
})

describe("NetworkDiagram -- a state with no deposited energy is omitted, never guessed", () => {
    it("does not draw a level for hash_n6, and states the omission count", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        const levelLabels = Array.from(container.querySelectorAll(".net-pes-level-label")).map((el) => el.textContent)
        expect(levelLabels).not.toContain("2 [NH2]")
        expect(screen.getByText(/1 of 4 states has no deposited energy/)).toBeVisible()
    })
})

describe("NetworkDiagram -- the accessible tables always render, regardless of surface state", () => {
    it("renders both state and channel tables alongside the surface", () => {
        renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(screen.getByRole("table", { name: "States in this network" })).toBeInTheDocument()
        expect(screen.getByRole("table", { name: "Channels in this network" })).toBeInTheDocument()
        expect(screen.getAllByRole("row", { name: /./ }).length).toBeGreaterThan(HYDRAZINE_STATES.length)
    })

    it("still renders both tables when no state carries a deposited energy at all", () => {
        const { container } = renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS, [], [])
        expect(container.querySelector(".net-pes-svg")).toBeNull()
        expect(screen.getByRole("table", { name: "States in this network" })).toBeInTheDocument()
        expect(screen.getByRole("table", { name: "Channels in this network" })).toBeInTheDocument()
        expect(screen.getByText(/No state energies are deposited/)).toBeVisible()
    })
})

describe("NetworkDiagram -- legend counts are computed live, not hardcoded", () => {
    it("states the true well/bimolecular/kind counts for this fixture", () => {
        renderDiagram(HYDRAZINE_STATES, HYDRAZINE_CHANNELS)
        expect(screen.getByText(/Well level \(2 of 3 plotted states\)/)).toBeVisible()
        expect(screen.getByText(/Bimolecular level \(1 of 3 plotted states\)/)).toBeVisible()
        expect(screen.getByText(/isomerization \(1 saddle point shown\)/)).toBeVisible()
    })

    it("a different fixture produces a different sentence, proving the count is not fixed", () => {
        const oneWell = [state("hash_only", "well", "only state")]
        const { container } = renderDiagram(oneWell, [], [energy("hash_only", 0)], [])
        expect(screen.getByText(/Well level \(1 of 1 plotted states\)/)).toBeVisible()
        expect(screen.queryByText(/Well level \(2 of/)).not.toBeInTheDocument()
        expect(container.querySelectorAll(".net-pes-level-link")).toHaveLength(1)
    })
})

/**
 * MUTATION TABLE (`NetworkDiagram.test.tsx`)
 *
 * | # | Test | Mutation landed | Result |
 * |---|------|------------------|--------|
 * | 1 | "draws exactly one saddle point (channel_1) though three channels exist" | In `NetworkDiagram.tsx`, changed `layout.saddles.map(...)` to `channels.map((c) => ({ ...saddleFromChannelIgnoringBarrier }))`-shaped stand-in: concretely, replaced `layout.saddles` with `layout.saddles.concat(layout.saddles)` (doubling) as a cheap one-line stand-in for "draws a saddle for a channel with no barrier" | RED -- `saddleLinks` had length 2, `toHaveLength(1)` failed |
 * | 2 | "draws no saddle point at all when NO channel carries a deposited barrier" | Passed `HYDRAZINE_BARRIERS` instead of `[]` as the 4th arg in this one test (simulating the component ignoring an empty barrier list) | RED -- `toHaveLength(0)` failed, length was 1 |
 * | 3 | "shows 274.7, never the bare forward barrier" | In `NetworkDiagram.tsx`, changed `{saddle.heightKjMol.toFixed(1)}` to `{(94.6).toFixed(1)}` (hardcoding the bare forward barrier value) | RED -- expected "274.7", got "94.6" |
 * | 4 | "channel_1 appears as a data-channel-key attribute" | Removed `data-channel-key={saddle.channelKey ?? undefined}` from the `<polyline>` | RED -- `querySelector('polyline[data-channel-key="channel_1"]')` was null |
 * | 5 | "renders each plotted state's state_label as SVG text, never the composition_hash" | Changed `level.label` to `level.compositionHash` in the level `<text>` | RED -- `svgTexts` no longer contained "NN"/"[NH-][NH3+]"; contained "hash_n1" instead, failing the negative assertion |
 * | 6 | "does not draw a level for hash_n6" | Removed the `resolvedInServedOrder` filter in `networkPesLayout.ts` (placed every state regardless of a deposited energy) | RED -- `levelLabels` contained "2 [NH2]" |
 * | 7 | "still renders both tables when no state carries a deposited energy at all" | Wrapped `<NetworkStatesTable .../>`/`<NetworkChannelsTable .../>` in `{layout && (...)}` in `NetworkDiagram.tsx` (regressing invariant 5) | RED -- `screen.getByRole("table", ...)` threw, element not found |
 *
 * Each mutation was landed as a single edit, the named test confirmed red
 * (`npx vitest run src/components/NetworkDiagram.test.tsx`), then reverted
 * and confirmed via `git diff --stat` showing no changes and
 * `sha256sum -c` against a pre-mutation checksum of the touched file.
 */
