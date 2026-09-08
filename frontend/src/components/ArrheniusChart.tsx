import { useId, useState, type ReactNode } from "react"
import "../arrhenius-chart.css"
import type { ReactionKineticsRecord } from "../api/reactionEntryApi"
import {
    ARRHENIUS_CHART_HEIGHT,
    ARRHENIUS_CHART_MARGIN,
    ARRHENIUS_CHART_WIDTH,
    type ArrheniusPanel as ArrheniusPanelData,
    type ArrheniusXAxisMode,
    arrheniusPanelKey,
    arrheniusPointX,
    arrheniusUnitLabel,
    buildArrheniusChartData,
    convertArrheniusSeriesUnits,
    panelLog10KDomain,
    panelXDomain,
} from "../domain/arrheniusChartLayout"
import { arrheniusUnitConversionFactor, FAMILY_NAME } from "../domain/arrheniusUnits"
import { formatTicks, linearScale } from "../domain/chartScale"
import { computeKineticsTable, convertKineticsTableRows, log10Text, scientificText } from "../domain/kineticsTable"
import { niceTicks, seriesColor } from "../domain/thermoCpChartLayout"
import { Disclosure } from "./Disclosure"

// ---------------------------------------------------------------------------
// Hand-rolled SVG, no charting library -- same precedent `ThermoCpChart.tsx`
// follows (see that file's own header comment). One panel per ORDER FAMILY
// (unimolecular/`per_s`, bimolecular, termolecular): unlike Cp records,
// which all share one unit family, kinetics records on one reaction entry
// can legitimately report rate constants in physically different order
// families (a unimolecular record in s⁻¹ beside a bimolecular one in
// cm³ mol⁻¹ s⁻¹), and overlaying those on one y-axis would silently compare
// two different quantities on the same scale. Two records that merely
// differ in WHICH unit of the same family they were deposited in
// (`cm3_mol_s` vs `m3_mol_s`) now share ONE panel -- that's the whole point
// of the per-panel unit selector below, and grouping them apart (as this
// file used to, by the raw `A_units` token) made them uncomparable on a
// page whose job is comparing deposits.
//
// x = temperature (K) by default, or 1000/T (K⁻¹, high T on the left) when
// the top-level axis-mode control is switched -- one control governs every
// panel at once, since which axis a reader wants is a reading-convention
// choice, not a per-record fact. Either way the x-domain is the UNION of
// every plotted series' own fitted range for that panel; each curve is
// drawn only within ITS OWN `record_min_k..record_max_k`
// (`arrheniusPointX` only re-projects an already-sampled point, it never
// resamples). y = log10(k) -- the Arrhenius equation's own log-linear axis.
//
// Each convertible panel (order family known, more than one unit in it)
// carries its own unit `<select>`, defaulted to the unit most of that
// panel's OWN records were deposited in
// (`arrheniusChartLayout.ts`'s `modalDepositedUnit`). Selecting a different
// unit re-converts every series in the panel via `convertArrheniusSeriesUnits`
// (`domain/arrheniusUnits.ts`'s conversion factor -- NEVER offered across
// order families) and, per this PR's own "the k(T) table must follow the
// same selection" rule, the SAME selection also re-converts that panel's
// records' own k(T) tables (`convertKineticsTableRows`) -- one piece of
// state (`selectedUnitsByPanel` below) feeds both surfaces, so they cannot
// drift apart. A record's OWN deposited unit stays visible regardless of
// the current selection: the legend chip below states it explicitly
// (`series N — ref (deposited: …)`).
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
//
// CONTROLS (owner's report, round 2 -- pasted the actual rendered text:
// "Chart controls / X-axis / applies to every panel below / Y-axis / this
// panel only / A unimolecular rate coefficient is reported in s⁻¹ only —
// there is no other unit it could be converted to. / s⁻¹", and: "this
// looks so shit"). An earlier version of this section rendered a titled,
// bordered "Chart controls" box (X-axis) immediately followed by a SECOND
// bordered box per panel (Y-axis), each with its own full-sentence scope
// caption -- on 14 of this archive's 17 live reaction entries there is
// only ONE panel, so "applies to every panel below" and "this panel only"
// disambiguate nothing, and the no-alternative-unit reason printed as a
// standalone sentence in the layout on top of that.
//
// `onlyPanel` (below) is that common case: when there is EXACTLY one
// panel, the page-wide X-axis control and that one panel's own Y-axis
// control render together as ONE compact row (`ArrheniusControlsRow`, no
// heading, no scope caption -- nothing on the page to disambiguate FROM).
// Two or more panels keep the X-axis control in its own single,
// still-page-wide row (`ArrheniusChart -- the x-axis mode is a SINGLE
// control governing every panel at once` stays true regardless of this
// change: exactly one instance either way, never duplicated per panel),
// with each panel's own compact Y-axis row below it -- NOW naming its
// scope in two words ("all panels" / "this panel"), not a sentence, and
// only rendered at all because a second panel genuinely exists to
// disambiguate against.
//
// The no-alternative-unit reason (`yAxisUnitNote`) is never a sentence
// sitting in the page layout any more: the disabled `<select>` still
// shows the one unit (`ArrheniusYAxisSelect`'s own `hasUnitChoice` --
// "never silently omit the y control" stands unchanged, present but
// compact is the target), and a small, real `<button>` next to it carries
// the full reason via `title` (mouse hover) AND `aria-describedby`
// pointing at a visually-hidden paragraph (screen reader, and a REAL
// interactive element -- unlike the disabled select itself, which native
// HTML excludes from the tab order entirely -- so a sighted keyboard-only
// user can still reach it, not just a mouse or a screen reader).
// ---------------------------------------------------------------------------

export function ArrheniusChart({ kinetics }: { kinetics: ReactionKineticsRecord[] }) {
    const { panels, excluded } = buildArrheniusChartData(kinetics)

    // ONE control for the whole section (see header comment above) --
    // default unchanged from before this PR: temperature in K.
    const [xAxisMode, setXAxisMode] = useState<ArrheniusXAxisMode>("temperature")
    // Per-panel unit selection, keyed by `arrheniusPanelKey` -- lifted to
    // THIS component (not local to a panel's own sub-component) because the
    // k(T) table below, keyed by kinetics_ref rather than by panel, needs
    // to read the very same selection the chart is currently showing.
    const [selectedUnitsByPanel, setSelectedUnitsByPanel] = useState<Record<string, string>>({})

    // The unit each panel is CURRENTLY displaying -- the user's own choice
    // if they've made one for that panel, else the panel's own modal
    // deposited unit, else (unrecorded/unrecognised units) `undefined`.
    // Built once here and handed to both the chart panels and the table
    // below, so neither can read a stale or differently-derived value.
    const displayUnitsByPanelKey = new Map<string, string | undefined>()
    for (const panel of panels) {
        const key = arrheniusPanelKey(panel)
        displayUnitsByPanelKey.set(key, selectedUnitsByPanel[key] ?? panel.defaultUnits)
    }
    const displayUnitsByRef = new Map<string, string | undefined>()
    for (const panel of panels) {
        const displayUnits = displayUnitsByPanelKey.get(arrheniusPanelKey(panel))
        for (const series of panel.series) displayUnitsByRef.set(series.kinetics_ref, displayUnits)
    }

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

    // See the header comment's own "CONTROLS" section for the reasoning.
    // `onlyPanel` is non-null exactly when there is nothing on the page a
    // scope caption could disambiguate FROM.
    const onlyPanel = panels.length === 1 ? panels[0] : null
    const xAxisSelectId = useId()

    return (
        <div className="arrhenius-chart-section">
            {onlyPanel && (
                <ArrheniusControlsRow>
                    <ArrheniusXAxisSelect id={xAxisSelectId} xAxisMode={xAxisMode} onSelectXAxisMode={setXAxisMode} />
                    <ArrheniusYAxisSelect
                        panel={onlyPanel}
                        selectedUnits={displayUnitsByPanelKey.get(arrheniusPanelKey(onlyPanel))}
                        onSelectUnits={(units) => setSelectedUnitsByPanel((prev) => ({ ...prev, [arrheniusPanelKey(onlyPanel)]: units }))}
                    />
                </ArrheniusControlsRow>
            )}
            {panels.length > 1 && (
                <ArrheniusControlsRow scope="all panels">
                    <ArrheniusXAxisSelect id={xAxisSelectId} xAxisMode={xAxisMode} onSelectXAxisMode={setXAxisMode} />
                </ArrheniusControlsRow>
            )}

            {panels.length === 0 ? (
                <p className="empty-projection">
                    No rate coefficient among this reaction entry's kinetics records can be rendered as a single
                    k(T) curve.
                </p>
            ) : (
                panels.map((panel) => {
                    const key = arrheniusPanelKey(panel)
                    return (
                        <ArrheniusPanelChart
                            key={key}
                            panel={panel}
                            xAxisMode={xAxisMode}
                            selectedUnits={displayUnitsByPanelKey.get(key)}
                            onSelectUnits={(units) => setSelectedUnitsByPanel((prev) => ({ ...prev, [key]: units }))}
                            // The single panel's own Y-axis control already
                            // rendered above, combined with X-axis -- never
                            // render it a second time here.
                            ownControls={panel !== onlyPanel}
                        />
                    )
                })
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
                        const depositedUnits = record.parameters.A_units ?? null
                        // Falls back to the record's OWN deposited unit
                        // (never to some other unit) when it isn't part of
                        // any panel's selection map for some reason -- this
                        // table must never show a unit the chart never
                        // offered for this exact record.
                        const displayUnits = displayUnitsByRef.get(record.kinetics_ref) ?? depositedUnits
                        const factor = depositedUnits != null && displayUnits != null
                            ? arrheniusUnitConversionFactor(depositedUnits, displayUnits) ?? 1
                            : 1
                        const displayRows = convertKineticsTableRows(table, factor)
                        const unitLabel = displayUnits ? arrheniusUnitLabel(displayUnits) : ""
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
                                count={displayRows.length}
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
                                            {displayRows.map((row) => (
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

function ArrheniusLegend({ panel, displaySeriesDepositedUnits }: { panel: ArrheniusPanelData, displaySeriesDepositedUnits: (ref: string) => string | null }) {
    return (
        <ul className="arrhenius-chart-legend" aria-label="Kinetics record series">
            {panel.series.map((series, index) => (
                <li key={series.kinetics_ref} className="arrhenius-chart-legend-item" data-testid={`arrhenius-legend-${series.kinetics_ref}`}>
                    <span className="arrhenius-chart-swatch" style={{ background: seriesColor(index) }} aria-hidden="true" />
                    {/* The ref is folded into ONE text run with the record's
                        OWN deposited unit ("series N — ref (deposited: …)"),
                        never a bare `<code>{series.kinetics_ref}</code>` leaf
                        on its own -- see the excluded-notes comment above
                        for why a second isolated leaf node with the exact
                        ref text breaks `ReactionEntryPage.test.tsx`'s
                        `findByText(ref)` uniqueness assumption. Stating the
                        DEPOSITED unit here (not the panel's currently
                        SELECTED one) is this PR's own invariant: a record's
                        deposited units must remain visible somewhere on its
                        own surface regardless of what the panel is
                        currently displaying. */}
                    <code className="data">
                        {`series ${index + 1} — ${series.kinetics_ref} (deposited: ${arrheniusUnitLabel(displaySeriesDepositedUnits(series.kinetics_ref))})`}
                    </code>
                </li>
            ))}
        </ul>
    )
}

/**
 * Why this panel's y-axis (unit) control offers no alternative to switch
 * to -- shown NEXT TO a control that is always rendered, never in place of
 * one (owner's report: 14 of 17 live reaction entries are `per_s`, and the
 * old "fewer than two options -> render nothing" rule left the majority of
 * readers looking at an x-axis control and nothing for y, with no
 * explanation). `null` whenever the panel genuinely offers a choice
 * (`availableUnits.length > 1`) -- the control is enabled and no note is
 * shown. The two `null`-`availableUnits`-but-still-rendered cases below are
 * physically different facts and get different, chemist-legible wording:
 * order family 1 (`per_s`) has exactly one unit BY DEFINITION (a
 * unimolecular rate coefficient is s⁻¹, full stop -- there is nothing to
 * convert to, not an oversight), whereas `orderFamily === null` means this
 * file could not place the record's units in any family at all (unrecorded,
 * or a token `arrheniusUnits.ts` doesn't recognise) -- a DIFFERENT kind of
 * "nothing to offer", and conflating the two wordings would overstate one
 * of them as a settled physical fact when it might just be a missing value.
 */
function yAxisUnitNote(panel: ArrheniusPanelData): string | null {
    if (panel.availableUnits.length > 1) return null
    if (panel.orderFamily === 1) {
        return "A unimolecular rate coefficient is reported in s⁻¹ only — there is no other unit it could be converted to."
    }
    return panel.defaultUnits != null
        ? "This record's units aren't recognised, so no alternative unit can be offered."
        : "This record's units weren't recorded, so no alternative unit can be offered."
}

/** The compact controls row -- X-axis alone (2+ panels) or X+Y together
 * (exactly one panel, the common case). No heading, no bordered "section"
 * feel: `.arrhenius-chart-controls` is one thin inline-flex strip. `scope`,
 * when given, is the two/three-word disambiguator a row genuinely needs
 * because something ELSE on the page could be confused with it -- omitted
 * entirely for the single-panel combined row, which has nothing else to
 * be confused with (owner's report: "applies to every panel below" /
 * "this panel only" "distinguish nothing" when there is only one panel). */
function ArrheniusControlsRow({ children, scope }: { children: ReactNode; scope?: string }) {
    return (
        <div className="arrhenius-chart-controls">
            {children}
            {scope && <span className="arrhenius-chart-controls-scope">{scope}</span>}
        </div>
    )
}

function ArrheniusXAxisSelect({ id, xAxisMode, onSelectXAxisMode }: {
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
 * The Y-axis (unit) control, always rendered -- "never silently omit the
 * y control" stands from the previous round of this fix; present-but-
 * compact is the target, not hidden. A panel with a single option
 * (`per_s`'s own family, or an unrecorded/unrecognised panel's empty
 * `availableUnits`) shows the control DISABLED with the one unit it's
 * stuck at, and -- unlike the previous round, which printed
 * `yAxisUnitNote`'s full sentence directly in the layout -- the reason is
 * now on demand: a small, real `<button>` (not just a `title` on the
 * disabled select, which native HTML excludes from the tab order and so
 * a sighted keyboard-only user could never reach) carries the reason via
 * BOTH `title` (mouse hover) and `aria-describedby` pointing at a
 * visually-hidden paragraph (screen reader, and Chrome/Firefox also show
 * a focused element's own `title` as a tooltip, so keyboard focus reveals
 * it too). */
function ArrheniusYAxisSelect({ panel, selectedUnits, onSelectUnits }: {
    panel: ArrheniusPanelData
    selectedUnits: string | undefined
    onSelectUnits: (units: string) => void
}) {
    const selectId = useId()
    const noteId = `${selectId}-note`
    const displayUnits = selectedUnits ?? panel.defaultUnits
    const unitLabel = arrheniusUnitLabel(displayUnits ?? null)
    const hasUnitChoice = panel.availableUnits.length > 1
    const unitNote = yAxisUnitNote(panel)

    return (
        <span className="arrhenius-chart-y-control">
            <label className="arrhenius-chart-control arrhenius-chart-panel-unit-control" htmlFor={selectId}>
                <span className="arrhenius-chart-control-label">Y-axis</span>
                <select
                    id={selectId}
                    className="arrhenius-chart-control-select"
                    aria-label={panel.orderFamily != null ? `Y-axis (${FAMILY_NAME[panel.orderFamily]})` : "Y-axis"}
                    aria-describedby={unitNote ? noteId : undefined}
                    disabled={!hasUnitChoice}
                    value={displayUnits ?? panel.availableUnits[0] ?? ""}
                    onChange={(event) => onSelectUnits(event.target.value)}
                >
                    {hasUnitChoice
                        ? panel.availableUnits.map((units) => (
                            <option key={units} value={units}>{arrheniusUnitLabel(units)}</option>
                        ))
                        : <option value={displayUnits ?? ""}>{unitLabel}</option>}
                </select>
            </label>
            {unitNote && (
                <button type="button" className="arrhenius-chart-unit-reason" title={unitNote} aria-describedby={noteId}>
                    <span aria-hidden="true">?</span>
                    <span className="arrhenius-chart-visually-hidden">Why this unit can't be changed</span>
                </button>
            )}
            {unitNote && <span id={noteId} className="arrhenius-chart-visually-hidden">{unitNote}</span>}
        </span>
    )
}

function ArrheniusPanelChart({ panel, xAxisMode, selectedUnits, onSelectUnits, ownControls }: {
    panel: ArrheniusPanelData
    xAxisMode: ArrheniusXAxisMode
    selectedUnits: string | undefined
    onSelectUnits: (units: string) => void
    /** `false` for the one panel already given its own Y-axis control in
     * the combined single-panel row above (`ArrheniusChart`'s `onlyPanel`)
     * -- rendering it again here would duplicate the control. */
    ownControls: boolean
}) {
    const displayUnits = selectedUnits ?? panel.defaultUnits
    // `panel.series` itself, UNCONVERTED, in every place only the
    // temperature range matters (unit conversion never touches
    // `minK`/`maxK`) -- conversion is only applied where log10(k) is
    // actually read (`displaySeries` below).
    const displaySeries = displayUnits != null
        ? panel.series.map((series) => convertArrheniusSeriesUnits(series, displayUnits))
        : panel.series
    const unitLabel = arrheniusUnitLabel(displayUnits ?? null)

    const xDomain = panelXDomain(panel.series, xAxisMode)
    const kDomain = panelLog10KDomain(displaySeries)
    const { top, right, bottom, left } = ARRHENIUS_CHART_MARGIN
    const plotWidth = ARRHENIUS_CHART_WIDTH - left - right
    const plotHeight = ARRHENIUS_CHART_HEIGHT - top - bottom
    const xScale = linearScale(xDomain, [left, left + plotWidth])
    const yScale = linearScale(kDomain, [top + plotHeight, top])
    const xTicks = niceTicks(xDomain, 5)
    const yTicks = niceTicks(kDomain, 5)
    const xTickLabels = formatTicks(xTicks)
    const yTickLabels = formatTicks(yTicks)

    const refList = panel.series.map((series) => series.kinetics_ref).join(", ")
    // The axis clause names what x actually is, and -- in the inverse mode
    // -- states BOTH the reading convention (high T at the left) and the
    // straight-line fact this PR's brief calls out explicitly, since the
    // `aria-label` is this panel's own accessible substitute for seeing
    // the axis drawn.
    const axisDescription = xAxisMode === "temperature"
        ? "temperature in kelvin"
        : "1000 divided by temperature in inverse kelvin, high temperature at the left; a simple Arrhenius record draws as a straight line on this axis"
    const ariaLabel = `Arrhenius plot, log10 k versus ${axisDescription}, in ${unitLabel}: `
        + `${panel.series.length} record${panel.series.length === 1 ? "" : "s"} (${refList})`

    return (
        <div className="arrhenius-chart-panel-wrap">
            {/* Rendered only when this panel does NOT already have its own
                Y-axis control from the combined single-panel row above
                (`ArrheniusChart`'s `onlyPanel`). "this panel" only means
                something once a second panel exists to tell it apart
                from -- which is exactly the case `ownControls` is true
                for. */}
            {ownControls && (
                <ArrheniusControlsRow scope="this panel">
                    <ArrheniusYAxisSelect panel={panel} selectedUnits={selectedUnits} onSelectUnits={onSelectUnits} />
                </ArrheniusControlsRow>
            )}
            <p className="arrhenius-chart-panel-heading">{unitLabel}</p>
            <ArrheniusLegend
                panel={panel}
                displaySeriesDepositedUnits={(ref) => panel.series.find((series) => series.kinetics_ref === ref)?.depositedUnits ?? null}
            />
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
                        {displaySeries.map((series, index) => (
                            <polyline
                                key={series.kinetics_ref}
                                data-testid={`arrhenius-line-${series.kinetics_ref}`}
                                points={series.points.map((point) => `${xScale(arrheniusPointX(point, xAxisMode))},${yScale(point.log10k)}`).join(" ")}
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
                        {xAxisMode === "temperature" ? "Temperature (K)" : "1000 / T (K⁻¹)"}
                    </p>
                </div>
            </div>
        </div>
    )
}
