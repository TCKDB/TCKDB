import { domainWithPadding } from "./chartScale"
import type { CpChartSeries } from "./thermoCpSeries"

// Layout constants and domain-computation functions for `ThermoCpChart.tsx`,
// pulled into their own (non-component) module for two reasons: partly so
// `eslint-plugin-react-refresh`'s `only-export-components` rule does not
// fire on a `.tsx` file exporting non-component values (see the same note
// on `domain/geometryXyz.ts`), and partly because the residual panel's
// "own scale" claim is exactly the kind of thing that needs an
// independently-callable function to actually verify against rendered
// output, rather than eyeballing the SVG — see `ThermoCpChart.test.tsx`.

export type CpChartMode = "raw" | "fit" | "both"

export const CP_CHART_WIDTH = 720
export const CP_CHART_HEIGHT = 340
export const RESIDUAL_CHART_HEIGHT = 220
export const CHART_MARGIN = { top: 16, right: 20, bottom: 40, left: 58 } as const

/** One of eight hand-picked categorical hues (`theme.css`), cycling if
 * there are ever more than eight deposited records for one entry. */
export function seriesColor(index: number): string {
    return `var(--chart-series-${(index % 8) + 1})`
}

/** Every temperature any series actually plots (measured or fitted) — the
 * SHARED x-domain for both panels, computed once regardless of the
 * RAW/FIT/BOTH toggle so switching that toggle never rescales the axes. */
export function computeTemperatureDomain(series: readonly CpChartSeries[]): [number, number] {
    const temperatures: number[] = []
    for (const item of series) {
        for (const point of item.measured) temperatures.push(point.temperatureK)
        if (item.fitted) {
            for (const point of item.fitted.low) temperatures.push(point.temperatureK)
            for (const point of item.fitted.high) temperatures.push(point.temperatureK)
        }
    }
    return domainWithPadding(temperatures, 0.04)
}

/** Every Cp value (display units) any series actually plots. */
export function computeCpValueDomain(series: readonly CpChartSeries[]): [number, number] {
    const values: number[] = []
    for (const item of series) {
        for (const point of item.measured) values.push(point.cpDisplay)
        if (item.fitted) {
            for (const point of item.fitted.low) values.push(point.cpDisplay)
            for (const point of item.fitted.high) values.push(point.cpDisplay)
        }
    }
    return domainWithPadding(values, 0.12)
}

/**
 * A DIFFERENT domain-construction function from `computeCpValueDomain`
 * above, on purpose — this is what keeps the residual panel honestly on
 * its own scale rather than sharing the Cp axis by accident. Always
 * symmetric about zero (a residual panel with an asymmetric axis makes
 * "is this fit biased high or low" harder to read at a glance), and never
 * narrower than +/-1% even when every residual is exactly zero, so a
 * perfect fit still renders a real, non-degenerate axis.
 */
export function computeResidualPercentDomain(series: readonly CpChartSeries[]): [number, number] {
    const values: number[] = []
    for (const item of series) for (const residual of item.residuals) values.push(residual.residualPercent)
    const maxAbs = Math.max(1, ...values.map((value) => Math.abs(value)))
    const padded = maxAbs * 1.15
    return [-padded, padded]
}

export function seriesAbsenceNote(series: CpChartSeries): string | null {
    if (series.hasUsableFit && !series.hasMeasuredPoints) {
        return `${series.label}: no measured points on file for this record — the fitted curve is drawn without markers.`
    }
    if (!series.hasUsableFit && series.hasMeasuredPoints) {
        return `${series.label}: no usable NASA-7 fit on file for this record — measured points only, no curve.`
    }
    if (!series.hasUsableFit && !series.hasMeasuredPoints) {
        return `${series.label}: no measured points or usable NASA-7 fit on file for this record — nothing plotted.`
    }
    return null
}
