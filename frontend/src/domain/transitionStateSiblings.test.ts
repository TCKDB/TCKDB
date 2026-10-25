import { describe, expect, it } from "vitest"
import { summariseSiblingStage } from "./transitionStateSiblings"

const calc = (type: string, level: string | null) => ({ type, level_of_theory: level === null ? null : { display: level } })

describe("summariseSiblingStage", () => {
    it("reads the sp stage when there is one, whatever order the calculations came in", () => {
        const summary = summariseSiblingStage([calc("opt", "a"), calc("sp", "b"), calc("freq", "a")])
        expect(summary?.stage).toBe("sp")
        expect(summary?.calculation.level_of_theory).toEqual({ display: "b" })
    })

    it("falls through irc, freq, opt, path_search in that order", () => {
        expect(summariseSiblingStage([calc("opt", "a"), calc("freq", "a")])?.stage).toBe("freq")
        expect(summariseSiblingStage([calc("opt", "a"), calc("irc", "a"), calc("freq", "a")])?.stage).toBe("irc")
        expect(summariseSiblingStage([calc("path_search", "a"), calc("opt", "a")])?.stage).toBe("opt")
    })

    it("reads a stage the order does not know, rather than reporting nothing", () => {
        expect(summariseSiblingStage([calc("scan", "a")])?.stage).toBe("scan")
    })

    it("counts the different levels at the chosen stage, so a caller can refuse to pick one", () => {
        const summary = summariseSiblingStage([calc("sp", "a"), calc("sp", "b"), calc("sp", "a"), calc("opt", "c")])
        expect(summary?.stageCalculationCount).toBe(3)
        expect(summary?.distinctLevelCount).toBe(2)
    })

    it("does not count a calculation with no recorded level as a level", () => {
        const summary = summariseSiblingStage([calc("sp", null), calc("sp", "a")])
        expect(summary?.distinctLevelCount).toBe(1)
    })

    it("has nothing to summarise without calculations", () => {
        expect(summariseSiblingStage([])).toBeNull()
    })
})
