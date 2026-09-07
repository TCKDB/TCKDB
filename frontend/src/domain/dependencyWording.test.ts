import { describe, expect, it } from "vitest"
import {
    DEPENDENCY_ROLE_WORDING,
    dependencyChildSentenceTemplate,
    dependencyEdgeLabel,
    dependencyParentSentenceTemplate,
    roleLabel,
    splitLinkTemplate,
} from "./dependencyWording"

/**
 * Pins every `edgeLabel`/`childSentence`/`parentSentence` this table
 * produces as a LITERAL string, for all seven `CalculationDependencyRole`
 * values (`backend/app/db/models/common.py`). Review finding: the
 * component test (`CalculationDependencyGraph.test.tsx`) asserted
 * `dependencyEdgeLabel(role)` against the very table that defines it --
 * a wording change (e.g. "optimized from" -> "optimised from") stayed
 * green there, since both sides of the assertion move together. This
 * file is the one place that pins the ACTUAL WORDS, so a change to any
 * of them is a deliberate, visible diff here, not a silent pass-through.
 */
describe("DEPENDENCY_ROLE_WORDING — every bespoke role, pinned literally", () => {
    it("optimized_from", () => {
        expect(DEPENDENCY_ROLE_WORDING.optimized_from).toEqual({
            childSentence: "This was optimized from {link}",
            parentSentence: "{link} was optimized from this result",
            edgeLabel: "optimized from",
        })
    })

    it("freq_on", () => {
        expect(DEPENDENCY_ROLE_WORDING.freq_on).toEqual({
            childSentence: "This frequency calculation was run on the geometry from {link}",
            parentSentence: "{link} (frequency) was run on this geometry",
            edgeLabel: "run on this geometry",
        })
    })

    it("single_point_on", () => {
        expect(DEPENDENCY_ROLE_WORDING.single_point_on).toEqual({
            childSentence: "This single point was run on the geometry from {link}",
            parentSentence: "{link} single point was run on this geometry",
            edgeLabel: "run on this geometry",
        })
    })

    it("irc_start", () => {
        expect(DEPENDENCY_ROLE_WORDING.irc_start).toEqual({
            childSentence: "This IRC started from the geometry of {link}",
            parentSentence: "{link} IRC started from this geometry",
            edgeLabel: "IRC started from this geometry",
        })
    })

    it("carries exactly these four bespoke roles -- no more, no fewer", () => {
        expect(Object.keys(DEPENDENCY_ROLE_WORDING).sort()).toEqual(
            ["freq_on", "irc_start", "optimized_from", "single_point_on"].sort(),
        )
    })
})

/** The three `CalculationDependencyRole` values with NO bespoke entry
 * (`arkane_source`, `irc_followup`, `scan_parent`) -- never seen on the
 * live archive as of this writing, but a role the backend enum can send.
 * Pinned literally through the SAME fallback path a genuinely unknown
 * role takes, so both stay honest about what a reader actually sees. */
describe("fallback wording for roles with no bespoke entry, pinned literally", () => {
    it.each([
        ["arkane_source", "arkane source"],
        ["irc_followup", "irc followup"],
        ["scan_parent", "scan parent"],
    ])("%s falls back to the raw, spaced role token everywhere", (role, spaced) => {
        expect(roleLabel(role)).toBe(spaced)
        expect(dependencyEdgeLabel(role)).toBe(spaced)
        expect(dependencyChildSentenceTemplate(role)).toBe(`This — ${spaced} — {link}`)
        expect(dependencyParentSentenceTemplate(role)).toBe(`{link} — ${spaced}`)
    })
})

describe("splitLinkTemplate", () => {
    it("splits a template's exact before/after text around {link}, for a real bespoke template", () => {
        expect(splitLinkTemplate("This was optimized from {link}")).toEqual({
            before: "This was optimized from ", after: "",
        })
        expect(splitLinkTemplate("{link} was optimized from this result")).toEqual({
            before: "", after: " was optimized from this result",
        })
    })

    it("splits a fallback template's em-dash wording the same way", () => {
        expect(splitLinkTemplate("This — arkane source — {link}")).toEqual({
            before: "This — arkane source — ", after: "",
        })
    })
})
