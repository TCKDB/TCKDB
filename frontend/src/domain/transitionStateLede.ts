import { ledeText, ledeWord, type LedeText, type LedeWord } from "./ledeText"
import { words } from "./provenanceFormat"

/** Text TCKDB composed from a number or a fixed word. */
function composed(text: string): LedeWord {
    return ledeWord("text", { text })
}

/** The one sentence under a transition state entry's title, built from codes and counts only. */
export function transitionStateEntryLede(entry: { status: string }, calculationCount: number): LedeText {
    const status = composed(words(entry.status) ?? "not recorded")
    const calculations = composed(`${calculationCount} ${calculationCount === 1 ? "calculation" : "calculations"}`)
    return ledeText`The saddle point of this reaction from one deposit: status ${status}, ${calculations}.`
}
