import { useEffect, useId, useMemo, useState } from "react"
import "../arrhenius-chart.css"
import "../thermo-cp-chart.css"
import "../network-ktp-chart.css"
import type { NetworkChannel, NetworkFullRecord, NetworkState } from "../api/networkEntryApi"
import { loadNetworkKtpEvaluation, type NetworkKtpFit } from "../api/networkKineticsEvalApi"
import {
    ARRHENIUS_CHART_HEIGHT,
    ARRHENIUS_CHART_MARGIN,
    ARRHENIUS_CHART_WIDTH,
    arrheniusLog10AxisTitle,
    arrheniusUnitLabel,
} from "../domain/arrheniusChartLayout"
import { domainWithPadding, formatTicks, linearScale } from "../domain/chartScale"
import { log10Text, scientificText } from "../domain/kineticsTable"
import {
    buildKtpRequestGrid,
    groupKtpFitsByChannel,
    ktpLineSegments,
    ktpPlottedPoints,
    modelKindColor,
    modelKindLabel,
    type KtpChannelGroup,
    type KtpPlottedPoint,
    type KtpRequestGrid,
} from "../domain/networkKtpChartLayout"
import { niceTicks } from "../domain/thermoCpChartLayout"
import { Disclosure } from "./Disclosure"

/**
 * PR 4 of `docs/plans/pressure-dependent-network-surface.md` (§3.4/§5):
 * the network entry page's k(T,P) chart. Fires exactly ONE request per
 * mount -- `POST /scientific/networks/{ref}/kinetics/evaluate`
 * (`api/networkKineticsEvalApi.ts`) -- covering every stored fit on the
 * network at one shared `(temperature_k, pressure_bar)` grid, never one
 * request per channel or per fit (invariant 1).
 *
 * **Every channel that carries kinetics carries MORE THAN ONE fit on the
 * live archive** (21 channels, 42 fits: a Chebyshev AND a PLOG
 * parameterization of the SAME channel, not an edge case). This
 * component never picks, prefers, averages, or collapses them --
 * `groupKtpFitsByChannel` keeps every fit a channel has, and every panel
 * below renders all of them, colour-coded by model kind (a FIXED colour
 * per model kind across the whole page, not per panel, so "this colour
 * always means Chebyshev" holds everywhere).
 *
 * Renders only served `points[]` -- `k`, `in_range` -- and never
 * evaluates Chebyshev or PLOG itself (invariant 2); see
 * `domain/networkKtpChartLayout.ts`'s own header comment.
 */
export function NetworkKtpChart({ networkRef, network, channels, states }: {
    networkRef: string
    network: NetworkFullRecord["network"]
    channels: NetworkChannel[]
    states: NetworkState[]
}) {
    const grid = useMemo(
        () => buildKtpRequestGrid(
            network.solve_temperature_min_k,
            network.solve_temperature_max_k,
            network.solve_pressure_min_bar,
            network.solve_pressure_max_bar,
        ),
        [network.solve_temperature_min_k, network.solve_temperature_max_k, network.solve_pressure_min_bar, network.solve_pressure_max_bar],
    )
    const anyChannelHasKinetics = channels.some((channel) => channel.has_kinetics)

    type FetchState =
        | { status: "loading" }
        | { status: "error" }
        | { status: "ready"; fits: NetworkKtpFit[] }

    const [state, setState] = useState<FetchState>({ status: "loading" })

    useEffect(() => {
        // No fetch at all when there is nothing to ask for -- an honest
        // degrade (invariant 9), rendered below straight from `grid`/
        // `anyChannelHasKinetics` rather than routed through this
        // component's own fetch-state machine, so this effect never needs
        // to `setState` just to represent "there was nothing to fetch".
        if (!grid || !anyChannelHasKinetics) return
        const controller = new AbortController()
        loadNetworkKtpEvaluation(networkRef, grid.temperaturesK, grid.pressuresBar, controller.signal)
            .then((result) => setState({ status: "ready", fits: result.fits }))
            .catch((error: unknown) => {
                if (error instanceof DOMException && error.name === "AbortError") return
                setState({ status: "error" })
            })
        return () => controller.abort()
        // `grid` is memoised above from the network's own solve range,
        // which does not change across this component's lifetime -- this
        // effect is written to fire once per mount (invariant 1), not on
        // every render.
    }, [networkRef, grid, anyChannelHasKinetics])

    if (!grid || !anyChannelHasKinetics) {
        return (
            <p className="empty-projection">
                {!anyChannelHasKinetics
                    ? "No k(T,P) fits are deposited on this network."
                    : "This network's solve does not record a temperature/pressure range, so a k(T,P) grid cannot be requested."}
            </p>
        )
    }
    if (state.status === "loading") {
        return <p className="t-body">Evaluating this network's deposited k(T,P) fits…</p>
    }
    if (state.status === "error") {
        return <p className="empty-projection">This network's k(T,P) fits could not be evaluated right now.</p>
    }
    if (state.fits.length === 0) {
        return <p className="empty-projection">No k(T,P) fits are deposited on this network.</p>
    }

    return <NetworkKtpChartReady fits={state.fits} channels={channels} states={states} grid={grid as KtpRequestGrid} />
}

// ---------------------------------------------------------------------------
// Ready state -- grouping, selection, panels
// ---------------------------------------------------------------------------

function NetworkKtpChartReady({ fits, channels, states, grid }: {
    fits: NetworkKtpFit[]
    channels: NetworkChannel[]
    states: NetworkState[]
    grid: KtpRequestGrid
}) {
    const groups = useMemo(() => groupKtpFitsByChannel(fits, channels, states), [fits, channels, states])
    const pressureOptions = grid.pressuresBar
    const [selectedPressureBar, setSelectedPressureBar] = useState(() => pressureOptions[Math.floor(pressureOptions.length / 2)] ?? pressureOptions[0])
    const [selectedChannelKeys, setSelectedChannelKeys] = useState<string[]>(() => (groups[0] ? [groups[0].channelKey] : []))
    const pressureSelectId = useId()
    const xDomain = useMemo(() => domainWithPadding(grid.temperaturesK), [grid.temperaturesK])

    if (groups.length === 0) {
        return <p className="empty-projection">No k(T,P) fits are deposited on this network.</p>
    }

    function toggleChannel(key: string) {
        setSelectedChannelKeys((prev) => (prev.includes(key) ? prev.filter((existing) => existing !== key) : [...prev, key]))
    }

    const selectedGroups = groups.filter((group) => selectedChannelKeys.includes(group.channelKey))

    return (
        <div className="network-ktp-section">
            <div className="arrhenius-chart-controls">
                <label className="arrhenius-chart-control" htmlFor={pressureSelectId}>
                    <span className="arrhenius-chart-control-label">Pressure</span>
                    <select
                        id={pressureSelectId}
                        className="arrhenius-chart-control-select"
                        value={selectedPressureBar}
                        onChange={(event) => setSelectedPressureBar(Number(event.target.value))}
                    >
                        {pressureOptions.map((pressure) => (
                            <option key={pressure} value={pressure}>{`${scientificText(pressure)} bar`}</option>
                        ))}
                    </select>
                </label>
            </div>

            <fieldset className="network-ktp-channel-fieldset">
                <legend>{`Channels (${selectedGroups.length} of ${groups.length} shown)`}</legend>
                <div className="network-ktp-channel-options">
                    {groups.map((group) => (
                        <label className="network-ktp-channel-option" key={group.channelKey} data-channel-key={group.channelKey}>
                            <input
                                type="checkbox"
                                checked={selectedChannelKeys.includes(group.channelKey)}
                                onChange={() => toggleChannel(group.channelKey)}
                            />
                            {`${group.label} (${group.channelKind})`}
                        </label>
                    ))}
                </div>
            </fieldset>

            {selectedGroups.length === 0
                ? <p className="empty-projection">Select a channel above to see its evaluated rate coefficient.</p>
                : selectedGroups.map((group) => (
                    <KtpChannelPanel key={group.channelKey} group={group} pressureBar={selectedPressureBar} xDomain={xDomain} />
                ))}
        </div>
    )
}

// ---------------------------------------------------------------------------
// One channel's panel -- every one of its fits, never collapsed to one
// ---------------------------------------------------------------------------

function KtpChannelPanel({ group, pressureBar, xDomain }: { group: KtpChannelGroup; pressureBar: number; xDomain: [number, number] }) {
    const seriesData = useMemo(
        () => group.series.map((fit) => ({ fit, points: ktpPlottedPoints(fit.points, pressureBar) })),
        [group.series, pressureBar],
    )
    const allLog10k = seriesData.flatMap(({ points }) => points.map((point) => point.log10k))
    const yDomain = domainWithPadding(allLog10k)
    const { top, right, bottom, left } = ARRHENIUS_CHART_MARGIN
    const plotWidth = ARRHENIUS_CHART_WIDTH - left - right
    const plotHeight = ARRHENIUS_CHART_HEIGHT - top - bottom
    const xScale = linearScale(xDomain, [left, left + plotWidth])
    const yScale = linearScale(yDomain, [top + plotHeight, top])
    const xTicks = niceTicks(xDomain, 5)
    const yTicks = niceTicks(yDomain, 5)
    const xTickLabels = formatTicks(xTicks)
    const yTickLabels = formatTicks(yTicks)

    const kUnits = [...new Set(group.series.map((fit) => fit.k_units))]
    const axisUnits = kUnits.length === 1 ? kUnits[0] : null
    const anyOutOfRange = seriesData.some(({ points }) => points.some((point) => !point.inRange))

    const modelKindsText = group.series.map((fit) => modelKindLabel(fit.model_kind)).join(" and ")
    const ariaLabel = `k(T,P) evaluated at ${scientificText(pressureBar)} bar for channel ${group.label}: `
        + `${modelKindsText}, log10 k versus temperature in kelvin`

    return (
        <div className="arrhenius-chart-panel-wrap" data-channel-key={group.channelKey}>
            <p className="network-ktp-panel-heading">{`${group.label} (${group.channelKind})`}</p>
            <ul className="arrhenius-chart-legend" aria-label="Model kind and validity-range encoding">
                {group.series.map((fit) => (
                    <li className="arrhenius-chart-legend-item" key={fit.network_kinetics_ref}>
                        <span className="arrhenius-chart-swatch" style={{ background: modelKindColor(fit.model_kind) }} aria-hidden="true" />
                        <code className="data">{modelKindLabel(fit.model_kind)}</code>
                    </li>
                ))}
                {anyOutOfRange && (
                    <li className="arrhenius-chart-legend-item">
                        <span className="network-ktp-line-swatch network-ktp-line-swatch--dashed" aria-hidden="true" />
                        <span>outside this fit's own stated validity range</span>
                    </li>
                )}
            </ul>
            <p className="arrhenius-chart-axis-title arrhenius-chart-axis-title--y">{arrheniusLog10AxisTitle(axisUnits)}</p>
            <div className="arrhenius-chart-panel">
                <div className="arrhenius-chart-scroll">
                    <svg
                        width={ARRHENIUS_CHART_WIDTH}
                        height={ARRHENIUS_CHART_HEIGHT}
                        viewBox={`0 0 ${ARRHENIUS_CHART_WIDTH} ${ARRHENIUS_CHART_HEIGHT}`}
                        role="img"
                        aria-label={ariaLabel}
                        className="arrhenius-chart-svg"
                    >
                        {yTicks.map((tick, index) => (
                            <g key={`y-${tick}`}>
                                <line x1={left} x2={left + plotWidth} y1={yScale(tick)} y2={yScale(tick)} className="arrhenius-chart-gridline" />
                                <text x={left - 8} y={yScale(tick)} className="arrhenius-chart-tick-label arrhenius-chart-tick-label--y">
                                    {yTickLabels[index]}
                                </text>
                            </g>
                        ))}
                        {xTicks.map((tick, index) => (
                            <g key={`x-${tick}`}>
                                <line x1={xScale(tick)} x2={xScale(tick)} y1={top} y2={top + plotHeight} className="arrhenius-chart-gridline" />
                                <text x={xScale(tick)} y={top + plotHeight + 16} className="arrhenius-chart-tick-label arrhenius-chart-tick-label--x">
                                    {xTickLabels[index]}
                                </text>
                            </g>
                        ))}
                        <line x1={left} x2={left + plotWidth} y1={top + plotHeight} y2={top + plotHeight} className="arrhenius-chart-axis-line" />
                        <line x1={left} x2={left} y1={top} y2={top + plotHeight} className="arrhenius-chart-axis-line" />
                        {seriesData.map(({ fit, points }) => (
                            <g
                                key={fit.network_kinetics_ref}
                                data-testid={`ktp-line-${fit.network_kinetics_ref}`}
                                data-model-kind={fit.model_kind}
                                data-channel-key={group.channelKey}
                            >
                                {ktpLineSegments(points).map((segment, segmentIndex) => (
                                    <polyline
                                        key={segmentIndex}
                                        points={segment.points.map((point) => `${xScale(point.temperatureK)},${yScale(point.log10k)}`).join(" ")}
                                        fill="none"
                                        stroke={modelKindColor(fit.model_kind)}
                                        strokeWidth={1.75}
                                        strokeDasharray={segment.solid ? undefined : "4 3"}
                                        data-in-range={segment.solid}
                                    />
                                ))}
                            </g>
                        ))}
                    </svg>
                    <p className="arrhenius-chart-axis-title arrhenius-chart-axis-title--x" style={{ marginLeft: left, width: plotWidth }}>
                        Temperature (K)
                    </p>
                </div>
            </div>
            <KtpChannelTable group={group} pressureBar={pressureBar} seriesData={seriesData} axisUnits={axisUnits} />
        </div>
    )
}

// ---------------------------------------------------------------------------
// Table-behind-Disclosure -- the same selected-pressure data the panel plots
// ---------------------------------------------------------------------------

function KtpChannelTable({ group, pressureBar, seriesData, axisUnits }: {
    group: KtpChannelGroup
    pressureBar: number
    seriesData: { fit: NetworkKtpFit; points: KtpPlottedPoint[] }[]
    axisUnits: string | null
}) {
    const rows = seriesData
        .flatMap(({ fit, points }) => points.map((point) => ({ ref: fit.network_kinetics_ref, modelKind: fit.model_kind, ...point })))
        .sort((a, b) => a.temperatureK - b.temperatureK || a.modelKind.localeCompare(b.modelKind))
    const unitLabel = axisUnits ? arrheniusUnitLabel(axisUnits) : ""

    return (
        <Disclosure summary={<code className="data">{`k(T) table — ${group.label} (${group.channelKind})`}</code>} count={rows.length}>
            <div className="table-scroll">
                <table className="data-table kinetics-k-table" aria-label={`k(T) at ${scientificText(pressureBar)} bar for ${group.label}`}>
                    <caption>{`Evaluated at ${scientificText(pressureBar)} bar${unitLabel ? `, in ${unitLabel}` : ""}`}</caption>
                    <thead>
                        <tr>
                            <th scope="col">T (K)</th>
                            <th scope="col">Model</th>
                            <th scope="col">{unitLabel ? `k (${unitLabel})` : "k"}</th>
                            <th scope="col">log₁₀ k</th>
                            <th scope="col">In fitted range</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, index) => (
                            <tr key={`${row.ref}-${index}`}>
                                <td className="num" data-label="T (K)">{row.temperatureK.toFixed(2)}</td>
                                <td data-label="Model">{modelKindLabel(row.modelKind)}</td>
                                <td className="num" data-label="k">{scientificText(row.k)}</td>
                                <td className="num" data-label="log10 k">{log10Text(row.k)}</td>
                                <td data-label="In fitted range">{row.inRange ? "yes" : "no"}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Disclosure>
    )
}
