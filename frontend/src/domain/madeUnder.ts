import { z } from "zod"

/**
 * "Made under": which rubric (or rule) judged an assessment, at which integer version, and whether that is still the
 * version in force. The wire shape is the backend's `MadeUnder` (`app/schemas/reads/scientific_assessment.py`); this
 * module is the one place its words are chosen, so every page says the same thing the same way.
 *
 * Four states, kept apart on purpose:
 * - `current`: made under the version in force, and the inputs were compared with today's and match.
 * - `inputs_unchecked`: made under the version in force, but this route does not compare the inputs. Nothing is
 *   claimed about them. A list or search read says this, never `current`.
 * - `stale`: an assessment exists, but it was made under another version of the rubric (or, with the version unchanged,
 *   from inputs that have changed since). It says what it was made under and that it has not been re-assessed.
 * - `unassessed`: nobody has assessed this record, so there is no rubric to name. Not a kind of stale.
 *
 * Version 0 means "stamped before the 2026-10 versioning reset". No registry holds 0, so it is never current; it is
 * worded as what it is and never shown as "v0".
 *
 * A stored machine review (the curator queue) is worded as a review: "Reviewed under ...", never "assessed".
 *
 * Nothing here says a record is preferred or best. The line names the rule that judged it and nothing about its merit.
 */
export const madeUnderSchema = z.object({
    rubric: z.string().nullable().optional(),
    assessed_version: z.number().nullable().optional(),
    current_rubric: z.string().nullable().optional(),
    current_version: z.number().nullable().optional(),
    // Read as plain strings: a state or reason this client has not been taught is worded neutrally, never a parse error.
    state: z.string(),
    stale_reason: z.string().nullable().optional(),
    // True only on a route that compares the inputs; null when there is nothing to compare.
    inputs_checked: z.boolean().nullable().optional(),
}).passthrough()

export type MadeUnder = z.infer<typeof madeUnderSchema>

/** What the page renders: a state for styling, an optional badge word, and the plain sentence. */
export type MadeUnderWording = {
    state: "current" | "stale" | "unassessed" | "inputs_unchecked" | "unknown"
    badge: string | null
    sentence: string
}

/** An assessment of a record, or the stored machine review a curator task came from. */
export type MadeUnderKind = "assessment" | "review"

/** Rubric names are snake_case identifiers (`computed_transition_state`); read them as words. */
export function rubricLabel(name: string): string {
    return name.replaceAll("_", " ")
}

/** The assessment was just computed under the rubric version in force (the live trust badge): current by construction. */
export function liveMadeUnder(rubric: string, version: number): MadeUnder {
    return {
        rubric,
        assessed_version: version,
        current_rubric: rubric,
        current_version: version,
        state: "current",
        inputs_checked: true,
    }
}

function rubricPhrase(name: string | null | undefined): string {
    return name ? `rubric ${rubricLabel(name)}` : "a rubric that is not named"
}

export function describeMadeUnder(m: MadeUnder, kind: MadeUnderKind = "assessment"): MadeUnderWording {
    const review = kind === "review"
    const did = review ? "Reviewed" : "Assessed"
    const notRedone = review
        ? "It has not been re-reviewed under the current version."
        : "It has not been re-assessed under the current version."
    if (m.state === "unassessed") {
        return { state: "unassessed", badge: "Not assessed", sentence: "Not yet assessed under any rubric." }
    }
    const assessed = m.assessed_version
    const current = m.current_version
    if (assessed == null || !m.rubric) {
        return { state: "unknown", badge: null, sentence: "The rubric this was assessed under is not recorded." }
    }
    if (m.state === "current") {
        return {
            state: "current",
            badge: null,
            sentence: `${did} under ${rubricPhrase(m.rubric)}, version ${assessed}, the current version.`,
        }
    }
    if (m.state === "inputs_unchecked") {
        const unchecked = review
            ? "Whether the evidence it saw has changed since was not checked on this page."
            : "Whether its inputs have changed since was not checked on this page."
        return {
            state: "inputs_unchecked",
            badge: null,
            sentence: `${did} under ${rubricPhrase(m.rubric)}, version ${assessed} (the current version). ${unchecked}`,
        }
    }
    if (m.state === "stale") {
        const currentRubric = m.current_rubric ?? m.rubric
        const currentNow = current == null
            ? "The current version is not available."
            : currentRubric === m.rubric
                ? `The current version is ${current}.`
                : `The rubric now in force for this record is ${rubricLabel(currentRubric)}, version ${current}.`
        if (m.stale_reason === "prompt_version") {
            return {
                state: "stale",
                badge: "Stale",
                sentence:
                    `${did} under ${rubricPhrase(m.rubric)}, version ${assessed}, with a review prompt that is no longer `
                    + "the current one. It has not been re-reviewed since.",
            }
        }
        if (assessed === 0) {
            return {
                state: "stale",
                badge: "Stale",
                sentence:
                    `${did} under ${rubricPhrase(m.rubric)} (stamped before the 2026-10 versioning reset). `
                    + `${currentNow} ${notRedone}`,
            }
        }
        if (m.stale_reason === "inputs_changed") {
            return {
                state: "stale",
                badge: "Stale",
                sentence:
                    `${did} under ${rubricPhrase(m.rubric)}, version ${assessed}, which is still the current version, `
                    + "but the inputs to this assessment have changed since. It has not been re-assessed since.",
            }
        }
        return {
            state: "stale",
            badge: "Stale",
            sentence: `${did} under ${rubricPhrase(m.rubric)}, version ${assessed}. ${currentNow} ${notRedone}`,
        }
    }
    return {
        state: "unknown",
        badge: null,
        sentence: `${did} under ${rubricPhrase(m.rubric)}, version ${assessed}.`,
    }
}
