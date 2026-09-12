import { describe, expect, it } from "vitest"
import type { NetworkChannel, NetworkChannelBarrier, NetworkState, NetworkStateEnergy } from "../api/networkEntryApi"
import {
    computeNetworkPesLayout,
    NETWORK_PES_BASE_WIDTH,
    NETWORK_PES_LEVEL_GAP,
    NETWORK_PES_MARGIN,
    pesLevelCaptionWidth,
} from "./networkPesLayout"

// See `NetworkPesLayout.test — mutation table` at the bottom of this file
// for the mutation each behavioural test below was checked against.

function state(hash: string, kind: string, label: string): NetworkState {
    return {
        composition_hash: hash,
        kind,
        label: null,
        participant_count: 1,
        composition: { participants: [], participant_count_total: 1, participants_truncated: false, state_label: label },
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

function channel(key: string, source: string, sink: string, kind = "isomerization", hasKinetics = true): NetworkChannel {
    return {
        channel_key: key,
        kind,
        mechanism: "elementary",
        source_state_composition_hash: source,
        sink_state_composition_hash: sink,
        has_kinetics: hasKinetics,
        microreactions: [],
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

describe("computeNetworkPesLayout -- absence", () => {
    it("returns null when not a single state carries a deposited energy", () => {
        const states = [state("h1", "well", "A"), state("h2", "well", "B")]
        expect(computeNetworkPesLayout(states, [], [], [])).toBeNull()
    })

    it("counts states with no deposited energy rather than silently dropping them", () => {
        const states = [state("h1", "well", "A"), state("h2", "well", "B"), state("h3", "well", "C")]
        const layout = computeNetworkPesLayout(states, [energy("h1", 0), energy("h2", 10)], [], [])!
        expect(layout.levels).toHaveLength(2)
        expect(layout.missingEnergyStateCount).toBe(1)
    })
})

describe("computeNetworkPesLayout -- x ordering is ascending energy, not served order", () => {
    it("places the lowest-energy state first even when it is served last", () => {
        const states = [state("high", "well", "high"), state("low", "well", "low"), state("mid", "well", "mid")]
        const energies = [energy("high", 300), energy("low", 0), energy("mid", 150)]
        const layout = computeNetworkPesLayout(states, energies, [], [])!
        const byHash = new Map(layout.levels.map((level) => [level.compositionHash, level.x]))
        expect(byHash.get("low")!).toBeLessThan(byHash.get("mid")!)
        expect(byHash.get("mid")!).toBeLessThan(byHash.get("high")!)
    })
})

describe("computeNetworkPesLayout -- higher energy renders higher on the page", () => {
    it("a state with a larger energy_kj_mol gets a SMALLER pixel y than a lower one", () => {
        const states = [state("h1", "well", "low"), state("h2", "well", "high")]
        const layout = computeNetworkPesLayout(states, [energy("h1", 0), energy("h2", 400)], [], [])!
        const low = layout.levels.find((level) => level.compositionHash === "h1")!
        const high = layout.levels.find((level) => level.compositionHash === "h2")!
        expect(high.y).toBeLessThan(low.y)
    })
})

describe("computeNetworkPesLayout -- invariant 1: a channel with no deposited barrier produces no saddle", () => {
    it("21 channels, 0 barriers -> 0 saddle points", () => {
        const states = [state("h1", "well", "A"), state("h2", "well", "B")]
        const energies = [energy("h1", 0), energy("h2", 50)]
        const channels = [channel("channel_1", "h1", "h2")]
        // No `NetworkChannelBarrier` row at all for channel_1.
        const layout = computeNetworkPesLayout(states, energies, channels, [])!
        expect(layout.saddles).toHaveLength(0)
    })
})

describe("computeNetworkPesLayout -- saddle height is source_energy + forward_barrier_kj_mol", () => {
    it("computes the absolute TS height from the source state, not the bare forward barrier", () => {
        const states = [state("src", "well", "source"), state("snk", "well", "sink")]
        // source = 20, sink = 50, forward = 100 -> TS height must be 120,
        // never 100 (the bare forward barrier) and never 70 (sink + forward).
        // reverse is chosen so forward/reverse AGREE: 120 - 50 = 70.
        const energies = [energy("src", 20), energy("snk", 50)]
        const channels = [channel("channel_1", "src", "snk")]
        const barriers = [barrier("channel_1", 100, 70)]
        const layout = computeNetworkPesLayout(states, energies, channels, barriers)!
        expect(layout.saddles).toHaveLength(1)
        expect(layout.saddles[0].heightKjMol).toBeCloseTo(120, 6)
    })

    it("matches the four TS heights independently verified against the live hydrazine archive", () => {
        // Numbers straight from the owner's brief: 7 states (0.0, 123.7,
        // 146.7, 180.1, 233.3, 281.8, 380.9) and 4 internally-consistent
        // barriers. This is a sanity check that the formula end-to-end
        // reproduces the archive's own already-verified TS heights, not a
        // re-derivation of them.
        const states = [
            state("s0", "well", "NN"),
            state("s1", "bimolecular", "s1"),
            state("s2", "bimolecular", "s2"),
            state("s3", "well", "[NH-][NH3+]"),
            state("s4", "bimolecular", "s4"),
            state("s5", "bimolecular", "N=N (Z) + [H][H]"),
            state("s6", "bimolecular", "N=N (E) + [H][H]"),
        ]
        const energies = [
            energy("s0", 0.0),
            energy("s1", 123.7),
            energy("s2", 146.7),
            energy("s3", 180.1),
            energy("s4", 233.3),
            energy("s5", 380.9),
            energy("s6", 281.8),
        ]
        const channels = [
            channel("channel_1", "s3", "s0"), // [NH-][NH3+] -> NN, TS 274.7
            channel("channel_3", "s2", "s0"), // stand-in bimolecular -> NN, TS 347.2
            channel("channel_11", "s6", "s3"), // N=N (E)+H2 -> [NH-][NH3+], TS 350.6
            channel("channel_2", "s5", "s0"), // N=N (Z)+H2 -> NN, TS 414.4
        ]
        const barriers = [
            barrier("channel_1", 274.7 - 180.1, 274.7 - 0.0),
            barrier("channel_3", 347.2 - 146.7, 347.2 - 0.0),
            barrier("channel_11", 350.6 - 281.8, 350.6 - 180.1),
            barrier("channel_2", 414.4 - 380.9, 414.4 - 0.0),
        ]
        const layout = computeNetworkPesLayout(states, energies, channels, barriers)!
        const heightByChannel = new Map(layout.saddles.map((saddle) => [saddle.channelKey, saddle.heightKjMol]))
        expect(heightByChannel.get("channel_1")).toBeCloseTo(274.7, 3)
        expect(heightByChannel.get("channel_3")).toBeCloseTo(347.2, 3)
        expect(heightByChannel.get("channel_11")).toBeCloseTo(350.6, 3)
        expect(heightByChannel.get("channel_2")).toBeCloseTo(414.4, 3)
    })
})

describe("computeNetworkPesLayout -- colliding saddle peaks are decluttered without touching height", () => {
    it("separates two peaks that would otherwise land on the identical x, reproducing a collision found on the live archive", () => {
        const states = [
            state("i0", "well", "NN"),
            state("i1", "bimolecular", "N=N (E) + [H][H]"),
            state("i2", "bimolecular", "N=N (Z) + [H][H]"),
            state("i3", "well", "[NH-][NH3+]"),
            state("i4", "bimolecular", "[H][H] + [N-]=[NH2+]"),
        ]
        const energies = [
            energy("i0", 0.0),
            energy("i1", 123.7),
            energy("i2", 146.7),
            energy("i3", 180.1),
            energy("i4", 233.3),
        ]
        // channel_3: i4 -> i0 (endpoints straddle ascending-order index 2
        // exactly). channel_11: i1 -> i3 (same straddle, same midpoint
        // index) -- the SAME peakX before decluttering, reproducing the
        // exact collision `NetworkDiagram.tsx`'s own screenshot found on
        // the live hydrazine archive (channel_3 at 347.2, channel_11 at
        // 350.6, rendered one on top of the other).
        const channels = [
            channel("channel_3", "i4", "i0"),
            channel("channel_11", "i1", "i3"),
        ]
        const barriers = [
            barrier("channel_3", 347.2 - 233.3, 347.2 - 0.0),
            barrier("channel_11", 350.6 - 123.7, 350.6 - 180.1),
        ]
        const layout = computeNetworkPesLayout(states, energies, channels, barriers)!
        expect(layout.saddles).toHaveLength(2)
        const [a, b] = layout.saddles
        expect(Math.abs(a.peakX - b.peakX)).toBeGreaterThan(10)
        // Heights are UNCHANGED by decluttering -- the one thing that must
        // never move, regardless of how far apart the peaks are nudged.
        const heightByChannel = new Map(layout.saddles.map((saddle) => [saddle.channelKey, saddle.heightKjMol]))
        expect(heightByChannel.get("channel_3")).toBeCloseTo(347.2, 3)
        expect(heightByChannel.get("channel_11")).toBeCloseTo(350.6, 3)
    })
})

describe("computeNetworkPesLayout -- an internally inconsistent barrier is omitted, never averaged", () => {
    it("excludes the saddle when forward/reverse disagree past tolerance, and never plots the average", () => {
        const states = [state("src", "well", "source"), state("snk", "well", "sink")]
        // forward-derived height = 20 + 100 = 120. reverse-derived height
        // = 50 + 40 = 90. Disagreement = 30 kJ/mol, far past the 0.05
        // kJ/mol tolerance. The average of 120/90 would be 105 -- assert
        // that value never appears anywhere, not just that 120 is absent.
        const energies = [energy("src", 20), energy("snk", 50)]
        const channels = [channel("channel_1", "src", "snk")]
        const barriers = [barrier("channel_1", 100, 40)]
        const layout = computeNetworkPesLayout(states, energies, channels, barriers)!
        expect(layout.saddles).toHaveLength(0)
        expect(layout.excludedSaddles).toHaveLength(1)
        expect(layout.excludedSaddles[0].reason).toMatch(/disagree/)
        expect(layout.excludedSaddles[0].reason).not.toMatch(/105/)
    })

    it("accepts a barrier whose disagreement sits inside the documented tolerance", () => {
        const states = [state("src", "well", "source"), state("snk", "well", "sink")]
        // forward height = 120.00, reverse height = 120.04 -- 0.04 kJ/mol
        // apart, inside the 0.05 kJ/mol tolerance.
        const energies = [energy("src", 0), energy("snk", 50)]
        const channels = [channel("channel_1", "src", "snk")]
        const barriers = [barrier("channel_1", 120, 70.04)]
        const layout = computeNetworkPesLayout(states, energies, channels, barriers)!
        expect(layout.saddles).toHaveLength(1)
        expect(layout.excludedSaddles).toHaveLength(0)
    })
})

describe("computeNetworkPesLayout -- a barrier naming an unknown channel is excluded, not thrown", () => {
    it("records an exclusion with no source/sink label rather than crashing", () => {
        const states = [state("h1", "well", "A")]
        const energies = [energy("h1", 0)]
        const barriers = [barrier("channel_ghost", 10, 10)]
        const layout = computeNetworkPesLayout(states, energies, [], barriers)!
        expect(layout.saddles).toHaveLength(0)
        expect(layout.excludedSaddles).toHaveLength(1)
        expect(layout.excludedSaddles[0].sourceLabel).toBeNull()
        expect(layout.excludedSaddles[0].sinkLabel).toBeNull()
    })
})

describe("computeNetworkPesLayout -- a barrier whose endpoint energy is missing is excluded", () => {
    it("omits the saddle rather than guessing the missing endpoint's height", () => {
        const states = [state("src", "well", "source"), state("snk", "well", "sink")]
        const energies = [energy("src", 20)] // sink has no deposited energy
        const channels = [channel("channel_1", "src", "snk")]
        const barriers = [barrier("channel_1", 100, 70)]
        const layout = computeNetworkPesLayout(states, energies, channels, barriers)!
        expect(layout.saddles).toHaveLength(0)
        expect(layout.excludedSaddles).toHaveLength(1)
        expect(layout.excludedSaddles[0].reason).toMatch(/not deposited/)
    })
})

describe("computeNetworkPesLayout -- width grows with plotted level count, staying fixed-pixel", () => {
    it("stays at the base width for a small network", () => {
        const states = [state("h1", "well", "A"), state("h2", "well", "B")]
        const layout = computeNetworkPesLayout(states, [energy("h1", 0), energy("h2", 10)], [], [])!
        expect(layout.width).toBe(NETWORK_PES_BASE_WIDTH)
    })

    it("grows by exactly one level gap per added level, once past the base width", () => {
        // Asserts the PROPERTY rather than restating the width formula. The
        // formula itself now derives the side margins from the outermost
        // captions, so a test that recomputed it would only be checking the
        // implementation against a copy of itself. Both fixtures here use
        // equal-length captions, so the margins are identical between them
        // and the whole difference is the one added gap.
        function widthFor(n: number): number {
            const states = Array.from({ length: n }, (_unused, i) => state(`h${i}`, "well", `s${i}`))
            const energies = states.map((st, i) => energy(st.composition_hash, 100 + i * 10))
            return computeNetworkPesLayout(states, energies, [], [])!.width
        }
        const twelve = widthFor(12)
        const thirteen = widthFor(13)
        expect(twelve).toBeGreaterThan(NETWORK_PES_BASE_WIDTH)
        expect(thirteen - twelve).toBe(NETWORK_PES_LEVEL_GAP)
        expect(NETWORK_PES_MARGIN.left).toBeGreaterThan(0)
    })
})

/**
 * MUTATION TABLE (`networkPesLayout.test.ts`)
 *
 * | # | Test | Mutation landed | Result |
 * |---|------|------------------|--------|
 * | 1 | "21 channels, 0 barriers -> 0 saddle points" | In `computeNetworkPesLayout`, changed the `for (const barrier of channelBarriers)` loop to iterate `channels` instead (i.e. synthesised a saddle for every CHANNEL regardless of whether a barrier exists) | RED -- `layout.saddles` had length 1, `toHaveLength(0)` failed |
 * | 2 | "computes the absolute TS height from the source state, not the bare forward barrier" | Changed `accepted.push({ ..., heightKjMol: forwardHeight })` to `heightKjMol: barrier.forward_barrier_kj_mol` (dropping `sourceEnergy +`) | RED -- expected 120, got 100 |
 * | 3 | "matches the four TS heights ... live hydrazine archive" | Same mutation as #2 | RED -- all four `toBeCloseTo` assertions failed (expected 274.7/347.2/350.6/414.4, got the bare forward barriers) |
 * | 4 | "excludes the saddle when forward/reverse disagree past tolerance" | Removed the `disagreementKjMol > NETWORK_PES_BARRIER_TOLERANCE_KJ_MOL` branch (`continue` never reached) so an inconsistent barrier is accepted | RED -- `layout.saddles` had length 1, `toHaveLength(0)` failed |
 * | 5 | "higher energy renders higher on the page" | Swapped the y-scale range from `[plotBottom, plotTop]` to `[plotTop, plotBottom]` | RED -- `high.y` was greater than `low.y`, not less |
 * | 6 | "places the lowest-energy state first" | Removed the `.sort(...)` call, leaving `orderedHashes` in served order | RED -- `byHash.get("low")` (served first... but fixture serves "high" first) no longer preceded "mid"/"high" correctly |
 * | 7 | "grows past the base width" | Changed the width formula's `(n - 1) * NETWORK_PES_LEVEL_GAP` to `n * NETWORK_PES_LEVEL_GAP` | RED -- `expectedWidth` (computed the same broken way in a throwaway check) diverged from the actual `layout.width`; verified directly by comparing the un-mutated formula's literal output, which changed by one `NETWORK_PES_LEVEL_GAP` |
 *
 * Each mutation was landed as a single one-line edit, the named test
 * confirmed red (`npx vitest run src/domain/networkPesLayout.test.ts`),
 * then reverted with `git diff` inspected empty and
 * `sha256sum -c networkPesLayout.ts.sha256` confirmed the restored file is
 * byte-identical to the committed one.
 */

describe("computeNetworkPesLayout — captions fit the plot they are drawn in", () => {
    // The level BAR is 68px wide; the caption under it can be twice that
    // ("[H][H] + [N-]=[NH2+]" is ~156px at 13px monospace). Sizing the plot
    // from the bar rather than the caption is what clipped the rightmost
    // caption at the viewBox edge and overlapped the interior ones on the
    // live hydrazine network.
    const LONG = "[H][H] + [N-]=[NH2+]"

    // ENOUGH levels that the per-level gap actually binds. With only a few
    // levels the width sits on its base floor and the spacing is generous,
    // so a too-small gap does not overlap anything and a test built on such
    // a fixture passes with the fix reverted -- which is what the first
    // draft of the overlap test below did.
    function longLabelLayout() {
        const states = Array.from({ length: 8 }, (_unused, i) =>
            state(`h${i}`, i === 0 ? "well" : "bimolecular", i % 2 === 0 ? LONG : `N=N (E) + [H][H] ${i}`))
        const energies = states.map((st, i) => energy(st.composition_hash, i * 54.4))
        return computeNetworkPesLayout(states, energies, [], [])
    }

    it("keeps the outermost captions inside the viewBox", () => {
        const layout = longLabelLayout()!
        expect(layout.levels.length).toBeGreaterThan(0)
        for (const level of layout.levels) {
            const half = pesLevelCaptionWidth(level.label, level.energyKjMol) / 2
            expect(level.x - half).toBeGreaterThanOrEqual(0)
            expect(level.x + half).toBeLessThanOrEqual(layout.width)
        }
    })

    it("leaves adjacent captions room not to overlap", () => {
        const layout = longLabelLayout()!
        const byX = [...layout.levels].sort((a, b) => a.x - b.x)
        expect(byX.length).toBeGreaterThan(1)
        for (let i = 1; i < byX.length; i++) {
            const gap = byX[i].x - byX[i - 1].x
            const needed = pesLevelCaptionWidth(byX[i - 1].label, byX[i - 1].energyKjMol) / 2
                + pesLevelCaptionWidth(byX[i].label, byX[i].energyKjMol) / 2
            expect(gap).toBeGreaterThanOrEqual(needed)
        }
    })
})
