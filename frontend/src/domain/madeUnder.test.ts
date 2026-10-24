import { describe, expect, it } from "vitest"
import { describeMadeUnder, liveMadeUnder, rubricLabel } from "./madeUnder"

const base = { rubric: "tckdb_reproducibility", current_rubric: "tckdb_reproducibility" }

describe("describeMadeUnder", () => {
    it("current: names the rubric and its version, and no badge", () => {
        const w = describeMadeUnder({ ...base, assessed_version: 2, current_version: 2, state: "current", inputs_checked: true })
        expect(w.state).toBe("current")
        expect(w.badge).toBeNull()
        expect(w.sentence).toBe("Assessed under rubric tckdb reproducibility, version 2, the current version.")
    })

    it("inputs_unchecked: the rubric is the current one and nothing is claimed about the inputs", () => {
        const w = describeMadeUnder({ ...base, assessed_version: 1, current_version: 1, state: "inputs_unchecked", inputs_checked: false })
        expect(w.state).toBe("inputs_unchecked")
        expect(w.badge).toBeNull()
        expect(w.sentence).toBe(
            "Assessed under rubric tckdb reproducibility, version 1 (the current version). "
            + "Whether its inputs have changed since was not checked on this page.",
        )
    })

    it("inputs_unchecked under the curated profile says so", () => {
        const w = describeMadeUnder({
            ...base, assessed_version: 1, current_version: 1, state: "inputs_unchecked", inputs_checked: false,
            inputs_unchecked_reason: "curated_profile",
        })
        expect(w.sentence).toBe(
            "Assessed under rubric tckdb reproducibility, version 1 (the current version). "
            + "Whether its inputs have changed since is not checked under the curated profile.",
        )
    })

    it("stale: states the assessed version, the current version, a Stale badge and that it has not been re-assessed", () => {
        const w = describeMadeUnder({
            ...base, assessed_version: 1, current_version: 3, state: "stale", stale_reason: "rubric_version",
        })
        expect(w.state).toBe("stale")
        expect(w.badge).toBe("Stale")
        expect(w.sentence).toBe(
            "Assessed under rubric tckdb reproducibility, version 1. The current version is 3. "
            + "It has not been re-assessed under the current version.",
        )
    })

    it("never says re-assessment is pending: nothing schedules one", () => {
        const w = describeMadeUnder({ ...base, assessed_version: 1, current_version: 2, state: "stale" })
        expect(w.sentence).not.toMatch(/pending/i)
    })

    it("version 0: worded as stamped before the 2026-10 versioning reset, never as a raw v0, and still stale", () => {
        const w = describeMadeUnder({
            ...base, assessed_version: 0, current_version: 1, state: "stale", stale_reason: "rubric_version",
        })
        expect(w.state).toBe("stale")
        expect(w.badge).toBe("Stale")
        expect(w.sentence).toBe(
            "Assessed under rubric tckdb reproducibility (stamped before the 2026-10 versioning reset). "
            + "The current version is 1. It has not been re-assessed under the current version.",
        )
        expect(w.sentence).not.toMatch(/\bv0\b|version 0|numbered/)
    })

    it("stale because its inputs changed under the same version: says so and does not claim a version mismatch", () => {
        const w = describeMadeUnder({
            ...base, assessed_version: 1, current_version: 1, state: "stale", stale_reason: "inputs_changed",
        })
        expect(w.badge).toBe("Stale")
        expect(w.sentence).toContain("still the current version")
        expect(w.sentence).toContain("the inputs to this assessment have changed since")
        expect(w.sentence.endsWith("It has not been re-assessed since.")).toBe(true)
        expect(w.sentence).not.toContain("The current version is")
    })

    it("a different rubric now in force is named, not passed off as a version change of the same rubric", () => {
        const w = describeMadeUnder({
            rubric: "old_rubric", assessed_version: 2, current_rubric: "new_rubric", current_version: 1, state: "stale",
            stale_reason: "rubric_version",
        })
        expect(w.sentence).toContain("rubric old rubric, version 2")
        expect(w.sentence).toContain("now in force for this record is new rubric, version 1")
    })

    it("unassessed is its own state with its own badge, and is not worded as stale", () => {
        const w = describeMadeUnder({ state: "unassessed", inputs_checked: null })
        expect(w.state).toBe("unassessed")
        expect(w.badge).toBe("Not assessed")
        expect(w.sentence).not.toMatch(/stale|re-assessed/i)
    })

    it("a stored machine review is worded as a review, in every state", () => {
        const stale = describeMadeUnder(
            { rubric: "computed_kinetics", assessed_version: 0, current_rubric: "computed_kinetics", current_version: 1, state: "stale" },
            "review",
        )
        expect(stale.sentence).toBe(
            "Reviewed under rubric computed kinetics (stamped before the 2026-10 versioning reset). "
            + "The current version is 1. It has not been re-reviewed under the current version.",
        )
        const unchecked = describeMadeUnder(
            { rubric: "computed_kinetics", assessed_version: 1, current_version: 1, state: "inputs_unchecked" },
            "review",
        )
        expect(unchecked.sentence).toBe(
            "Reviewed under rubric computed kinetics, version 1 (the current version). "
            + "Whether the evidence it saw has changed since was not checked on this page.",
        )
        const prompt = describeMadeUnder(
            { rubric: "computed_kinetics", assessed_version: 1, current_version: 1, state: "stale", stale_reason: "prompt_version" },
            "review",
        )
        expect(prompt.sentence).toContain("review prompt that is no longer the current one")
        for (const w of [stale, unchecked, prompt]) expect(w.sentence).not.toMatch(/assess/i)
    })

    it("an unknown state is worded neutrally and never throws", () => {
        const w = describeMadeUnder({ ...base, assessed_version: 4, current_version: 4, state: "some_future_state" })
        expect(w.state).toBe("unknown")
        expect(w.sentence).toBe("Assessed under rubric tckdb reproducibility, version 4.")
    })

    it("never implies the record is preferred or best", () => {
        const words = [
            describeMadeUnder({ ...base, assessed_version: 2, current_version: 2, state: "current" }),
            describeMadeUnder({ ...base, assessed_version: 1, current_version: 1, state: "inputs_unchecked" }),
            describeMadeUnder({ ...base, assessed_version: 0, current_version: 2, state: "stale" }),
            describeMadeUnder({ state: "unassessed" }),
        ].map((w) => w.sentence).join(" ")
        expect(words).not.toMatch(/best|prefer|recommend|top|ranked/i)
    })

    it("liveMadeUnder is current by construction", () => {
        expect(liveMadeUnder("computed_transition_state", 2)).toMatchObject({ state: "current", assessed_version: 2, current_version: 2 })
    })

    it("rubricLabel reads a snake_case name as words", () => {
        expect(rubricLabel("computed_transition_state")).toBe("computed transition state")
    })
})
