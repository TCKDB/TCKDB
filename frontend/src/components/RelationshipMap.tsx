import { useId, useRef, type Ref } from "react"
import { Link, useLocation } from "react-router-dom"
import "../relationship-map.css"
import { RecordGlyph, type RecordKind } from "./RecordGlyph"
import { useNearViewport, useSpeciesEntryNeighbours, type NeighboursState } from "../hooks/useNeighbours"
import type { NeighbourNode } from "../domain/neighbourNodes"

const WIDTH = 320
const ROW = 54
const PAD = 12
const SELF_X = 50
const NODE_X = 118
const NODE_W = WIDTH - NODE_X - 4
const MAX_LABEL = 22

const clip = (value: string) => (value.length > MAX_LABEL ? `${value.slice(0, MAX_LABEL - 1)}…` : value)
const nodeAria = (node: NeighbourNode) =>
    [node.label, node.relation, node.found].filter((part): part is string => part !== null).join(", ")

/**
 * Shows where the record sits: this entry on the left, what it connects to on the right. The drawing is a group of
 * links, never one image, and the "Connected records" list beside it says the same in text. Below 560px only the list
 * is shown.
 */
export function RelationshipMap({ selfKind, state, innerRef }: {
    selfKind: RecordKind
    state: NeighboursState
    innerRef?: Ref<HTMLElement>
}) {
    const headingId = useId()
    const { pathname } = useLocation()
    const { nodes } = state
    const height = Math.max(96, nodes.length * ROW + PAD * 2)
    const cy = height / 2
    const rowY = (index: number) => PAD + ROW / 2 + index * ROW

    return (
        <section className="rel-map" aria-labelledby={headingId} ref={innerRef}>
            <h2 id={headingId} className="rel-map-heading">Where this record sits</h2>
            {nodes.length > 0 && (
                <svg className="rel-map-figure" viewBox={`0 0 ${WIDTH} ${height}`} role="group" aria-labelledby={headingId}>
                    {nodes.map((node, index) => (
                        <line key={node.id} className="rel-map-edge" x1={SELF_X + 38} y1={cy} x2={NODE_X} y2={rowY(index)} />
                    ))}
                    <Link to={pathname} aria-current="page" aria-label="This entry (you are here)" className="rel-map-node rel-map-node--self">
                        <rect x={4} y={cy - 30} width={92} height={60} rx={4} />
                        <g transform={`translate(${SELF_X - 10} ${cy - 22})`}><RecordGlyph kind={selfKind} size={20} /></g>
                        <text x={SELF_X} y={cy + 20} textAnchor="middle" className="rel-map-label">This entry</text>
                    </Link>
                    {nodes.map((node, index) => {
                        const y = rowY(index)
                        const body = (
                            <>
                                <rect x={NODE_X} y={y - 22} width={NODE_W} height={44} rx={4} />
                                <g transform={`translate(${NODE_X + 8} ${y - 10})`}><RecordGlyph kind={node.kind} size={20} /></g>
                                <text x={NODE_X + 36} y={y - 3} className="rel-map-label">{clip(node.label)}</text>
                                <text x={NODE_X + 36} y={y + 14} className="rel-map-note">
                                    {clip(node.found ?? node.relation)}
                                </text>
                            </>
                        )
                        return node.to === null ? (
                            <g key={node.id} className="rel-map-node rel-map-node--plain">{body}</g>
                        ) : (
                            <Link key={node.id} to={node.to} aria-label={nodeAria(node)} className="rel-map-node">{body}</Link>
                        )
                    })}
                </svg>
            )}
            <ul className="rel-map-list" aria-label="Connected records">
                {state.status !== "ready" && nodes.length === 0 && <li className="rel-map-empty">Loading connected records.</li>}
                {state.status === "ready" && nodes.length === 0 && <li className="rel-map-empty">No connected records were found.</li>}
                {nodes.map((node) => (
                    <li key={node.id} className="rel-map-item">
                        <RecordGlyph kind={node.kind} />
                        {" "}
                        {node.to === null ? <span>{node.label}</span> : <Link to={node.to}>{node.label}</Link>}
                        <span className="rel-map-item-note">{[node.relation, node.found].filter(Boolean).join(", ")}</span>
                    </li>
                ))}
            </ul>
        </section>
    )
}

/** The map for a species entry page: reads its neighbours once the map is near the viewport. */
export function SpeciesEntryRelationshipMap({ entryRef, smiles }: { entryRef: string; smiles: string | null }) {
    const ref = useRef<HTMLElement | null>(null)
    const near = useNearViewport(ref)
    const state = useSpeciesEntryNeighbours(entryRef, smiles, near)
    return <RelationshipMap selfKind="species-entry" state={state} innerRef={ref} />
}
