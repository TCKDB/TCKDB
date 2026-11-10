import { useId, useRef, type Ref } from "react"
import { Link, useLocation } from "react-router-dom"
import "../relationship-map.css"
import { RecordGlyph, type RecordKind } from "./RecordGlyph"
import { useNearViewport, useSpeciesEntryNeighbours, type NeighboursState } from "../hooks/useNeighbours"
import type { NeighbourNode } from "../domain/neighbourNodes"

const WIDTH = 320
const PAD = 8
const SELF_X = 50
const NODE_X = 118
const NODE_W = WIDTH - NODE_X - 8
const LINE_CHARS = 21

/** Splits a label at word boundaries; a single long word stays whole on its own line. */
function wrapLabel(label: string): string[] {
    const lines: string[] = []
    for (const word of label.split(" ")) {
        const last = lines[lines.length - 1]
        if (last !== undefined && `${last} ${word}`.length <= LINE_CHARS) lines[lines.length - 1] = `${last} ${word}`
        else lines.push(word)
    }
    return lines
}

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
    const rows = nodes.map((node) => {
        const lines = wrapLabel(node.label)
        return { lines, height: 22 + 16 * (lines.length + 1) }
    })
    const tops: number[] = []
    let total = PAD
    for (const row of rows) {
        tops.push(total)
        total += row.height
    }
    const height = Math.max(96, total + PAD)
    const cy = height / 2

    return (
        <section className="rel-map" aria-labelledby={headingId} ref={innerRef}>
            <h2 id={headingId} className="rel-map-heading">Where this record sits</h2>
            {nodes.length > 0 && (
                <svg className="rel-map-figure" viewBox={`0 0 ${WIDTH} ${height}`} role="group" aria-labelledby={headingId}>
                    {nodes.map((node, index) => (
                        <line key={node.id} className="rel-map-edge" x1={SELF_X + 38} y1={cy} x2={NODE_X} y2={tops[index] + rows[index].height / 2} />
                    ))}
                    <Link to={pathname} aria-current="page" aria-label="This entry (you are here)" className="rel-map-node rel-map-node--self">
                        <rect className="rel-map-box" x={4} y={cy - 30} width={92} height={60} rx={4} />
                        <g transform={`translate(${SELF_X - 10} ${cy - 22})`}><RecordGlyph kind={selfKind} size={20} /></g>
                        <text x={SELF_X} y={cy + 20} textAnchor="middle" className="rel-map-label">This entry</text>
                    </Link>
                    {nodes.map((node, index) => {
                        const { lines, height: rowHeight } = rows[index]
                        const boxTop = tops[index] + 4
                        const mid = tops[index] + rowHeight / 2
                        const body = (
                            <>
                                <rect className="rel-map-box" x={NODE_X} y={boxTop} width={NODE_W} height={rowHeight - 8} rx={4} />
                                <g transform={`translate(${NODE_X + 8} ${mid - 10})`}><RecordGlyph kind={node.kind} size={20} /></g>
                                <text x={NODE_X + 36} y={boxTop + 18} className="rel-map-label">
                                    {lines.map((line, lineIndex) => (
                                        <tspan key={lineIndex} x={NODE_X + 36} dy={lineIndex === 0 ? 0 : 16}>{line}</tspan>
                                    ))}
                                </text>
                                <text x={NODE_X + 36} y={boxTop + 18 + 16 * lines.length} className="rel-map-note">
                                    {node.found ?? node.relation}
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
