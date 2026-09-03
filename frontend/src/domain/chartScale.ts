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

/**
 * Fixed-precision labels for a whole axis's worth of ticks -- never
 * scientific notation, so gridline labels stay readable at a glance
 * regardless of the chart's own value range.
 *
 * Precision is chosen ONCE per axis, from the axis's own tick STEP (the
 * smallest positive gap between consecutive ticks), and then applied to
 * EVERY tick on that axis. Deciding it per VALUE instead (as an earlier
 * version of this function did) let each tick round to its own "natural"
 * precision independently -- a review finding caught an axis reading
 * "8.50, 10.0, 100": three different decimal counts on ticks that are all
 * multiples of the same step and should read as one consistent scale.
 *
 * `decimals = max(0, -floor(log10(step)))` -- a step of 1 or more (the
 * common case for `niceTicks`' {1, 2, 5}×10^n steps) needs no decimal
 * places; a step below 1 needs enough to distinguish consecutive ticks
 * (step 0.5 -> 1 place, step 0.05 -> 2 places). Guarded against a
 * non-finite or non-positive step (fewer than two ticks, or ticks that
 * collide exactly) by falling back to 0 decimals rather than propagating
 * `NaN`/`Infinity` into every label.
 */
export function formatTicks(ticks: readonly number[]): string[] {
    if (ticks.length === 0) return []
    let step = Infinity
    const sorted = [...ticks].sort((a, b) => a - b)
    for (let i = 1; i < sorted.length; i++) {
        const gap = sorted[i] - sorted[i - 1]
        if (gap > 0 && gap < step) step = gap
    }
    const decimals = Number.isFinite(step) && step > 0 ? Math.max(0, -Math.floor(Math.log10(step))) : 0
    return ticks.map((value) => {
        // Avoid `(-0).toFixed(n)` printing a spurious leading minus sign --
        // a tick that lands on exactly zero should always read "0"/"0.0"/…
        const normalized = Object.is(value, -0) ? 0 : value
        return normalized.toFixed(decimals)
    })
}
