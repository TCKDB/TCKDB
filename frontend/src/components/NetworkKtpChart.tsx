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
    arrheniusPointX,
    arrheniusUnitLabel,
    type ArrheniusXAxisMode,
} from "../domain/arrheniusChartLayout"
import { FAMILY_NAME } from "../domain/arrheniusUnits"
import { domainWithPadding, formatTicks, linearScale } from "../domain/chartScale"
import { log10Text, scientificText } from "../domain/kineticsTable"
import {
    buildKtpRequestGrid,
    channelColorIndex,
    channelSeriesColor,
    convertKtpPlottedPoints,
    groupKtpChannelsByUnitFamily,
    groupKtpFitsByChannel,
    ktpLineSegments,
    ktpPlottedPoints,
    ktpXDomain,
    modelKindLabel,
    modelKindStrokeColor,
    modelKindStrokeWidth,
    type KtpChannelGroup,
    type KtpFamilyPanel,
    type KtpPlottedPoint,
    type KtpRequestGrid,
} from "../domain/networkKtpChartLayout"
import { niceTicks } from "../domain/thermoCpChartLayout"
import { Disclosure } from "./Disclosure"

/**
 * PR 4 of `docs/plans/pressure-dependent-network-surface.md` (§3.4/§5),
 * extended by the stacked-panel/unit-control follow-up: the network entry
 * page's k(T,P) chart. Fires exactly ONE request per mount -- `POST
 * /scientific/networks/{ref}/kinetics/evaluate`
 * (`api/networkKineticsEvalApi.ts`) -- covering every stored fit on the
 * network at one shared `(temperature_k, pressure_bar)` grid, never one
 * request per channel or per fit (invariant 1).
 *
 * **Every channel that carries kinetics carries MORE THAN ONE fit on the
 * live archive** (21 channels, 42 fits: a Chebyshev AND a PLOG
 * parameterization of the SAME channel, not an edge case). This
 * component never picks, prefers, averages, or collapses them --
 * `groupKtpFitsByChannel` keeps every fit a channel has.
 *
 * SHAPE (owner's report: "shouldn't they all stack on one graph not
 * create multiple for each clicked? Also why is there no control for
 * changing the y axis to other units like we do with Arrhenius?"). Both
 * were fair, but "stack on one graph" is only TRUE within one unit
 * FAMILY: on this network `per_s` (isomerization, unimolecular) and
 * `cm3_mol_s` (association/exchange, bimolecular) are different physical
 * DIMENSIONS and cannot share a y-axis without silently comparing two
 * incomparable quantities. `groupKtpChannelsByUnitFamily`
 * (`domain/networkKtpChartLayout.ts`) is the one place that split is
 * decided -- this component renders exactly one panel per family it
 * returns (at most two here), overlaying every SELECTED channel that
 * belongs to that family on one shared axis, with its own per-panel unit
 * `<select>` when the family has more than one interchangeable unit
 * (`per_s`'s family has exactly one member -- a unimolecular rate
 * coefficient has no concentration unit to convert to -- so that panel
 * gets no control at all, not a disabled one; see
 * `KtpFamilyUnitSelect` below).
 *
 * SERIES ENCODING -- the genuinely hard part once channels overlay:
 * N channels x 2 model kinds (Chebyshev, PLOG, both always shown) is up
 * to 2N lines on one axes, and dash is already spoken for (`ktpLineSegments`
 * -- in_range vs extrapolated, per point). Chosen: COLOUR is channel
 * identity (`channelSeriesColor`, a stable per-channel hue that does not
 * shift when a different channel is toggled), and model kind is a tint of
 * that SAME hue (`modelKindStrokeColor` -- PLOG mixed 45% toward white,
 * Chebyshev untouched) PLUS a stroke-width difference
 * (`modelKindStrokeWidth` -- 2.25px vs 1.25px), so the two are still
 * tell-apart-able even where the tint alone is hard to perceive (a
 * colour-vision deficiency, a greyscale printout). REJECTED: colour-by-
 * channel with model kind as opacity alone -- opacity interacts badly
 * with the dashed (out-of-range) segments already on the chart: a thin,
 * already-faded PLOG dash all but disappears against a busy multi-channel
 * panel, where a fully-opaque tinted stroke does not. Also rejected:
 * colour by MODEL KIND (the old, pre-overlay scheme) -- with several
 * channels overlaid, two channels of the same model kind would render
 * identically and be indistinguishable from one another, which is a worse
 * failure than the one this redesign set out to fix.
 *
 * Renders only served `points[]` -- `k`, `in_range` -- and never
 * evaluates Chebyshev or PLOG itself (invariant 2); a per-panel unit
 * conversion multiplies an already-served `k` by a fixed dimensional
 * factor from `arrheniusUnits.ts` (`convertKtpPlottedPoints`), which is
 * NOT a re-evaluation -- see `domain/networkKtpChartLayout.ts`'s own
 * header comment.
 *
 * X-AXIS (owner: "why can't I change temp to 1/temp" -- the Arrhenius chart
 * already has this control, this one didn't). `KtpXAxisSelect` below is the
 * third control of this kind on this chart (after unit selection and family
 * panels): one chart-wide `<select>` (`NetworkKtpChartReady`'s own
 * `xAxisMode` state), governing every family panel's x-axis at once, exactly
 * like `ArrheniusChart.tsx`'s own single `xAxisMode` control. Point
 * projection reuses `arrheniusChartLayout.ts`'s `arrheniusPointX` verbatim
 * (never re-derives `1000/T`); the domain reuses the SAME module's
 * monotonicity argument via this file's own `ktpXDomain`
 * (`domain/networkKtpChartLayout.ts`) -- see that function's comment for why
 * no separate "reverse the axis" branch is needed. The k(T) TABLE behind
 * each channel's `Disclosure` is unaffected by this control, exactly
 * matching `ArrheniusChart.tsx`'s own k(T) table: both always tabulate T in
 * kelvin regardless of which x-axis mode the plot above is currently
 * showing.
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
    const colorIndexByChannelKey = useMemo(() => channelColorIndex(groups), [groups])
    const pressureOptions = grid.pressuresBar
    const [selectedPressureBar, setSelectedPressureBar] = useState(() => pressureOptions[Math.floor(pressureOptions.length / 2)] ?? pressureOptions[0])
    // Only the FIRST channel is selected by default -- the "sensible
    // default" the owner asked for (overlaying every selected channel's
    // every fit means N channels is already 2N lines before any unit
    // conversion is even in play; opening on all 21 channels at once would
    // be exactly the "42 lines" problem the panel-per-family redesign does
    // NOT solve by itself, since it only bounds the PANEL count, not the
    // line count within one). A reader opts into more via the checkboxes
    // below.
    const [selectedChannelKeys, setSelectedChannelKeys] = useState<string[]>(() => (groups[0] ? [groups[0].channelKey] : []))
    const [selectedUnitsByFamily, setSelectedUnitsByFamily] = useState<Record<string, string>>({})
    // ONE control for the whole chart (mirrors `ArrheniusChart.tsx`'s own
    // single top-level `xAxisMode` -- see that component's header comment):
    // which axis a reader wants is a reading-convention choice, not a
    // per-channel or per-family fact, so this state lives here, above
    // `familyPanels`, and is read (never re-derived) by every family panel
    // below rather than each panel owning its own copy.
    const [xAxisMode, setXAxisMode] = useState<ArrheniusXAxisMode>("temperature")
    const pressureSelectId = useId()
    const xAxisSelectId = useId()
    const xDomain = useMemo(() => ktpXDomain(grid.temperaturesK, xAxisMode), [grid.temperaturesK, xAxisMode])

    if (groups.length === 0) {
        return <p className="empty-projection">No k(T,P) fits are deposited on this network.</p>
    }

    function toggleChannel(key: string) {
        setSelectedChannelKeys((prev) => (prev.includes(key) ? prev.filter((existing) => existing !== key) : [...prev, key]))
    }

    const selectedGroups = groups.filter((group) => selectedChannelKeys.includes(group.channelKey))
    // Not `useMemo` -- this runs after the `groups.length === 0` early
    // return above, and a hook may never be called conditionally
    // (`react-hooks/rules-of-hooks`). Grouping a handful of already-
    // filtered channel groups by unit family is cheap enough that a plain
    // per-render call costs nothing worth memoising anyway.
    const familyPanels = groupKtpChannelsByUnitFamily(selectedGroups)

    return (
        <div className="network-ktp-section">
            <div className="arrhenius-chart-controls">
                <KtpXAxisSelect id={xAxisSelectId} xAxisMode={xAxisMode} onSelectXAxisMode={setXAxisMode} />
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
                            <span
                                className="network-ktp-channel-swatch"
                                style={{ background: channelSeriesColor(colorIndexByChannelKey.get(group.channelKey) ?? 0) }}
                                aria-hidden="true"
                            />
                            {`${group.label} (${group.channelKind})`}
                        </label>
                    ))}
                </div>
            </fieldset>

            {selectedGroups.length === 0
                ? <p className="empty-projection">Select a channel above to see its evaluated rate coefficient.</p>
                : familyPanels.map((panel) => (
                    <KtpFamilyPanelChart
                        key={panel.key}
                        panel={panel}
                        pressureBar={selectedPressureBar}
                        xDomain={xDomain}
                        xAxisMode={xAxisMode}
                        colorIndexByChannelKey={colorIndexByChannelKey}
                        selectedUnits={selectedUnitsByFamily[panel.key] ?? panel.defaultUnits}
                        onSelectUnits={(units) => setSelectedUnitsByFamily((prev) => ({ ...prev, [panel.key]: units }))}
                    />
                ))}
        </div>
    )
}

// ---------------------------------------------------------------------------
// One unit-family panel -- every SELECTED channel that belongs to it,
// overlaid; every one of each channel's fits, never collapsed.
// ---------------------------------------------------------------------------

interface ChannelSeriesEntry {
    group: KtpChannelGroup
    fits: { fit: NetworkKtpFit; points: KtpPlottedPoint[] }[]
}

function KtpFamilyPanelChart({ panel, pressureBar, xDomain, xAxisMode, colorIndexByChannelKey, selectedUnits, onSelectUnits }: {
    panel: KtpFamilyPanel
    pressureBar: number
    xDomain: [number, number]
    xAxisMode: ArrheniusXAxisMode
    colorIndexByChannelKey: Map<string, number>
    selectedUnits: string | undefined
    onSelectUnits: (units: string) => void
}) {
    const displayUnits = selectedUnits ?? panel.defaultUnits
    const hasUnitChoice = panel.availableUnits.length > 1

    const channelSeriesData: ChannelSeriesEntry[] = useMemo(() => panel.groups.map((group) => ({
        group,
        fits: group.series.map((fit) => {
            const rawPoints = ktpPlottedPoints(fit.points, pressureBar)
            const points = displayUnits && displayUnits !== fit.k_units
                ? convertKtpPlottedPoints(rawPoints, fit.k_units, displayUnits)
                : rawPoints
            return { fit, points }
        }),
    })), [panel.groups, pressureBar, displayUnits])

    const allLog10k = channelSeriesData.flatMap(({ fits }) => fits.flatMap(({ points }) => points.map((point) => point.log10k)))
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

    const anyOutOfRange = channelSeriesData.some(({ fits }) => fits.some(({ points }) => points.some((point) => !point.inRange)))
    const modelKindsPresent = [...new Set(channelSeriesData.flatMap(({ fits }) => fits.map(({ fit }) => fit.model_kind)))]

    const familyHeading = panel.orderFamily != null
        ? `${FAMILY_NAME[panel.orderFamily].replace(/^./, (c) => c.toUpperCase())} channels`
        : "Channels with unrecognised units"

    const channelLabelsText = panel.groups.map((entry) => entry.label).join(", ")
    const modelKindsText = modelKindsPresent.map(modelKindLabel).join(" and ")
    // Mirrors `ArrheniusChart.tsx`'s own `axisDescription` -- the
    // `aria-label` is this panel's accessible substitute for seeing the
    // axis drawn, so it must name whichever axis is actually current, not
    // always "temperature in kelvin". No "draws as a straight line" claim
    // here (unlike the Arrhenius chart's own inverse-mode wording): a k(T,P)
    // fit can be Chebyshev or PLOG, neither of which reduces to a single
    // Arrhenius term in general, so that claim would not be true of every
    // line this panel can draw.
    const axisDescription = xAxisMode === "temperature"
        ? "temperature in kelvin"
        : "1000 divided by temperature in inverse kelvin, high temperature at the left"
    const ariaLabel = `k(T,P) evaluated at ${scientificText(pressureBar)} bar for ${channelLabelsText}: `
        + `${modelKindsText}, ${arrheniusLog10AxisTitle(displayUnits ?? null)} versus ${axisDescription}`

    return (
        <div className="arrhenius-chart-panel-wrap" data-family-key={panel.key}>
            <div className="network-ktp-panel-header">
                <p className="network-ktp-panel-heading">{familyHeading}</p>
                {hasUnitChoice && (
                    <KtpFamilyUnitSelect panel={panel} selectedUnits={displayUnits} onSelectUnits={onSelectUnits} />
                )}
            </div>

            <ul className="arrhenius-chart-legend" aria-label="Channels plotted in this panel">
                {panel.groups.map((entry) => (
                    <li className="arrhenius-chart-legend-item" key={entry.channelKey} data-channel-key={entry.channelKey}>
                        <span
                            className="arrhenius-chart-swatch"
                            style={{ background: channelSeriesColor(colorIndexByChannelKey.get(entry.channelKey) ?? 0) }}
                            aria-hidden="true"
                        />
                        <code className="data">{`${entry.label} (${entry.channelKind})`}</code>
                    </li>
                ))}
            </ul>
            <ul className="arrhenius-chart-legend" aria-label="Model kind and validity-range encoding">
                {modelKindsPresent.map((kind) => (
                    <li className="arrhenius-chart-legend-item" key={kind}>
                        <span
                            className={`network-ktp-line-swatch ${modelKindStrokeWidth(kind) >= 2 ? "network-ktp-line-swatch--thick" : "network-ktp-line-swatch--thin"}`}
                            aria-hidden="true"
                        />
                        <span>{modelKindLabel(kind)}</span>
                    </li>
                ))}
                {anyOutOfRange && (
                    <li className="arrhenius-chart-legend-item">
                        <span className="network-ktp-line-swatch network-ktp-line-swatch--dashed" aria-hidden="true" />
                        <span>outside this fit's own stated validity range</span>
                    </li>
                )}
            </ul>
            {modelKindsPresent.length > 1 && (
                // Said once, under the legend, rather than repeated verbatim in
                // every model-kind row -- which is how it first shipped and
                // read as though each row carried different information.
                <p className="t-body network-ktp-encoding-note">
                    Each channel keeps one colour; within it the heavier line is Chebyshev and the lighter
                    tint is PLOG.
                </p>
            )}

            <div className="arrhenius-chart-panel">
                {/* Inside the grid, not before it -- see `ArrheniusChart.tsx`'s
                    own comment on this same structural requirement. */}
                <p className="arrhenius-chart-axis-title arrhenius-chart-axis-title--y">{arrheniusLog10AxisTitle(displayUnits ?? null)}</p>
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
                        {channelSeriesData.map(({ group, fits }) => (
                            fits.map(({ fit, points }) => {
                                const baseColor = channelSeriesColor(colorIndexByChannelKey.get(group.channelKey) ?? 0)
                                const stroke = modelKindStrokeColor(baseColor, fit.model_kind)
                                const strokeWidth = modelKindStrokeWidth(fit.model_kind)
                                return (
                                    <g
                                        key={fit.network_kinetics_ref}
                                        data-testid={`ktp-line-${fit.network_kinetics_ref}`}
                                        data-model-kind={fit.model_kind}
                                        data-channel-key={group.channelKey}
                                    >
                                        {ktpLineSegments(points).map((segment, segmentIndex) => (
                                            <polyline
                                                key={segmentIndex}
                                                points={segment.points.map((point) => `${xScale(arrheniusPointX(point, xAxisMode))},${yScale(point.log10k)}`).join(" ")}
                                                fill="none"
                                                stroke={stroke}
                                                strokeWidth={strokeWidth}
                                                strokeDasharray={segment.solid ? undefined : "4 3"}
                                                data-in-range={segment.solid}
                                            />
                                        ))}
                                    </g>
                                )
                            })
                        ))}
                    </svg>
                    <p className="arrhenius-chart-axis-title arrhenius-chart-axis-title--x" style={{ marginLeft: left, width: plotWidth }}>
                        {xAxisMode === "temperature" ? "Temperature (K)" : "1000 / T (K⁻¹)"}
                    </p>
                </div>
            </div>

            {channelSeriesData.map(({ group, fits }) => (
                <KtpChannelTable key={group.channelKey} group={group} pressureBar={pressureBar} seriesData={fits} axisUnits={displayUnits ?? null} />
            ))}
        </div>
    )
}

/**
 * The chart-wide X-axis (temperature vs 1000/T) control -- one instance,
 * rendered once in `NetworkKtpChartReady`'s own controls row alongside
 * Pressure, governing every family panel at once (invariant 7: this is a
 * reading-convention choice, not a per-panel fact, exactly the reasoning
 * `arrheniusChartLayout.ts`'s own `ArrheniusXAxisMode` doc comment gives).
 *
 * Deliberately NOT imported from `ArrheniusChart.tsx`: that component's own
 * `ArrheniusXAxisSelect` is module-private (this file must not reach into a
 * page-scoped component module for a bit of markup), so this is a small,
 * separate duplicate -- same sanctioned pattern `arrheniusChartLayout.ts`'s
 * own `A_UNIT_LABELS` comment documents ("otherwise duplicate one function
 * and note it"). The OPTION WORDING is kept byte-identical to that
 * component's own ("Temperature (K)" / "1000 / T (K⁻¹)") so the Arrhenius
 * and k(T,P) pages read as one consistent reading convention, not two.
 */
function KtpXAxisSelect({ id, xAxisMode, onSelectXAxisMode }: {
    id: string
    xAxisMode: ArrheniusXAxisMode
    onSelectXAxisMode: (mode: ArrheniusXAxisMode) => void
}) {
    return (
        <label className="arrhenius-chart-control" htmlFor={id}>
            <span className="arrhenius-chart-control-label">X-axis</span>
            <select
                id={id}
                className="arrhenius-chart-control-select"
                value={xAxisMode}
                onChange={(event) => onSelectXAxisMode(event.target.value as ArrheniusXAxisMode)}
            >
                <option value="temperature">Temperature (K)</option>
                <option value="inverse_temperature">1000 / T (K⁻¹)</option>
            </select>
        </label>
    )
}

/**
 * The per-panel unit control -- rendered ONLY when `panel.availableUnits`
 * has more than one member (`KtpFamilyPanelChart`'s own `hasUnitChoice`
 * gate above). Unlike `ArrheniusChart.tsx`'s own `ArrheniusYAxisSelect`,
 * a single-option panel here gets NO control at all rather than a
 * disabled one -- per this PR's own brief: `per_s` (order-1, isomerization
 * on this network) has exactly one member in its family
 * (`familyUnits(1) === ["per_s"]`, confirmed directly against
 * `arrheniusUnits.ts` rather than assumed), so there is nothing to offer a
 * reader a choice between, and a control with nothing behind it reads as
 * broken rather than simply absent.
 */
function KtpFamilyUnitSelect({ panel, selectedUnits, onSelectUnits }: {
    panel: KtpFamilyPanel
    selectedUnits: string | undefined
    onSelectUnits: (units: string) => void
}) {
    const selectId = useId()
    const displayUnits = selectedUnits ?? panel.defaultUnits
    return (
        <label className="arrhenius-chart-control" htmlFor={selectId}>
            <span className="arrhenius-chart-control-label">Y-axis</span>
            <select
                id={selectId}
                className="arrhenius-chart-control-select"
                aria-label={panel.orderFamily != null ? `Y-axis (${FAMILY_NAME[panel.orderFamily]})` : "Y-axis"}
                value={displayUnits ?? panel.availableUnits[0] ?? ""}
                onChange={(event) => onSelectUnits(event.target.value)}
            >
                {panel.availableUnits.map((units) => (
                    <option key={units} value={units}>{arrheniusUnitLabel(units)}</option>
                ))}
            </select>
        </label>
    )
}

// ---------------------------------------------------------------------------
// Table-behind-Disclosure -- the same selected-pressure, selected-unit data
// the panel plots (invariant 8: table and chart must never disagree).
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
