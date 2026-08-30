import type { ConformerProjection } from "../api/speciesEntryApi"

// The archive's own auto-numbering convention for a basin that was never
// given a real depositor-chosen label: `conformer_<N>`. A reader who
// doesn't know that convention reads it as an opaque token, not as "the
// Nth conformer group" -- so it displays as "Conformer Group N" instead.
// Anchored at both ends so a label that merely CONTAINS the pattern (a
// depositor-chosen label like "conformer_1_reoptimized") is left alone.
const AUTO_NUMBERED_LABEL = /^conformer_(\d+)$/

/**
 * The label a reader picks a conformer by.
 *
 * - No deposited label at all (`null`): falls back to the group's own
 *   stable ref, verbatim. A ref is not a label and is never run through
 *   the pattern below -- this function never invents a display name for
 *   a group that was never given one.
 * - A deposited label matching the archive's `conformer_<N>` auto-numbering
 *   convention: displays as "Conformer Group N".
 * - Any other deposited label (including one a depositor chose themselves,
 *   even if it happens to contain the word "conformer"): renders verbatim.
 *   This function never coerces a label into a shape it does not have.
 */
export function conformerLabel(conformer: ConformerProjection): string {
    const label = conformer.conformer_group.label
    if (label === null) return conformer.conformer_group.conformer_group_ref
    const match = label.match(AUTO_NUMBERED_LABEL)
    return match ? `Conformer Group ${match[1]}` : label
}

// Stages read in a fixed, chemistry-meaningful order (opt precedes freq
// precedes sp, the order evidence normally accumulates) with any type this
// module doesn't specifically know about appended afterward -- never
// dropped, just unordered relative to the three named stages.
const KNOWN_CALCULATION_TYPE_ORDER = ["opt", "freq", "sp"]

export type CalculationTypeCount = { type: string; count: number }

/**
 * Raw calculation-row counts by stage (`type`), read off the conformer's
 * own flat `calculations` list -- e.g. 7 opt + 4 freq + 3 sp. This is a
 * DIFFERENT number from `evidence_summary.evidence_coverage`, which counts
 * observations having a stage, not calculation rows of that stage. Never
 * conflate the two: this function answers "how many rows", coverage
 * answers "how many observations".
 */
export function calculationTypeCounts(conformer: ConformerProjection): CalculationTypeCount[] {
    const counts = new Map<string, number>()
    for (const calculation of conformer.calculations ?? []) {
        counts.set(calculation.type, (counts.get(calculation.type) ?? 0) + 1)
    }
    const known = KNOWN_CALCULATION_TYPE_ORDER
        .filter((type) => counts.has(type))
        .map((type) => ({ type, count: counts.get(type) as number }))
    const rest = [...counts.entries()]
        .filter(([type]) => !KNOWN_CALCULATION_TYPE_ORDER.includes(type))
        .map(([type, count]) => ({ type, count }))
    return [...known, ...rest]
}

export type GeometryConvergenceEntry = { geometryRef: string; calculationCount: number }

/**
 * How many calculation outputs converge on each distinct stored geometry
 * this conformer's evidence links to -- e.g. one geometry produced by 4
 * calculation outputs, another by 3. Derived from the conformer's own
 * `geometries` link list (never from a pre-aggregated count, since the
 * per-geometry breakdown has no other source on the wire).
 */
export function geometryConvergence(conformer: ConformerProjection): GeometryConvergenceEntry[] {
    const counts = new Map<string, number>()
    for (const link of conformer.geometries ?? []) {
        counts.set(link.geometry.geometry_ref, (counts.get(link.geometry.geometry_ref) ?? 0) + 1)
    }
    return [...counts.entries()].map(([geometryRef, calculationCount]) => ({ geometryRef, calculationCount }))
}

export type ConformerAttribution<T> = {
    thisConformer: T[]
    /** Each entry is a DIFFERENT conformer the wire actually names — never merged into one generic "other" bucket. */
    otherConformers: Array<{ ref: string; label: string; records: T[] }>
    noLink: T[]
}

/**
 * Three-way split by a record's own conformer-group link, never a guess:
 * the group actually selected, any OTHER group the wire names (kept
 * separate and labeled — a record IS linked, just not to what's selected),
 * and records with no link at all. A binary matched/unmatched split cannot
 * express the middle case honestly — it must either invent a match to the
 * wrong conformer or claim "no link" about a record that has one. Shared by
 * thermo (`thermoConformerGroupRef`, PR #285's real per-record field) and
 * statmech (`statmechConformerGroupRef`, the real `include=conformers`
 * field) — both extractors return a real wire value, never an inference.
 */
export function partitionByConformerLink<T>(
    records: T[],
    conformers: ConformerProjection[],
    selectedRef: string,
    linkedGroupRef: (record: T) => string | null | undefined,
): ConformerAttribution<T> {
    const thisConformer: T[] = []
    const noLink: T[] = []
    const otherByRef = new Map<string, T[]>()
    for (const record of records) {
        const ref = linkedGroupRef(record)
        if (!ref) { noLink.push(record); continue }
        if (ref === selectedRef) { thisConformer.push(record); continue }
        const bucket = otherByRef.get(ref)
        if (bucket) bucket.push(record)
        else otherByRef.set(ref, [record])
    }
    const otherConformers = [...otherByRef.entries()].map(([ref, groupRecords]) => {
        const match = conformers.find((candidate) => candidate.conformer_group.conformer_group_ref === ref)
        // Falls back to the raw ref as its own label if the linked group
        // somehow isn't in the loaded conformer list -- still true of the
        // record (it IS linked to something), never invented.
        return { ref, label: match ? conformerLabel(match) : ref, records: groupRecords }
    })
    return { thisConformer, otherConformers, noLink }
}

export type ThermoConformerLinkLike = {
    provenance?: { conformer_group_ref?: string | null } | null
}

/**
 * Thermo's REAL conformer link (PR #285): `provenance.conformer_group_ref`
 * resolves through the SAME primary calculation already used for
 * `primary_calculation`/`level_of_theory`, sourced server-side via
 * `calculation.conformer_observation_id`. `null` for a record with no
 * resolvable primary calculation (population B) -- reported as "no
 * conformer link", never guessed at from calculation refs. The prior
 * calc-ref-intersection heuristic that lived here was removed because it
 * could not tell that case apart from a record that genuinely does trace
 * to one observation's own opt/freq/sp chain -- see the review that caught
 * it. This field is what makes the distinction real.
 */
export function thermoConformerGroupRef(record: ThermoConformerLinkLike): string | null {
    return record.provenance?.conformer_group_ref ?? null
}

export type ConformerContextLike = Array<{ conformer_group_ref: string }> | null | undefined

/**
 * Statmech's REAL conformer link: `include=conformers` on the entry-scoped
 * statmech list returns the conformer_group_ref(s) a record was actually
 * computed against — a first-class field, not a client-side guess. A
 * record can in principle name more than one group; this reads the first,
 * which is every case observed live (a statmech treatment belongs to one
 * basin's geometry) and documented here so a genuine multi-group record
 * would show as linked-to-its-first-named-group rather than being dropped.
 */
export function statmechConformerGroupRef(conformerContext: ConformerContextLike): string | null {
    if (!conformerContext || conformerContext.length === 0) return null
    return conformerContext[0]?.conformer_group_ref ?? null
}
