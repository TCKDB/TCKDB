import type { ConformerProjection } from "../api/speciesEntryApi"

/**
 * The label a reader picks a conformer by: its deposited basin label when
 * one exists, its stable ref otherwise. Never invents a name.
 */
export function conformerLabel(conformer: ConformerProjection): string {
    return conformer.conformer_group.label ?? conformer.conformer_group.conformer_group_ref
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
