import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { chargeText, spinWord } from "./chemistryFormat"
import { ledeText, ledeWord, type LedeText, type LedeWord } from "./ledeText"
import { words } from "./provenanceFormat"

const STATE_WORDS: Readonly<Record<string, string>> = { ground: "ground state", excited: "excited state" }
const KIND_WORDS: Readonly<Record<string, string>> = { minimum: "an energy minimum", vdw_complex: "a van der Waals complex" }

/** TCKDB's wording for an API code; a code with no wording is spelled out from the code itself, never "not recorded". */
function codeWord(code: string, wording: Readonly<Record<string, string>>): LedeWord {
    return ledeWord(code, { ...wording, [code]: wording[code] ?? words(code) ?? code })
}

/** Text TCKDB composed from a number or a fixed word. */
function composed(text: string): LedeWord {
    return ledeWord("text", { text })
}

/** The one sentence under the title, built from codes only. */
export function entryLede(entry: SpeciesEntryProjection): LedeText {
    const charge = composed(`charge ${chargeText(entry.charge) ?? "not recorded"}`)
    const spin = composed(spinWord(entry.multiplicity) ?? `multiplicity ${entry.multiplicity}`)
    const state = codeWord(entry.electronic_state_kind, STATE_WORDS)
    const kind = codeWord(entry.species_entry_kind, KIND_WORDS)
    return ledeText`One version of this molecule: ${charge}, ${spin}, ${state}, ${kind}.`
}
