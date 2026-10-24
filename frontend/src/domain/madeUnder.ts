import { z } from "zod"

/**
 * "Made under": which rubric (or rule) judged an assessment, at which integer version, and whether that is still the
 * version in force. The wire shape is the backend's `MadeUnder` (`app/schemas/reads/scientific_assessment.py`); this
 * module is the one place its words are chosen, so every page says the same thing the same way.
 *
 * Three states, kept apart on purpose:
 * - `current`: made under the version in force, on the inputs there are now.
 * - `stale`: an assessment exists, but it was made under another version of the rubric (or, with the version unchanged,
 *   before its inputs changed). It says what it was made under and that it has not been re-assessed under the current version.
 * - `unassessed`: nobody has assessed this record, so there is no rubric to name. Not a kind of stale.
 *
 * Version 0 means "stamped before the 2026-10 versioning reset". No registry holds 0, so it is never current; it is
 * worded as what it is and never shown as "v0".
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
    // False on a list: only the rubric name and version were compared, not the inputs the assessment was made from.
    inputs_checked: z.boolean().optional(),
}).passthrough()

export type MadeUnder = z.infer<typeof madeUnderSchema>

/** What the page renders: a state for styling, an optional badge word, and the plain sentence. */
export type MadeUnderWording = {
    state: "current" | "stale" | "unassessed" | "unknown"
    badge: string | null
    sentence: string
}

/** Rubric names are snake_case identifiers (`computed_transition_state`); read them as words. */
export function rubricLabel(name: string): string {
    return name.replaceAll("_", " ")
}

/** The assessment was just computed under the rubric version in force (the live trust badge): current by construction. */
export function liveMadeUnder(rubric: string, version: number): MadeUnder {
    return { rubric, assessed_version: version, current_rubric: rubric, current_version: version, state: "current" }
}

function rubricPhrase(name: string | null | undefined): string {
    return name ? `rubric ${rubricLabel(name)}` : "a rubric that is not named"
}

export function describeMadeUnder(m: MadeUnder): MadeUnderWording {
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
            sentence: `Assessed under ${rubricPhrase(m.rubric)}, version ${assessed}, the current version.`
                + (m.inputs_checked === false ? " Whether its inputs have changed since was not checked here." : ""),
        }
    }
    if (m.state === "stale") {
        const pending = "It has not been re-assessed under the current version."
        const currentRubric = m.current_rubric ?? m.rubric
        const currentNow = current == null
            ? "The current version is not available."
            : currentRubric === m.rubric
                ? `The current version is ${current}.`
                : `The rubric now in force for this record is ${rubricLabel(currentRubric)}, version ${current}.`
        if (assessed === 0) {
            return {
                state: "stale",
                badge: "Stale",
                sentence:
                    `Assessed under ${rubricPhrase(m.rubric)} before versions were numbered `
                    + `(stamped before the 2026-10 versioning reset). ${currentNow} ${pending}`,
            }
        }
        if (m.stale_reason === "inputs_changed") {
            return {
                state: "stale",
                badge: "Stale",
                sentence:
                    `Assessed under ${rubricPhrase(m.rubric)}, version ${assessed}, which is still the current version, `
                    + `but the inputs to this assessment have changed since. ${pending}`,
            }
        }
        return {
            state: "stale",
            badge: "Stale",
            sentence: `Assessed under ${rubricPhrase(m.rubric)}, version ${assessed}. ${currentNow} ${pending}`,
        }
    }
    return {
        state: "unknown",
        badge: null,
        sentence: `Assessed under ${rubricPhrase(m.rubric)}, version ${assessed}.`,
    }
}
