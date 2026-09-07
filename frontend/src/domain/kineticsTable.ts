import type { ReactionKineticsRecord } from "../api/reactionEntryApi"

/** R in kJ mol⁻¹ K⁻¹ -- the same constant plan §4's future `domain/arrhenius.ts` names. */
export const GAS_CONSTANT_KJ_MOL_K = 8.314462618e-3

/** k(T) = A·T^n·exp(−Ea/(R·T)) for one (modified-)Arrhenius term. */
export function arrheniusTermK(A: number, n: number | null | undefined, Ea_kj_mol: number | null | undefined, temperatureK: number): number {
    const exponent = n ?? 0
    const ea = Ea_kj_mol ?? 0
    return A * Math.pow(temperatureK, exponent) * Math.exp(-ea / (GAS_CONSTANT_KJ_MOL_K * temperatureK))
}

export const TABLE_POINT_COUNT = 12

export interface KineticsTableRow {
    temperatureK: number
    k: number
}

/**
 * k(T) sampled at `TABLE_POINT_COUNT` evenly spaced temperatures over the
 * record's own fitted range -- the table equivalent for the Arrhenius
 * chart PR 3 ships (plan §4/§6: "PR 2 owns cards + the k(T) TABLE only").
 * Only computed for a plain `arrhenius`/`modified_arrhenius` record (a
 * single `A`) or `multi_arrhenius` (summed over its own terms) -- a
 * pressure-dependent form (PLOG/Chebyshev/falloff) has no single k(T)
 * curve without a pressure, and is excluded (`null`) rather than plotted
 * against an unstated pressure.
 *
 * Pinned against hand-computed values (`kineticsTable.test.ts`): for
 * `kin_spkzatwjlvmmnja3i5im4fl7hq` (A=3025.44 cm³ mol⁻¹ s⁻¹, n=3.11242,
 * Ea=39.9711 kJ/mol, 300–3000 K), k(300 K) = 1.7029×10⁴, k(3000 K) =
 * 4.0467×10¹³ -- the same figures the PR 0 design-review mock's own
 * hand-computed table cited.
 */
export function computeKineticsTable(record: Pick<ReactionKineticsRecord, "plog_entries" | "chebyshev" | "falloff" | "temperature_coverage" | "multi_arrhenius" | "parameters">): KineticsTableRow[] | null {
    if (record.plog_entries || record.chebyshev || record.falloff) return null
    const min = record.temperature_coverage?.record_min_k
    const max = record.temperature_coverage?.record_max_k
    if (min == null || max == null || !(max > min)) return null

    const terms = record.multi_arrhenius && record.multi_arrhenius.length > 0
        ? record.multi_arrhenius
        : record.parameters.A != null
            ? [{ A: record.parameters.A, n: record.parameters.n, Ea_kj_mol: record.parameters.Ea_kj_mol }]
            : null
    if (!terms) return null

    const rows: KineticsTableRow[] = []
    for (let i = 0; i < TABLE_POINT_COUNT; i++) {
        const temperatureK = min + (i * (max - min)) / (TABLE_POINT_COUNT - 1)
        const k = terms.reduce((sum, term) => sum + arrheniusTermK(term.A, term.n, term.Ea_kj_mol, temperatureK), 0)
        rows.push({ temperatureK, k })
    }
    return rows
}

export function log10Text(value: number): string {
    if (value <= 0) return "n/a"
    return Math.log10(value).toFixed(4)
}

const SUPERSCRIPT_DIGITS: Record<string, string> = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "-": "⁻",
}

function superscript(value: number): string {
    return String(value).split("").map((ch) => SUPERSCRIPT_DIGITS[ch] ?? ch).join("")
}

export function scientificText(value: number): string {
    if (value === 0) return "0"
    const exponent = Math.floor(Math.log10(Math.abs(value)))
    const mantissa = value / Math.pow(10, exponent)
    return `${mantissa.toFixed(4)}×10${superscript(exponent)}`
}
