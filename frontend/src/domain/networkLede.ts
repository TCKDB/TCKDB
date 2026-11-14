import { ledeText, ledeWord, type LedeText, type LedeWord } from "./ledeText"

/** Text TCKDB composed from a number or a fixed word. */
function composed(text: string): LedeWord {
    return ledeWord("text", { text })
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`

/** The one sentence under a network's title, built from counts only. */
export function networkLede(counts: { states: number; channels: number; rates: number }): LedeText {
    const states = composed(plural(counts.states, "state", "states"))
    const channels = composed(plural(counts.channels, "channel", "channels"))
    const rates = composed(plural(counts.rates, "rate record", "rate records"))
    return ledeText`${states} joined by ${channels}, with ${rates}.`
}

/** The one sentence about the energies a network's solve carries. */
export const NETWORK_ENERGY_LEDE = "Energies are electronic only, relative to the lowest state; they are not free energies."
