import { ledeText, ledeWord, type LedeText, type LedeWord } from "./ledeText"
import { words } from "./provenanceFormat"

/** Text TCKDB composed from a number or a fixed word. */
function composed(text: string): LedeWord {
    return ledeWord("text", { text })
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`

/** The one sentence under a reaction entry's title, built from codes and counts only. */
export function reactionEntryLede(entry: { family?: string | null; reversible: boolean }, counts: { kinetics: number; transitionStates: number }): LedeText {
    const direction = composed(entry.reversible ? "reversible" : "not reversible")
    const family = composed(entry.family ? `family ${words(entry.family) ?? "not recorded"}` : "no family recorded")
    const kinetics = composed(plural(counts.kinetics, "rate record", "rate records"))
    const states = composed(plural(counts.transitionStates, "transition-state entry", "transition-state entries"))
    return ledeText`One deposited version of this reaction: ${direction}, ${family}, ${kinetics} and ${states}.`
}

/** The sentence under a reaction's title on the page that lists its versions. */
export function reactionOverviewLede(total: number): LedeText {
    const versions = composed(plural(total, "deposited version", "deposited versions"))
    return ledeText`${versions}; none is chosen for you.`
}
