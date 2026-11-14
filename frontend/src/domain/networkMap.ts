import type { NetworkChannel, NetworkChannelBarrier, NetworkState } from "../api/networkEntryApi"

export type NetworkMapNode = {
    hash: string
    label: string
    /** A stable molecule (circle) or molecules apart (hexagon). */
    isWell: boolean
    x: number
    y: number
    /** Unit direction away from the centre, used to place the caption. */
    dx: number
    dy: number
}

export type NetworkMapEdge = {
    /** 1-based position in the served channel list. */
    number: number
    channelKey: string | null
    kind: string
    hasKinetics: boolean
    hasBarrier: boolean
    sourceLabel: string
    sinkLabel: string
    path: string
    /** Midpoint of the drawn line, where the transition-state triangle sits. */
    midX: number
    midY: number
    /** Reaction entries this channel names. */
    reactionEntryRefs: string[]
}

export type NetworkMapLayout = {
    width: number
    height: number
    nodes: NetworkMapNode[]
    edges: NetworkMapEdge[]
}

const CHAR_WIDTH = 7.4
const RING_RX = 170
const RING_RY = 105
const CAPTION_ROOM = 24

/** The words a reader sees for a state's kind. */
export function stateKindWords(isWell: boolean): string {
    return isWell ? "a stable molecule" : "two molecules apart"
}

/**
 * Places states on a ring in served order and joins them with the served channels. Nothing here is a reaction
 * coordinate or an energy: position carries no meaning. A triangle is offered only for a channel whose own barrier
 * row exists.
 */
export function layoutNetworkMap(
    states: readonly NetworkState[],
    channels: readonly NetworkChannel[],
    barriers: readonly NetworkChannelBarrier[] | null,
    options: { captions: boolean },
): NetworkMapLayout {
    const labelOf = (state: NetworkState) => state.composition.state_label || "unnamed state"
    const longest = options.captions ? Math.max(0, ...states.map((state) => labelOf(state).length)) : 0
    const margin = options.captions ? Math.ceil(longest * CHAR_WIDTH) + CAPTION_ROOM : 24
    const rx = options.captions ? RING_RX : 96
    const ry = options.captions ? RING_RY : 70
    const width = 2 * (rx + margin)
    const height = 2 * (ry + (options.captions ? 48 : 24))
    const cx = width / 2
    const cy = height / 2
    const count = states.length

    const nodes: NetworkMapNode[] = states.map((state, index) => {
        const angle = count === 1 ? 0 : (2 * Math.PI * index) / count - Math.PI / 2
        const dx = count === 1 ? 0 : Math.cos(angle)
        const dy = count === 1 ? 1 : Math.sin(angle)
        return {
            hash: state.composition_hash,
            label: labelOf(state),
            isWell: state.kind === "well",
            x: cx + (count === 1 ? 0 : rx * dx),
            y: cy + (count === 1 ? 0 : ry * dy),
            dx,
            dy,
        }
    })
    const byHash = new Map(nodes.map((node) => [node.hash, node]))
    const barrierKeys = new Set((barriers ?? []).map((barrier) => barrier.channel_key))
    const seenPairs = new Map<string, number>()

    const edges: NetworkMapEdge[] = []
    channels.forEach((channel, index) => {
        const source = byHash.get(channel.source_state_composition_hash)
        const sink = byHash.get(channel.sink_state_composition_hash)
        if (!source || !sink) return
        const pair = [source.hash, sink.hash].sort().join("|")
        const nth = seenPairs.get(pair) ?? 0
        seenPairs.set(pair, nth + 1)
        // Channels between the same two states bow apart so none hides another.
        const bow = nth === 0 ? 0 : (nth % 2 === 1 ? 1 : -1) * Math.ceil(nth / 2) * 26
        const mx = (source.x + sink.x) / 2
        const my = (source.y + sink.y) / 2
        const length = Math.hypot(sink.x - source.x, sink.y - source.y) || 1
        const nx = -(sink.y - source.y) / length
        const ny = (sink.x - source.x) / length
        const qx = mx + nx * bow * 2
        const qy = my + ny * bow * 2
        edges.push({
            number: index + 1,
            channelKey: channel.channel_key ?? null,
            kind: channel.kind,
            hasKinetics: channel.has_kinetics,
            hasBarrier: channel.channel_key != null && barrierKeys.has(channel.channel_key),
            sourceLabel: source.label,
            sinkLabel: sink.label,
            path: `M ${source.x} ${source.y} Q ${qx} ${qy} ${sink.x} ${sink.y}`,
            midX: 0.25 * source.x + 0.5 * qx + 0.25 * sink.x,
            midY: 0.25 * source.y + 0.5 * qy + 0.25 * sink.y,
            reactionEntryRefs: channel.microreactions.map((micro) => micro.reaction_entry_ref),
        })
    })
    return { width, height, nodes, edges }
}

/** SVG points for a regular hexagon centred on a node. */
export function hexagonPoints(x: number, y: number, radius: number): string {
    return Array.from({ length: 6 }, (_unused, i) => {
        const angle = (Math.PI / 3) * i
        return `${(x + radius * Math.cos(angle)).toFixed(1)},${(y + radius * Math.sin(angle)).toFixed(1)}`
    }).join(" ")
}

/** SVG points for an upward triangle centred on a point. */
export function trianglePoints(x: number, y: number, size: number): string {
    return `${x},${y - size} ${x + size},${y + size * 0.8} ${x - size},${y + size * 0.8}`
}

export function stateRowId(compositionHash: string): string {
    return `state-row-${compositionHash}`
}

/** Position only: a channel key is a string the depositor typed and never goes into the DOM. */
export function channelRowId(index: number): string {
    return `channel-row-${index}`
}
