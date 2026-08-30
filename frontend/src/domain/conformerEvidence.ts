import type { ConformerProjection } from "../api/speciesEntryApi"

/**
 * The label a reader picks a conformer by: its deposited basin label when
 * one exists, its stable ref otherwise. Never invents a name.
 */
export function conformerLabel(conformer: ConformerProjection): string {
    return conformer.conformer_group.label ?? conformer.conformer_group.conformer_group_ref
}

// ---------------------------------------------------------------------------
// REMOVED: a thermo-side "which conformer does this belong to" inference
// used to live here, matching `provenance.sp_calculation_ref` /
// `freq_calculation_ref` / `primary_calculation.calculation_ref` against a
// conformer's own observation calc-refs. It was wrong, not just imprecise:
// measured against the archive (thermo id 4, `spe_dfcw4tvy6tkqxnyittmn6d3vdu`)
// a record with ZERO source calculations of its own -- thermo's own
// software is Arkane, `thermo_source_calculation` is empty -- still carries
// a populated `sp_calculation_ref` on the wire, because that field is
// filled via a SECOND route this client cannot distinguish from the first:
// `thermo.statmech_id -> statmech_source_calculation -> calculation`. Both
// "this thermo traces to one observation's own opt/freq/sp chain" (real
// per-conformer evidence) and "this thermo was produced by a separate tool
// but cites the statmech it borrowed frequencies from" (population B) put a
// calculation ref in the same field. No amount of cleverness over the
// CURRENT wire shape can tell those apart -- the fact that actually
// separates them (does this thermo have its own source calculations, and
// its own software) is exactly what the API does not serve today. That is
// what `backend/thermo-provenance-truth` adds.
//
// So: no thermo-side matching function exists here. `EntryThermoSection`
// renders every deposited thermo record for the entry, unfiltered by the
// selected conformer, and says so explicitly rather than grouping records
// under a conformer attribution this client cannot support. Once
// `backend/thermo-provenance-truth` lands a real per-record conformer (or
// observation) key, add a `thermoMatchesConformer(record, conformer)`
// function here that matches on THAT field directly -- never re-derive one
// from calculation refs again.
// ---------------------------------------------------------------------------

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
