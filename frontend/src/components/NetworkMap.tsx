import { useId, useRef } from "react"
import "../network-map.css"
import type { NetworkChannel, NetworkChannelBarrier, NetworkState } from "../api/networkEntryApi"
import { channelRowId, hexagonPoints, layoutNetworkMap, stateKindWords, stateRowId, trianglePoints } from "../domain/networkMap"
import { channelHandle } from "../domain/publicText"
import { useOverflows } from "../hooks/useOverflows"

const NODE_RADIUS = 11
const TRIANGLE_SIZE = 7

function plural(count: number, one: string, many: string): string {
    return `${count} ${count === 1 ? one : many}`
}

function Legend({ wells, ends, edges, withBarrier, withoutKinetics }: {
    wells: number
    ends: number
    edges: number
    withBarrier: number
    withoutKinetics: number
}) {
    return (
        <ul className="net-map-legend" aria-label="What the shapes mean">
            <li>
                <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><circle cx="11" cy="11" r="8" className="net-map-node" /></svg>
                {plural(wells, "stable molecule", "stable molecules")} (circle)
            </li>
            <li>
                <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><polygon points={hexagonPoints(11, 11, 9)} className="net-map-node net-map-node--end" /></svg>
                {plural(ends, "pair of molecules apart", "pairs of molecules apart")} (hexagon)
            </li>
            <li>
                <svg width="28" height="22" viewBox="0 0 28 22" aria-hidden="true"><line x1="2" y1="11" x2="26" y2="11" className="net-map-edge" /></svg>
                {plural(edges, "channel", "channels")} (line); dashed when no rate is deposited ({withoutKinetics})
            </li>
            <li>
                <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><polygon points={trianglePoints(11, 11, 7)} className="net-map-ts" /></svg>
                A transition state with a deposited barrier ({withBarrier})
            </li>
        </ul>
    )
}

/**
 * The network as a map: stable molecules are circles, molecules apart are hexagons, channels are lines, and a triangle
 * marks a channel with a deposited barrier. Position carries no meaning. The states and channels tables carry the same
 * facts. With `compact` the map has no captions and no links, and lines that name `highlightReactionEntryRef` stand out.
 */
export function NetworkMap({ states, channels, channelBarriers, compact = false, highlightReactionEntryRef, caption }: {
    states: NetworkState[]
    channels: NetworkChannel[]
    channelBarriers: NetworkChannelBarrier[] | null
    compact?: boolean
    highlightReactionEntryRef?: string
    caption?: string
}) {
    const captionId = useId()
    const boxRef = useRef<HTMLDivElement | null>(null)
    const overflows = useOverflows(boxRef)
    const layout = layoutNetworkMap(states, channels, channelBarriers, { captions: !compact })
    if (states.length === 0) return <p className="empty-projection">No states are deposited for this network.</p>

    const wells = layout.nodes.filter((node) => node.isWell).length
    const withBarrier = layout.edges.filter((edge) => edge.hasBarrier).length
    const withoutKinetics = layout.edges.filter((edge) => !edge.hasKinetics).length
    const text = caption ?? `${plural(states.length, "state", "states")} joined by ${plural(channels.length, "channel", "channels")}. Position carries no meaning.`

    return (
        <figure className={compact ? "net-map net-map--compact" : "net-map"}>
            <div
                className="net-map-box"
                ref={boxRef}
                {...(overflows ? { tabIndex: 0, role: "region", "aria-label": "Network map, scrolls sideways" } : {})}
            >
                <svg
                    className="net-map-svg"
                    width={layout.width}
                    height={layout.height}
                    viewBox={`0 0 ${layout.width} ${layout.height}`}
                    role="group"
                    aria-labelledby={captionId}
                >
                    <g>
                        {layout.edges.map((edge) => {
                            const here = highlightReactionEntryRef != null && edge.reactionEntryRefs.includes(highlightReactionEntryRef)
                            const className = `net-map-edge${edge.hasKinetics ? "" : " net-map-edge--dashed"}${here ? " net-map-edge--here" : ""}`
                            const drawing = (
                                <>
                                    <path d={edge.path} className={className} fill="none" data-channel-key={channelHandle(edge.channelKey)} />
                                    {edge.hasBarrier && <polygon points={trianglePoints(edge.midX, edge.midY, TRIANGLE_SIZE)} className="net-map-ts" data-channel-key={channelHandle(edge.channelKey)} />}
                                </>
                            )
                            if (compact) return <g key={edge.number}>{drawing}</g>
                            return (
                                <a
                                    key={edge.number}
                                    href={`#${channelRowId(edge.number - 1)}`}
                                    className="net-map-link"
                                    aria-label={`Channel ${edge.number}, ${edge.kind}, ${edge.sourceLabel} to ${edge.sinkLabel}`
                                        + `${edge.hasBarrier ? ", transition state with a deposited barrier" : ""}`
                                        + `${edge.hasKinetics ? "" : ", no rate deposited"}`}
                                >
                                    <path d={edge.path} className="net-map-hit" fill="none" />
                                    {drawing}
                                </a>
                            )
                        })}
                    </g>
                    <g>
                        {layout.nodes.map((node) => {
                            const shape = node.isWell
                                ? <circle cx={node.x} cy={node.y} r={NODE_RADIUS} className="net-map-node" />
                                : <polygon points={hexagonPoints(node.x, node.y, NODE_RADIUS + 2)} className="net-map-node net-map-node--end" />
                            if (compact) return <g key={node.hash}>{shape}</g>
                            const anchor = node.dx > 0.3 ? "start" : node.dx < -0.3 ? "end" : "middle"
                            const textX = node.x + node.dx * (NODE_RADIUS + 10)
                            const textY = node.y + node.dy * (NODE_RADIUS + 12) + 4
                            return (
                                <a
                                    key={node.hash}
                                    href={`#${stateRowId(node.hash)}`}
                                    className="net-map-link"
                                    aria-label={`State ${node.label}, ${stateKindWords(node.isWell)}`}
                                >
                                    {shape}
                                    <text x={textX} y={textY} textAnchor={anchor} className="net-map-label t-preserve-case">{node.label}</text>
                                </a>
                            )
                        })}
                    </g>
                </svg>
            </div>
            <figcaption id={captionId} className="net-map-caption">{text}</figcaption>
            {overflows && !compact && <p className="net-map-hint">Scroll sideways inside the box to see the whole map.</p>}
            {!compact && (
                <Legend
                    wells={wells}
                    ends={layout.nodes.length - wells}
                    edges={layout.edges.length}
                    withBarrier={withBarrier}
                    withoutKinetics={withoutKinetics}
                />
            )}
        </figure>
    )
}
