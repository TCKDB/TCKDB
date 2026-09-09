import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"

/**
 * PR 3 of `docs/plans/pressure-dependent-network-surface.md` (§2.2/§5):
 * client-side layout for the network diagram. States become nodes,
 * channels become edges. No server-side layout, no graph-layout library
 * (per the plan's own recommendation and this PR's forbidden list) --
 * 7 nodes / 21 channels on the live archive is trivial to lay out at
 * request time, so this file hand-rolls a small, deterministic
 * force-directed placement (Fruchterman-Reingold: mutual node repulsion,
 * spring attraction along edges, a linearly-cooling step size).
 *
 * Deterministic on purpose: initial positions are placed on a circle in
 * the SAME order `states[]` was served in (no `Math.random`), and the
 * simulation runs a FIXED number of iterations with fixed constants, so
 * the same `(states, channels)` input always produces the exact same
 * `(x, y)` for every node -- `networkDiagramLayout.test.ts` pins this
 * directly (same input twice -> identical output), which a randomised
 * layout could never do.
 *
 * `NETWORK_DIAGRAM_WIDTH`/`HEIGHT` match the reviewed design mock's own
 * `viewBox="0 0 1000 720"` (`docs/plans/mocks/network-entry.html`), NOT
 * the plan text's incidental "same 720px chart budget as the Arrhenius
 * chart" (`ARRHENIUS_CHART_WIDTH` is 720x340) -- that number in the
 * plan's §2.2 is used only to justify the ~15-20 node degrade estimate,
 * not as a literal dimension mandate, and the mock (already reviewed and
 * approved) is authoritative for the real diagram's own canvas size. See
 * this PR's own report for this discrepancy, flagged rather than quietly
 * resolved.
 */
export const NETWORK_DIAGRAM_WIDTH = 1000
export const NETWORK_DIAGRAM_HEIGHT = 720

const FORCE_ITERATIONS = 300
const COOLING_FACTOR = 0.97

export interface NetworkDiagramNode {
    compositionHash: string
    /** Always `composition.state_label` -- server-computed, never
     *  `states[].label` (depositor free text, null on every state
     *  measured against the live archive) and never the raw hash. */
    label: string
    /** `true` for `kind === "well"`. Everything else (only "bimolecular"
     *  is measured today) renders with the bimolecular shape -- a binary
     *  visual encoding, matching the plan's own "well vs bimolecular,
     *  never inferred from label text" instruction. */
    isWell: boolean
    x: number
    y: number
}

export interface NetworkDiagramEdge {
    /** Data face only -- never rendered as an SVG `<text>` label (see
     *  `NetworkDiagram.tsx`). `null` is defensive: the served schema
     *  allows it even though every channel measured against the live
     *  archive carries one. */
    channelKey: string | null
    kind: string
    hasKinetics: boolean
    sourceHash: string
    sinkHash: string
    x1: number
    y1: number
    x2: number
    y2: number
}

export interface NetworkDiagramLayout {
    nodes: NetworkDiagramNode[]
    edges: NetworkDiagramEdge[]
}

/**
 * Past this many states, a circular/force-directed layout on this canvas
 * budget stops being legible (plan §2.2: "roughly 15-20 nodes on the same
 * 720px chart budget" -- 18 is the upper end of that stated range, picked
 * as the concrete cutoff). `networkDiagramLayout.test.ts`/
 * `NetworkDiagramPage.test.tsx` exercise an injected 25-node fixture to
 * prove the fallback actually fires, not just that the constant exists.
 */
export const NETWORK_DIAGRAM_NODE_THRESHOLD = 18

/**
 * A SECOND trigger, independent of raw node count: a smaller network that
 * is unusually dense -- more edges than a COMPLETE graph on
 * `NETWORK_DIAGRAM_NODE_THRESHOLD` nodes would have -- is just as
 * unreadable as a larger sparse one. `n(n-1)/2` is the standard complete-
 * graph edge count (the plan's own "more edges than nodes squared over 2"
 * phrasing is the same formula, rounded). The live archive's one network
 * (7 states, 21 channels -- itself a complete graph on 7 nodes) sits
 * comfortably under both triggers: 7 <= 18, and 21 <= 153.
 */
function completeGraphEdgeCount(nodeCount: number): number {
    return (nodeCount * (nodeCount - 1)) / 2
}

export function shouldDegradeNetworkDiagram(nodeCount: number, edgeCount: number): boolean {
    if (nodeCount > NETWORK_DIAGRAM_NODE_THRESHOLD) return true
    return edgeCount > completeGraphEdgeCount(NETWORK_DIAGRAM_NODE_THRESHOLD)
}

/**
 * Fruchterman-Reingold force-directed layout, computed entirely
 * client-side. `states`/`channels` are exactly what `NetworkFullRecord`
 * already carries (`record.states`/`record.channels`) -- no new fetch.
 */
export function computeNetworkDiagramLayout(
    states: NetworkState[],
    channels: NetworkChannel[],
    width = NETWORK_DIAGRAM_WIDTH,
    height = NETWORK_DIAGRAM_HEIGHT,
): NetworkDiagramLayout {
    const n = states.length
    if (n === 0) return { nodes: [], edges: [] }

    // Margins leave room for a node's own label, which can extend well
    // past the node's circle/hexagon on either side (see
    // `NetworkDiagram.tsx`'s label placement) -- keeping node CENTRES
    // inside this smaller inner rect, rather than the full canvas, keeps
    // labels from being clipped by the SVG's own viewBox.
    const marginX = Math.min(220, width * 0.22)
    const marginY = Math.min(140, height * 0.22)
    const innerW = Math.max(width - 2 * marginX, 1)
    const innerH = Math.max(height - 2 * marginY, 1)
    const cx = width / 2
    const cy = height / 2
    const R = Math.min(innerW, innerH) / 2

    // Deterministic starting layout: a circle, in served-array order.
    const positions = states.map((_state, i) => {
        const angle = (2 * Math.PI * i) / n - Math.PI / 2
        return { x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle) }
    })

    const hashIndex = new Map(states.map((state, i) => [state.composition_hash, i]))
    const edgeList = channels
        // A channel naming a composition hash absent from `states[]`
        // (should not happen against a well-formed payload, but the
        // schema does not forbid it) is skipped rather than crashing the
        // whole diagram.
        .filter((channel) => hashIndex.has(channel.source_state_composition_hash) && hashIndex.has(channel.sink_state_composition_hash))
        .map((channel) => ({
            a: hashIndex.get(channel.source_state_composition_hash)!,
            b: hashIndex.get(channel.sink_state_composition_hash)!,
            channel,
        }))

    if (n > 1) {
        const area = innerW * innerH
        // The Fruchterman-Reingold "ideal distance" constant: repulsion
        // and attraction balance at this spacing.
        const k = Math.sqrt(area / n)
        let temperature = Math.max(innerW, innerH) / 10

        for (let iter = 0; iter < FORCE_ITERATIONS; iter++) {
            const disp = positions.map(() => ({ x: 0, y: 0 }))

            // Mutual repulsion, every pair.
            for (let i = 0; i < n; i++) {
                for (let j = i + 1; j < n; j++) {
                    const dx = positions[i].x - positions[j].x
                    const dy = positions[i].y - positions[j].y
                    const dist = Math.hypot(dx, dy) || 0.01
                    const force = (k * k) / dist
                    const ux = dx / dist
                    const uy = dy / dist
                    disp[i].x += ux * force
                    disp[i].y += uy * force
                    disp[j].x -= ux * force
                    disp[j].y -= uy * force
                }
            }

            // Spring attraction along every edge (a self-loop, a === b,
            // contributes nothing -- not measured on the live archive but
            // the schema does not forbid a channel with equal source/sink).
            for (const edge of edgeList) {
                if (edge.a === edge.b) continue
                const dx = positions[edge.a].x - positions[edge.b].x
                const dy = positions[edge.a].y - positions[edge.b].y
                const dist = Math.hypot(dx, dy) || 0.01
                const force = (dist * dist) / k
                const ux = dx / dist
                const uy = dy / dist
                disp[edge.a].x -= ux * force
                disp[edge.a].y -= uy * force
                disp[edge.b].x += ux * force
                disp[edge.b].y += uy * force
            }

            // Apply, capped by the cooling `temperature`, clamped inside
            // the inner rect so a node can never drift into the label
            // margin reserved above.
            for (let i = 0; i < n; i++) {
                const dlen = Math.hypot(disp[i].x, disp[i].y) || 0.01
                const limited = Math.min(dlen, temperature)
                positions[i].x += (disp[i].x / dlen) * limited
                positions[i].y += (disp[i].y / dlen) * limited
                positions[i].x = Math.min(cx + innerW / 2, Math.max(cx - innerW / 2, positions[i].x))
                positions[i].y = Math.min(cy + innerH / 2, Math.max(cy - innerH / 2, positions[i].y))
            }
            temperature *= COOLING_FACTOR
        }
    }

    const nodes: NetworkDiagramNode[] = states.map((state, i) => ({
        compositionHash: state.composition_hash,
        label: state.composition.state_label || "unnamed state",
        isWell: state.kind === "well",
        x: Math.round(positions[i].x * 100) / 100,
        y: Math.round(positions[i].y * 100) / 100,
    }))

    const edges: NetworkDiagramEdge[] = edgeList.map(({ a, b, channel }) => ({
        channelKey: channel.channel_key ?? null,
        kind: channel.kind,
        hasKinetics: channel.has_kinetics,
        sourceHash: channel.source_state_composition_hash,
        sinkHash: channel.sink_state_composition_hash,
        x1: nodes[a].x,
        y1: nodes[a].y,
        x2: nodes[b].x,
        y2: nodes[b].y,
    }))

    return { nodes, edges }
}
