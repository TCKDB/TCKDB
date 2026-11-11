import type { TransitionStateEntryRecord, ValidationEvidence } from "../api/transitionStateEntryApi"

export type CheckKey = "imaginary_mode" | "irc" | "energy_ordering"

export type CheckLink = { to: string; text: string }

export type CheckRow = {
    key: CheckKey
    label: string
    /** `recorded`: passed or failed was deposited; `not_recorded`: none was; `unknown`: a token this page was not taught. */
    status: "recorded" | "not_recorded" | "unknown"
    /** The recorded outcome in words, or the absence; for `unknown`, the sentence that follows the token. */
    outcome: string
    /** The token itself, only for `unknown`. */
    token?: string
    /** A fact the entry does state that is not the check itself. */
    note?: string
    /** The check's own calculations; empty unless the check was recorded. */
    evidence: CheckLink[]
}

/** The server's whole vocabulary for a validation token. */
const OUTCOMES: Readonly<Record<string, { recorded: boolean; outcome: string }>> = {
    present: { recorded: true, outcome: "Passed" },
    failed: { recorded: true, outcome: "Did not pass" },
    absent: { recorded: false, outcome: "No check of this kind was deposited" },
}

export const UNKNOWN_OUTCOME = "outcome not known to this page"
export const NOT_SERVED_OUTCOME = "Not served by this deployment"

function outcomeOf(token: unknown): Pick<CheckRow, "status" | "outcome" | "token"> {
    if (typeof token === "string" && Object.hasOwn(OUTCOMES, token)) {
        const known = OUTCOMES[token]
        return { status: known.recorded ? "recorded" : "not_recorded", outcome: known.outcome }
    }
    // A missing token is a deployment that does not serve it, which says nothing about what was deposited.
    if (token === undefined || token === null) return { status: "not_recorded", outcome: NOT_SERVED_OUTCOME }
    return { status: "unknown", outcome: UNKNOWN_OUTCOME, token: String(token) }
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`

function participantWords(participant: string): string {
    if (participant === "ts") return "transition state"
    const match = /^(reactant|product):(\d+)$/.exec(participant)
    // The server numbers its participants from 1.
    return match ? `${match[1]} ${Number(match[2])}` : "a compared point"
}

/** Links whose text repeats get their calculation ref added, so no two links share a name. */
function distinctNames(links: CheckLink[]): CheckLink[] {
    const counts = new Map<string, number>()
    for (const link of links) counts.set(link.text, (counts.get(link.text) ?? 0) + 1)
    return links.map((link) => ((counts.get(link.text) ?? 0) > 1 ? { ...link, text: `${link.text} ${link.to.split("/").pop()}` } : link))
}

function reconstructionLinks(records: ValidationEvidence[], text: string): CheckLink[] {
    const refs = [...new Set(records.flatMap((record) => (record.reconstruction_calculation_ref ? [record.reconstruction_calculation_ref] : [])))]
    return distinctNames(refs.map((ref) => ({ to: `/calculations/${ref}`, text })))
}

function comparedEnergyLinks(records: ValidationEvidence[]): CheckLink[] {
    const seen = new Set<string>()
    const links: CheckLink[] = []
    for (const record of records) {
        for (const energy of record.compared_energies ?? []) {
            const ref = energy.source_calculation_ref
            if (!ref || seen.has(ref)) continue
            seen.add(ref)
            links.push({ to: `/calculations/${ref}`, text: `Energy of ${participantWords(energy.participant)}` })
        }
    }
    return distinctNames(links)
}

/**
 * The three checks a transition state is held to, each as the entry's own `validation` token says: recorded as passed or
 * failed, or not recorded. No outcome is worked out from other fields, and a check's evidence links are the calculations
 * its own deposited record names.
 */
export function transitionStateChecks(
    record: Pick<TransitionStateEntryRecord, "validation" | "saddle_point" | "validation_evidence">,
): CheckRow[] {
    const validation = record.validation
    const own = (kind: string) => (record.validation_evidence ?? []).filter((item) => item.kind === kind)
    const imaginary = outcomeOf(validation.imaginary_mode)
    const irc = outcomeOf(validation.irc)
    const energy = outcomeOf(validation.energy_ordering)
    const recorded = (row: Pick<CheckRow, "status">, links: CheckLink[]) => (row.status === "recorded" ? links : [])
    const saddle = record.saddle_point
    const reported = saddle?.n_imag != null
        ? `The frequency calculation reports ${plural(saddle.n_imag, "imaginary frequency", "imaginary frequencies")}. That is a result, not a recorded check.`
        : undefined
    return [
        {
            key: "imaginary_mode",
            label: "One imaginary frequency",
            ...imaginary,
            ...(imaginary.status === "not_recorded" && reported ? { note: reported } : {}),
            evidence: recorded(imaginary, reconstructionLinks(own("imaginary_mode"), "Frequency calculation")),
        },
        {
            key: "irc",
            label: "IRC connects reactants and products",
            ...irc,
            evidence: recorded(irc, reconstructionLinks(own("irc"), "IRC calculation")),
        },
        {
            key: "energy_ordering",
            label: "Energy ordering (transition state above both sides)",
            ...energy,
            evidence: recorded(energy, comparedEnergyLinks(own("energy_ordering"))),
        },
    ]
}
