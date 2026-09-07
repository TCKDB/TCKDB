import type { ReactionFullCalculationEvidence, ReactionKineticsRecord } from "../api/reactionEntryApi"
import type { ProductLevels } from "./productLevels"

/**
 * Given `provenance.ts_opt_calculation_ref`/`ts_freq_calculation_ref`/
 * `ts_sp_calculation_ref`, resolves each role's level of theory by looking
 * it up among the `calculations[]` evidence summaries `/full?include=
 * calculations` carries -- the SAME cross-reference
 * `ReactionTransitionStatesSection`'s calculation table performs, reused
 * here for the fallback path only (a served `levels` object is always
 * trusted as-is via `resolveProductLevels`; see
 * `ReactionKineticsSection.tsx`'s own call site).
 *
 * Plan §7's live oddity: a kinetics record's `ts_sp_calculation_ref` can
 * name a calculation this TS entry's OWN `calculations` list does not
 * carry (the kinetics provenance resolver and the TS evidence can
 * disagree). When that happens for the ENERGY role specifically, this
 * falls back to `provenance.primary_level_of_theory` -- the one level this
 * provenance block still asserts for the record as a whole -- rather than
 * silently reporting "not recorded" for a role the record plainly has
 * *some* answer for.
 */
export function deriveKineticsLevelsFallback(
    provenance: ReactionKineticsRecord["provenance"],
    calculationsByRef: Map<string, ReactionFullCalculationEvidence>,
): ProductLevels {
    const levelForRef = (ref: string | null | undefined) => {
        if (!ref) return null
        return calculationsByRef.get(ref)?.level_of_theory ?? null
    }
    const geometry = levelForRef(provenance.ts_opt_calculation_ref)
    const freqLevel = levelForRef(provenance.ts_freq_calculation_ref)
    const frequency = freqLevel ?? geometry

    let energy = null as ReturnType<typeof levelForRef>
    let energySource: string | null = null
    if (provenance.ts_sp_calculation_ref) {
        const spLevel = levelForRef(provenance.ts_sp_calculation_ref)
        if (spLevel) {
            energy = spLevel
            energySource = "sp"
        } else if (provenance.primary_level_of_theory) {
            energy = provenance.primary_level_of_theory
            energySource = "sp"
        } else if (geometry) {
            energy = geometry
            energySource = "opt"
        }
    } else if (geometry) {
        energy = geometry
        energySource = "opt"
    }
    return { geometry, frequency, energy, energy_source: energySource }
}

/** Indexes `/full`'s top-level `calculations[]` list by `calculation_ref` -- shared by the kinetics fallback above and `ReactionTransitionStatesSection`'s own "Calculations by stage" table. */
export function buildCalculationsByRef(calculations: ReactionFullCalculationEvidence[] | null | undefined): Map<string, ReactionFullCalculationEvidence> {
    const map = new Map<string, ReactionFullCalculationEvidence>()
    for (const calc of calculations ?? []) {
        if (calc.calculation_ref) map.set(calc.calculation_ref, calc)
    }
    return map
}
