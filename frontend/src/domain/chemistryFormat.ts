/**
 * Chemistry text/typography helpers shared by the identifier-search results
 * list. These are a deliberate TypeScript port of the same rules
 * `backend/app/api/landing.py` already uses for its own hand-rolled search
 * results (`formulaNode`, `chargeText`, `SPIN_WORDS`, the `ABSENT` word) —
 * a second, differently-behaved implementation of "how does this project
 * write a formula/charge/spin" would be worse than the duplication of
 * having two files that each know the rule.
 *
 * `landing.py` is server-rendered vanilla JS and this is a React/TS
 * frontend, so the code cannot literally be shared — only the behaviour
 * can, and that is what is ported here, rule for rule.
 */

/** What the archive calls a missing value, project-wide (see `landing.py`'s `ABSENT`). */
export const ABSENT = "not recorded"

const MINUS = "−"

const SPIN_WORDS: Record<string, string> = {
    "1": "singlet",
    "2": "doublet",
    "3": "triplet",
    "4": "quartet",
    "5": "quintet",
    "6": "sextet",
    "7": "septet",
    "8": "octet",
}

export type FormulaToken = { element: string; count: string }

/**
 * Split a formula string into element/count pairs, the way `landing.py`'s
 * `formulaNode` does: match runs of `[A-Z][a-z]?[0-9]*`, then require the
 * matched parts to *round-trip* back to the original string exactly. A
 * formula that does not round-trip (an unexpected character, a case that
 * does not parse as element symbols) returns `null` so the caller can fall
 * back to printing the string exactly as it arrived — a formula rendered
 * wrongly is worse than a formula rendered plainly.
 */
export function formulaTokens(formula: string): FormulaToken[] | null {
    const parts = formula.match(/[A-Z][a-z]?[0-9]*/g)
    if (!parts || parts.join("") !== formula) return null
    return parts.map((part) => {
        const split = /^([A-Z][a-z]?)([0-9]*)$/.exec(part)
        // Unreachable given the regex above matched `part`, but keeps this total.
        if (!split) return { element: part, count: "" }
        return { element: split[1], count: split[2] }
    })
}

/**
 * A charge is a signed quantity and is written as one: the neutral case
 * stays a bare `"0"`, and a negative charge gets U+2212 (real minus), not
 * the hyphen a column of plain integers would leave there. Ported from
 * `landing.py`'s `chargeText`. Returns `null` only when there is no charge
 * to report at all (distinct from a charge of zero).
 */
export function chargeText(charge: number | null | undefined): string | null {
    if (charge === null || charge === undefined) return null
    if (charge > 0) return `+${charge}`
    if (charge < 0) return `${MINUS}${Math.abs(charge)}`
    return "0"
}

/**
 * Multiplicity is 2S+1, and the word ("doublet", "triplet"...) is what a
 * chemist reads first. Ported from `landing.py`'s `SPIN_WORDS` table.
 * `null` for a multiplicity outside the mapped range (the number itself
 * is still meaningful and the caller prints it) or when multiplicity is
 * absent.
 */
export function spinWord(multiplicity: number | null | undefined): string | null {
    if (multiplicity === null || multiplicity === undefined) return null
    return SPIN_WORDS[String(multiplicity)] ?? null
}

/** "charge 0", "charge +1", "charge not recorded" -- the row's charge context segment. */
export function chargeDisplay(charge: number | null | undefined): string {
    return chargeText(charge) ?? ABSENT
}

/**
 * "doublet (2)", "9" (an unmapped multiplicity, printed as the bare
 * number rather than hidden), or `ABSENT` when multiplicity itself is
 * missing -- matching `landing.py`'s `stateCell` fallback order exactly.
 */
export function spinDisplay(multiplicity: number | null | undefined): string {
    if (multiplicity === null || multiplicity === undefined) return ABSENT
    const word = spinWord(multiplicity)
    return word ? `${word} (${multiplicity})` : String(multiplicity)
}

/** "1 entry" / "4 entries". */
export function entryCountDisplay(entryCount: number): string {
    return `${entryCount} ${entryCount === 1 ? "entry" : "entries"}`
}
