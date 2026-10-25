// ---------------------------------------------------------------------------
// The one-line level-of-theory / software summary beside each sibling saddle
// point on a transition-state entry page.
//
// A sibling can carry calculations at several stages (opt, freq, sp, ...) at
// different levels of theory. A summary line has room for one of them, so
// WHICH one is shown has to be stated, not implied. The stage is chosen by a
// fixed, documented rule below (a rule about stages, not about methods: no
// method or level of theory outranks another), the line names the stage it
// came from, and level and software are read from that SAME calculation so the
// two are never mixed from different ones. When the sibling has several
// different levels at that stage, the line says so instead of showing one.
// ---------------------------------------------------------------------------

/** Stages in the order a sibling's line looks for one to summarise: the final energy stage first. */
export const SIBLING_SUMMARY_STAGE_ORDER = ["sp", "irc", "freq", "opt", "path_search"] as const

export type SiblingCalculationLike = {
    type: string
    level_of_theory?: unknown
    software_release?: unknown
}

export type SiblingStageSummary<T extends SiblingCalculationLike> = {
    stage: string
    /** The calculation the displayed level and software are read from. */
    calculation: T
    /** How many of the sibling's calculations are at this stage. */
    stageCalculationCount: number
    /** How many DIFFERENT levels of theory those calculations use (0 when none records one). */
    distinctLevelCount: number
    /** How many DIFFERENT software releases those calculations use (0 when none records one). */
    distinctSoftwareCount: number
    /**
     * The calculations at this stage do not all agree on level of theory or software (they differ, or some record one
     * and some do not), so no single calculation's level and software describe them. A line must not show one.
     */
    varies: boolean
}

function levelKey(level: unknown): string | null {
    return level === null || level === undefined ? null : JSON.stringify(level)
}

/** The stage a sibling's line is read from, or `null` when the sibling has no calculation rows at all. */
export function summariseSiblingStage<T extends SiblingCalculationLike>(
    calculations: readonly T[],
): SiblingStageSummary<T> | null {
    const stages = [...SIBLING_SUMMARY_STAGE_ORDER, ...calculations.map((calculation) => calculation.type)]
    for (const stage of stages) {
        const atStage = calculations.filter((calculation) => calculation.type === stage)
        if (atStage.length === 0) continue
        const levelKeys = atStage.map((calculation) => levelKey(calculation.level_of_theory))
        const softwareKeys = atStage.map((calculation) => levelKey(calculation.software_release))
        const levels = new Set(levelKeys.filter((key) => key !== null))
        const software = new Set(softwareKeys.filter((key) => key !== null))
        const mixedPresence = (keys: Array<string | null>) => keys.some((key) => key === null) && keys.some((key) => key !== null)
        const varies = levels.size > 1 || software.size > 1 || mixedPresence(levelKeys) || mixedPresence(softwareKeys)
        return {
            stage,
            calculation: atStage[0],
            stageCalculationCount: atStage.length,
            distinctLevelCount: levels.size,
            distinctSoftwareCount: software.size,
            varies,
        }
    }
    return null
}

export const SIBLING_SUMMARY_STAGE_NOTE =
    "Each line is read from one calculation of that saddle point, at the stage named on the line "
    + "(the single-point stage where there is one). It does not describe the sibling's other stages."
