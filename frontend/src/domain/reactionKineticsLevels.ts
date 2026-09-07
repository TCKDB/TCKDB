import type { ReactionFullCalculationEvidence, ReactionKineticsRecord, ReactionTransitionStateInFull } from "../api/reactionEntryApi"
import type { ProductLevels } from "./productLevels"

/** Indexes `/full`'s top-level `calculations[]` list by `calculation_ref` -- shared by the kinetics fallback below and `ReactionTransitionStatesSection`'s own "Calculations by stage" table. */
export function buildCalculationsByRef(calculations: ReactionFullCalculationEvidence[] | null | undefined): Map<string, ReactionFullCalculationEvidence> {
    const map = new Map<string, ReactionFullCalculationEvidence>()
    for (const calc of calculations ?? []) {
        if (calc.calculation_ref) map.set(calc.calculation_ref, calc)
    }
    return map
}

export interface DependencyEdgeInfo {
    role: string
    parentCalculationRef: string
}

/**
 * Indexes every `freq_on`/`single_point_on` dependency edge served across
 * ALL of this reaction entry's transition states, keyed by the CHILD
 * (freq/sp) calculation ref -- the one hop `deriveKineticsLevelsFallback`
 * below needs to resolve a kinetics record's geometry the same way the
 * server does. Other dependency roles (`irc_start`, `optimized_from`, ...)
 * are not indexed here; they play no part in level resolution.
 */
export function buildDependencyEdgesByChildRef(
    transitionStates: Pick<ReactionTransitionStateInFull, "dependencies">[],
): Map<string, DependencyEdgeInfo> {
    const map = new Map<string, DependencyEdgeInfo>()
    for (const ts of transitionStates) {
        for (const dep of ts.dependencies) {
            if (dep.role === "freq_on" || dep.role === "single_point_on") {
                map.set(dep.child_calculation_ref, { role: dep.role, parentCalculationRef: dep.parent_calculation_ref })
            }
        }
    }
    return map
}

export interface KineticsLevelsFallbackResult {
    levels: ProductLevels
    /**
     * Set only when `levels.energy` was substituted from
     * `provenance.primary_level_of_theory` because the record's own cited
     * single-point calculation is not among this page's known
     * calculations (the plan §7 "resolver disagreement" case) -- names
     * which fact was substituted, for an honest caveat at the call site.
     * `null` whenever `energy` was resolved directly (or is itself
     * `null`) -- never set "just in case".
     */
    energyFallbackNote: string | null
}

/**
 * Client-side mirror of the backend's `_build_kinetics_levels`
 * (`backend/app/services/scientific_read/kinetics.py`) -- used only when
 * a kinetics record's own served `levels` is absent (an API generation
 * older than PR 1's `feat/reaction-full-additions`, #398, deployed
 * 2026-09-07; every live record now serves `levels` directly, so this
 * path is a defensive fallback, not the common case, as of this PR).
 *
 * The backend's own finding (PR 398's post-review commit) is the reason
 * this is NOT a simple "look up `ts_opt_calculation_ref`" lookup:
 * `_KINETICS_ROLE_COMPATIBILITY` (`app/services/kinetics_resolution.py`)
 * permits no kinetics role that accepts an opt-typed calculation at all,
 * so `provenance.ts_opt_calculation_ref` is structurally `null` on every
 * legally-uploaded TS-backed record -- it is never merely absent for
 * THIS record. The opt is not lost, though: a cited `freq`/`sp` was
 * itself computed at an optimised geometry, recorded as a
 * `calculation_dependency` edge (`freq_on`/`single_point_on`, parent
 * opt -> child freq/sp) at upload time. `dependencyEdgesByChildRef`
 * (`buildDependencyEdgesByChildRef` above, built from every TS entry's
 * own `dependencies[]`) is that one-hop lookup, preferring the
 * freq-derived opt when both a freq and an sp are cited -- exactly
 * mirroring `_resolve_ts_opt_via_dependency`'s own preference order. The
 * resolved parent is additionally required to actually be `opt`-typed
 * (via `calculationsByRef`), the same defensive check the backend
 * performs rather than trusting the edge's role name alone.
 *
 * `frequency` is the DIRECT `ts_freq_calculation_ref` citation's own
 * level only -- never falling back to the resolved opt's level the way
 * `domain/productLevels.ts`'s generic `deriveProductLevelsFromSourceCalculations`
 * does for OTHER record kinds. The backend's own `_build_kinetics_levels`
 * does not apply that fallback here either (every `RoleCalcInfo` it
 * builds is `carries_frequencies=False`), so mirroring the generic
 * derivation here would silently diverge from what the server actually
 * returns once `levels` deploys.
 *
 * `energy` prefers the cited `ts_sp_calculation_ref`'s own level; when
 * that calculation is not among `calculationsByRef` (the live "resolver
 * disagreement" case: a kinetics record's own sp citation naming a
 * calculation this reaction's TS graph does not otherwise carry --
 * verified live, plan §7), this falls back to
 * `provenance.primary_level_of_theory` as a best-effort substitute and
 * says so via `energyFallbackNote` -- the caller must render that note
 * alongside the value; presenting the substituted level as though it
 * were the sp calculation's own resolved level would misattribute it.
 */
export function deriveKineticsLevelsFallback(
    provenance: ReactionKineticsRecord["provenance"],
    calculationsByRef: Map<string, ReactionFullCalculationEvidence>,
    dependencyEdgesByChildRef: Map<string, DependencyEdgeInfo>,
): KineticsLevelsFallbackResult {
    const levelForRef = (ref: string | null | undefined) => {
        if (!ref) return null
        return calculationsByRef.get(ref)?.level_of_theory ?? null
    }

    function resolveOptViaDependency(childRef: string | null | undefined, requiredRole: "freq_on" | "single_point_on"): string | null {
        if (!childRef) return null
        const edge = dependencyEdgesByChildRef.get(childRef)
        if (!edge || edge.role !== requiredRole) return null
        const parent = calculationsByRef.get(edge.parentCalculationRef)
        if (!parent || parent.calculation_type !== "opt") return null
        return edge.parentCalculationRef
    }

    // A direct `ts_opt_calculation_ref` citation is checked first even
    // though it is structurally null on a legally-uploaded record (see
    // this function's own docstring) -- an unenforced/legacy row that
    // somehow does carry one is still honoured, never overridden by the
    // dependency-edge guess.
    const geometryRef = provenance.ts_opt_calculation_ref
        ?? resolveOptViaDependency(provenance.ts_freq_calculation_ref, "freq_on")
        ?? resolveOptViaDependency(provenance.ts_sp_calculation_ref, "single_point_on")
    const geometry = geometryRef ? levelForRef(geometryRef) : null

    const frequency = levelForRef(provenance.ts_freq_calculation_ref)

    let energy: ProductLevels["energy"] = null
    let energySource: ProductLevels["energy_source"] = null
    let energyFallbackNote: string | null = null
    if (provenance.ts_sp_calculation_ref) {
        const spLevel = levelForRef(provenance.ts_sp_calculation_ref)
        if (spLevel) {
            energy = spLevel
            energySource = "sp"
        } else if (provenance.primary_level_of_theory) {
            energy = provenance.primary_level_of_theory
            energySource = "sp"
            energyFallbackNote = `This record's own cited single-point calculation (${provenance.ts_sp_calculation_ref}) is not among this page's known calculations -- Energy above is this record's primary level of theory, a best-effort substitute, not a directly resolved single-point level.`
        } else if (geometry) {
            energy = geometry
            energySource = "opt"
        }
    } else if (geometry) {
        energy = geometry
        energySource = "opt"
    }

    return { levels: { geometry, frequency, energy, energy_source: energySource }, energyFallbackNote }
}
