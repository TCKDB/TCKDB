import type { TransitionStateEntryRecord } from "../api/transitionStateEntryApi"

export type CheckKey = "imaginary_mode" | "irc" | "energy_ordering"

export type CheckRow = {
    key: CheckKey
    label: string
    recorded: boolean
    /** The recorded outcome in words, or the absence. */
    outcome: string
    /** A fact the entry does state that is not the check itself. */
    note?: string
    evidence?: { to: string; text: string }
}

type Calculation = NonNullable<TransitionStateEntryRecord["calculations"]>[number]

const OUTCOMES: Readonly<Record<string, string>> = {
    present: "Passed",
    failed: "Did not pass",
}

function outcomeOf(token: unknown): { recorded: boolean; outcome: string } {
    if (typeof token === "string" && Object.hasOwn(OUTCOMES, token)) return { recorded: true, outcome: OUTCOMES[token] }
    return { recorded: false, outcome: "No check of this kind was deposited" }
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`

/**
 * The three checks a transition state is held to, each as the entry's own `validation` token says: recorded as passed or
 * failed, or not recorded. A missing token is not recorded; no outcome is worked out from other fields.
 */
export function transitionStateChecks(
    record: Pick<TransitionStateEntryRecord, "validation" | "saddle_point">,
    ircCalculation: Calculation | null,
): CheckRow[] {
    const validation = record.validation
    const imaginary = outcomeOf(validation.imaginary_mode)
    const irc = outcomeOf(validation.irc)
    const energy = outcomeOf(validation.energy_ordering)
    const saddle = record.saddle_point
    const reported = saddle?.n_imag != null
        ? `The frequency calculation reports ${plural(saddle.n_imag, "imaginary frequency", "imaginary frequencies")}. That is a result, not a recorded check.`
        : undefined
    return [
        {
            key: "imaginary_mode",
            label: "One imaginary frequency",
            ...imaginary,
            ...(!imaginary.recorded && reported ? { note: reported } : {}),
            ...(saddle?.calculation_ref ? { evidence: { to: `/calculations/${saddle.calculation_ref}`, text: "Frequency calculation" } } : {}),
        },
        {
            key: "irc",
            label: "IRC connects reactants and products",
            ...irc,
            ...(ircCalculation ? { evidence: { to: `/calculations/${ircCalculation.calculation_ref}`, text: "IRC calculation" } } : {}),
        },
        {
            key: "energy_ordering",
            label: "Energy ordering (transition state above both sides)",
            ...energy,
            ...(energy.recorded ? { evidence: { to: "#energy-heading", text: "Energy hill on this page" } } : {}),
        },
    ]
}
