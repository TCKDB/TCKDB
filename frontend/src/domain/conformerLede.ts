import { ledeText, ledeWord, type LedeText, type LedeWord } from "./ledeText"

/** "1 observation" or "3 observations": a count TCKDB composed. */
function counted(count: number, one: string, many: string): LedeWord {
    return ledeWord("text", { text: `${count} ${count === 1 ? one : many}` })
}

/** The line under a conformer group's title. */
export function groupLede(observations: number, calculations: number): LedeText {
    const found = counted(observations, "observation", "observations")
    const runs = counted(calculations, "calculation", "calculations")
    return ledeText`One 3D shape this molecule can take, deposited as ${found} and ${runs}.`
}

/** The line under a conformer observation's title. */
export function observationLede(): LedeText {
    return ledeText`One deposit of this conformer.`
}

/** The line under a geometry's title. */
export function geometryLede(): LedeText {
    return ledeText`One stored set of atom positions.`
}
