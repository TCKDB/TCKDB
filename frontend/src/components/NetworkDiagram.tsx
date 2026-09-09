import { Link } from "react-router-dom"
import "../network-diagram.css"
import "../thermo-cp-chart.css"
import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"
import {
    computeNetworkDiagramLayout,
    NETWORK_DIAGRAM_HEIGHT,
    NETWORK_DIAGRAM_NODE_THRESHOLD,
    NETWORK_DIAGRAM_WIDTH,
    shouldDegradeNetworkDiagram,
    type NetworkDiagramEdge,
    type NetworkDiagramNode,
} from "../domain/networkDiagramLayout"
import { Disclosure } from "./Disclosure"

/**
 * PR 3 of `docs/plans/pressure-dependent-network-surface.md` (§3.3, §5):
 * the network diagram -- states as nodes, channels as edges -- plus the
 * ALWAYS-rendered accessible data-table equivalent (invariant 4: renders
 * regardless of whether the SVG renders, regardless of degrade state).
 *
 * Reuses `ArrheniusChart.tsx`'s own fixed-pixel-SVG / `overflow-x: auto`
 * pattern (`network-diagram.css`'s header comment) rather than a new
 * chart shell, and `thermo-cp-chart.css`'s existing `.cp-chart-legend`
 * primitive for the encoding legend (same class the reviewed PR 0 mock
 * used).
 *
 * `states`/`channels` come straight off `NetworkFullRecord` -- no new
 * fetch (per this PR's own brief: everything needed is already served by
 * PR 2's `loadNetworkEntry`).
 */
export function NetworkDiagram({ states, channels }: { states: NetworkState[]; channels: NetworkChannel[] }) {
    const degrade = shouldDegradeNetworkDiagram(states.length, channels.length)
    // The force simulation is genuinely useless work once degrading --
    // skip it rather than compute a layout nobody renders.
    const layout = degrade ? null : computeNetworkDiagramLayout(states, channels)

    return (
        <>
            <NetworkDiagramLegend states={states} channels={channels} />
            {degrade
                ? (
                    <div className="net-diagram-degraded card card--sunken">
                        <p className="empty-projection" style={{ margin: 0 }}>
                            This network's diagram would not stay legible; showing the channel table.
                        </p>
                        <p className="note" style={{ marginTop: ".6rem" }}>
                            {`This network has ${states.length} states — past ${NETWORK_DIAGRAM_NODE_THRESHOLD} states (or an equally dense smaller network), a diagram on this canvas stops being readable. The accessible tables below are unaffected.`}
                        </p>
                    </div>
                )
                : layout && <NetworkDiagramSvg layout={layout} nodeCount={states.length} channelCount={channels.length} />}
            <NetworkStatesTable states={states} />
            <NetworkChannelsTable channels={channels} states={states} />
        </>
    )
}

// ---------------------------------------------------------------------------
// Legend -- shape/colour/dash encoding, counts computed live (never hardcoded)
// ---------------------------------------------------------------------------

const EDGE_KIND_ORDER = ["isomerization", "association", "exchange"]

function edgeKindClass(kind: string): string {
    return EDGE_KIND_ORDER.includes(kind) ? `net-edge-${kind}` : "net-edge-other"
}

function NetworkDiagramLegend({ states, channels }: { states: NetworkState[]; channels: NetworkChannel[] }) {
    const wellCount = states.filter((state) => state.kind === "well").length
    const bimolecularCount = states.length - wellCount

    const kindCounts = new Map<string, number>()
    for (const channel of channels) kindCounts.set(channel.kind, (kindCounts.get(channel.kind) ?? 0) + 1)
    const kindsPresent = [...EDGE_KIND_ORDER, ...[...kindCounts.keys()].filter((kind) => !EDGE_KIND_ORDER.includes(kind))]
        .filter((kind) => kindCounts.has(kind))

    const noKineticsCount = channels.filter((channel) => !channel.has_kinetics).length

    return (
        <ul className="cp-chart-legend" aria-label="Diagram encoding">
            <li className="cp-chart-legend-item">
                <svg className="net-legend-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                    <circle cx="8" cy="8" r="7" className="net-node-shape net-node-well" />
                </svg>
                <span>{`Well (${wellCount} state${wellCount === 1 ? "" : "s"} here)`}</span>
            </li>
            <li className="cp-chart-legend-item">
                <svg className="net-legend-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                    <polygon points="15,8 11.5,13.7 4.5,13.7 1,8 4.5,2.3 11.5,2.3" className="net-node-shape net-node-bimolecular" />
                </svg>
                <span>{`Bimolecular (${bimolecularCount} state${bimolecularCount === 1 ? "" : "s"} here, incl. "2 x the same species")`}</span>
            </li>
            {kindsPresent.map((kind) => (
                <li className="cp-chart-legend-item" key={kind}>
                    <span className={`net-legend-line net-legend-line-${EDGE_KIND_ORDER.includes(kind) ? kind : "other"}`} aria-hidden="true" />
                    <span>{`${kind} (${kindCounts.get(kind)} channel${kindCounts.get(kind) === 1 ? "" : "s"} here)`}</span>
                </li>
            ))}
            <li className="cp-chart-legend-item">
                <svg className="net-legend-icon" width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">
                    <line x1="1" y1="5" x2="21" y2="5" className="net-edge" strokeWidth={2} strokeDasharray="4 3" />
                </svg>
                <span>{`no kinetics fit deposited (${noKineticsCount} of ${channels.length} channels here)`}</span>
            </li>
        </ul>
    )
}

// ---------------------------------------------------------------------------
// SVG -- fixed pixel size, overflow-x: auto container
// ---------------------------------------------------------------------------

function stateRowId(compositionHash: string): string {
    return `state-row-${compositionHash}`
}

function channelRowId(channelKey: string | null, index: number): string {
    return `channel-row-${channelKey ?? `unkeyed-${index}`}`
}

function NetworkDiagramSvg({ layout, nodeCount, channelCount }: { layout: { nodes: NetworkDiagramNode[]; edges: NetworkDiagramEdge[] }; nodeCount: number; channelCount: number }) {
    const nodeByHash = new Map(layout.nodes.map((node) => [node.compositionHash, node]))

    return (
        <div className="network-diagram">
            <svg
                className="network-diagram-svg"
                width={NETWORK_DIAGRAM_WIDTH}
                height={NETWORK_DIAGRAM_HEIGHT}
                viewBox={`0 0 ${NETWORK_DIAGRAM_WIDTH} ${NETWORK_DIAGRAM_HEIGHT}`}
                role="img"
                aria-label={`Network diagram: ${nodeCount} states, ${channelCount} channels`}
            >
                <g aria-hidden="true">
                    {layout.edges.map((edge, index) => {
                        const sourceLabel = nodeByHash.get(edge.sourceHash)?.label ?? "unresolved state"
                        const sinkLabel = nodeByHash.get(edge.sinkHash)?.label ?? "unresolved state"
                        return (
                            <a
                                key={`${edge.channelKey ?? "unkeyed"}-${index}`}
                                href={`#${channelRowId(edge.channelKey, index)}`}
                                className="net-edge-link"
                                aria-label={`Channel ${edge.channelKey ?? "unkeyed"}, ${edge.kind}, ${sourceLabel} to ${sinkLabel}`}
                            >
                                <line
                                    x1={edge.x1}
                                    y1={edge.y1}
                                    x2={edge.x2}
                                    y2={edge.y2}
                                    // `channel_key` (invariant 1) never sits on
                                    // a `<text>` element -- only here, a
                                    // `data-*` attribute, and the table row.
                                    data-channel-key={edge.channelKey ?? undefined}
                                    className={`net-edge ${edgeKindClass(edge.kind)}`}
                                    strokeDasharray={edge.hasKinetics ? undefined : "4 3"}
                                />
                            </a>
                        )
                    })}
                </g>
                <g>
                    {layout.nodes.map((node) => (
                        <a
                            key={node.compositionHash}
                            href={`#${stateRowId(node.compositionHash)}`}
                            className="net-node-link"
                            aria-label={`State ${node.label}, ${node.isWell ? "well" : "bimolecular"}`}
                        >
                            {node.isWell
                                ? <circle cx={node.x} cy={node.y} r={30} className="net-node-shape net-node-well" />
                                : <polygon points={hexagonPoints(node.x, node.y, 30)} className="net-node-shape net-node-bimolecular" />}
                            {/* The ONLY safe node label -- `composition.state_label`,
                                already baked into `node.label` by
                                `computeNetworkDiagramLayout`. Never
                                `states[].label`, never `species_entry_label`,
                                never the raw hash (invariant 2). */}
                            <text x={node.x} y={node.y + 30 + 18} textAnchor="middle" className="net-node-label">{node.label}</text>
                        </a>
                    ))}
                </g>
            </svg>
        </div>
    )
}

function hexagonPoints(cx: number, cy: number, r: number): string {
    return Array.from({ length: 6 }, (_unused, i) => {
        const angle = (Math.PI / 3) * i - Math.PI / 6
        const x = cx + r * Math.cos(angle)
        const y = cy + r * Math.sin(angle)
        return `${x.toFixed(2)},${y.toFixed(2)}`
    }).join(" ")
}

// ---------------------------------------------------------------------------
// Accessible tables -- ALWAYS rendered (invariant 4), regardless of the SVG
// ---------------------------------------------------------------------------

function NetworkStatesTable({ states }: { states: NetworkState[] }) {
    return (
        <Disclosure summary="Table equivalent — states" count={states.length} defaultOpen>
            <div className="table-scroll">
                <table className="data-table" aria-label="States in this network">
                    <thead>
                        <tr>
                            <th scope="col">State</th>
                            <th scope="col">Kind</th>
                            <th scope="col">Participants</th>
                        </tr>
                    </thead>
                    <tbody>
                        {states.map((state) => (
                            <tr key={state.composition_hash} id={stateRowId(state.composition_hash)} data-composition-hash={state.composition_hash}>
                                <td data-label="State">{state.composition.state_label || "unnamed state"}</td>
                                <td data-label="Kind"><span className="value-pill">{state.kind}</span></td>
                                <td data-label="Participants">
                                    <ul className="net-participant-list">
                                        {state.composition.participants.map((participant, index) => (
                                            <li key={`${participant.species_entry_ref}-${index}`}>
                                                {participant.stoichiometry > 1 ? `${participant.stoichiometry}× ` : ""}
                                                <Link to={`/species-entries/${participant.species_entry_ref}`}>
                                                    <code className="data">{participant.canonical_smiles}</code>
                                                </Link>
                                                {" — "}
                                                <code className="data">{participant.species_entry_ref}</code>
                                            </li>
                                        ))}
                                    </ul>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Disclosure>
    )
}

function NetworkChannelsTable({ channels, states }: { channels: NetworkChannel[]; states: NetworkState[] }) {
    const stateLabelByHash = new Map(states.map((state) => [state.composition_hash, state.composition.state_label || "unnamed state"]))

    return (
        <Disclosure summary="Table equivalent — channels" count={channels.length} defaultOpen>
            <div className="table-scroll">
                <table className="data-table" aria-label="Channels in this network">
                    <thead>
                        <tr>
                            <th scope="col">Channel</th>
                            <th scope="col">Kind</th>
                            <th scope="col">Mechanism</th>
                            <th scope="col">Source state</th>
                            <th scope="col">Sink state</th>
                            <th scope="col">Kinetics</th>
                        </tr>
                    </thead>
                    <tbody>
                        {channels.map((channel, index) => (
                            <tr key={`${channel.channel_key ?? "unkeyed"}-${index}`} id={channelRowId(channel.channel_key ?? null, index)}>
                                <td data-label="Channel">
                                    {channel.channel_key
                                        ? <code className="data">{channel.channel_key}</code>
                                        : <span className="record-identity-absent-inline">not recorded</span>}
                                </td>
                                <td data-label="Kind"><span className="value-pill">{channel.kind}</span></td>
                                <td data-label="Mechanism">{channel.mechanism}</td>
                                <td data-label="Source state">{stateLabelByHash.get(channel.source_state_composition_hash) ?? "unresolved state"}</td>
                                <td data-label="Sink state">{stateLabelByHash.get(channel.sink_state_composition_hash) ?? "unresolved state"}</td>
                                <td data-label="Kinetics">
                                    {channel.has_kinetics
                                        ? <span className="value-pill">has kinetics</span>
                                        : <span className="value-pill value-pill--muted">no kinetics fit deposited</span>}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Disclosure>
    )
}
