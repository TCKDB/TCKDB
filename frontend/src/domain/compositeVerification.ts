import type { CompositeEnergyVerification } from "../api/scientificSchemas"
import { words } from "./provenanceFormat"

// ---------------------------------------------------------------------------
// How far a composite energy (CBS-QB3, G4, a CCSD(T)/CBS extrapolation, a
// focal-point sum) has been checked -- ADR 0021, P7a. The server computes
// `composite_energy_verification` on every read; this module only words it.
//
// Two rules decide the shape of everything below:
//  - A CONTRADICTION is never softened. `recompute_mismatch`, and
//    `program_reported` with reason `log_mismatch`/`log_method_mismatch`,
//    are the cases where the archive holds evidence AGAINST the number.
//    They get their own tone and are never worded as a mere absence of
//    confirmation.
//  - Wording explains the CHECK, not the chemistry: the reader is a
//    chemist and does not need CBS explained, only what was compared with
//    what.
// ---------------------------------------------------------------------------

export type VerificationTone = "confirmed" | "neutral" | "contradiction"

export interface VerificationView {
    /** The server's state token, verbatim (a hook for tests and styling). */
    state: string
    tone: VerificationTone
    /** Short pill/lead text. */
    headline: string
    /** One plain sentence saying what was compared, or `null`. */
    detail: string | null
    /** Hartree figures to show with the detail, only when the server sent them. */
    difference: string | null
    tolerance: string | null
}

export const REASON_LOG_MISMATCH = "log_mismatch"
export const REASON_LOG_METHOD_MISMATCH = "log_method_mismatch"

/** Plain-language form of each stable reason token the server can name. An
 *  unknown token is transcribed with `words()`, never translated: inventing
 *  an expansion for a token this page has not met would invent a claim. */
const REASON_TEXT: Record<string, string> = {
    input_energy_not_stated: "one of the calculations it is built from has no energy stored",
    component_not_stated: "one of its inputs is missing an energy component the recipe needs",
    correlation_convention_undeterminable: "it cannot be determined whether an input's correlation energy includes (T)",
    extrapolation_degenerate: "the extrapolation cannot be evaluated for these inputs",
    no_total_deposited: "no total energy was stated",
    no_energy_stated: "no energy was stated",
    no_result_stated: "no composite result is recorded",
    scheme_not_bound: "its level of theory is not tied to a recipe, so there is nothing to recompute",
    log_did_not_confirm: "an output log was attached, but it could not confirm the number",
}

export function reasonText(reason: string | null | undefined): string | null {
    if (!reason) return null
    return REASON_TEXT[reason] ?? words(reason)
}

/** A contradiction: the archive holds evidence against the stated number. */
export function isContradiction(verification: Pick<CompositeEnergyVerification, "state" | "reason"> | null | undefined): boolean {
    if (!verification) return false
    if (verification.state === "recompute_mismatch") return true
    return verification.state === "program_reported"
        && (verification.reason === REASON_LOG_MISMATCH || verification.reason === REASON_LOG_METHOD_MISMATCH)
}

/** Hartree figures are tiny by design (a rounding-sized gap); scientific
 *  notation keeps the magnitude readable and never rounds one to "0". */
export function formatHartree(value: number): string {
    if (value === 0) return "0 hartree"
    return `${Math.abs(value) < 0.001 ? value.toExponential(2) : value.toFixed(6)} hartree`
}

function directionOf(difference: number | null | undefined): string | null {
    if (difference === null || difference === undefined) return null
    if (difference === 0) return "equal to"
    return difference > 0 ? "above" : "below"
}

export function verificationView(verification: CompositeEnergyVerification): VerificationView {
    const { state, reason } = verification
    const difference = verification.difference_hartree ?? null
    const tolerance = verification.tolerance_hartree ?? null
    const differenceText = difference === null ? null : formatHartree(Math.abs(difference))
    const toleranceText = tolerance === null ? null : formatHartree(tolerance)
    const base = { state, difference: differenceText, tolerance: toleranceText }

    switch (state) {
        case "recomputed":
            return {
                ...base,
                tone: "confirmed",
                headline: "Recomputed from its inputs",
                detail: "The stored input energies, run through the recipe just now, give the stated total.",
            }
        case "recompute_mismatch": {
            const where = directionOf(difference)
            return {
                ...base,
                tone: "contradiction",
                headline: "Does not match its inputs",
                detail: where
                    ? `The stated total is ${where} what the stored input energies give when run through the recipe now, by more than the tolerance.`
                    : "The stated total differs from what the stored input energies give when run through the recipe now, by more than the tolerance.",
            }
        }
        case "log_reconciled":
            return {
                ...base,
                tone: "confirmed",
                headline: "Confirmed against the output log",
                detail: "The output log attached at upload gave the number that was stated.",
            }
        case "program_reported":
            if (reason === REASON_LOG_MISMATCH) {
                return {
                    ...base,
                    tone: "contradiction",
                    headline: "Output log disagrees",
                    detail: "The output log attached at upload gave a different number from the one stated.",
                }
            }
            if (reason === REASON_LOG_METHOD_MISMATCH) {
                return {
                    ...base,
                    tone: "contradiction",
                    headline: "Output log is a different method",
                    detail: "The output log attached at upload is from a different method than the level of theory stated.",
                }
            }
            return {
                ...base,
                tone: "neutral",
                headline: "Reported by the program",
                detail: reason ? `Not confirmed: ${reasonText(reason)}.` : "No output log was attached to confirm it.",
            }
        case "unverifiable": {
            const why = reasonText(reason)
            return {
                ...base,
                tone: "neutral",
                headline: "Cannot be checked",
                detail: why ? `This cannot be checked because ${why}.` : "This cannot be checked, and no reason was given.",
            }
        }
        default:
            return {
                ...base,
                tone: "neutral",
                headline: words(state) ?? "Unrecognised state",
                detail: "This verification state is not one this page knows how to describe.",
            }
    }
}

/** Plain wording for `legacy_composite_shape`; `null` for an absent value. An unknown token
 *  is transcribed, never invented. */
export function legacyShapeText(shape: string | null | undefined): string | null {
    if (!shape) return null
    switch (shape) {
        case "composite_role_on_non_composite_calculation":
            return "Deposited in the older shape: a calculation that is not a composite calculation is linked under the composite role."
        case "named_method_level_on_non_composite_calculation":
            return "Deposited in the older shape: an ordinary optimisation, frequency or single-point calculation ran at the level of a named composite method, so the method's one printed energy is not recorded as a composite calculation."
        default:
            return `Deposited in an older shape (${words(shape)}).`
    }
}
