import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel } from "./conformerEvidence"

/**
 * `statmech` has no conformer column at all -- it is entry-scoped
 * (`species_entry_id` XOR `transition_state_entry_id`), never
 * conformer-scoped (`backend/app/schemas/reads/scientific_statmech.py`'s
 * `StatmechConformerContextItem`: "statmech does not have a direct FK to a
 * single basin"). Its SOURCE CALCULATIONS are conformer-scoped, though --
 * every calculation this app already loads for the entry
 * (`ConformerProjection.observations[].calculations[]`, fetched once via
 * `loadEntryConformers`) carries its own conformer link. Measured live
 * (2026-09-02, 101 statmech records archive-wide with source calculations,
 * across 57 species entries): every one of the 101 resolves via this
 * function to EXACTLY one conformer group -- opt, freq and sp all trace to
 * the same observation every time today. Zero disagreement cases exist in
 * the archive right now, which is exactly why this function still has to
 * handle the disagreement case explicitly rather than assume it away: a
 * derivation that silently picked "the first" conformer the moment a second
 * archive stopped agreeing would be wrong in a way nothing today would
 * catch.
 *
 * This is a READ-TIME derivation only -- no new column, no migration. A
 * conformer this function reports is never a stored fact about the
 * statmech record; it is inferred from calculations the record happens to
 * cite, and callers must label it as derived, not as a property of the
 * record itself.
 */
export type StatmechDerivedConformer =
    // No source-calculation refs resolved to any conformer this app has
    // loaded -- either there is nothing to resolve (no source calculations
    // at all) or the ones that exist don't trace to a loaded observation.
    // Callers distinguish those two cases using `evidence_summary
    // .source_calculation_count` themselves; this function only reports
    // what it could resolve.
    | { kind: "unresolved" }
    | { kind: "single"; conformerGroupRef: string; label: string }
    // Every DIFFERENT conformer group the source calculations actually
    // name, in the order first encountered -- never collapsed to "the
    // first one", which is precisely the wrong call a derivation like this
    // must never make silently.
    | { kind: "disagreement"; conformers: Array<{ conformerGroupRef: string; label: string }> }

/**
 * `calculation_ref` -> `conformer_group_ref`, built from every observation
 * on every loaded conformer group. A calculation cited by more than one
 * observation (should not happen, but this function does not assume the
 * data is clean) keeps whichever mapping it saw first -- deterministic,
 * never a source of its own disagreement.
 */
function buildCalculationConformerIndex(conformers: readonly ConformerProjection[]): Map<string, string> {
    const index = new Map<string, string>()
    for (const group of conformers) {
        const groupRef = group.conformer_group.conformer_group_ref
        for (const observation of group.observations ?? []) {
            for (const calculation of observation.calculations ?? []) {
                if (!index.has(calculation.calculation_ref)) index.set(calculation.calculation_ref, groupRef)
            }
        }
    }
    return index
}

/**
 * Derives which conformer a statmech record's evidence traces to, from its
 * `source_calculations` (`include=source_calculations`) cross-referenced
 * against the entry's already-loaded conformer projections (never the
 * statmech record's own weak `include=conformers` hint -- see
 * `conformerEvidence.ts`'s `statmechConformerGroupRefs`, which is a
 * curator-set "context hint, not a hard membership pointer" and can be
 * empty even when the source calculations plainly agree).
 */
export function deriveStatmechConformer(
    sourceCalculationRefs: readonly string[] | null | undefined,
    conformers: readonly ConformerProjection[],
): StatmechDerivedConformer {
    if (!sourceCalculationRefs || sourceCalculationRefs.length === 0) return { kind: "unresolved" }
    const index = buildCalculationConformerIndex(conformers)
    const orderedGroupRefs: string[] = []
    const seen = new Set<string>()
    for (const calculationRef of sourceCalculationRefs) {
        const groupRef = index.get(calculationRef)
        if (groupRef && !seen.has(groupRef)) {
            seen.add(groupRef)
            orderedGroupRefs.push(groupRef)
        }
    }
    if (orderedGroupRefs.length === 0) return { kind: "unresolved" }
    const labelFor = (groupRef: string) => {
        const match = conformers.find((candidate) => candidate.conformer_group.conformer_group_ref === groupRef)
        return match ? conformerLabel(match) : groupRef
    }
    if (orderedGroupRefs.length === 1) {
        return { kind: "single", conformerGroupRef: orderedGroupRefs[0], label: labelFor(orderedGroupRefs[0]) }
    }
    return {
        kind: "disagreement",
        conformers: orderedGroupRefs.map((groupRef) => ({ conformerGroupRef: groupRef, label: labelFor(groupRef) })),
    }
}
