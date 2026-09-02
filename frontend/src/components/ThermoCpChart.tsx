import { useMemo, useState } from "react"
import "../thermo-cp-chart.css"
import type { ConformerProjection } from "../api/speciesEntryApi"
import type { ThermoRecord } from "../api/thermoApi"
import { evenTicks, formatTick, linearScale } from "../domain/chartScale"
import {
    CHART_MARGIN,
    CP_CHART_HEIGHT,
    CP_CHART_WIDTH,
    type CpChartMode,
    computeCpValueDomain,
    computeTemperatureDomain,
    seriesAbsenceNote,
    seriesColor,
} from "../domain/thermoCpChartLayout"
import { buildCpChartSeries, type CpChartSeries } from "../domain/thermoCpSeries"
import { JOULES_PER_CALORIE, cpUnitLabel, type CpUnit } from "../domain/thermoNasa"
import { SectionHeading } from "./PageSections"

// ---------------------------------------------------------------------------
// Hand-rolled SVG, no charting library: this project has no plotting
// dependency in `package.json` today and has precedent for hand-rolled SVG
// elsewhere (`GeometryViewer.tsx`), so this follows that precedent rather
// than adding a new npm dependency for one chart.
//
// Cp vs. temperature, per DEPOSITED thermo record, overlaid — never
// combined and never Boltzmann-averaged across conformers. A species entry
// can carry several thermo records (one per conformer observation); the
// owner explicitly wants to see whether those conformers AGREE, which a
// single averaged line would hide, and averaging would require relative
// conformer energies this archive largely does not have — this component
// would be computing science, not archiving it. Each record gets its own
// series (`buildCpChartSeries`, `domain/thermoCpSeries.ts`); the series
// tracing to the entry page's currently-selected conformer is highlighted,
// never filtered to.
//
// Colour carries series (conformer) identity; measured-vs-fitted is a
// SHAPE distinction (discrete circle markers vs. a continuous line), never
// a colour distinction — the two axes of "which record" and "measured or
// fitted" stay independently readable rather than needing 2x the series
// colours.
//
// The stored Cp value is always J/mol/K (`cp_j_mol_k` on the wire); the
// cal/mol/K toggle below is a DISPLAY conversion only (`convertCpForDisplay`,
// `domain/thermoNasa.ts`) and never feeds back into anything computed from
// the archive's own numbers — same rule `GeometryDetailPage.tsx`'s Å/bohr
// toggle follows for coordinates.
//
// There used to be a second panel here plotting residuals (measured minus
// NASA-7 fit, as a percentage of the fit) on its own scale, underneath this
// one. It was removed: checked against a real record
// (`thm_5dzg66kvcuslgw6swkuc7gduiu`), the archive's stored "measured"
// points reproduce the NASA-7 fit to four decimal places at every checked
// temperature (300 K: 122.212 vs 122.212, residual 0.0001; 1000 K: 306.454
// vs 306.453, residual 0.0003; 2400 K: 381.075 vs 381.074, residual
// 0.0004) — 0.000% of Cp across the range, on all 65 thermo records this
// archive holds. The stored "raw" points were evidently evaluated FROM the
// polynomial in the first place, so a residual plot compared a curve with
// itself and was flat by construction, not because the fit was good. A
// residual chart needs measured points genuinely INDEPENDENT of the fit --
// no record in this archive has that today. Re-add the panel only once one
// does; see `domain/thermoNasa.ts`'s own module comment for where the
// removed computation lived.
// ---------------------------------------------------------------------------

export function ThermoCpChart({ records, conformers, selectedConformerGroupRef }: {
    records: ThermoRecord[]
    conformers: ConformerProjection[]
    selectedConformerGroupRef: string | null
}) {
    const [unit, setUnit] = useState<CpUnit>("j_mol_k")
    const [mode, setMode] = useState<CpChartMode>("both")

    const series = useMemo(
        () => buildCpChartSeries(records, conformers, selectedConformerGroupRef, unit),
        [records, conformers, selectedConformerGroupRef, unit],
    )

    const plottable = series.filter((item) => item.hasUsableFit || item.hasMeasuredPoints)
    const anySelected = series.some((item) => item.isSelected)

    return (
        <section className="ledger-section cp-chart-section" aria-labelledby="cp-chart-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Derived view</p>
                <SectionHeading id="cp-chart-heading">Heat capacity vs. temperature</SectionHeading>
                <p>
                    Every deposited thermo record's own measured points and NASA-7 fit, overlaid — never combined
                    or averaged across conformers. Comparing the lines is how far the conformers actually agree.
                </p>
            </div>

            {plottable.length === 0 ? (
                <p className="empty-projection">
                    No NASA-7 fit or measured points are available to plot for any thermo record deposited on
                    this entry.
                </p>
            ) : (
                <>
                    <ChartControls mode={mode} unit={unit} onModeChange={setMode} onUnitChange={setUnit} />
                    <ChartLegend series={series} />
                    <CpPanel series={series} mode={mode} unit={unit} anySelected={anySelected} />
                    <AbsenceNotes series={series} />
                </>
            )}
        </section>
    )
}

function ChartControls({ mode, unit, onModeChange, onUnitChange }: {
    mode: CpChartMode
    unit: CpUnit
    onModeChange: (mode: CpChartMode) => void
    onUnitChange: (unit: CpUnit) => void
}) {
    return (
        <div className="cp-chart-controls">
            <fieldset className="cp-chart-toggle">
                <legend>Show</legend>
                <button type="button" aria-pressed={mode === "raw"} onClick={() => onModeChange("raw")}>Measured</button>
                <button type="button" aria-pressed={mode === "fit"} onClick={() => onModeChange("fit")}>Fit</button>
                <button type="button" aria-pressed={mode === "both"} onClick={() => onModeChange("both")}>Both</button>
            </fieldset>
            <fieldset className="cp-chart-toggle">
                <legend>Units</legend>
                <button type="button" aria-pressed={unit === "j_mol_k"} onClick={() => onUnitChange("j_mol_k")}>J/mol·K</button>
                <button type="button" aria-pressed={unit === "cal_mol_k"} onClick={() => onUnitChange("cal_mol_k")}>cal/mol·K</button>
            </fieldset>
            {/* Same rule the Å/bohr note follows in geometry-detail.css: names
                the stored unit plainly rather than let the converted column
                imply that's how the archive holds it. */}
            <p className="cp-chart-unit-note">
                Always stored in J/mol·K (<code>cp_j_mol_k</code> on the wire); cal/mol·K here is a display
                conversion only, at 1 cal = {JOULES_PER_CALORIE} J exactly.
            </p>
        </div>
    )
}

function ChartLegend({ series }: { series: CpChartSeries[] }) {
    return (
        <ul className="cp-chart-legend" aria-label="Thermo record series">
            {series.map((item, index) => (
                <li
                    key={item.thermoRef}
                    className={item.isSelected ? "cp-chart-legend-item cp-chart-legend-item--selected" : "cp-chart-legend-item"}
                    data-testid={`legend-${item.thermoRef}`}
                >
                    <span className="cp-chart-swatch" style={{ background: seriesColor(index) }} aria-hidden="true" />
                    <span className="cp-chart-legend-label">{item.label}</span>
                    {item.isSelected && <span className="cp-chart-legend-flag">selected</span>}
                </li>
            ))}
        </ul>
    )
}

function AbsenceNotes({ series }: { series: CpChartSeries[] }) {
    const notes = series.map(seriesAbsenceNote).filter((note): note is string => !!note)
    if (notes.length === 0) return null
    return (
        <ul className="cp-chart-absence-notes">
            {notes.map((note) => <li key={note} className="section-note">{note}</li>)}
        </ul>
    )
}

function ChartAxes({ xTicks, yTicks, xScale, yScale, plotHeight, plotWidth, yTickSuffix = "" }: {
    xTicks: number[]
    yTicks: number[]
    xScale: (value: number) => number
    yScale: (value: number) => number
    plotHeight: number
    plotWidth: number
    yTickSuffix?: string
}) {
    const { top, left } = CHART_MARGIN
    return (
        <>
            {yTicks.map((tick) => (
                <g key={`y-${tick}`}>
                    <line x1={left} x2={left + plotWidth} y1={yScale(tick)} y2={yScale(tick)} className="cp-chart-gridline" />
                    <text x={left - 8} y={yScale(tick)} className="cp-chart-tick-label cp-chart-tick-label--y">
                        {formatTick(tick)}{yTickSuffix}
                    </text>
                </g>
            ))}
            {xTicks.map((tick) => (
                <g key={`x-${tick}`}>
                    <line x1={xScale(tick)} x2={xScale(tick)} y1={top} y2={top + plotHeight} className="cp-chart-gridline" />
                    <text x={xScale(tick)} y={top + plotHeight + 16} className="cp-chart-tick-label cp-chart-tick-label--x">
                        {formatTick(tick)}
                    </text>
                </g>
            ))}
            <line x1={left} x2={left + plotWidth} y1={top + plotHeight} y2={top + plotHeight} className="cp-chart-axis-line" />
            <line x1={left} x2={left} y1={top} y2={top + plotHeight} className="cp-chart-axis-line" />
        </>
    )
}

function CpPanel({ series, mode, unit, anySelected }: {
    series: CpChartSeries[]
    mode: CpChartMode
    unit: CpUnit
    anySelected: boolean
}) {
    const temperatureDomain = computeTemperatureDomain(series)
    const cpDomain = computeCpValueDomain(series)
    const { top, right, bottom, left } = CHART_MARGIN
    const plotWidth = CP_CHART_WIDTH - left - right
    const plotHeight = CP_CHART_HEIGHT - top - bottom
    const xScale = linearScale(temperatureDomain, [left, left + plotWidth])
    const yScale = linearScale(cpDomain, [top + plotHeight, top])

    return (
        <div className="cp-chart-panel">
            <svg
                viewBox={`0 0 ${CP_CHART_WIDTH} ${CP_CHART_HEIGHT}`}
                role="img"
                aria-label={`Heat capacity versus temperature, in ${cpUnitLabel(unit)}`}
                className="cp-chart-svg"
            >
                <ChartAxes
                    xTicks={evenTicks(temperatureDomain, 5)}
                    yTicks={evenTicks(cpDomain, 5)}
                    xScale={xScale}
                    yScale={yScale}
                    plotHeight={plotHeight}
                    plotWidth={plotWidth}
                />
                {series.map((item, index) => {
                    const color = seriesColor(index)
                    const dimmed = anySelected && !item.isSelected
                    const groupClassName = dimmed ? "cp-chart-series cp-chart-series--dimmed" : "cp-chart-series"
                    const strokeWidth = item.isSelected ? 2.6 : 1.5
                    return (
                        <g
                            key={item.thermoRef}
                            data-testid={`series-${item.thermoRef}`}
                            data-selected={item.isSelected}
                            className={groupClassName}
                        >
                            {mode !== "raw" && item.fitted && (
                                <>
                                    <polyline
                                        data-testid={`fit-low-${item.thermoRef}`}
                                        points={item.fitted.low.map((point) => `${xScale(point.temperatureK)},${yScale(point.cpDisplay)}`).join(" ")}
                                        fill="none"
                                        stroke={color}
                                        strokeWidth={strokeWidth}
                                    />
                                    <polyline
                                        data-testid={`fit-high-${item.thermoRef}`}
                                        points={item.fitted.high.map((point) => `${xScale(point.temperatureK)},${yScale(point.cpDisplay)}`).join(" ")}
                                        fill="none"
                                        stroke={color}
                                        strokeWidth={strokeWidth}
                                    />
                                </>
                            )}
                            {mode !== "fit" && item.measured.map((point, pointIndex) => (
                                <circle
                                    key={`m-${pointIndex}`}
                                    data-testid={`measured-point-${item.thermoRef}-${pointIndex}`}
                                    cx={xScale(point.temperatureK)}
                                    cy={yScale(point.cpDisplay)}
                                    r={item.isSelected ? 4.5 : 3.5}
                                    fill={color}
                                    stroke="var(--surface)"
                                    strokeWidth={1}
                                />
                            ))}
                        </g>
                    )
                })}
            </svg>
            <p className="cp-chart-axis-title cp-chart-axis-title--y">{`Cp (${cpUnitLabel(unit)})`}</p>
            <p className="cp-chart-axis-title cp-chart-axis-title--x">Temperature (K)</p>
        </div>
    )
}

