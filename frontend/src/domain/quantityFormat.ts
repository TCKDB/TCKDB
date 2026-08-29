/**
 * Numeric-quantity formatting rules, ported rule-for-rule from
 * `backend/app/api/landing.py` (`fixed`, `scientific`, `fillValue`, and the
 * digits/units table scattered across its thermo/statmech/transport/
 * calculation/kinetics view builders) — see `chemistryFormat.ts` for why
 * this is a port of behaviour rather than of code.
 *
 * Three rules this file exists to enforce, in `landing.py`'s own words:
 *
 * - A quantity is a *number and a unit*, and the unit belongs next to the
 *   number, never folded into a label.
 * - A number is rounded to what the quantity actually supports. The API
 *   answers `143.8942674605864` because that is what a double holds; past
 *   the precision a quantity was measured/fit to, printing more digits is
 *   the clearest possible signal that nobody looked at the output.
 * - A value that is not there says so, on its own row — never a silently
 *   skipped one.
 *
 * ---- one absent-value spelling (brief section E) --------------------
 *
 * `chemistryFormat.ts`'s `ABSENT` is `"not recorded"` (lowercase) and is
 * re-exported from here unchanged. Before this change, 40+ call sites
 * across the app independently wrote `"Not recorded"` (capitalized) — the
 * inconsistency the brief asked to resolve. Lowercase wins, for one
 * concrete reason rather than a style preference: `IdentifierSearch.tsx`
 * already composes `ABSENT` into a mid-sentence context string —
 * `` `charge ${chargeDisplay(match.charge)}` `` reads as "charge not
 * recorded". Capitalizing the shared constant would either read as
 * "charge Not recorded" at that call site, or force every consumer to
 * choose sentence-case-or-not per call. Lowercase is the one spelling that
 * is correct both standalone in a `<dd>` and inline in a sentence, so nothing
 * downstream needs to special-case it. The 40+ existing "Not recorded"
 * literals across the app were changed to match (see the PR diff), not the
 * other way around, since `chemistryFormat.ts` is the newly-audited,
 * pinned-by-test module and the rename is purely mechanical.
 */

import { ABSENT } from "./chemistryFormat"

export { ABSENT }

const MINUS = "−"

/** A number-and-unit pair, or a number-mantissa-and-exponent-and-unit triple. */
export type Quantity = { value: string; unit: string | null; exponent?: string }

/** A rendering the page states in its own words, standing in for a missing value. */
export type NamedAbsence = { sentence: string }

/**
 * Everything `fillValue` (below) knows how to render: a plain string/number
 * a component already formatted itself, a `Quantity` from `fixed`/
 * `scientific`, a `NamedAbsence`, or nothing at all.
 */
export type FillableValue = string | number | Quantity | NamedAbsence | null | undefined

/**
 * `fixed(value, digits, unit)` — ported from `landing.py:2069-2072`.
 * `null`/`undefined` in, `null` out: an absent value is never coerced into
 * `"0.00"`. Digits and unit are the caller's judgement about what this
 * particular quantity supports (see `QUANTITY_SPECS` below).
 */
export function fixed(value: number | null | undefined, digits: number, unit?: string | null): Quantity | null {
    if (value === null || value === undefined) return null
    return { value: Number(value).toFixed(digits), unit: unit ?? null }
}

/**
 * `scientific(value, digits, unit)` — ported from `landing.py:2083-2111`.
 * `digits` is significant figures, matching the Python call sites (`A` at
 * 3sf, `n` at 4sf). Values whose magnitude falls inside `[1e-2, 1e4)` print
 * as plain digits (`toPrecision`), never forced into `a × 10ⁿ` notation
 * when a chemist would just read the number directly. Outside that range,
 * the mantissa/exponent split comes from `toExponential` — no string
 * surgery on the caller's number — with U+2212 (not ASCII `-`) on a
 * negative exponent, and U+00D7 supplied by the caller at render time
 * (kept out of this pure layer; see `fillValue` below). A value whose
 * `toExponential` output doesn't match the expected shape falls through to
 * plain digits rather than being reshaped on a guess — same rule as
 * `formulaTokens`'s round-trip guard in `chemistryFormat.ts`.
 */
export function scientific(value: number | null | undefined, digits: number, unit?: string | null): Quantity | null {
    if (value === null || value === undefined) return null
    const number = Number(value)
    if (!Number.isFinite(number)) return null
    if (number === 0) return { value: "0", unit: unit ?? null }
    const magnitude = Math.abs(number)
    if (magnitude >= 1e4 || magnitude < 1e-2) {
        const match = /^(-?[0-9](?:\.[0-9]+)?)e([+-][0-9]+)$/.exec(number.toExponential(digits - 1))
        if (match) {
            return {
                value: match[1],
                exponent: match[2].charAt(0) === "-" ? `${MINUS}${match[2].slice(1)}` : match[2].slice(1),
                unit: unit ?? null,
            }
        }
    }
    return { value: String(Number(number.toPrecision(digits))), unit: unit ?? null }
}

/**
 * What `fillValue` decided to render, for a caller (typically a small React
 * component — see `components/Quantity.tsx`) to turn into markup. This is
 * the pure half of `landing.py:2725-2760`'s `fillValue`; that function
 * mutated a DOM node directly, which has no equivalent in a React tree, so
 * the dispatch decision is split out here and stays independently testable
 * without mounting anything.
 *
 * The rule that matters, in `landing.py`'s own words: **absence is a
 * rendering, never a skipped row.** A list of pairs that filters out
 * `null`/`undefined` entries before mapping to rows silently turns "this
 * record does not carry an uncertainty" into "this page never thought to
 * show one," and a reader cannot tell those apart. `fillValue` exists so
 * every field gets a row, and the row says what kind of nothing it is.
 */
export type Filled =
    | { kind: "absent"; text: string }
    | { kind: "named-absence"; text: string }
    | { kind: "quantity"; value: string; unit: string | null; exponent?: string }
    | { kind: "plain"; text: string }

export function fillValue(value: FillableValue): Filled {
    if (value === null || value === undefined || value === "") {
        return { kind: "absent", text: ABSENT }
    }
    if (typeof value === "object" && "sentence" in value) {
        return { kind: "named-absence", text: value.sentence }
    }
    if (typeof value === "object") {
        return { kind: "quantity", value: value.value, unit: value.unit, exponent: value.exponent }
    }
    return { kind: "plain", text: String(value) }
}

/**
 * ---- the digits-and-units table (brief section B) --------------------
 *
 * Collected from `landing.py:2472-2479, 2507, 2524-2528, 2597, 3401-3412`
 * into one place, as the brief asks, instead of the precision for each
 * quantity being a literal re-typed at every call site. This is chemistry
 * judgement about what precision each quantity supports — not a style
 * choice — so a change to any one of these belongs in exactly one place.
 *
 * | Quantity                     | digits | unit    | landing.py call site |
 * |-------------------------------|--------|---------|----------------------|
 * | thermo ΔH°(298 K)              | 2      | kJ/mol  | thermoView headline  |
 * | thermo ΔH° uncertainty         | 2      | kJ/mol  | thermoView facts     |
 * | thermo S°(298 K)                | 2      | J/mol·K | thermoView headline  |
 * | thermo S° uncertainty           | 2      | J/mol·K | thermoView facts     |
 * | statmech frequency scale factor | 4      | (none)  | statmechView facts   |
 * | transport collision diameter σ  | 3      | Å       | transportView headline |
 * | transport well depth ε/k        | 1      | K       | transportView headline |
 * | transport dipole moment         | 3      | D       | transportView facts  |
 * | calculation electronic energy   | 6      | hartree | calculationView headline |
 * | kinetics activation energy Ea   | 2      | kJ/mol  | kineticsView headline |
 * | kinetics pre-exponential A      | 3 sf (scientific) | per A_units enum | kineticsView headline |
 * | kinetics temperature exponent n | 4 sf (scientific) | (none)  | kineticsView facts   |
 *
 * `kinetics_a`'s unit is not fixed here: `landing.py` resolves it per record
 * from the `A_units` enum token via an `A_UNITS` typeset-unit table, which
 * this port intentionally does not carry forward — no frontend surface
 * currently renders kinetics, so there is nothing to adopt it into yet (see
 * the "Do NOT port" boundary this brief drew around enum→prose tables); a
 * caller that gains a kinetics view should pass the resolved unit string in
 * explicitly via `formatQuantity("kinetics_a", value, unitString)`.
 */
export const QUANTITY_SPECS = {
    thermo_h298_kj_mol: { kind: "fixed", digits: 2, unit: "kJ/mol" },
    thermo_h298_uncertainty_kj_mol: { kind: "fixed", digits: 2, unit: "kJ/mol" },
    thermo_s298_j_mol_k: { kind: "fixed", digits: 2, unit: "J/mol·K" },
    thermo_s298_uncertainty_j_mol_k: { kind: "fixed", digits: 2, unit: "J/mol·K" },
    statmech_frequency_scale_factor: { kind: "fixed", digits: 4, unit: null },
    transport_sigma_angstrom: { kind: "fixed", digits: 3, unit: "Å" },
    transport_epsilon_over_k_k: { kind: "fixed", digits: 1, unit: "K" },
    transport_dipole_debye: { kind: "fixed", digits: 3, unit: "D" },
    calculation_electronic_energy_hartree: { kind: "fixed", digits: 6, unit: "hartree" },
    kinetics_ea_kj_mol: { kind: "fixed", digits: 2, unit: "kJ/mol" },
    kinetics_a: { kind: "scientific", digits: 3, unit: null },
    kinetics_n: { kind: "scientific", digits: 4, unit: null },
} as const satisfies Record<string, { kind: "fixed" | "scientific"; digits: number; unit: string | null }>

export type QuantitySpecKey = keyof typeof QUANTITY_SPECS

/**
 * The single dispatcher a component calls: look up digits/unit/kind for a
 * named quantity and format it. `unitOverride` exists only for
 * `kinetics_a`, whose unit is per-record (see the table note above); every
 * other spec's unit is fixed and `unitOverride` should be omitted.
 */
export function formatQuantity(
    key: QuantitySpecKey,
    value: number | null | undefined,
    unitOverride?: string | null,
): Quantity | null {
    const spec = QUANTITY_SPECS[key]
    const unit = unitOverride !== undefined ? unitOverride : spec.unit
    return spec.kind === "fixed" ? fixed(value, spec.digits, unit) : scientific(value, spec.digits, unit)
}
