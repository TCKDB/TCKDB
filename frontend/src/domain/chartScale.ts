// Tiny, generic linear-scale helpers shared by any hand-rolled SVG chart in
// this app (currently just `ThermoCpChart.tsx`). Kept separate from that
// component and separately testable so a chart panel's scale/domain math
// can be verified directly against its rendered output, rather than only
// eyeballed -- see `ThermoCpChart.test.tsx`'s "own scale" tests, which
// recompute an expected pixel position with these same functions and
// compare it against what actually rendered.

/** Maps a value in `domain` linearly onto `range`. A zero-span domain (every
 * value identical) would otherwise divide by zero -- returns the range's own
 * midpoint for every input in that case, rather than NaN or Infinity. */
export function linearScale(domain: readonly [number, number], range: readonly [number, number]): (value: number) => number {
    const [d0, d1] = domain
    const [r0, r1] = range
    if (d1 === d0) return () => (r0 + r1) / 2
    return (value: number) => r0 + ((value - d0) / (d1 - d0)) * (r1 - r0)
}

/**
 * A padded `[min, max]` domain spanning every value in `values`. `minSpan`
 * guards the same zero-span case as `linearScale` above (e.g. every
 * measured Cp on one record happens to be identical) so the padded domain
 * is never a single point. Returns `[0, 1]` for an empty `values` -- the
 * caller decides whether to render anything at all; this function never
 * throws on "nothing to plot".
 */
export function domainWithPadding(values: readonly number[], paddingFraction = 0.12, minSpan = 1e-6): [number, number] {
    if (values.length === 0) return [0, 1]
    const min = Math.min(...values)
    const max = Math.max(...values)
    const span = Math.max(max - min, minSpan)
    const pad = span * paddingFraction
    return [min - pad, max + pad]
}

/** `count` evenly spaced values across `[domain[0], domain[1]]`, inclusive of both ends. */
export function evenTicks(domain: readonly [number, number], count = 5): number[] {
    const [d0, d1] = domain
    if (count <= 1) return [d0]
    return Array.from({ length: count }, (_, i) => d0 + ((d1 - d0) * i) / (count - 1))
}

/** Highest decimal count `formatTicks` will ever try before giving up and
 * using it anyway -- keeps the search below bounded and terminating for
 * every input, never a source of unbounded work. */
const MAX_TICK_DECIMALS = 6

/**
 * Fixed-precision labels for a whole axis's worth of ticks -- never
 * scientific notation, so gridline labels stay readable at a glance
 * regardless of the chart's own value range.
 *
 * Precision is chosen ONCE per axis and applied to EVERY tick on it, never
 * per value -- a review finding caught an axis reading "8.50, 10.0, 100":
 * three different decimal counts on ticks that should read as one
 * consistent scale.
 *
 * The precision itself is the SMALLEST decimal count `d` (0..6) at which
 * every finite tick round-trips through `Number(t.toFixed(d))` back to
 * (within float noise of) its own value -- the least precision that loses
 * no tick's own digits, not a guess derived from the axis's tick STEP. An
 * earlier version of this function derived `d` from
 * `-floor(log10(step))`, which is only correct when the step is exactly
 * {1, 2, 5}×10^n (`niceTicks`' own output shape) -- fed a step of 2.5, it
 * silently rounded the step's own multiples to whole numbers ("0, 3, 5, 8,
 * 10" for ticks 0/2.5/5/7.5/10, and "-8" for a tick at -7.5): a step-based
 * guess about precision, not a check that the tick a reader sees is the
 * tick that was actually plotted. The round-trip check has no such blind
 * spot -- it is correct for any tick set, nice-stepped or not, by
 * construction, and doubles as the guard for a non-finite tick (which
 * never round-trips at any `d`, and is simply skipped when deciding
 * precision -- see below) or an all-identical/degenerate tick set (each
 * tick is still checked against itself, so nothing special is needed).
 */
export function formatTicks(ticks: readonly number[]): string[] {
    if (ticks.length === 0) return []
    const finiteTicks = ticks.filter((tick) => Number.isFinite(tick))
    let decimals = MAX_TICK_DECIMALS
    for (let candidate = 0; candidate <= MAX_TICK_DECIMALS; candidate++) {
        const everyTickRoundTrips = finiteTicks.every((tick) => {
            const normalized = Object.is(tick, -0) ? 0 : tick
            const tolerance = 1e-9 * Math.max(1, Math.abs(normalized))
            return Math.abs(Number(normalized.toFixed(candidate)) - normalized) <= tolerance
        })
        if (everyTickRoundTrips) { decimals = candidate; break }
    }
    return ticks.map((value) => {
        // Avoid `(-0).toFixed(n)` printing a spurious leading minus sign --
        // a tick that lands on exactly zero should always read "0"/"0.0"/…
        // `toFixed` itself never throws on NaN/±Infinity (returns the
        // "NaN"/"Infinity"/"-Infinity" string per spec), so a non-finite
        // tick still gets a label here, just not one that shaped `decimals`.
        const normalized = Object.is(value, -0) ? 0 : value
        return normalized.toFixed(decimals)
    })
}
