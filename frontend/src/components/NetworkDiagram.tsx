import { Link } from "react-router-dom"
import "../network-diagram.css"
import "../thermo-cp-chart.css"
import type { NetworkChannel, NetworkChannelBarrier, NetworkState, NetworkStateEnergy } from "../api/networkEntryApi"
import { formatTicks, linearScale } from "../domain/chartScale"
import {
    computeNetworkPesLayout,
    NETWORK_PES_CAPTION_CHAR_WIDTH,
    NETWORK_PES_LEVEL_HALF_WIDTH,
    NETWORK_PES_MARGIN,
    NETWORK_PES_TS_BAR_HALF_WIDTH,
    type NetworkPesLayout,
} from "../domain/networkPesLayout"
import { niceTicks } from "../domain/thermoCpChartLayout"
import { Disclosure } from "./Disclosure"

/**
 * Replaces the earlier force-directed web (states as nodes, channels as
 * edges, no energy axis) with a potential-energy surface: states become
 * horizontal level bars at their own deposited relative energy, and a
 * channel becomes a saddle point -- ALSO drawn as a short horizontal bar,
 * matching the reference figure's TS convention -- ONLY when it carries a
 * deposited barrier (`domain/networkPesLayout.ts`'s own header comment has
 * the full reasoning, including why x is connectivity-derived, never a
 * real reaction coordinate).
 *
 * READABILITY REWRITE (owner: "hard to read with lines overlapping etc",
 * checked against the group's own published PES for this exact chemical
 * system, `raghunath_n2h4_pes_fig7.1.png`, a design reference only and
 * never copied into this repo): x used to be ascending energy rank, which
 * put unrelated states next to each other and routed a connector between a
 * low and a high state straight through every level in between. x is now
 * derived from which states a deposited barrier actually connects
 * (`networkPesLayout.ts`'s `computeNetworkPesLayout`), so a connector never
 * has to travel further than its own two endpoints and their shared TS
 * bar -- see that module's header for the full tree-walk rationale, the
 * non-tree fallback, and how a state connected to nothing is handled
 * (never invented a connector for; grouped and captioned instead).
 *
 * SCOPE DECISION (owner's brief asked for this, explicitly, not just the
 * code): channel `kind` (isomerization/association/exchange) and
 * `has_kinetics` still encode the drawn connectors' colour/dash -- both
 * are still meaningful facts about the 4 (today) channels that actually
 * get drawn. What does NOT carry over is the old size-based
 * degrade-to-table-only banner (`shouldDegradeNetworkDiagram`,
 * `NETWORK_DIAGRAM_NODE_THRESHOLD`): that existed because a force layout on
 * a FIXED canvas gets crowded and unstable past a node/edge count, and
 * neither failure mode applies here. A level bar's own x/y position is
 * computed directly from data (never a physics simulation that can
 * collapse), the SVG's `width` grows with the number of PLOTTED levels
 * (`networkPesLayout.ts`'s own `NETWORK_PES_LEVEL_GAP`) inside the SAME
 * fixed-pixel/`overflow-x: auto` shell every chart in this app already
 * uses (invariant 6) -- so a wide network scrolls instead of degrading --
 * and the connector count is separately, structurally capped by however
 * many barriers are actually deposited (never by the full channel count:
 * invariant 1 forces this regardless of network size). A 21-channel
 * network with only 4 barriers was never going to draw 21 connectors here;
 * a hypothetical future network with, say, 40 deposited barriers among 40
 * states would still be legible by the same "scroll, don't degrade" logic
 * that already covers a wide level axis.
 *
 * `states`/`channels`/`stateEnergies`/`channelBarriers` all come straight
 * off `NetworkFullRecord` -- no new fetch.
 */
export function NetworkDiagram({ states, channels, stateEnergies, channelBarriers }: {
    states: NetworkState[]
    channels: NetworkChannel[]
    stateEnergies: NetworkStateEnergy[] | null
    channelBarriers: NetworkChannelBarrier[] | null
}) {
    const barriers = channelBarriers ?? []
    const layout = computeNetworkPesLayout(states, stateEnergies ?? [], channels, barriers)

    return (
        <>
            {layout
                ? <NetworkPesSection layout={layout} states={states} channels={channels} barrierTotal={barriers.length} />
                : (
                    <p className="empty-projection">
                        No state energies are deposited for this network's own solve, so no potential-energy surface
                        can be drawn. The tables below still carry this network's full topology.
                    </p>
                )}
            <NetworkStatesTable states={states} />
            <NetworkChannelsTable channels={channels} states={states} />
        </>
    )
}

// ---------------------------------------------------------------------------
// Legend -- shape/colour/dash encoding, counts computed live (never hardcoded)
// ---------------------------------------------------------------------------

const CONNECTOR_KIND_ORDER = ["isomerization", "association", "exchange"]

function pesConnectorKindClass(kind: string): string {
    return CONNECTOR_KIND_ORDER.includes(kind) ? `net-pes-connector-${kind}` : "net-pes-connector-other"
}

function NetworkPesLegend({ layout }: { layout: NetworkPesLayout }) {
    const wellCount = layout.levels.filter((level) => level.isWell).length
    const bimolecularCount = layout.levels.length - wellCount

    const kindCounts = new Map<string, number>()
    for (const saddle of layout.saddles) kindCounts.set(saddle.kind, (kindCounts.get(saddle.kind) ?? 0) + 1)
    const kindsPresent = [...CONNECTOR_KIND_ORDER, ...[...kindCounts.keys()].filter((kind) => !CONNECTOR_KIND_ORDER.includes(kind))]
        .filter((kind) => kindCounts.has(kind))
    const noKineticsCount = layout.saddles.filter((saddle) => !saddle.hasKinetics).length

    return (
        <ul className="cp-chart-legend" aria-label="Diagram encoding">
            <li className="cp-chart-legend-item">
                <svg className="net-legend-icon" width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">
                    <line x1="1" y1="5" x2="21" y2="5" className="net-pes-level net-pes-level-well" />
                </svg>
                <span>{`Well level (${wellCount} of ${layout.levels.length} plotted states)`}</span>
            </li>
            <li className="cp-chart-legend-item">
                <svg className="net-legend-icon" width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">
                    <line x1="1" y1="5" x2="21" y2="5" className="net-pes-level net-pes-level-bimolecular" />
                </svg>
                <span>{`Bimolecular level (${bimolecularCount} of ${layout.levels.length} plotted states)`}</span>
            </li>
            {kindsPresent.map((kind) => (
                <li className="cp-chart-legend-item" key={kind}>
                    <span className={`net-legend-line net-legend-line-${CONNECTOR_KIND_ORDER.includes(kind) ? kind : "other"}`} aria-hidden="true" />
                    <span>{`${kind} (${kindCounts.get(kind)} saddle point${kindCounts.get(kind) === 1 ? "" : "s"} shown)`}</span>
                </li>
            ))}
            {layout.saddles.length > 0 && (
                <li className="cp-chart-legend-item">
                    <svg className="net-legend-icon" width="22" height="10" viewBox="0 0 22 10" aria-hidden="true">
                        <line x1="1" y1="5" x2="21" y2="5" className="net-pes-connector" strokeWidth={2} strokeDasharray="4 3" />
                    </svg>
                    <span>{`no kinetics fit deposited (${noKineticsCount} of ${layout.saddles.length} shown saddle points)`}</span>
                </li>
            )}
        </ul>
    )
}

// ---------------------------------------------------------------------------
// PES section -- legend, SVG, honest-absence notes
// ---------------------------------------------------------------------------

function NetworkPesSection({ layout, states, channels, barrierTotal }: {
    layout: NetworkPesLayout
    states: NetworkState[]
    channels: NetworkChannel[]
    barrierTotal: number
}) {
    return (
        <>
            <NetworkPesLegend layout={layout} />
            {/* Invariant 1: 17 of 21 channels on the live archive carry no
                deposited barrier and draw NO saddle point at all -- this is
                the explicit statement of that count the brief asks for,
                not a silently smaller diagram. */}
            <p className="t-body" style={{ margin: ".6rem 0 0" }}>
                {`${barrierTotal} of ${channels.length} channel${channels.length === 1 ? "" : "s"} `
                    + `carr${barrierTotal === 1 ? "ies" : "y"} a deposited barrier and appear${barrierTotal === 1 ? "s" : ""} below as a saddle point; `
                    + `the remaining channels are topology only, shown in the channel table beneath.`}
            </p>
            {layout.missingEnergyStateCount > 0 && (
                <p className="t-body" style={{ margin: ".3rem 0 0" }}>
                    {`${layout.missingEnergyStateCount} of ${states.length} state${states.length === 1 ? "" : "s"} `
                        + `${layout.missingEnergyStateCount === 1 ? "has" : "have"} no deposited energy and ${layout.missingEnergyStateCount === 1 ? "is" : "are"} omitted from this surface; see the states table below.`}
                </p>
            )}
            {layout.isolatedStateHashes.length > 0 && (
                // A plotted state that is the endpoint of zero accepted
                // saddles -- there is no deposited barrier to position it
                // by, so it is never given a connectivity-derived x
                // (module header, "UNCONNECTED STATES"). Shown as its own
                // group (see `NetworkPesSvg`'s divider) rather than
                // silently absent.
                <p className="t-body" style={{ margin: ".3rem 0 0" }}>
                    {`${layout.isolatedStateHashes.length} of ${layout.levels.length} plotted state${layout.levels.length === 1 ? "" : "s"} `
                        + `${layout.isolatedStateHashes.length === 1 ? "is" : "are"} not connected by any deposited barrier to another plotted state and `
                        + `${layout.isolatedStateHashes.length === 1 ? "is" : "are"} shown as a separate group, right of the dashed divider.`}
                </p>
            )}
            {layout.components.some((component) => component.usedSpanningTree) && (
                <p className="t-body" style={{ margin: ".3rem 0 0" }}>
                    {"This network's deposited-barrier connectivity contains a cycle in at least one group of states; "
                        + "layout falls back to a spanning tree for position there, so a connector may cross another."}
                </p>
            )}
            <NetworkPesSvg layout={layout} channels={channels} totalStates={states.length} totalChannels={channels.length} />
            {layout.excludedSaddles.length > 0 && (
                // A barrier row that WAS deposited but could not be placed
                // (an unmatched channel reference, a missing endpoint
                // energy, or a forward/reverse disagreement past
                // tolerance -- `networkPesLayout.ts`'s own header comment).
                // Named by chemistry only: `channelKey` sits in
                // `data-channel-key`, never in this list's own text
                // (invariant 4).
                <ul className="net-pes-excluded-notes">
                    {layout.excludedSaddles.map((item, index) => (
                        <li
                            key={`${item.channelKey ?? "unkeyed"}-${index}`}
                            className="note"
                            data-channel-key={item.channelKey ?? undefined}
                        >
                            {item.sourceLabel && item.sinkLabel
                                ? `${item.sourceLabel} to ${item.sinkLabel}: ${item.reason}.`
                                : `A deposited barrier could not be shown: ${item.reason}.`}
                        </li>
                    ))}
                </ul>
            )}
        </>
    )
}

// ---------------------------------------------------------------------------
// SVG -- fixed pixel size (grows with plotted level count, never a
// percentage), overflow-x: auto container -- same shell every chart in this
// app uses (invariant 6).
// ---------------------------------------------------------------------------

function stateRowId(compositionHash: string): string {
    return `state-row-${compositionHash}`
}

function channelRowId(channelKey: string | null, index: number): string {
    return `channel-row-${channelKey ?? `unkeyed-${index}`}`
}

function NetworkPesSvg({ layout, channels, totalStates, totalChannels }: {
    layout: NetworkPesLayout
    channels: NetworkChannel[]
    totalStates: number
    totalChannels: number
}) {
    // Every saddle carries a real `channelKey` (it is built from a
    // `NetworkChannelBarrier` row, whose own `channel_key` field is
    // required) -- this map exists only so the table anchor uses the
    // SAME index `NetworkChannelsTable` gave that row, never a
    // recomputed/guessed one.
    const channelIndexByKey = new Map(channels.map((channel, index) => [channel.channel_key, index]))
    const yScale = linearScale(layout.yDomain, [layout.plotBottom, layout.plotTop])
    const yTicks = niceTicks(layout.yDomain, 5)
    const yTickLabels = formatTicks(yTicks)
    const plotLeft = NETWORK_PES_MARGIN.left
    const plotRight = layout.width - NETWORK_PES_MARGIN.right

    const ariaLabel = `Potential-energy surface: ${layout.levels.length} of ${totalStates} states plotted, `
        + `${layout.saddles.length} of ${totalChannels} channels shown as a saddle point, `
        + `relative energy in kilojoules per mole, electronic-only, referenced to the lowest state`

    // A dashed vertical divider between the connectivity-laid-out group(s)
    // and the unconnected-states group, only drawn when both actually
    // exist (module header, "UNCONNECTED STATES") -- placed at the
    // midpoint of the gap between them, never touching either group's own
    // bar or caption.
    const connectedLevels = layout.levels.filter((level) => !level.isUnconnected)
    const unconnectedLevels = layout.levels.filter((level) => level.isUnconnected)
    const dividerX = connectedLevels.length > 0 && unconnectedLevels.length > 0
        ? (Math.max(...connectedLevels.map((level) => level.x)) + Math.min(...unconnectedLevels.map((level) => level.x))) / 2
        : null

    return (
        <div className="network-diagram">
            <svg
                className="network-diagram-svg net-pes-svg"
                width={layout.width}
                height={layout.height}
                viewBox={`0 0 ${layout.width} ${layout.height}`}
                role="img"
                aria-label={ariaLabel}
            >
                <g aria-hidden="true">
                    {yTicks.map((tick, index) => (
                        <g key={`y-${tick}`}>
                            <line x1={plotLeft} x2={plotRight} y1={yScale(tick)} y2={yScale(tick)} className="net-pes-gridline" />
                            <text x={plotLeft - 10} y={yScale(tick)} className="net-pes-tick-label">{yTickLabels[index]}</text>
                        </g>
                    ))}
                    <line x1={plotLeft} x2={plotLeft} y1={layout.plotTop} y2={layout.plotBottom} className="net-pes-axis-line" />
                    {dividerX != null && (
                        <line
                            x1={dividerX}
                            x2={dividerX}
                            y1={layout.plotTop}
                            y2={layout.plotBottom}
                            className="net-pes-group-divider"
                            data-testid="net-pes-group-divider"
                        />
                    )}
                </g>

                <g aria-hidden="true">
                    {layout.saddles.map((saddle, index) => {
                        const rowIndex = channelIndexByKey.get(saddle.channelKey) ?? index
                        // Saddles are drawn as a short horizontal BAR (like
                        // a state level), not a point -- the reference
                        // figure's own TS convention. The two connector
                        // LEGS run from each endpoint state to whichever
                        // bar edge sits on that endpoint's own side, so the
                        // shape reads as the classic peak/valley trapezoid
                        // rather than converging on the bar's centre.
                        const barLeftX = saddle.peakX - NETWORK_PES_TS_BAR_HALF_WIDTH
                        const barRightX = saddle.peakX + NETWORK_PES_TS_BAR_HALF_WIDTH
                        const sourceEdgeX = saddle.sourceX <= saddle.sinkX ? barLeftX : barRightX
                        const sinkEdgeX = saddle.sourceX <= saddle.sinkX ? barRightX : barLeftX
                        // The STATE end of each leg launches from
                        // `sourceLegX`/`sinkLegX` (`networkPesLayout.ts`'s
                        // own `connectorLegX`), not `sourceX`/`sinkX` --
                        // a state's caption sits centred on its bar, and a
                        // chain state (a straight run like [NH-][NH3+]
                        // between two other accepted saddles) has one leg
                        // arriving and another departing from that same
                        // centre point; two lines converging there cut
                        // straight through the caption's digits even with
                        // the halo (found by screenshotting the live
                        // hydrazine archive and looking, not by inspection
                        // -- "180.1 kJ/mol" read as "180?1¢kJ/mol").
                        const dash = saddle.hasKinetics ? undefined : "4 3"
                        return (
                            <a
                                key={saddle.channelKey ?? `${saddle.sourceHash}-${saddle.sinkHash}-${index}`}
                                href={`#${channelRowId(saddle.channelKey, rowIndex)}`}
                                className="net-pes-saddle-link"
                                // Identified by chemistry only -- never
                                // `channel_key` (invariant 4).
                                aria-label={`${saddle.kind} channel, ${saddle.sourceLabel} to ${saddle.sinkLabel}, `
                                    + `transition-state height ${saddle.heightKjMol.toFixed(1)} kilojoules per mole`
                                    + `${saddle.hasKinetics ? "" : ", no kinetics fit deposited"}`}
                            >
                                <polyline
                                    points={`${saddle.sourceLegX},${saddle.sourceY} ${sourceEdgeX},${saddle.peakY}`}
                                    // `channel_key` lives in exactly two
                                    // places: this `data-*` hook and the
                                    // channel table row (invariant 4).
                                    data-channel-key={saddle.channelKey ?? undefined}
                                    className={`net-pes-connector ${pesConnectorKindClass(saddle.kind)}`}
                                    strokeDasharray={dash}
                                />
                                <line
                                    x1={barLeftX}
                                    x2={barRightX}
                                    y1={saddle.peakY}
                                    y2={saddle.peakY}
                                    data-channel-key={saddle.channelKey ?? undefined}
                                    className={`net-pes-ts-bar ${pesConnectorKindClass(saddle.kind)}`}
                                    strokeDasharray={dash}
                                />
                                <polyline
                                    points={`${sinkEdgeX},${saddle.peakY} ${saddle.sinkLegX},${saddle.sinkY}`}
                                    data-channel-key={saddle.channelKey ?? undefined}
                                    className={`net-pes-connector ${pesConnectorKindClass(saddle.kind)}`}
                                    strokeDasharray={dash}
                                />
                                {/* A NUMBER, never `channel_key` -- the one
                                    piece of text this whole surface exists
                                    to show (owner: "it's best to show a PES
                                    with TS energies"), drawn ABOVE the bar
                                    (reference figure convention, module
                                    header point 2). */}
                                <text x={saddle.peakX} y={saddle.peakY - 10} textAnchor="middle" className="net-pes-peak-label">
                                    {saddle.heightKjMol.toFixed(1)}
                                </text>
                            </a>
                        )
                    })}
                </g>

                <g>
                    {layout.levels.map((level) => {
                        const energyCaption = `${level.energyKjMol.toFixed(1)} kJ/mol`
                        // A caption's own opaque backing -- wide enough for
                        // ITS string specifically (energy and label are
                        // usually different lengths), drawn before the
                        // `<text>` so it sits behind it but in front of
                        // everything else in this SVG, gridlines included.
                        // The per-glyph stroke halo below (`paint-order:
                        // stroke` in the stylesheet) is not enough on its
                        // own: it protects each GLYPH's own outline but
                        // not the gaps between glyphs, and a y-axis
                        // gridline landing close to a caption's own y (a
                        // coincidence of that state's specific energy, not
                        // of the x layout) drew straight through those
                        // gaps on the live hydrazine archive -- found by
                        // screenshotting it and looking, not by
                        // inspection.
                        const energyHalf = (energyCaption.length * NETWORK_PES_CAPTION_CHAR_WIDTH) / 2
                        const labelHalf = (level.label.length * NETWORK_PES_CAPTION_CHAR_WIDTH) / 2
                        return (
                            <a
                                key={level.compositionHash}
                                href={`#${stateRowId(level.compositionHash)}`}
                                className="net-pes-level-link"
                                data-unconnected={level.isUnconnected ? "true" : undefined}
                                aria-label={`State ${level.label}, relative energy ${level.energyKjMol.toFixed(1)} kilojoules per mole, ${level.isWell ? "well" : "bimolecular"}`
                                    + `${level.isUnconnected ? ", not connected by any deposited barrier to another plotted state" : ""}`}
                            >
                                <line
                                    x1={level.x - NETWORK_PES_LEVEL_HALF_WIDTH}
                                    x2={level.x + NETWORK_PES_LEVEL_HALF_WIDTH}
                                    y1={level.y}
                                    y2={level.y}
                                    className={`net-pes-level ${level.isWell ? "net-pes-level-well" : "net-pes-level-bimolecular"}`}
                                />
                                <rect
                                    aria-hidden="true"
                                    x={level.x - energyHalf - 2}
                                    y={level.y - 23}
                                    width={energyHalf * 2 + 4}
                                    height={14}
                                    className="net-pes-caption-backing"
                                />
                                <rect
                                    aria-hidden="true"
                                    x={level.x - labelHalf - 2}
                                    y={level.y + 10}
                                    width={labelHalf * 2 + 4}
                                    height={14}
                                    className="net-pes-caption-backing"
                                />
                                {/* Energy caption ABOVE the bar, species
                                    label BELOW it -- the reference
                                    figure's own convention (module header
                                    point 5), the reverse of this
                                    component's earlier layout. The ONLY
                                    safe level label --
                                    `composition.state_label`, already
                                    baked into `level.label` by
                                    `computeNetworkPesLayout`. Never
                                    `states[].label`, never the raw hash
                                    (invariant 3). */}
                                <text x={level.x} y={level.y - 12} textAnchor="middle" className="net-pes-level-value">
                                    {energyCaption}
                                </text>
                                <text x={level.x} y={level.y + 20} textAnchor="middle" className="net-pes-level-label">{level.label}</text>
                            </a>
                        )
                    })}
                </g>
            </svg>
            <p className="net-pes-axis-title net-pes-axis-title--y">
                Relative energy (kJ/mol) — electronic-only, referenced to the lowest state. Not a free-energy surface.
            </p>
            <p className="net-pes-axis-title net-pes-axis-title--x">
                Horizontal position reflects each state's deposited-barrier connectivity, radiating out from the
                most-connected state — this axis is not a reaction coordinate and carries no energy meaning.
            </p>
        </div>
    )
}

// ---------------------------------------------------------------------------
// Accessible tables -- ALWAYS rendered (invariant 5), unchanged from the
// force-directed diagram's own tables.
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
