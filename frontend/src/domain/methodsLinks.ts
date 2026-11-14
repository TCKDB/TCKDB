/**
 * Path builders for the methods surface. Three ref kinds get their own
 * thin route family: a level of theory, a deposited energy-correction
 * scheme, and a frequency scale factor.
 *
 * Kept as one-line functions in their own module (not inlined at each call
 * site) so the three route shapes are declared exactly once -- `App.tsx`'s
 * route table and every call site below (`LevelLabel.tsx`, the
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

/** A composite recipe (CBS-QB3, a CCSD(T)/CBS extrapolation, ...).
 *  `GET /scientific/composite-schemes/{ref}` is its read; this is its page. */
export function compositeSchemePath(ref: string): string {
    return `/methods/composite-schemes/${ref}`
}
