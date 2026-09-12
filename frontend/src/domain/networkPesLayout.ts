import type { NetworkChannel, NetworkChannelBarrier, NetworkState, NetworkStateEnergy } from "../api/networkEntryApi"
import { domainWithPadding, linearScale } from "./chartScale"

/**
 * Client-side layout for `NetworkDiagram.tsx`'s potential-energy surface
 * (PES). States become horizontal level bars at their own deposited
 * relative energy; a channel becomes a saddle point ONLY when it carries a
 * deposited barrier -- see the invariant below, which is the one rule this
 * whole module exists to enforce.
 *
 * INVARIANT (owner's brief, "never draw a barrier that was not deposited"):
 * a channel with no `NetworkChannelBarrier` row produces no saddle-point
 * element, full stop -- not a peak, not an interpolated hump, not a guessed
 * height. On the live hydrazine archive that is 17 of 21 channels; they
 * are visible only in `NetworkDiagram.tsx`'s own accessible channel table,
 * which already carries all 21 regardless of what this module computes.
 *
 * SADDLE HEIGHT (owner's brief): `source_energy + forward_barrier_kj_mol`,
 * never the reverse-derived figure and never an average of the two. The
 * two are supposed to agree -- `source_energy + forward == sink_energy +
 * reverse` is the physical consistency check a correctly-deposited barrier
 * satisfies -- so this module checks that agreement (within
 * `NETWORK_PES_BARRIER_TOLERANCE_KJ_MOL`) and OMITS the saddle entirely
 * when it fails, rather than rendering a number that isn't trustworthy
 * either way it's computed. See `NetworkPesExclusion` below.
 *
 * X ORDERING -- rewritten from "ascending energy" to "connectivity",
 * reading a published PES rather than a rank list (owner: "hard to read
 * with lines overlapping etc", matched against
 * `raghunath_n2h4_pes_fig7.1.png`, the group's own literature figure for
 * this exact chemical system):
 *
 *   1. Build a graph whose nodes are PLOTTED states (a deposited energy)
 *      and whose edges are ACCEPTED saddles (a deposited, placeable,
 *      internally-consistent barrier -- exactly the ones that get drawn).
 *      A state with zero such edges cannot be positioned by connectivity
 *      at all; see "UNCONNECTED STATES" below.
 *   2. Within each connected component, root at the state with the most
 *      edges (ties: lower energy, then served order) and walk outward.
 *      Each leaf claims the next integer "slot"; an internal node's slot
 *      is the average of its children's -- the standard compact-tree
 *      placement, and the one that provably draws ZERO crossing
 *      connectors for an actual tree, because every subtree owns a
 *      contiguous, non-overlapping slot range. A hub with several leaf
 *      children therefore has its own children spread out on both sides
 *      of it (its slot is their average), reproducing the reference
 *      figure's "radiates outward from a bottom-centre well" shape without
 *      hand-picking a centre.
 *   3. A component whose accepted-saddle edge count exceeds `nodes - 1` has
 *      a cycle and is not a tree. It still renders -- `usedSpanningTree`
 *      on `NetworkPesComponent` records that position was computed from a
 *      spanning tree of the component (same root/walk rule above, only
 *      tree edges consulted for x), and the non-spanning edge(s) are drawn
 *      from the resulting positions like any other saddle. They may cross;
 *      that is the documented, honest fallback for a graph shape this
 *      layout was not designed to avoid crossings for, not a crash and not
 *      a silently dropped channel. No live TCKDB network is known to hit
 *      this branch as of this PR (the hydrazine archive's accepted-barrier
 *      graph is a tree), so it is exercised only by a synthetic fixture.
 *   4. Components are laid out left to right, largest first (ties: lowest
 *      served index of any member), each occupying its own contiguous slot
 *      range with a one-slot gap from the next.
 *
 * UNCONNECTED STATES -- a plotted state that is the endpoint of zero
 * accepted saddles (on the live hydrazine archive: "2 [NH2]" and "[H] +
 * [NH]N", both deposited, neither on the path of any deposited barrier)
 * cannot be given a position by the algorithm above -- there is no edge to
 * walk. They are not dropped (that would silently understate the
 * archive) and no edge is invented for them (that would violate invariant
 * 1's spirit one level up: never draw a connection that was not
 * deposited). Instead they are placed as their own group, ascending by
 * energy (the same honest, arbitrary-but-legible tie-break the whole
 * surface used before this rewrite, kept for the one case where no
 * connectivity signal exists at all), one extra slot-gap to the right of
 * every connected component, with a dashed divider and a stated count so a
 * reader never mistakes "no connector drawn" for "no barrier exists" --
 * `NetworkDiagram.tsx` states exactly that via `isolatedStateHashes`.
 *
 * SADDLES ARE LEVEL BARS, NOT POINTS: `NetworkDiagram.tsx` draws each
 * saddle as a short horizontal bar at `peakY`, `NETWORK_PES_TS_BAR_HALF_WIDTH`
 * either side of `peakX`, with its own energy caption above it -- the
 * reference figure's convention ("TS4 / 113.5") minus a name, because
 * nothing in this API gives a transition state a depositor-facing label
 * that is not `channel_key` (invariant 4 forbids rendering that). The two
 * connector legs run from each endpoint state to the NEAR edge of that bar
 * (whichever edge is on that state's own side), producing the classic
 * peak/valley trapezoid instead of a single vertex.
 *
 * WIDTH: grows with the number of PLOTTED levels and their captions
 * (`NETWORK_PES_LEVEL_GAP` per slot), never the fixed old force-diagram
 * canvas. `NetworkDiagram.tsx` wraps the SVG in `overflow-x: auto`
 * (invariant 6 -- fixed-pixel SVG, never percentage-scaled), so a wide
 * network simply scrolls.
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const NETWORK_PES_HEIGHT = 460
export const NETWORK_PES_MARGIN = { top: 40, right: 40, bottom: 72, left: 72 } as const
/** Fixed pixel size for a small network (matches this archive's live 7-state
 *  network without growing) -- same "fixed-pixel SVG" convention as
 *  `ARRHENIUS_CHART_WIDTH`/the old `NETWORK_DIAGRAM_WIDTH`. */
export const NETWORK_PES_BASE_WIDTH = 760
/** Pixel width of one x "slot" -- the unit a tree walk counts leaves in
 *  (`assignConnectivityLayout` below) before conversion to pixels. Also the
 *  historical minimum gap between two adjacent level captions, kept as the
 *  floor so a sparse layout never looks cramped. */
export const NETWORK_PES_LEVEL_GAP = 130
/** Half-width of one level bar, in pixels either side of its centre x. */
export const NETWORK_PES_LEVEL_HALF_WIDTH = 34
/** Half-width of one saddle's TS bar -- narrower than a state's own level
 *  bar (`NETWORK_PES_LEVEL_HALF_WIDTH`) so the two read as different kinds
 *  of mark at a glance, matching the reference figure's thinner TS bars. */
export const NETWORK_PES_TS_BAR_HALF_WIDTH = 24
/** Slot-space gap left between one connectivity component and the next
 *  (and again before the unconnected-states group), so components read as
 *  visually distinct groups rather than one continuous line of states. */
export const NETWORK_PES_COMPONENT_GAP_SLOTS = 1

/** Advance width of one character at `--type-data-font` (13px monospace),
 *  which is what the level and energy captions render in. Used to size the
 *  plot from the CAPTIONS rather than from the level bars: a level bar is
 *  68px wide, but "[H][H] + [N-]=[NH2+]" under it is over twice that, and
 *  sizing to the bar is what made the rightmost caption overflow the
 *  viewBox and the interior captions collide. SVG cannot measure text
 *  without layout, and monospace is the one face where a character count
 *  IS the width, which is why these captions are monospace to begin with. */
export const NETWORK_PES_CAPTION_CHAR_WIDTH = 7.8

/** Breathing room between two adjacent captions, and between the outermost
 *  caption and the viewBox edge. */
export const NETWORK_PES_CAPTION_PAD = 14

/** Widest string rendered beneath a level: its own label, or its energy
 *  caption, whichever is longer. */
export function pesLevelCaptionWidth(label: string, energyKjMol: number): number {
    const energyCaption = `${energyKjMol.toFixed(1)} kJ/mol`
    return Math.max(label.length, energyCaption.length) * NETWORK_PES_CAPTION_CHAR_WIDTH
}
const Y_DOMAIN_PADDING_FRACTION = 0.14
/**
 * Owner's brief: "I verified for all four that source_energy +
 * forward_barrier == sink_energy + reverse_barrier to within 0.05 kJ/mol."
 * The same tolerance is the accept/reject boundary here -- a barrier row
 * whose two sides disagree by more than this is treated as a data problem,
 * not rendered (never averaged; see the module header comment above).
 */
export const NETWORK_PES_BARRIER_TOLERANCE_KJ_MOL = 0.05

/** Two level captions whose bounding boxes would come within this many
 *  pixels of each other on the y axis are close enough to be considered
 *  the "same row" for horizontal-collision purposes -- a level's own
 *  caption spans roughly 12px above to 20px below its bar plus text
 *  height, so two levels much further apart than this in y never collide
 *  regardless of x (their captions simply sit at different heights on the
 *  canvas), and only a genuine near-vertical stack needs nudging apart. */
const NETWORK_PES_LEVEL_VERTICAL_BAND_PX = 40
const NETWORK_PES_LEVEL_NUDGE_STEP_PX = 26
const NETWORK_PES_LEVEL_DECLUTTER_MAX_ATTEMPTS = 20

// ---------------------------------------------------------------------------
// Shapes
// ---------------------------------------------------------------------------

export interface NetworkPesLevel {
    compositionHash: string
    /** Always `composition.state_label` (invariant 3) -- never
     *  `states[].label`, never the raw hash. */
    label: string
    isWell: boolean
    energyKjMol: number
    x: number
    y: number
    /** True when this state is the endpoint of zero accepted saddles --
     *  drawn in the separate unconnected-states group, never given a
     *  connectivity-derived position (there is no edge to derive one
     *  from). See the module header's "UNCONNECTED STATES" section. */
    isUnconnected: boolean
}

export interface NetworkPesSaddle {
    /** Data face only (invariant 4) -- carried for `data-channel-key` and
     *  for resolving the matching table row, never rendered as `<text>`,
     *  an `aria-label`, or a `title`. */
    channelKey: string | null
    kind: string
    hasKinetics: boolean
    sourceHash: string
    sinkHash: string
    /** `composition.state_label` for the two endpoints -- what every
     *  user-facing description of this saddle point is built from. */
    sourceLabel: string
    sinkLabel: string
    heightKjMol: number
    sourceX: number
    sourceY: number
    sinkX: number
    sinkY: number
    /** Where this saddle's connector leg actually touches the source/sink
     *  state on the x axis -- `sourceX`/`sinkX` offset toward the peak, far
     *  enough to clear that state's own caption where there is room, but
     *  never past the midpoint to the nearest OTHER state, so a wide
     *  caption's own clearance can never push a leg into a neighbouring
     *  state's connector (see `computeConnectorLegX`'s own comment: this
     *  is what a fixed clearance, tried first, got wrong). `sourceY`/
     *  `sinkY` still apply -- a state's bar is horizontal, no y offset. */
    sourceLegX: number
    sinkLegX: number
    /** Centre of the TS bar. The bar itself spans
     *  `peakX ± NETWORK_PES_TS_BAR_HALF_WIDTH` at `peakY`. */
    peakX: number
    peakY: number
}

/** A deposited barrier row that could not be placed on the surface, and
 *  why. Never carries `channelKey` as user-facing text (invariant 4) --
 *  `NetworkDiagram.tsx` renders the reason by chemistry (source/sink
 *  labels), exactly as a shown saddle point's own `aria-label` does. */
export interface NetworkPesExclusion {
    channelKey: string | null
    sourceLabel: string | null
    sinkLabel: string | null
    reason: string
}

/** One connected component of the accepted-saddle graph -- every state in
 *  `hashes` was reachable from `rootHash` by walking only accepted
 *  saddles. `isTree` is `edges === hashes.length - 1`; when it is `false`
 *  (a cycle), `usedSpanningTree` is `true` and x was derived from a
 *  spanning tree of the component rather than the full graph -- see the
 *  module header's point 3. */
export interface NetworkPesComponent {
    rootHash: string
    hashes: string[]
    isTree: boolean
    usedSpanningTree: boolean
}

export interface NetworkPesLayout {
    width: number
    height: number
    yDomain: [number, number]
    plotTop: number
    plotBottom: number
    levels: NetworkPesLevel[]
    saddles: NetworkPesSaddle[]
    excludedSaddles: NetworkPesExclusion[]
    /** States present on this network that carry no deposited energy --
     *  omitted from the surface entirely (never guessed), counted here so
     *  the page can state "X of Y states" rather than silently drawing a
     *  smaller surface with no explanation. */
    missingEnergyStateCount: number
    /** Connected components of the accepted-saddle graph, largest first.
     *  Empty when every plotted state is unconnected. */
    components: NetworkPesComponent[]
    /** Composition hashes of states plotted but reachable by zero accepted
     *  saddles -- same hashes as the levels with `isUnconnected: true`,
     *  exposed at the layout level so the page can state the count without
     *  re-filtering `levels`. */
    isolatedStateHashes: string[]
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

/**
 * `null` when no state on this network carries a deposited energy -- there
 * is nothing to plot, and the caller (`NetworkDiagram.tsx`) renders an
 * explicit "no energies deposited" sentence rather than an empty canvas.
 */
/**
 * Which rule decides a state's horizontal position.
 *
 * `connectivity` is the default and the one the published PES for this
 * system uses: root at the most-connected state, branches radiating out,
 * so every connector stays short. `energy` reproduces the ascending-energy
 * ordering this surface shipped with before, which reads as a left-to-right
 * energy ladder but forces long diagonals across the plot (measured on the
 * live hydrazine network: 9 proper connector crossings, against 1 for
 * `connectivity`).
 *
 * Both are offered because the choice is a readability judgement the owner
 * wanted a second opinion on, not a correctness one. NEITHER changes a
 * single energy: y position, saddle heights and which channels are drawn
 * are identical under both.
 */
export type NetworkPesLayoutMode = "connectivity" | "energy"

export const NETWORK_PES_LAYOUT_MODES: readonly NetworkPesLayoutMode[] = ["connectivity", "energy"]

/** Ascending-energy slot assignment: the pre-connectivity behaviour. */
function assignEnergyRankLayout(
    hashes: readonly string[],
    energyByHash: Map<string, number>,
    tieBreak: (hash: string) => number,
): { slotByHash: Map<string, number>; components: NetworkPesComponent[]; isolatedStateHashes: string[] } {
    const ordered = [...hashes].sort((a, b) => {
        const diff = energyByHash.get(a)! - energyByHash.get(b)!
        if (diff !== 0) return diff
        return tieBreak(a) - tieBreak(b)
    })
    const slotByHash = new Map<string, number>()
    ordered.forEach((hash, index) => slotByHash.set(hash, index))
    // No connectivity was consulted, so there is no root and no component
    // structure to report, and no state is "isolated" in this mode -- every
    // state is positioned by the same rule.
    return { slotByHash, components: [], isolatedStateHashes: [] }
}

export function computeNetworkPesLayout(
    states: readonly NetworkState[],
    stateEnergies: readonly NetworkStateEnergy[],
    channels: readonly NetworkChannel[],
    channelBarriers: readonly NetworkChannelBarrier[],
    mode: NetworkPesLayoutMode = "connectivity",
): NetworkPesLayout | null {
    const stateByHash = new Map(states.map((state) => [state.composition_hash, state]))

    // First deposited energy for a given hash wins (defensive against a
    // duplicate row in a malformed payload) -- never overwritten by a
    // later one, so this function's output is deterministic regardless of
    // any such duplication.
    const energyByHash = new Map<string, number>()
    for (const entry of stateEnergies) {
        if (!stateByHash.has(entry.state_composition_hash)) continue
        if (!energyByHash.has(entry.state_composition_hash)) {
            energyByHash.set(entry.state_composition_hash, entry.energy_kj_mol)
        }
    }

    // Served `states[]` order first -- the one deterministic tie-break
    // basis every ordering decision below (root choice, child order,
    // unconnected-group order, component order) falls back to.
    const resolvedInServedOrder = states.map((state) => state.composition_hash).filter((hash) => energyByHash.has(hash))
    if (resolvedInServedOrder.length === 0) return null

    const servedIndex = new Map(resolvedInServedOrder.map((hash, index) => [hash, index]))
    const tieBreak = (hash: string): number => servedIndex.get(hash) ?? Number.MAX_SAFE_INTEGER

    // ---- candidate saddles: join each barrier to its channel, then check
    // the two things that can make a deposited barrier unplaceable. Moved
    // ahead of x placement (unlike the old ascending-energy layout) because
    // x now needs the ACCEPTED saddles as graph edges. ----
    const channelByKey = new Map(
        channels.filter((channel): channel is NetworkChannel & { channel_key: string } => channel.channel_key != null)
            .map((channel) => [channel.channel_key, channel]),
    )

    const excludedSaddles: NetworkPesExclusion[] = []
    const accepted: { barrier: NetworkChannelBarrier; channel: NetworkChannel; heightKjMol: number }[] = []

    for (const barrier of channelBarriers) {
        const channel = channelByKey.get(barrier.channel_key)
        if (!channel) {
            excludedSaddles.push({
                channelKey: barrier.channel_key,
                sourceLabel: null,
                sinkLabel: null,
                reason: "this deposited barrier's channel reference did not match any channel on this network",
            })
            continue
        }
        const sourceLabel = stateByHash.get(channel.source_state_composition_hash)?.composition.state_label || "unresolved state"
        const sinkLabel = stateByHash.get(channel.sink_state_composition_hash)?.composition.state_label || "unresolved state"
        const sourceEnergy = energyByHash.get(channel.source_state_composition_hash)
        const sinkEnergy = energyByHash.get(channel.sink_state_composition_hash)
        if (sourceEnergy == null || sinkEnergy == null) {
            excludedSaddles.push({
                channelKey: barrier.channel_key,
                sourceLabel,
                sinkLabel,
                reason: "the source or sink state's own energy is not deposited, so this transition state cannot be placed on the surface",
            })
            continue
        }
        // Saddle height is ALWAYS source + forward (owner's brief) -- the
        // reverse-derived figure below exists only to check agreement,
        // never to compute the plotted value, and never to be averaged in.
        const forwardHeight = sourceEnergy + barrier.forward_barrier_kj_mol
        const reverseHeight = sinkEnergy + barrier.reverse_barrier_kj_mol
        const disagreementKjMol = Math.abs(forwardHeight - reverseHeight)
        if (disagreementKjMol > NETWORK_PES_BARRIER_TOLERANCE_KJ_MOL) {
            excludedSaddles.push({
                channelKey: barrier.channel_key,
                sourceLabel,
                sinkLabel,
                reason: `the forward and reverse barrier disagree on the transition-state height by ${disagreementKjMol.toFixed(3)} kJ/mol, past the ${NETWORK_PES_BARRIER_TOLERANCE_KJ_MOL} kJ/mol tolerance; omitted rather than averaged`,
            })
            continue
        }
        accepted.push({ barrier, channel, heightKjMol: forwardHeight })
    }

    // ---- connectivity graph over PLOTTED states, edges = accepted
    // saddles only (invariant 1 propagates here: a channel with no
    // placeable barrier contributes no edge, so it cannot pull two states
    // together on the surface either) ----
    const adjacency = new Map<string, Set<string>>()
    for (const hash of resolvedInServedOrder) adjacency.set(hash, new Set())
    for (const { channel } of accepted) {
        const a = channel.source_state_composition_hash
        const b = channel.sink_state_composition_hash
        if (!adjacency.has(a) || !adjacency.has(b)) continue
        adjacency.get(a)!.add(b)
        adjacency.get(b)!.add(a)
    }

    const { slotByHash, components, isolatedStateHashes } = mode === "energy"
        ? assignEnergyRankLayout(resolvedInServedOrder, energyByHash, tieBreak)
        : assignConnectivityLayout(resolvedInServedOrder, adjacency, accepted, energyByHash, tieBreak)

    // ---- size the plot from the CAPTIONS, not the level bars (unchanged
    // rationale from the ascending-energy layout this replaces) ----
    const captionWidthByHash = new Map(resolvedInServedOrder.map((hash) =>
        [hash, pesLevelCaptionWidth(stateByHash.get(hash)?.composition.state_label || "unresolved state", energyByHash.get(hash) ?? 0)]))
    const widestCaption = Math.max(0, ...captionWidthByHash.values())
    const levelGap = Math.max(NETWORK_PES_LEVEL_GAP, Math.ceil(widestCaption) + NETWORK_PES_CAPTION_PAD)

    const slotValues = [...slotByHash.values()]
    const minSlot = Math.min(...slotValues)
    // Only the LEFT margin is derived analytically, from whichever level(s)
    // sit at the smallest slot -- it anchors `pixelXForSlot`'s own origin.
    // The right-hand edge is instead settled by the viewBox-safety clamp
    // below (after decluttering, which can move a caption further right
    // than this initial slot placement would suggest), which is the more
    // robust source of truth for "how wide does this actually need to be".
    const edgeHalf = (extremeSlot: number): number => Math.max(
        0, ...resolvedInServedOrder.filter((hash) => slotByHash.get(hash) === extremeSlot).map((hash) => captionWidthByHash.get(hash)! / 2),
    )
    const marginLeft = Math.max(NETWORK_PES_MARGIN.left, edgeHalf(minSlot) + NETWORK_PES_CAPTION_PAD)

    const pixelXForSlot = (slot: number): number => marginLeft + (slot - minSlot) * levelGap

    // ---- y scale, over every PLOTTED value (levels AND accepted saddles) ----
    const levelEnergies = resolvedInServedOrder.map((hash) => energyByHash.get(hash)!)
    const saddleHeights = accepted.map((entry) => entry.heightKjMol)
    const yDomain = domainWithPadding([...levelEnergies, ...saddleHeights], Y_DOMAIN_PADDING_FRACTION)
    const plotTop = NETWORK_PES_MARGIN.top
    const plotBottom = NETWORK_PES_HEIGHT - NETWORK_PES_MARGIN.bottom
    // Higher energy renders HIGHER on the page -- domain's low end maps to
    // the bottom pixel, domain's high end to the top pixel.
    const yScale = linearScale(yDomain, [plotBottom, plotTop])

    const isolatedSet = new Set(isolatedStateHashes)
    const levels: NetworkPesLevel[] = resolvedInServedOrder.map((hash) => {
        const state = stateByHash.get(hash)!
        const energyKjMol = energyByHash.get(hash)!
        return {
            compositionHash: hash,
            label: state.composition.state_label || "unnamed state",
            isWell: state.kind === "well",
            energyKjMol,
            x: pixelXForSlot(slotByHash.get(hash)!),
            y: Math.round(yScale(energyKjMol) * 100) / 100,
            isUnconnected: isolatedSet.has(hash),
        }
    })

    // Two states whose connectivity-derived x lands within one slot of each
    // other (a straight chain: a hub's only child, that child's only child,
    // and so on) can legitimately share an x -- that is the desired
    // "vertical stack" reading (see the module header, point 2, and the
    // hydrazine tree's own [NH-][NH3+]/N=N(E)+H2 pair). It is only a
    // collision when their CAPTIONS would actually overlap on screen,
    // which needs both a close x AND a close y -- two states stacked at
    // very different energies never collide regardless of x.
    declutterLevelX(levels, captionWidthByHash)

    // Final viewBox-safety clamp: shift everything right if decluttering
    // pushed a caption's left edge past 0, then size width to the actual
    // rightmost caption edge rather than trusting the pre-declutter
    // estimate. Robust to any layout shape (tree, fallback, or a future
    // one), unlike computing margins analytically up front.
    let minEdge = Infinity
    let maxEdge = -Infinity
    for (const level of levels) {
        const half = captionWidthByHash.get(level.compositionHash)! / 2
        minEdge = Math.min(minEdge, level.x - half)
        maxEdge = Math.max(maxEdge, level.x + half)
    }
    const shift = minEdge < NETWORK_PES_CAPTION_PAD ? NETWORK_PES_CAPTION_PAD - minEdge : 0
    if (shift > 0) {
        for (const level of levels) level.x = Math.round((level.x + shift) * 100) / 100
        maxEdge += shift
    } else {
        for (const level of levels) level.x = Math.round(level.x * 100) / 100
    }
    const width = Math.max(NETWORK_PES_BASE_WIDTH, Math.ceil(maxEdge + NETWORK_PES_CAPTION_PAD))

    const xByHash = new Map(levels.map((level) => [level.compositionHash, level.x]))
    const yByHash = new Map(levels.map((level) => [level.compositionHash, level.y]))

    // Where a connector leg touches its endpoint state -- offset toward
    // the peak, far enough to clear that state's own caption where there
    // is room, but CAPPED at the midpoint to the nearest OTHER state's own
    // final x, so a wide caption can never push a leg past a neighbouring
    // state's own territory. Same shape as `declutterLevelX`'s own cap,
    // over the LEVELS' final (post-declutter) positions rather than their
    // pre-declutter natural ones -- what matters here is not colliding
    // with what actually rendered.
    const distinctLevelX = [...new Set(xByHash.values())].sort((a, b) => a - b)
    function legClearanceCap(x: number): { leftCap: number; rightCap: number } {
        const index = distinctLevelX.indexOf(x)
        const leftCap = index > 0 ? (x - distinctLevelX[index - 1]) / 2 : Infinity
        const rightCap = index < distinctLevelX.length - 1 ? (distinctLevelX[index + 1] - x) / 2 : Infinity
        return { leftCap, rightCap }
    }
    function connectorLegX(stateHash: string, stateX: number, peakX: number): number {
        // A saddle whose peak sits directly above/below this state (a
        // coincidental tie -- e.g. a hub and one of its own children
        // landing on the identical slot) has no real "outward" side to
        // offset toward; pushing it sideways anyway is what sent one leg
        // sweeping wide enough to cross a DIFFERENT saddle's own leg
        // instead of just clearing this state's caption (found by running
        // the geometric crossing check against the live hydrazine tree, not
        // by inspection). A near-vertical connector's own sweep through the
        // caption band is narrow regardless -- leaving it unoffset trades a
        // small, roughly one-character graze for avoiding that crossing.
        if (Math.abs(peakX - stateX) < NETWORK_PES_TS_BAR_HALF_WIDTH) return stateX
        const direction = Math.sign(peakX - stateX)
        const preferred = captionWidthByHash.get(stateHash)! / 2 + NETWORK_PES_CAPTION_PAD
        const { leftCap, rightCap } = legClearanceCap(stateX)
        const clearance = Math.max(NETWORK_PES_LEVEL_HALF_WIDTH, Math.min(preferred, direction > 0 ? rightCap : leftCap))
        return Math.round((stateX + direction * clearance) * 100) / 100
    }

    const saddles: NetworkPesSaddle[] = accepted.map(({ barrier, channel, heightKjMol }) => {
        const sourceX = xByHash.get(channel.source_state_composition_hash)!
        const sinkX = xByHash.get(channel.sink_state_composition_hash)!
        const sourceY = yByHash.get(channel.source_state_composition_hash)!
        const sinkY = yByHash.get(channel.sink_state_composition_hash)!
        const peakX = Math.round(((sourceX + sinkX) / 2) * 100) / 100
        return {
            channelKey: barrier.channel_key,
            kind: channel.kind,
            hasKinetics: channel.has_kinetics,
            sourceHash: channel.source_state_composition_hash,
            sinkHash: channel.sink_state_composition_hash,
            sourceLabel: stateByHash.get(channel.source_state_composition_hash)?.composition.state_label || "unresolved state",
            sinkLabel: stateByHash.get(channel.sink_state_composition_hash)?.composition.state_label || "unresolved state",
            heightKjMol: Math.round(heightKjMol * 1000) / 1000,
            sourceX,
            sourceY,
            sinkX,
            sinkY,
            sourceLegX: connectorLegX(channel.source_state_composition_hash, sourceX, peakX),
            sinkLegX: connectorLegX(channel.sink_state_composition_hash, sinkX, peakX),
            peakX,
            peakY: Math.round(yScale(heightKjMol) * 100) / 100,
        }
    })
    declutterPeakX(saddles)

    return {
        width,
        height: NETWORK_PES_HEIGHT,
        yDomain,
        plotTop,
        plotBottom,
        levels,
        saddles,
        excludedSaddles,
        missingEnergyStateCount: states.length - levels.length,
        components,
        isolatedStateHashes,
    }
}

// ---------------------------------------------------------------------------
// Connectivity-driven x placement -- the module's own centrepiece; see the
// header comment's "X ORDERING" section for the full rationale.
// ---------------------------------------------------------------------------

function assignConnectivityLayout(
    resolvedInServedOrder: string[],
    adjacency: Map<string, Set<string>>,
    accepted: { channel: NetworkChannel }[],
    energyByHash: Map<string, number>,
    tieBreak: (hash: string) => number,
): { slotByHash: Map<string, number>; components: NetworkPesComponent[]; isolatedStateHashes: string[] } {
    const isolatedStateHashes = resolvedInServedOrder.filter((hash) => (adjacency.get(hash)?.size ?? 0) === 0)

    // Connected components among nodes with at least one accepted-saddle
    // edge (a plain BFS/union over `adjacency`).
    const visitedGlobal = new Set<string>()
    const rawComponents: string[][] = []
    for (const hash of resolvedInServedOrder) {
        if (visitedGlobal.has(hash) || (adjacency.get(hash)?.size ?? 0) === 0) continue
        const members: string[] = []
        const queue = [hash]
        visitedGlobal.add(hash)
        while (queue.length > 0) {
            const current = queue.shift()!
            members.push(current)
            for (const neighbor of adjacency.get(current)!) {
                if (!visitedGlobal.has(neighbor)) {
                    visitedGlobal.add(neighbor)
                    queue.push(neighbor)
                }
            }
        }
        rawComponents.push(members)
    }

    // Largest component first (the hydrazine archive's one hub tree), ties
    // by the lowest served index among its own members -- deterministic,
    // never dependent on `Map`/`Set` iteration order.
    rawComponents.sort((a, b) => {
        if (b.length !== a.length) return b.length - a.length
        return Math.min(...a.map(tieBreak)) - Math.min(...b.map(tieBreak))
    })

    const edgeCountWithin = (members: string[]): number => {
        const memberSet = new Set(members)
        let count = 0
        for (const { channel } of accepted) {
            if (memberSet.has(channel.source_state_composition_hash) && memberSet.has(channel.sink_state_composition_hash)) count += 1
        }
        return count
    }

    const slotByHash = new Map<string, number>()
    const components: NetworkPesComponent[] = []
    let cursor = 0
    for (const members of rawComponents) {
        const edgeCount = edgeCountWithin(members)
        const isTree = edgeCount === members.length - 1
        const { positions, rootHash } = layoutComponentAsTree(members, adjacency, energyByHash, tieBreak)
        const localValues = [...positions.values()]
        const localMin = Math.min(...localValues)
        const localMax = Math.max(...localValues)
        for (const [hash, value] of positions) slotByHash.set(hash, cursor + (value - localMin))
        components.push({ rootHash, hashes: members, isTree, usedSpanningTree: !isTree })
        cursor += (localMax - localMin) + NETWORK_PES_COMPONENT_GAP_SLOTS
    }

    // Unconnected states: no edge exists to derive a position from, so
    // fall back to the one signal every state has regardless of
    // connectivity -- ascending energy, the same rule the whole surface
    // used before this rewrite -- one extra gap past the last component.
    if (isolatedStateHashes.length > 0 && components.length > 0) cursor += NETWORK_PES_COMPONENT_GAP_SLOTS
    const isolatedSorted = [...isolatedStateHashes].sort((a, b) => {
        const diff = energyByHash.get(a)! - energyByHash.get(b)!
        if (diff !== 0) return diff
        return tieBreak(a) - tieBreak(b)
    })
    isolatedSorted.forEach((hash, index) => slotByHash.set(hash, cursor + index))

    return { slotByHash, components, isolatedStateHashes }
}

/**
 * Root the component at its highest-degree member (owner's suggested
 * approach) and walk outward, giving each leaf the next integer slot and
 * each internal node the average of its children's slots. Provably
 * crossing-free for an actual tree: every node's subtree occupies a
 * contiguous slot range disjoint from every sibling subtree's, by
 * construction, regardless of traversal order.
 *
 * Only edges within THIS component's spanning tree are walked (a node's
 * neighbour is skipped once visited) -- a component with a cycle
 * (`edgeCount > members.length - 1` at the call site) still gets a full,
 * valid set of positions this way; the non-tree edge(s) are simply never
 * walked for position, which is exactly the documented fallback (module
 * header, point 3).
 */
function layoutComponentAsTree(
    members: string[],
    adjacency: Map<string, Set<string>>,
    energyByHash: Map<string, number>,
    tieBreak: (hash: string) => number,
): { positions: Map<string, number>; rootHash: string } {
    let root = members[0]
    for (const hash of members) {
        const degree = adjacency.get(hash)?.size ?? 0
        const rootDegree = adjacency.get(root)?.size ?? 0
        if (degree > rootDegree) { root = hash; continue }
        if (degree === rootDegree) {
            const energy = energyByHash.get(hash)!
            const rootEnergy = energyByHash.get(root)!
            if (energy < rootEnergy || (energy === rootEnergy && tieBreak(hash) < tieBreak(root))) root = hash
        }
    }

    const positions = new Map<string, number>()
    const visited = new Set<string>([root])
    let leafCounter = 0

    function assign(hash: string): number {
        const children = [...(adjacency.get(hash) ?? [])]
            .filter((neighbor) => !visited.has(neighbor))
            .sort((a, b) => tieBreak(a) - tieBreak(b))
        for (const child of children) visited.add(child)
        if (children.length === 0) {
            const slot = leafCounter
            leafCounter += 1
            positions.set(hash, slot)
            return slot
        }
        const childSlots = children.map(assign)
        const slot = childSlots.reduce((sum, value) => sum + value, 0) / childSlots.length
        positions.set(hash, slot)
        return slot
    }
    assign(root)
    return { positions, rootHash: root }
}

// ---------------------------------------------------------------------------
// Level-caption decluttering -- generalises the saddle-peak decluttering
// below to level captions, which (unlike the old ascending-energy layout)
// can now legitimately share an x when they form a vertical chain; see the
// call site's own comment.
// ---------------------------------------------------------------------------

/**
 * Nudges a level's x to resolve a genuine caption collision (close in BOTH x
 * and y -- see `collides` below), same idea as `declutterPeakX`, but capped
 * so a nudge can never cross the MIDPOINT to the nearest state at a
 * genuinely different natural (pre-nudge) x. That cap is what keeps this
 * crossing-safe: the connectivity layout's own non-crossing guarantee (see
 * the module header) rests on x PRESERVING slot order, and an uncapped
 * nudge can invade a neighbouring slot's territory and reintroduce a
 * crossing it was never meant to fix (found exactly this way on the live
 * hydrazine archive's own shape while building this module -- a sibling
 * nudged toward a collision with an unrelated state ended up sandwiched
 * between two OTHER states it had no edge to, crossing both their
 * connectors). A boundary state (nothing further out on one side) has no
 * cap on that side, which is also why pushing a same-slot chain further
 * AWAY from the rest of the tree always eventually succeeds: there is
 * nowhere on that side left to invade.
 */
function declutterLevelX(levels: NetworkPesLevel[], captionWidthByHash: Map<string, number>): void {
    // "Natural" = each level's pre-nudge x, i.e. its value on entry here --
    // captured up front since the loop below mutates `level.x` in place.
    const naturalXByHash = new Map(levels.map((level) => [level.compositionHash, level.x]))
    const distinctNaturalX = [...new Set(naturalXByHash.values())].sort((a, b) => a - b)

    function capFor(naturalX: number): { leftCap: number; rightCap: number } {
        const index = distinctNaturalX.indexOf(naturalX)
        const leftCap = index > 0 ? (naturalX - distinctNaturalX[index - 1]) / 2 : Infinity
        const rightCap = index < distinctNaturalX.length - 1 ? (distinctNaturalX[index + 1] - naturalX) / 2 : Infinity
        return { leftCap, rightCap }
    }

    const placed: { x: number; y: number; half: number }[] = []
    // Deterministic order: by x first (so the visually-leftmost items settle
    // first), then by composition hash to break exact ties.
    const ordered = [...levels].sort((a, b) => (a.x - b.x) || a.compositionHash.localeCompare(b.compositionHash))
    for (const level of ordered) {
        const half = captionWidthByHash.get(level.compositionHash)! / 2
        const naturalX = naturalXByHash.get(level.compositionHash)!
        const { leftCap, rightCap } = capFor(naturalX)
        let x = naturalX
        let attempt = 0
        const collides = (candidateX: number): boolean => placed.some((p) =>
            Math.abs(candidateX - p.x) < half + p.half + NETWORK_PES_CAPTION_PAD
            && Math.abs(level.y - p.y) < NETWORK_PES_LEVEL_VERTICAL_BAND_PX)
        while (collides(x) && attempt < NETWORK_PES_LEVEL_DECLUTTER_MAX_ATTEMPTS) {
            attempt += 1
            const direction = attempt % 2 === 1 ? 1 : -1
            const magnitude = Math.ceil(attempt / 2) * NETWORK_PES_LEVEL_NUDGE_STEP_PX
            const cap = direction > 0 ? rightCap : leftCap
            x = naturalX + direction * Math.min(magnitude, cap)
        }
        level.x = x
        placed.push({ x, y: level.y, half })
    }
}

// ---------------------------------------------------------------------------
// Peak decluttering -- found by screenshotting the real archive and looking
// ---------------------------------------------------------------------------

/** Two peaks whose marker+label would render within this many pixels of
 *  each other, on BOTH axes, are treated as colliding. */
const NETWORK_PES_PEAK_COLLISION_PX = 34
const NETWORK_PES_PEAK_NUDGE_STEP_PX = 22

function peaksCollide(ax: number, ay: number, bx: number, by: number): boolean {
    return Math.abs(ax - bx) < NETWORK_PES_PEAK_COLLISION_PX && Math.abs(ay - by) < NETWORK_PES_PEAK_COLLISION_PX
}

/**
 * Mutates `saddles` in place, nudging ONLY `peakX` apart -- never `peakY`,
 * i.e. never the height, which is the one number this whole surface exists
 * to show and must stay exactly correct regardless of decluttering.
 *
 * Kept as a defensive pass even under the connectivity layout (which
 * mostly avoids the coincidence that motivated this originally -- two
 * states equidistant from a common hub landing two different saddles on
 * the same midpoint): a hub with several symmetric branches can still
 * produce two peaks whose x AND y are both close, and heights are real
 * chemistry that must never move to fix it.
 */
function declutterPeakX(saddles: NetworkPesSaddle[]): void {
    const placed: { x: number; y: number }[] = []
    for (const saddle of saddles) {
        let x = saddle.peakX
        let attempt = 0
        while (placed.some((p) => peaksCollide(x, saddle.peakY, p.x, p.y)) && attempt < 8) {
            attempt += 1
            const direction = attempt % 2 === 1 ? 1 : -1
            const magnitude = Math.ceil(attempt / 2) * NETWORK_PES_PEAK_NUDGE_STEP_PX
            x = Math.round((saddle.peakX + direction * magnitude) * 100) / 100
        }
        saddle.peakX = x
        placed.push({ x, y: saddle.peakY })
    }
}
