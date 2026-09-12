import type { NetworkChannel, NetworkChannelBarrier, NetworkState, NetworkStateEnergy } from "../api/networkEntryApi"
import { domainWithPadding, linearScale } from "./chartScale"

/**
 * Client-side layout for `NetworkDiagram.tsx`'s potential-energy surface
 * (PES), replacing the earlier force-directed web (`networkDiagramLayout.ts`,
 * deleted alongside this file). States become horizontal level bars at
 * their own deposited relative energy; a channel becomes a saddle point
 * ONLY when it carries a deposited barrier -- see the invariant below,
 * which is the one rule this whole module exists to enforce.
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
 * X ORDERING: states are placed left-to-right in ascending energy order.
 * This is a deliberate, honest choice among several arbitrary ones (the
 * plan's own brief leaves it open) -- x carries no numeric meaning of its
 * own (there is no deposited "reaction coordinate"), so `NetworkDiagram.tsx`
 * labels the axis accordingly. Ascending energy was picked over "served
 * array order" (which is an accident of the API response, not a reading
 * aid) and over "shortest-path/topological" orderings (no defensible single
 * path exists on a NETWORK -- 21 channels among 7 states is not a linear
 * mechanism) because it produces the one visual property every reader
 * immediately understands without a legend: walking left to right always
 * walks uphill, then a reader's eye finds a channel's own two wells by
 * their y-height, not by hunting for a specific x slot.
 *
 * WIDTH: NOT the fixed `NETWORK_DIAGRAM_WIDTH`/`HEIGHT` constants the old
 * force layout used. A force-directed graph on a fixed canvas degrades
 * because too many NODES crowd a fixed area; a PES built from level bars
 * degrades the same way only if too many bars are forced into the same
 * fixed width, so this module grows `width` with the number of PLOTTED
 * levels (`NETWORK_PES_LEVEL_GAP` per level) instead of capping level
 * count and falling back to a table-only banner. `NetworkDiagram.tsx` still
 * wraps the SVG in `overflow-x: auto` (invariant 6 -- fixed-pixel SVG,
 * never percentage-scaled), so a wide network simply scrolls; nothing
 * about the SADDLE side of the diagram needs a similar growth allowance,
 * because invariant 1 above already caps the connector count at however
 * many barriers are actually deposited, never at the full channel count.
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
/** Minimum horizontal gap between two adjacent level-bar centres --
 *  `width` grows past `NETWORK_PES_BASE_WIDTH` (never shrinks below it)
 *  once `(n - 1) * NETWORK_PES_LEVEL_GAP` would no longer fit, so labels
 *  never get crammed regardless of how many states carry a deposited
 *  energy. */
export const NETWORK_PES_LEVEL_GAP = 130
/** Half-width of one level bar, in pixels either side of its centre x. */
export const NETWORK_PES_LEVEL_HALF_WIDTH = 34
const Y_DOMAIN_PADDING_FRACTION = 0.14
/**
 * Owner's brief: "I verified for all four that source_energy +
 * forward_barrier == sink_energy + reverse_barrier to within 0.05 kJ/mol."
 * The same tolerance is the accept/reject boundary here -- a barrier row
 * whose two sides disagree by more than this is treated as a data problem,
 * not rendered (never averaged; see the module header comment above).
 */
export const NETWORK_PES_BARRIER_TOLERANCE_KJ_MOL = 0.05

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
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

/**
 * `null` when no state on this network carries a deposited energy -- there
 * is nothing to plot, and the caller (`NetworkDiagram.tsx`) renders an
 * explicit "no energies deposited" sentence rather than an empty canvas.
 */
export function computeNetworkPesLayout(
    states: readonly NetworkState[],
    stateEnergies: readonly NetworkStateEnergy[],
    channels: readonly NetworkChannel[],
    channelBarriers: readonly NetworkChannelBarrier[],
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

    // Served `states[]` order first (stable tie-break basis), THEN
    // re-sorted ascending by energy for the actual x placement below.
    const resolvedInServedOrder = states.map((state) => state.composition_hash).filter((hash) => energyByHash.has(hash))
    if (resolvedInServedOrder.length === 0) return null

    const servedIndex = new Map(resolvedInServedOrder.map((hash, index) => [hash, index]))
    const orderedHashes = [...resolvedInServedOrder].sort((a, b) => {
        const diff = energyByHash.get(a)! - energyByHash.get(b)!
        if (diff !== 0) return diff
        return servedIndex.get(a)! - servedIndex.get(b)!
    })

    const n = orderedHashes.length
    const innerWidthNeeded = (n - 1) * NETWORK_PES_LEVEL_GAP
    const width = Math.max(NETWORK_PES_BASE_WIDTH, NETWORK_PES_MARGIN.left + NETWORK_PES_MARGIN.right + innerWidthNeeded)
    const usableInnerWidth = width - NETWORK_PES_MARGIN.left - NETWORK_PES_MARGIN.right

    const xByHash = new Map<string, number>()
    orderedHashes.forEach((hash, index) => {
        const x = n === 1
            ? NETWORK_PES_MARGIN.left + usableInnerWidth / 2
            : NETWORK_PES_MARGIN.left + (usableInnerWidth * index) / (n - 1)
        xByHash.set(hash, x)
    })

    // ---- candidate saddles: join each barrier to its channel, then check
    // the two things that can make a deposited barrier unplaceable ----
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

    // ---- y scale, over every PLOTTED value (levels AND accepted saddles) ----
    const levelEnergies = orderedHashes.map((hash) => energyByHash.get(hash)!)
    const saddleHeights = accepted.map((entry) => entry.heightKjMol)
    const yDomain = domainWithPadding([...levelEnergies, ...saddleHeights], Y_DOMAIN_PADDING_FRACTION)
    const plotTop = NETWORK_PES_MARGIN.top
    const plotBottom = NETWORK_PES_HEIGHT - NETWORK_PES_MARGIN.bottom
    // Higher energy renders HIGHER on the page -- domain's low end maps to
    // the bottom pixel, domain's high end to the top pixel.
    const yScale = linearScale(yDomain, [plotBottom, plotTop])

    const levels: NetworkPesLevel[] = orderedHashes.map((hash) => {
        const state = stateByHash.get(hash)!
        const energyKjMol = energyByHash.get(hash)!
        return {
            compositionHash: hash,
            label: state.composition.state_label || "unnamed state",
            isWell: state.kind === "well",
            energyKjMol,
            x: Math.round(xByHash.get(hash)! * 100) / 100,
            y: Math.round(yScale(energyKjMol) * 100) / 100,
        }
    })
    const yByHash = new Map(levels.map((level) => [level.compositionHash, level.y]))

    const saddles: NetworkPesSaddle[] = accepted.map(({ barrier, channel, heightKjMol }) => {
        const sourceX = xByHash.get(channel.source_state_composition_hash)!
        const sinkX = xByHash.get(channel.sink_state_composition_hash)!
        const sourceY = yByHash.get(channel.source_state_composition_hash)!
        const sinkY = yByHash.get(channel.sink_state_composition_hash)!
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
            peakX: Math.round(((sourceX + sinkX) / 2) * 100) / 100,
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
 * Found on the live hydrazine archive, not invented: `channel_3` (347.2
 * kJ/mol, states 0 and 4 apart in ascending-energy order) and `channel_11`
 * (350.6 kJ/mol, states 1 and 3 apart) land on the EXACT SAME `peakX` --
 * `(i0+i4)/2 == (i1+i3)/2 == i2`'s own x, because `NETWORK_PES_LEVEL_GAP`
 * spaces every level equally. Two different transition states then draw
 * one on top of the other, one marker and label completely hiding the
 * other -- caught only by rendering the real payload and looking at the
 * screenshot, not by any hand-built fixture small enough to eyeball.
 *
 * `x` was already documented as carrying no physical meaning of its own
 * (this module's header comment) -- nudging it here, deterministically, in
 * served-barrier order, is a pure rendering convenience, never a claim
 * about the chemistry the way moving `peakY` would be.
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
