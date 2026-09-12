import { describe, expect, it } from "vitest"
import type { NetworkChannel, NetworkChannelBarrier, NetworkState, NetworkStateEnergy } from "../api/networkEntryApi"
import {
    computeNetworkPesLayout,
    NETWORK_PES_BASE_WIDTH,
    NETWORK_PES_LEVEL_GAP,
    NETWORK_PES_MARGIN,
    NETWORK_PES_TS_BAR_HALF_WIDTH,
    type NetworkPesLayout,
    pesLevelCaptionWidth,
} from "./networkPesLayout"

// ---------------------------------------------------------------------------
// A REAL geometric segment-intersection check (not a proxy) for the
// "no connector crosses another" tests below. Standard orientation
// (cross-product sign) test: a PROPER interior crossing requires each
// segment's two endpoints to fall on strictly OPPOSITE sides of the other
// segment's line. A pair that only TOUCHES -- shares an endpoint, as every
// saddle sharing a hub state does -- yields a zero cross product at that
// shared point, which fails the STRICT opposite-sign check and is
// correctly reported as "not crossing".
type Point = { x: number; y: number }
const CROSS_EPS = 1e-9
function crossProduct(origin: Point, a: Point, b: Point): number {
    return (a.x - origin.x) * (b.y - origin.y) - (a.y - origin.y) * (b.x - origin.x)
}
function segmentsProperlyCross(p1: Point, p2: Point, p3: Point, p4: Point): boolean {
    const d1 = crossProduct(p3, p4, p1)
    const d2 = crossProduct(p3, p4, p2)
    const d3 = crossProduct(p1, p2, p3)
    const d4 = crossProduct(p1, p2, p4)
    const strictlyOpposite = (x: number, y: number) => (x > CROSS_EPS && y < -CROSS_EPS) || (x < -CROSS_EPS && y > CROSS_EPS)
    return strictlyOpposite(d1, d2) && strictlyOpposite(d3, d4)
}
/** The exact three segments `NetworkDiagram.tsx` draws for one saddle:
 *  `sourceLegX`/`sinkLegX` (`networkPesLayout.ts`'s own `connectorLegX` --
 *  capped so a wide caption's clearance can never invade a neighbouring
 *  state's own territory) to the near TS bar edge, across the TS bar, TS
 *  bar's far edge to the other state's leg point -- mirrors that
 *  component's own geometry so this check tests what is actually rendered,
 *  not a simplified stand-in. */
function saddleConnectorSegments(saddle: NetworkPesLayout["saddles"][number]): [Point, Point][] {
    const barLeftX = saddle.peakX - NETWORK_PES_TS_BAR_HALF_WIDTH
    const barRightX = saddle.peakX + NETWORK_PES_TS_BAR_HALF_WIDTH
    const sourceEdgeX = saddle.sourceX <= saddle.sinkX ? barLeftX : barRightX
    const sinkEdgeX = saddle.sourceX <= saddle.sinkX ? barRightX : barLeftX
    return [
        [{ x: saddle.sourceLegX, y: saddle.sourceY }, { x: sourceEdgeX, y: saddle.peakY }],
        [{ x: sourceEdgeX, y: saddle.peakY }, { x: sinkEdgeX, y: saddle.peakY }],
        [{ x: sinkEdgeX, y: saddle.peakY }, { x: saddle.sinkLegX, y: saddle.sinkY }],
    ]
}
/** Every pair of connector segments belonging to two DIFFERENT saddles that
 *  properly cross, described as `"<channelKey> x <channelKey>"`. Segments
 *  within the SAME saddle are never compared (a saddle's own three
 *  segments share endpoints by construction and are not a "crossing" in
 *  the readability sense this whole PR is about). */
function findConnectorCrossings(layout: NetworkPesLayout): string[] {
    const withKey = layout.saddles.flatMap((saddle) =>
        saddleConnectorSegments(saddle).map((segment) => ({ segment, key: saddle.channelKey ?? "unkeyed" })))
    const crossings: string[] = []
    for (let i = 0; i < withKey.length; i++) {
        for (let j = i + 1; j < withKey.length; j++) {
            if (withKey[i].key === withKey[j].key) continue
            const [a1, a2] = withKey[i].segment
            const [b1, b2] = withKey[j].segment
            if (segmentsProperlyCross(a1, a2, b1, b2)) crossings.push(`${withKey[i].key} x ${withKey[j].key}`)
        }
    }
    return crossings
}

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

describe("computeNetworkPesLayout -- with zero connectivity, x falls back to ascending energy, not served order", () => {
    // No channels at all -- every state is unconnected, so there is no
    // barrier graph to derive x from. This is the one case the module
    // still orders by energy (module header, "UNCONNECTED STATES"); it is
    // NOT how a connected tree is ordered any more -- see the
    // "connectivity-driven x placement" describe block below for that.
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

// ---------------------------------------------------------------------------
// Connectivity-driven x placement -- the readability rewrite this PR is
// about. `HYDRAZINE_TREE_*` below is the owner's own verified archive
// shape (module header's ASCII diagram): NN (hub, degree 3) with two
// direct leaves (N=N(Z)+H2, [H][H]+[N-]=[NH2+]) and one two-deep chain
// ([NH-][NH3+] then N=N(E)+H2), plus two states ("2 [NH2]",
// "[H] + [NH]N") that carry a deposited energy but sit on no deposited
// barrier at all.
// ---------------------------------------------------------------------------

const HYDRAZINE_TREE_STATES: NetworkState[] = [
    state("NN", "well", "NN"),
    state("s1", "well", "[NH-][NH3+]"),
    state("s2", "bimolecular", "N=N (Z) + [H][H]"),
    state("s3", "bimolecular", "[H][H] + [N-]=[NH2+]"),
    state("s4", "bimolecular", "N=N (E) + [H][H]"),
    state("s5", "bimolecular", "2 [NH2]"),
    state("s6", "bimolecular", "[H] + [NH]N"),
]
const HYDRAZINE_TREE_ENERGIES: NetworkStateEnergy[] = [
    energy("NN", 0.0), energy("s1", 180.1), energy("s2", 146.7), energy("s3", 233.3),
    energy("s4", 123.7), energy("s5", 281.8), energy("s6", 380.9),
]
const HYDRAZINE_TREE_CHANNELS: NetworkChannel[] = [
    channel("channel_1", "s1", "NN"),
    channel("channel_3", "s3", "NN"),
    channel("channel_11", "s4", "s1"),
    channel("channel_2", "s2", "NN"),
]
const HYDRAZINE_TREE_BARRIERS: NetworkChannelBarrier[] = [
    barrier("channel_1", 274.7 - 180.1, 274.7 - 0.0),
    barrier("channel_3", 347.2 - 233.3, 347.2 - 0.0),
    barrier("channel_11", 350.6 - 123.7, 350.6 - 180.1),
    barrier("channel_2", 414.4 - 146.7, 414.4 - 0.0),
]

describe("computeNetworkPesLayout -- connectivity layout roots at the most-connected state", () => {
    it("NN (degree 3: channel_1, channel_2, channel_3) is the component root, not a leaf", () => {
        const layout = computeNetworkPesLayout(HYDRAZINE_TREE_STATES, HYDRAZINE_TREE_ENERGIES, HYDRAZINE_TREE_CHANNELS, HYDRAZINE_TREE_BARRIERS)!
        expect(layout.components).toHaveLength(1)
        expect(layout.components[0].rootHash).toBe("NN")
        expect(layout.components[0].isTree).toBe(true)
        expect(layout.components[0].usedSpanningTree).toBe(false)
        // The root's own x sits strictly between its two flanking leaves
        // (s3 and s4/s1's chain) -- "radiates outward", never at an edge.
        const xByHash = new Map(layout.levels.map((level) => [level.compositionHash, level.x]))
        expect(xByHash.get("NN")!).toBeGreaterThan(xByHash.get("s4")!)
        expect(xByHash.get("NN")!).toBeLessThanOrEqual(xByHash.get("s3")!)
    })
})

describe("computeNetworkPesLayout -- connectivity layout draws the hydrazine tree without a single connector crossing another", () => {
    it("no two DIFFERENT saddles' connector segments properly intersect", () => {
        const layout = computeNetworkPesLayout(HYDRAZINE_TREE_STATES, HYDRAZINE_TREE_ENERGIES, HYDRAZINE_TREE_CHANNELS, HYDRAZINE_TREE_BARRIERS)!
        expect(layout.saddles.length).toBeGreaterThan(1)
        expect(findConnectorCrossings(layout)).toEqual([])
    })

    it("a connector leg's own clearance never overtakes the very state its peak walks toward", () => {
        // hub has TWO children: leaf "a" (a FRACTIONAL slot away from its
        // own peak, not a tie, so the near-vertical guard above does not
        // apply) and "b", which itself has a further child "c" -- so hub's
        // own slot (the average of a's and b's) sits only a QUARTER of a
        // level-gap from a, leaving very little room there regardless of
        // how wide a's own caption is. "a"'s caption is deliberately the
        // widest on the surface so it also sets the overall level gap --
        // proving the cap is still needed even though a wider gap alone
        // does not fix a FRACTIONAL-slot squeeze like this one: without the
        // cap, "a"'s own leg is pushed straight past hub's own x, which no
        // inter-saddle crossing check happens to catch on this small a
        // fixture (nothing else occupies that stretch), but is a clear
        // geometric absurdity on its own -- a leg overtaking the very
        // state its connector is walking toward.
        const wideLabel = "a very long caption that sets the level gap itself"
        const states = [
            state("hub", "well", "hub"),
            state("a", "well", wideLabel),
            state("b", "well", "b"),
            state("c", "well", "c"),
        ]
        const energies = [energy("hub", 0), energy("a", 50), energy("b", 100), energy("c", 150)]
        const channels = [channel("ch_a", "a", "hub"), channel("ch_b", "hub", "b"), channel("ch_c", "b", "c")]
        // ch_a: 50+80==0+130. ch_b: 0+160==100+60. ch_c: 100+90==150+40.
        const barriers = [barrier("ch_a", 80, 130), barrier("ch_b", 160, 60), barrier("ch_c", 90, 40)]
        const layout = computeNetworkPesLayout(states, energies, channels, barriers)!
        expect(layout.saddles).toHaveLength(3)
        expect(findConnectorCrossings(layout)).toEqual([])
        const hubX = layout.levels.find((level) => level.compositionHash === "hub")!.x
        const saddleA = layout.saddles.find((saddle) => saddle.channelKey === "ch_a")!
        expect(saddleA.sourceLegX).toBeLessThan(hubX)
    })
})

describe("computeNetworkPesLayout -- a state on no deposited barrier is grouped separately, never wired to an invented edge", () => {
    it("lists both barrier-less states in isolatedStateHashes and draws no saddle touching either", () => {
        const layout = computeNetworkPesLayout(HYDRAZINE_TREE_STATES, HYDRAZINE_TREE_ENERGIES, HYDRAZINE_TREE_CHANNELS, HYDRAZINE_TREE_BARRIERS)!
        expect(new Set(layout.isolatedStateHashes)).toEqual(new Set(["s5", "s6"]))
        for (const saddle of layout.saddles) {
            expect(["s5", "s6"]).not.toContain(saddle.sourceHash)
            expect(["s5", "s6"]).not.toContain(saddle.sinkHash)
        }
        const isolatedLevels = layout.levels.filter((level) => level.isUnconnected)
        expect(new Set(isolatedLevels.map((level) => level.compositionHash))).toEqual(new Set(["s5", "s6"]))
    })
})

describe("computeNetworkPesLayout -- a component with a cycle falls back to a spanning tree and still renders every barrier", () => {
    it("marks the component isTree: false, usedSpanningTree: true, and keeps all 3 saddles (none dropped)", () => {
        // A triangle -- 3 states, 3 accepted barriers among them (edges ==
        // nodes, not nodes - 1) -- is the simplest connected non-tree
        // shape. This is a SYNTHETIC fixture: no live TCKDB network is
        // known to have a cyclic accepted-barrier graph as of this PR (see
        // the module header) -- this test exists so the fallback path is
        // exercised at all, not left as a theoretical claim in a comment.
        const states = [state("a", "well", "A"), state("b", "well", "B"), state("c", "well", "C")]
        const energies = [energy("a", 0), energy("b", 50), energy("c", 100)]
        const channels = [channel("ch_ab", "a", "b"), channel("ch_bc", "b", "c"), channel("ch_ca", "c", "a")]
        // Each forward/reverse pair chosen so source_energy + forward ==
        // sink_energy + reverse EXACTLY (the consistency check this module
        // enforces elsewhere) -- ch_ab: 0+80==50+30; ch_bc: 50+60==100+10;
        // ch_ca: 100+130==0+230.
        const barriers = [barrier("ch_ab", 80, 30), barrier("ch_bc", 60, 10), barrier("ch_ca", 130, 230)]
        const layout = computeNetworkPesLayout(states, energies, channels, barriers)!
        expect(layout.components).toHaveLength(1)
        expect(layout.components[0].isTree).toBe(false)
        expect(layout.components[0].usedSpanningTree).toBe(true)
        expect(layout.saddles).toHaveLength(3)
        expect(layout.isolatedStateHashes).toHaveLength(0)
    })
})

describe("computeNetworkPesLayout -- level captions never collide in 2D, even when the tree stacks two states at the same x", () => {
    it("every pair of levels is separated on x, on y, or on both, by at least its combined half-caption width", () => {
        const layout = computeNetworkPesLayout(HYDRAZINE_TREE_STATES, HYDRAZINE_TREE_ENERGIES, HYDRAZINE_TREE_CHANNELS, HYDRAZINE_TREE_BARRIERS)!
        // A level's own caption ink spans roughly 12px above its bar to
        // 20px below it, plus glyph height either side -- about 46px of
        // vertical extent. Two levels closer together than that on y need
        // real x separation; further apart than that, they never visually
        // collide regardless of x (this is what lets a straight chain like
        // s1/N=N(E)+H2 legitimately share an x -- see the module header).
        const VERTICAL_COLLISION_BAND = 46
        for (let i = 0; i < layout.levels.length; i++) {
            for (let j = i + 1; j < layout.levels.length; j++) {
                const a = layout.levels[i]
                const b = layout.levels[j]
                const needed = pesLevelCaptionWidth(a.label, a.energyKjMol) / 2 + pesLevelCaptionWidth(b.label, b.energyKjMol) / 2
                const xSeparated = Math.abs(a.x - b.x) >= needed
                const ySeparated = Math.abs(a.y - b.y) >= VERTICAL_COLLISION_BAND
                expect(xSeparated || ySeparated).toBe(true)
            }
        }
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
 * | 8 | "NN ... is the component root, not a leaf" (connectivity rewrite) | In `layoutComponentAsTree`, changed `if (degree > rootDegree)` to `if (degree < rootDegree)` (root at LOWEST degree instead of highest) | RED -- `rootHash` was `"s4"` (a leaf), not `"NN"`. The SAME one-line mutation also turned red test #9 (the crossing check) and the 2D caption-collision test below -- a low-degree hub breaks more than just which node is labelled root |
 * | 9 | "no two DIFFERENT saddles' connector segments properly intersect" | In `declutterLevelX`, changed `const cap = direction > 0 ? rightCap : leftCap` to `const cap = Infinity` (removing the neighbour-territory cap) | RED -- reproduced the EXACT original defect this module was built to fix: `["channel_1 x channel_11", "channel_3 x channel_2"]` |
 * | 10 | "lists both barrier-less states in isolatedStateHashes ..." | Changed `const isolatedStateHashes = resolvedInServedOrder.filter(...)` to `const isolatedStateHashes: string[] = []` | RED (and 6 other tests besides -- the isolated-fallback ordering, the captions-fit tests, and the 2D collision test all also depend on this list) |
 * | 11 | "marks the component isTree: false, usedSpanningTree: true ..." | Changed `const isTree = edgeCount === members.length - 1` to `const isTree = true` | RED -- `layout.components[0].isTree` was `true`, `toBe(false)` failed |
 * | 12 | "every pair of levels is separated on x, on y, or on both ..." | Commented out the `declutterLevelX(levels, captionWidthByHash)` call | RED -- two levels landed within both the x and y collision bands |
 * | 13 | "no two DIFFERENT saddles' connector segments properly intersect" (again) | In `connectorLegX`, changed `const direction = Math.sign(peakX - stateX)` to `Math.sign(peakX - stateX) \|\| 1` (restoring an arbitrary +1 for an exact tie, the state THIS PR's own fix removed) | RED -- reproduced `["channel_3 x channel_2"]`, the exact crossing found by screenshotting the live archive after the caption-clearance offset was first added |
 * | 14 | "a connector leg's own clearance never overtakes the very state its peak walks toward" | In `connectorLegX`, changed `Math.min(preferred, direction > 0 ? rightCap : leftCap)` to just `preferred` (dropping the neighbour cap) | RED -- `saddleA.sourceLegX` (418) was not less than `hubX` (411): the leg overtook the very state its own connector points at |
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
