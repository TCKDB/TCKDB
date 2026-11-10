import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { ledeText, ledeWord, type LedeText } from "./ledeText"

const SPIN_WORDS: Readonly<Record<string, string>> = { 1: "singlet", 2: "doublet", 3: "triplet", 4: "quartet", 5: "quintet", 6: "sextet" }
const STATE_WORDS: Readonly<Record<string, string>> = { ground: "ground state", excited: "excited state" }
const KIND_WORDS: Readonly<Record<string, string>> = { minimum: "an energy minimum", vdw_complex: "a van der Waals complex" }

/** The one sentence under the title, built from codes only. */
export function entryLede(entry: SpeciesEntryProjection): LedeText {
    const spin = ledeWord(String(entry.multiplicity), SPIN_WORDS)
    const state = ledeWord(entry.electronic_state_kind, STATE_WORDS)
    const kind = ledeWord(entry.species_entry_kind, KIND_WORDS)
    return ledeText`One version of this molecule: charge ${entry.charge}, ${spin}, ${state}, ${kind}. A molecule can have several versions.`
}
