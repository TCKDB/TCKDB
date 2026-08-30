import type { ConformerProjection } from "../api/speciesEntryApi"

/**
 * The label a reader picks a conformer by: its deposited basin label when
 * one exists, its stable ref otherwise. Never invents a name.
 */
export function conformerLabel(conformer: ConformerProjection): string {
    return conformer.conformer_group.label ?? conformer.conformer_group.conformer_group_ref
}

/**
 * Every calculation ref this conformer's deposited evidence actually
 * contains — pooled across its observations (the precise source) and its
 * own group-level `calculations` list (a fallback for a projection that
 * requested `include=calculations` without `include=observations`). A
 * calculation ref that appears here was run against THIS basin; nothing
 * else is inferred from that fact.
 */
function conformerCalculationRefs(conformer: ConformerProjection): Set<string> {
    const refs = new Set<string>()
    for (const observation of conformer.observations ?? []) {
        for (const calculation of observation.calculations ?? []) {
            if (calculation.calculation_ref) refs.add(calculation.calculation_ref)
        }
    }
    for (const calculation of conformer.calculations ?? []) {
        if (calculation.calculation_ref) refs.add(calculation.calculation_ref)
    }
    return refs
}

export type ThermoProvenanceLike = {
    provenance?: {
        sp_calculation_ref?: string | null
        freq_calculation_ref?: string | null
        primary_calculation?: { calculation_ref?: string | null } | null
    } | null
}

/**
 * SEAM (see the design brief for `design/species-entry-conformer-first`):
 * `thermo.provenance` carries no conformer key today — `sp_calculation_ref`,
 * `freq_calculation_ref` and `primary_calculation.calculation_ref` are the
 * only citations it makes, and none of them names a conformer or
 * observation directly. This infers the same link the archive itself
 * resolved under review, by testing whether any of those three cited refs
 * is a calculation this conformer's own observations actually contain.
 *
 * A record with no such intersection is not broken, not missing evidence,
 * and not this component's error to report — it is "population B": a
 * thermo record produced by a thermo tool (Arkane, etc.) rather than one
 * this client can trace through an opt/freq/sp chain to a single
 * observation. Callers must render that case as entry-level evidence
 * attributed to its own software, never as a failed or absent lookup.
 *
 * DELETE this function once `backend/thermo-provenance-truth` lands a real
 * `conformer_observation_ref` (or equivalent) on thermo provenance, and
 * match on that field directly instead of re-deriving it here.
 */
export function thermoMatchesConformer(record: ThermoProvenanceLike, conformer: ConformerProjection): boolean {
    const provenance = record.provenance
    if (!provenance) return false
    const refs = conformerCalculationRefs(conformer)
    if (refs.size === 0) return false
    const candidates = [
        provenance.sp_calculation_ref,
        provenance.freq_calculation_ref,
        provenance.primary_calculation?.calculation_ref,
    ]
    return candidates.some((ref): ref is string => !!ref && refs.has(ref))
}

export type ConformerContextLike = Array<{ conformer_group_ref: string }> | null | undefined

/**
 * Statmech's REAL (not inferred) conformer link: `include=conformers` on
 * the entry-scoped statmech list returns the conformer_group_ref(s) a
 * record was actually computed against — a first-class field, not a
 * client-side guess. Group-level, which matches the granularity the
 * conformer picker selects at.
 */
export function statmechMatchesConformer(conformerContext: ConformerContextLike, conformer: ConformerProjection): boolean {
    if (!conformerContext || conformerContext.length === 0) return false
    const ref = conformer.conformer_group.conformer_group_ref
    return conformerContext.some((item) => item.conformer_group_ref === ref)
}

/** Splits `records` into what belongs to the selected conformer and what does not, preserving order in both. */
export function partitionByConformer<T>(records: T[], matches: (record: T) => boolean): { matched: T[]; entryLevel: T[] } {
    const matched: T[] = []
    const entryLevel: T[] = []
    for (const record of records) (matches(record) ? matched : entryLevel).push(record)
    return { matched, entryLevel }
}
