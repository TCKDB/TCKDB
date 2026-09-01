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

/** A short, fixed-precision label for an axis tick -- never scientific
 * notation, so gridline labels stay readable at a glance regardless of the
 * chart's own value range. */
export function formatTick(value: number): string {
    const magnitude = Math.abs(value)
    if (magnitude === 0) return "0"
    if (magnitude >= 100) return value.toFixed(0)
    if (magnitude >= 10) return value.toFixed(1)
    return value.toFixed(2)
}
