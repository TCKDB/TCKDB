import type { LevelOfTheory } from "../api/scientificSchemas"

// ---------------------------------------------------------------------------
// A statmech or thermo record can use different levels of theory for its
// optimised geometry, its frequencies, and its electronic energy (e.g.
// geometry and frequencies at B3LYP/def2-TZVP, energy from a single point
// at CCSD(T)/cc-pVTZ). This module resolves those three levels for one
// record, either from the server's own additive `levels` object (once
// deployed) or, when that field is absent, by deriving the same three from
// the record's `source_calculations[]` roles — the fallback path an older
// API response still needs. Both paths produce the identical `ProductLevels`
// shape, so `EntryStatmechSection.tsx`/`EntryThermoSection.tsx` render a
// record the same way regardless of which one the server used.
// ---------------------------------------------------------------------------

/** The two energy-has-its-own-calculation cases the backend distinguishes,
 *  plus the two evidence-free classifications a record can also declare.
 *  Kept as a plain string (not a zod/TS enum) on the wire and here — see
 *  `api/scientificSchemas.ts`'s `productLevelsSchema` — so an unrecognised
 *  future value never fails to parse; it just renders with no note. */
export type EnergySource = "opt" | "sp" | "composite" | "imported" | (string & {})

export interface ProductLevels {
    geometry: LevelOfTheory | null
    frequency: LevelOfTheory | null
    energy: LevelOfTheory | null
    energy_source: EnergySource | null
}

/** One `source_calculations[]` row's role and level of theory — the subset
 *  of `StatmechRecord["source_calculations"][number]` /
 *  `ThermoRecord["source_calculations"][number]` this module actually
 *  reads. Both wire shapes satisfy this structurally; neither needs to be
 *  imported here. */
export interface RoleLevelSource {
    role: string
    level_of_theory?: LevelOfTheory | null
}

export const EMPTY_PRODUCT_LEVELS: ProductLevels = {
    geometry: null,
    frequency: null,
    energy: null,
    energy_source: null,
}

/** First `source_calculations` row for `role`, or `null` if none is
 *  present or it carries no level of theory. Mirrors the backend rule,
 *  which resolves one role to one level — a record with more than one row
 *  for the same role (a re-run) is not a case this derivation tries to
 *  disambiguate; the first one, in wire order, wins. */
function levelForRole(sourceCalculations: RoleLevelSource[] | null | undefined, role: string): LevelOfTheory | null {
    const match = (sourceCalculations ?? []).find((calc) => calc.role === role)
    return match?.level_of_theory ?? null
}

/**
 * Derive geometry/frequency/energy from `source_calculations[]` roles, the
 * way the backend rule does when it builds the `levels` field itself:
 *  - geometry = the `opt` role's level of theory.
 *  - frequency = the `freq` role's level, else the opt's (a record with no
 *    dedicated frequency job reuses the level its own geometry job ran at).
 *  - energy = the `sp` role's level when a single point is linked, else
 *    the opt's — an optimisation's final energy is its own single-point
 *    energy, not a missing fact. `energy_source` names which case applied:
 *    `"sp"` when a linked single point supplied it, `"opt"` when the
 *    optimisation's own energy was reused, `null` when there is no opt to
 *    fall back to either (nothing to derive from).
 *
 * A record with no `opt`/`freq`/`sp` roles at all (no source calculations
 * recorded, or none matching these three roles) returns
 * `EMPTY_PRODUCT_LEVELS` — every field `null`, not an error.
 */
export function deriveProductLevelsFromSourceCalculations(
    sourceCalculations: RoleLevelSource[] | null | undefined,
): ProductLevels {
    const opt = levelForRole(sourceCalculations, "opt")
    const freq = levelForRole(sourceCalculations, "freq")
    const sp = levelForRole(sourceCalculations, "sp")
    if (!opt && !freq && !sp) return EMPTY_PRODUCT_LEVELS
    return {
        geometry: opt,
        frequency: freq ?? opt,
        energy: sp ?? opt,
        energy_source: sp ? "sp" : opt ? "opt" : null,
    }
}

/**
 * Resolve one record's product levels: the server's own `levels` object
 * when present (either surface, additive), normalised so a missing field
 * inside it reads as `null` rather than `undefined` — falling back to
 * `deriveProductLevelsFromSourceCalculations` only when `levels` itself is
 * absent (an older API response that predates this field). A `levels`
 * object that is present but empty (every field `null`) is still "the
 * server's own answer" and is never second-guessed by re-deriving from
 * `source_calculations` — an explicit `null` and "not shipped yet" are
 * different facts.
 */
export function resolveProductLevels(
    levels: Partial<ProductLevels> | null | undefined,
    sourceCalculations: RoleLevelSource[] | null | undefined,
): ProductLevels {
    if (levels) {
        return {
            geometry: levels.geometry ?? null,
            frequency: levels.frequency ?? null,
            energy: levels.energy ?? null,
            energy_source: levels.energy_source ?? null,
        }
    }
    return deriveProductLevelsFromSourceCalculations(sourceCalculations)
}

/** A stable comparison key for one level of theory — the record's own ref
 *  when both sides carry one (the strongest identity this wire shape has),
 *  else the full method/basis/dispersion/solvent tuple, so two rows that
 *  render the same compact label (`lotLabel`) but differ only in
 *  dispersion or solvent treatment are never treated as the same level. */
function levelKey(level: LevelOfTheory | null): string | null {
    if (!level) return null
    if (level.level_of_theory_ref) return `ref:${level.level_of_theory_ref}`
    return JSON.stringify({
        method: level.method,
        basis: level.basis ?? null,
        dispersion: level.dispersion ?? null,
        solvent: level.solvent ?? null,
    })
}

/** Whether two levels of theory are the SAME fact — two `null`s agree
 *  (neither is recorded, which is one shared state); a `null` beside a
 *  recorded level never does (an unknown level is not the same fact as a
 *  known one, even provisionally). */
export function levelsOfTheoryEqual(a: LevelOfTheory | null, b: LevelOfTheory | null): boolean {
    return levelKey(a) === levelKey(b)
}

/**
 * Whether `levels`' geometry, frequency and energy are all the SAME level
 * of theory — the collapse condition for the rendering rule: render one
 * "Level of theory: X" fact when true, three labelled facts
 * (Geometry/Frequencies/Energy) when false. All-three-`null` counts as
 * agreeing (there is one shared fact to state: "not recorded"), matching
 * the single-fact display this page already used before any of the three
 * was ever split out.
 */
export function productLevelsAgree(levels: ProductLevels): boolean {
    return levelsOfTheoryEqual(levels.geometry, levels.frequency) && levelsOfTheoryEqual(levels.geometry, levels.energy)
}

/**
 * Whether every entry in `levelsList` resolves to the SAME product levels
 * as the first — used to decide whether an identical-values GROUP's own
 * shared display (a single lifted fact, computed from one representative)
 * is actually safe to show once. Unlike the scientific values
 * `statmechRecordFingerprint`/`thermoRecordFingerprint` group on, a
 * record's geometry/frequency/energy levels are not (yet) part of either
 * fingerprint, so two records in the same identical-values group can still
 * report different per-role levels — this check catches that case
 * explicitly rather than trusting the fingerprint to have covered it. An
 * empty list agrees vacuously (nothing to disagree about); this is never
 * called with one, since a group always has at least one record.
 */
export function allProductLevelsAgree(levelsList: ProductLevels[]): boolean {
    if (levelsList.length === 0) return true
    const [first, ...rest] = levelsList
    return rest.every((levels) => (
        levelsOfTheoryEqual(levels.geometry, first.geometry)
        && levelsOfTheoryEqual(levels.frequency, first.frequency)
        && levelsOfTheoryEqual(levels.energy, first.energy)
    ))
}

/**
 * Whether a table showing one row per record needs the three-column
 * (Geometry/Frequencies/Energy) layout — true the moment ANY entry's own
 * levels disagree internally (`!productLevelsAgree`), so every row in one
 * table uses the same column count (a table cannot vary its own column
 * count row to row). Lives here, not in `components/ProductLevels.tsx`,
 * because that file exports components only —
 * `react-refresh/only-export-components` (see `rateLimitFormat.ts`'s own
 * note on the same split).
 */
export function productLevelsTableNeedsThreeColumns(levelsList: ProductLevels[]): boolean {
    return levelsList.some((levels) => !productLevelsAgree(levels))
}
