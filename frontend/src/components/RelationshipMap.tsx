import { useId, useMemo, useRef, type ReactNode, type Ref } from "react"
import { Link, useLocation } from "react-router-dom"
import "../relationship-map.css"
import { RecordGlyph, type RecordKind } from "./RecordGlyph"
import { useNearViewport, useSpeciesEntryNeighbours, type NeighboursState } from "../hooks/useNeighbours"
import { wrapLabel } from "../domain/wrapLabel"
import { shortRefs } from "../domain/shortRefs"
import type { NeighbourNode } from "../domain/neighbourNodes"
import type { SpeciesOverview } from "../api/speciesOverviewApi"
import { versionFacts } from "../domain/entryVersions"

const WIDTH = 224
const PAD = 4
const SELF_H = 34
const SPINE_X = 12
const NODE_X = 20
const NODE_W = WIDTH - NODE_X - 4
const TEXT_X = NODE_X + 34

const nodeAria = (node: NeighbourNode) =>
    [node.label, node.relation, node.ref, node.sub, node.found].filter((part): part is string => !!part).join(", ")

/**
 * Shows where the record sits, sized for the 14rem side column: this entry on top, what it connects to stacked below.
 * The drawing is for the mouse (hidden from assistive technology, its links out of the tab order); the "Connected
 * records" list says the same in text and is the keyboard and screen-reader twin. On wide screens the list is visually
 * hidden and replaces the drawing while focus is inside it; below the narrow breakpoint only the list is shown.
 */
export function RelationshipMap({ selfKind, state, innerRef, selfLabel = "This entry", children }: {
    selfKind: RecordKind
    state: NeighboursState
    innerRef?: Ref<HTMLElement>
    selfLabel?: string
    /** Extra blocks under the connected records, such as a small map of the network this record belongs to. */
    children?: ReactNode
}) {
    const headingId = useId()
    const { pathname } = useLocation()
    const { nodes } = state
    const shorts = shortRefs(nodes.map((node) => node.ref ?? ""))
    const rows = nodes.map((node, index) => {
        const lines = wrapLabel(node.label)
        const noteLines = node.ref ? wrapLabel(shorts[index]) : wrapLabel(node.drawnNote ?? node.sub ?? node.found ?? node.relation)
        return { lines, noteLines, height: 22 + 16 * (lines.length + noteLines.length) }
    })
    const tops: number[] = []
    let total = PAD + SELF_H + 8
    for (const row of rows) {
        tops.push(total)
        total += row.height
    }
    const height = total + PAD

    return (
        <section className="rel-map" aria-labelledby={headingId} ref={innerRef}>
            <h2 id={headingId} className="rel-map-heading">Where this record sits</h2>
            {nodes.length > 0 && (
                <svg className="rel-map-figure" viewBox={`0 0 ${WIDTH} ${height}`} aria-hidden="true">
                    <line className="rel-map-edge" x1={SPINE_X} y1={PAD + SELF_H} x2={SPINE_X} y2={tops[tops.length - 1] + rows[rows.length - 1].height / 2} />
                    {nodes.map((node, index) => (
                        <line key={node.id} className="rel-map-edge" x1={SPINE_X} y1={tops[index] + rows[index].height / 2} x2={NODE_X} y2={tops[index] + rows[index].height / 2} />
                    ))}
                    <Link to={pathname} tabIndex={-1} aria-current="page" aria-label={`${selfLabel} (you are here)`} className="rel-map-node rel-map-node--self">
                        <rect className="rel-map-box" x={4} y={PAD} width={WIDTH - 8} height={SELF_H} rx={4} />
                        <g transform={`translate(${NODE_X - 8} ${PAD + SELF_H / 2 - 10})`}><RecordGlyph kind={selfKind} size={20} /></g>
                        <text x={TEXT_X - 8} y={PAD + SELF_H / 2 + 4.5} className="rel-map-label">{selfLabel}</text>
                    </Link>
                    {nodes.map((node, index) => {
                        const { lines, noteLines, height: rowHeight } = rows[index]
                        const boxTop = tops[index] + 4
                        const mid = tops[index] + rowHeight / 2
                        const body = (
                            <>
                                <title>{nodeAria(node)}</title>
                                <rect className="rel-map-box" x={NODE_X} y={boxTop} width={NODE_W} height={rowHeight - 8} rx={4} />
                                <g transform={`translate(${NODE_X + 6} ${mid - 10})`}><RecordGlyph kind={node.kind} size={20} /></g>
                                <text x={TEXT_X} y={boxTop + 18} className="rel-map-label">
                                    {lines.map((line, lineIndex) => (
                                        <tspan key={lineIndex} x={TEXT_X} dy={lineIndex === 0 ? 0 : 16}>{line}</tspan>
                                    ))}
                                </text>
                                <text x={TEXT_X} y={boxTop + 18 + 16 * lines.length} className={node.ref ? "rel-map-note rel-map-ref" : "rel-map-note"}>
                                    {noteLines.map((line, lineIndex) => (
                                        <tspan key={lineIndex} x={TEXT_X} dy={lineIndex === 0 ? 0 : 16}>{line}</tspan>
                                    ))}
                                </text>
                            </>
                        )
                        return node.to === null ? (
                            <g key={node.id} className="rel-map-node rel-map-node--plain">{body}</g>
                        ) : (
                            <Link key={node.id} to={node.to} tabIndex={-1} aria-label={nodeAria(node)} className="rel-map-node">{body}</Link>
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
                        {node.to === null ? <span>{node.label}</span> : <Link to={node.to} aria-describedby={`${headingId}-${node.id}-note`}>{node.label}</Link>}
                        <span className="rel-map-item-note" id={`${headingId}-${node.id}-note`}>{[node.relation, node.ref, node.sub, node.found].filter(Boolean).join(", ")}</span>
                    </li>
                ))}
            </ul>
            {children}
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

const MAP_VERSIONS = 8

/** The map for a molecule page: no reads connect a molecule to reactions or networks, so its nodes are its own versions. */
export function SpeciesRelationshipMap({ species }: { species: SpeciesOverview }) {
    const shown = species.entries.slice(0, MAP_VERSIONS)
    const nodes: NeighbourNode[] = shown.map((version) => ({
        id: version.species_entry_ref,
        kind: "species-entry",
        label: versionFacts(version),
        relation: "A version of this molecule",
        ref: version.species_entry_ref,
        to: `/species-entries/${version.species_entry_ref}`,
        found: null,
    }))
    const more = species.entries.length - shown.length
    if (more > 0) {
        nodes.push({
            id: "more-versions",
            kind: "species-entry",
            label: `${more} more under Versions`,
            relation: "Listed under Versions of this molecule",
            to: "#species-versions",
            found: null,
        })
    }
    return <RelationshipMap selfKind="species" selfLabel="This molecule" state={{ status: "ready", nodes }} />
}

/** The map for any record page whose neighbours are all known from the data the page already holds. */
export function RecordMap({ selfKind, selfLabel, nodes }: { selfKind: RecordKind; selfLabel: string; nodes: NeighbourNode[] }) {
    const state = useMemo<NeighboursState>(() => ({ status: "ready", nodes }), [nodes])
    return <RelationshipMap selfKind={selfKind} selfLabel={selfLabel} state={state} />
}
