import "../arrhenius-chart.css"
import type { ReactionKineticsRecord } from "../api/reactionEntryApi"
import {
    ARRHENIUS_CHART_HEIGHT,
    ARRHENIUS_CHART_MARGIN,
    ARRHENIUS_CHART_WIDTH,
    type ArrheniusPanel as ArrheniusPanelData,
    arrheniusUnitLabel,
    buildArrheniusChartData,
    panelLog10KDomain,
    panelTemperatureDomain,
} from "../domain/arrheniusChartLayout"
import { formatTicks, linearScale } from "../domain/chartScale"
import { computeKineticsTable, log10Text, scientificText } from "../domain/kineticsTable"
import { niceTicks, seriesColor } from "../domain/thermoCpChartLayout"
import { Disclosure } from "./Disclosure"

// ---------------------------------------------------------------------------
// Hand-rolled SVG, no charting library -- same precedent `ThermoCpChart.tsx`
// follows (see that file's own header comment). One panel per DISTINCT
// `A_units` (plan §4: "a page mixing per_s and cm3_mol_s gets two panels"):
// unlike Cp records, which all share one unit family, kinetics records on
// one reaction entry can legitimately report rate constants in physically
// different units (a unimolecular record in s⁻¹ beside a bimolecular one in
// cm³ mol⁻¹ s⁻¹), and overlaying those on one y-axis would silently compare
// two different quantities on the same scale.
//
// x = temperature (K), linear, over the UNION of every plotted series' own
// fitted range for that panel; each curve is drawn only within ITS OWN
// `record_min_k..record_max_k`, so a narrower-range record's line stops
// short of a wider panel's own axis rather than being extrapolated past its
// stated validity. y = log10(k) -- the Arrhenius equation's own log-linear
// axis, over several orders of magnitude for most real rate constants.
//
// PLOG/Chebyshev/falloff-fitted records have no single k(T) curve without a
// stated pressure, and a third-body record's rate depends on a bath-gas
// concentration this page does not plot against -- all four are EXCLUDED
// from every panel and listed by ref and reason instead of silently
// dropped or plotted against an unstated variable (`domain/arrheniusChartLayout.ts`'s
// `buildArrheniusChartData`).
//
// The k(T) TABLE is the chart's own accessible/text equivalent (each panel's
// `role="img"` `aria-label` is a summary sentence, not a substitute for the
// underlying numbers) -- built with the exact same `computeKineticsTable`/
// `arrheniusTermK` maths PR 2 pinned, reused rather than forked, and shown
// directly beneath the panels in a `Disclosure` per record.
// ---------------------------------------------------------------------------

export function ArrheniusChart({ kinetics }: { kinetics: ReactionKineticsRecord[] }) {
    const { panels, excluded } = buildArrheniusChartData(kinetics)

    // Every record whose OWN k(T) table is computable -- the chart's text
    // equivalent set. `computeKineticsTable` (`domain/kineticsTable.ts`)
    // gates on the SAME four conditions `exclusionReasons` below does --
    // plog/chebyshev/falloff/`is_third_body` -- so a record excluded from
    // the CHART for one of those reasons is refused a table too, rather
    // than handing out the same T-only numbers one surface lower on the
    // page (PR 3 review finding: a third-body record used to get exactly
    // that -- excluded from the plot, then tabulated anyway).
    const tableRecords = kinetics
        .map((record) => ({ record, table: computeKineticsTable(record) }))
        .filter((entry): entry is { record: ReactionKineticsRecord; table: NonNullable<ReturnType<typeof computeKineticsTable>> } => entry.table !== null)

    return (
        <div className="arrhenius-chart-section">
            {panels.length === 0 ? (
                <p className="empty-projection">
                    No rate coefficient among this reaction entry's kinetics records can be rendered as a single
                    k(T) curve.
                </p>
            ) : (
                panels.map((panel) => (
                    <ArrheniusPanelChart key={panel.aUnits ?? "\0unrecorded"} panel={panel} />
                ))
            )}

            {excluded.length > 0 && (
                // The `kin_` ref is folded into ONE text run here (a single
                // template string, not a separate leaf element wrapping
                // just the ref) -- `ReactionEntryPage.test.tsx`'s own "every
                // kin_/spe_/net_ ref appears exactly once outside
                // References" convention keys `screen.findByText(ref)` on
                // there being exactly one DOM node whose OWN text is the
                // bare ref; splitting it into its own `<code>` node here
                // would silently create a second such node and break every
                // test in that file using `findByText("kin_test1")` as its
                // "wait for the page" signal (see this PR's own mutation
                // notes -- MEASURED: 19 tests in that file went from green
                // to a 5s timeout before this was found and fixed).
                <ul className="arrhenius-chart-excluded-notes">
                    {excluded.map((item) => (
                        <li key={item.kinetics_ref} className="note">
                            {`${item.kinetics_ref} is not plotted: ${item.reasons.join("; ")}.`}
                        </li>
                    ))}
                </ul>
            )}

            {tableRecords.length > 0 && (
                <div className="arrhenius-chart-tables">
                    {tableRecords.map(({ record, table }) => {
                        const unitLabel = record.parameters.A_units ? arrheniusUnitLabel(record.parameters.A_units) : ""
                        return (
                            <Disclosure
                                key={record.kinetics_ref}
                                // Same one-text-run rule as the excluded-note
                                // above: a bare `<code>{record.kinetics_ref}</code>`
                                // here would be a SECOND leaf node with the
                                // exact text "kin_test1" (the first is the
                                // record card's own "Kinetics ref" fact),
                                // breaking `findByText`'s exact-match
                                // uniqueness assumption used throughout
                                // `ReactionEntryPage.test.tsx`.
                                summary={<code className="data">{`k(T) table — ${record.kinetics_ref}`}</code>}
                                count={table.length}
                            >
                                <div className="table-scroll">
                                    <table className="data-table kinetics-k-table" aria-label={`k(T) for ${record.kinetics_ref}`}>
                                        <caption>k(T) = A·T^n·exp(−Ea/(R·T)){unitLabel ? `, in ${unitLabel}` : ""}</caption>
                                        <thead>
                                            <tr>
                                                <th scope="col">T (K)</th>
                                                <th scope="col">{unitLabel ? `k (${unitLabel})` : "k"}</th>
                                                <th scope="col">log₁₀ k</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {table.map((row) => (
                                                <tr key={row.temperatureK}>
                                                    <td className="num" data-label="T (K)">{row.temperatureK.toFixed(2)}</td>
                                                    <td className="num" data-label="k">{scientificText(row.k)}</td>
                                                    <td className="num" data-label="log10 k">{log10Text(row.k)}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </Disclosure>
                        )
                    })}
                </div>
            )}
        </div>
    )
}

function ArrheniusLegend({ panel }: { panel: ArrheniusPanelData }) {
    return (
        <ul className="arrhenius-chart-legend" aria-label="Kinetics record series">
            {panel.series.map((series, index) => (
                <li key={series.kinetics_ref} className="arrhenius-chart-legend-item" data-testid={`arrhenius-legend-${series.kinetics_ref}`}>
                    <span className="arrhenius-chart-swatch" style={{ background: seriesColor(index) }} aria-hidden="true" />
                    {/* The ref is folded into ONE text run ("series N — ref"),
                        never a bare `<code>{series.kinetics_ref}</code>` leaf
                        on its own -- see the excluded-notes comment above
                        for why a second isolated leaf node with the exact
                        ref text breaks `ReactionEntryPage.test.tsx`'s
                        `findByText(ref)` uniqueness assumption. */}
                    <code className="data">{`series ${index + 1} — ${series.kinetics_ref}`}</code>
                </li>
            ))}
        </ul>
    )
}

function ArrheniusPanelChart({ panel }: { panel: ArrheniusPanelData }) {
    const temperatureDomain = panelTemperatureDomain(panel)
    const kDomain = panelLog10KDomain(panel)
    const { top, right, bottom, left } = ARRHENIUS_CHART_MARGIN
    const plotWidth = ARRHENIUS_CHART_WIDTH - left - right
    const plotHeight = ARRHENIUS_CHART_HEIGHT - top - bottom
    const xScale = linearScale(temperatureDomain, [left, left + plotWidth])
    const yScale = linearScale(kDomain, [top + plotHeight, top])
    const xTicks = niceTicks(temperatureDomain, 5)
    const yTicks = niceTicks(kDomain, 5)
    const xTickLabels = formatTicks(xTicks)
    const yTickLabels = formatTicks(yTicks)

    const refList = panel.series.map((series) => series.kinetics_ref).join(", ")
    const ariaLabel = `Arrhenius plot, log10 k versus temperature, in ${panel.unitLabel}: `
        + `${panel.series.length} record${panel.series.length === 1 ? "" : "s"} (${refList})`

    return (
        <div className="arrhenius-chart-panel-wrap">
            <p className="arrhenius-chart-panel-heading">{panel.unitLabel}</p>
            <ArrheniusLegend panel={panel} />
            <div className="arrhenius-chart-panel">
                <p className="arrhenius-chart-axis-title arrhenius-chart-axis-title--y">log₁₀ k</p>
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
                        {panel.series.map((series, index) => (
                            <polyline
                                key={series.kinetics_ref}
                                data-testid={`arrhenius-line-${series.kinetics_ref}`}
                                points={series.points.map((point) => `${xScale(point.temperatureK)},${yScale(point.log10k)}`).join(" ")}
                                fill="none"
                                stroke={seriesColor(index)}
                                strokeWidth={1.75}
                            />
                        ))}
                    </svg>
                    {/* Centred on the PLOT box (left..left+plotWidth), not
                        the full SVG box -- the SVG's own margins are NOT
                        symmetric (left=58 for the y-axis tick labels,
                        right=20), so centring on the full 720px width sits
                        the title ~19px off from the plotted curve's own
                        centre (a review finding). `marginLeft: left` +
                        `width: plotWidth` gives this paragraph the SAME
                        horizontal box the ticks/curve are drawn in, still
                        inside `.arrhenius-chart-scroll` so it keeps
                        scrolling in lockstep with the SVG. */}
                    <p
                        className="arrhenius-chart-axis-title arrhenius-chart-axis-title--x"
                        style={{ marginLeft: left, width: plotWidth }}
                    >
                        Temperature (K)
                    </p>
                </div>
            </div>
        </div>
    )
}
