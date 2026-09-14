/**
 * Path builders for the methods surface (`docs/plans/methods-surface.md`,
 * `plan-methods-surface-v2`, not committed to this repo -- see PR 3's own
 * brief). Three ref kinds get their own thin route family, exactly the
 * three dead-ref kinds §2.4 of that plan found rendered as unlinked text
 * across the site: a level of theory, a deposited energy-correction
 * scheme, and a frequency scale factor.
 *
 * Kept as one-line functions in their own module (not inlined at each call
 * site) so the three route shapes are declared exactly once -- `App.tsx`'s
 * route table and every call site below (`LevelOfTheoryLink.tsx`, the
 * dead-ref edits across `src/components`/`src/pages`) share this one
 * source of truth for what the path looks like.
 */
export function levelOfTheoryPath(ref: string): string {
    return `/methods/${ref}`
}

export function correctionSchemePath(ref: string): string {
    return `/methods/schemes/${ref}`
}

export function frequencyScaleFactorPath(ref: string): string {
    return `/methods/frequency-scale-factors/${ref}`
}
