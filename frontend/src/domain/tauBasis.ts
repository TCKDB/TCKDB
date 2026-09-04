/**
 * ADR 0012's imaginary-mode noise floor (τ) is resolved from the freq
 * job's Hessian method, carried on the wire as `imaginary_mode_tau_basis`
 * (`calculationApi.ts`'s `resultsSchema.freq`). This module is the one
 * place that token vocabulary is translated to plain language for the
 * calculation detail page's freq Result block.
 *
 * Vocabulary, as of the 2026-09-04 owner decision:
 *
 * - `analytic_tight` / `analytic_default` / `finite_difference_gradient` /
 *   `finite_difference_energy`: the producing protocol's Hessian method
 *   was actually RECORDED.
 * - `protocol_not_recorded`: the method was never determined at all.
 * - `assumed_analytic_default` / `assumed_finite_difference_gradient` /
 *   `assumed_finite_difference_energy`: the method was NOT recorded, and
 *   TCKDB assumed the program's default for it -- same τ as the matching
 *   recorded counterpart, but the value is an assumption, not an
 *   observation. Absence describes the request; null describes the data;
 *   an assumption is neither, and must be visibly labelled as one.
 * - `null` / `undefined`: the row was never judged at all (τ itself is
 *   also null on that record) -- "not recorded", same as
 *   `protocol_not_recorded`.
 * - anything else: a token this build does not recognise. The archive can
 *   ship new vocabulary before this frontend is rebuilt against it: the
 *   raw token is shown rather than silently dropped or mislabelled.
 *
 * The word "assumed" appears in rendered text if and only if an
 * `assumed_*` basis is being rendered -- see `tauBasis.test.ts`'s
 * `describe("the word 'assumed'"...)` block, which checks both
 * directions.
 */

/** One entry per RECORDED basis: the plain-language method, and a short note for the τ row. */
const RECORDED: Record<string, { method: string; note: string }> = {
    analytic_tight: { method: "analytic", note: "analytic, tight convergence" },
    analytic_default: { method: "analytic", note: "analytic, default convergence" },
    finite_difference_gradient: { method: "numerical (from gradients)", note: "numerical, from gradients" },
    finite_difference_energy: { method: "numerical (from energies)", note: "numerical, from energies" },
}

/** Each `assumed_*` token names the recorded counterpart it borrows method/note text from. */
const ASSUMED_TO_RECORDED: Record<string, keyof typeof RECORDED> = {
    assumed_analytic_default: "analytic_default",
    assumed_finite_difference_gradient: "finite_difference_gradient",
    assumed_finite_difference_energy: "finite_difference_energy",
}

const NOT_RECORDED = "not recorded"

/** The `(assumed: ...)` suffix appended to the method label -- a visible word, never a bare asterisk. */
const ASSUMED_METHOD_SUFFIX = " (assumed: the program's default for this method)"

/** True only for the three `assumed_*` tokens -- the one predicate `CalculationDetailPage.tsx` needs to decide "is this row an assumption". */
export function isAssumedTauBasis(basis: string | null | undefined): boolean {
    return typeof basis === "string" && own(ASSUMED_TO_RECORDED, basis)
}

/**
 * Own-property lookup. A bare `TABLE[basis]` finds INHERITED keys too, so a
 * basis token of "constructor" or "toString" resolved to a function and the
 * label functions below threw on it -- contradicting their own promise never
 * to throw on an unrecognised token. The archive is unlikely to ship such a
 * token, but "unlikely" is not the contract these functions state.
 */
function own<T extends object>(table: T, key: string): key is Extract<keyof T, string> {
    return Object.prototype.hasOwnProperty.call(table, key)
}

/**
 * "Hessian method" row text. `analytic` / `numerical (from gradients)` /
 * `numerical (from energies)` for a recorded basis; the same text plus a
 * visible assumed-suffix for an `assumed_*` basis; "not recorded" for
 * `protocol_not_recorded` and for a null/undefined basis; the raw token,
 * unmodified, for anything this build does not recognise.
 */
export function hessianMethodLabel(basis: string | null | undefined): string {
    if (basis === null || basis === undefined || basis === "protocol_not_recorded") return NOT_RECORDED
    if (own(ASSUMED_TO_RECORDED, basis)) {
        return `${RECORDED[ASSUMED_TO_RECORDED[basis]].method}${ASSUMED_METHOD_SUFFIX}`
    }
    if (own(RECORDED, basis)) return RECORDED[basis].method
    return basis
}

/**
 * The short muted note that explains the basis under the "Noise floor τ"
 * row's numeric value ("analytic, tight convergence" / "assumed from the
 * program's default (numerical, from gradients)" / ...). Only meaningful
 * when `imaginary_mode_tau_cm1` itself is non-null -- the caller renders
 * "not recorded" for the whole row in that case and never calls this.
 * Kept total anyway (same null/unrecognised handling as
 * `hessianMethodLabel`) so it never throws on a basis/τ pairing this
 * build has not seen live.
 */
export function tauBasisNote(basis: string | null | undefined): string {
    if (basis === null || basis === undefined || basis === "protocol_not_recorded") return NOT_RECORDED
    if (own(ASSUMED_TO_RECORDED, basis)) {
        return `assumed from the program's default (${RECORDED[ASSUMED_TO_RECORDED[basis]].note})`
    }
    if (own(RECORDED, basis)) return RECORDED[basis].note
    return basis
}
