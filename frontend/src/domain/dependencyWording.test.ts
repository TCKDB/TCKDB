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
 *
 * Owner complaint (2026-09): the live graph on a real calculation drew
 * the arrow from an optimisation DOWN to its frequency child labelled
 * "RUN ON THIS GEOMETRY" -- phrased from the PARENT's point of view
 * (the parent's own geometry) even though the arrow itself is drawn
 * parent -> child. Every `edgeLabel` below now reads in the direction
 * of the arrow, source -> target, and names what the TARGET is (e.g.
 * "geometry for frequencies" sits on the freq_on arrow and names the
 * frequency calculation it points at, not the geometry it left from).
 * `childSentence`/`parentSentence` were re-checked against the actual
 * parent/child semantics in `backend/app/db/models/calculation.py`
 * (`CalculationDependency.parent_calculation_id` /
 * `.child_calculation_id`) -- not just reworded in place -- each
 * sentence names the calc it is template'd for correctly as parent or
 * child of the edge.
 *
 * Post-review (blocking): ground truth for parent/child is the ENFORCED
 * `_DEPENDENCY_ROLE_TO_PARENT_TYPE` constraint in
 * `backend/app/services/calculation_resolution.py`, cross-checked
 * against live deployed edges -- NOT `trust/rubrics.py`'s
 * `_TS_UPSTREAM_DEPENDENCY_ROLES` docstring, which is wrong for
 * `scan_parent` (describes the parent as "a scan", when the enforced
 * constraint and 73 deployed edges show the parent is the `opt` that
 * provided the geometry and the scan is the CHILD). `optimized_from`'s
 * parent is not always an `opt` either (`_OPTIMIZED_FROM_PARENT_TYPES`
 * also allows `path_search`), so its wording is type-neutral about the
 * parent.
 */
describe("DEPENDENCY_ROLE_WORDING — every one of the seven roles, pinned literally", () => {
    it("optimized_from — parent is the geometry source (opt OR path_search, per _OPTIMIZED_FROM_PARENT_TYPES -- neither wording may name its type), child is the optimisation that used it", () => {
        expect(DEPENDENCY_ROLE_WORDING.optimized_from).toEqual({
            childSentence: "This is the fine optimisation; its starting geometry came from {link}",
            parentSentence: "This calculation's geometry was the starting point for {link}",
            edgeLabel: "geometry for the optimisation",
        })
    })

    it("freq_on — parent is the opt that provided the geometry, child is the frequency calculation", () => {
        expect(DEPENDENCY_ROLE_WORDING.freq_on).toEqual({
            childSentence: "This frequency calculation was computed on the geometry from {link}",
            parentSentence: "Frequencies were computed on this geometry by {link}",
            edgeLabel: "geometry for frequencies",
        })
    })

    it("single_point_on — parent is the opt that provided the geometry, child is the single point", () => {
        expect(DEPENDENCY_ROLE_WORDING.single_point_on).toEqual({
            childSentence: "This single point was computed on the geometry from {link}",
            parentSentence: "The single point was computed on this geometry by {link}",
            edgeLabel: "geometry for single point",
        })
    })

    it("irc_start — parent is the opt that provided the geometry, child is the IRC", () => {
        expect(DEPENDENCY_ROLE_WORDING.irc_start).toEqual({
            childSentence: "This IRC started from the geometry of {link}",
            parentSentence: "{link} IRC started from this geometry",
            edgeLabel: "starting point for the IRC",
        })
    })

    it("irc_followup — parent's required type is `irc` (_DEPENDENCY_ROLE_TO_PARENT_TYPE); parent is the original IRC run, child is the follow-up IRC run that continues it", () => {
        expect(DEPENDENCY_ROLE_WORDING.irc_followup).toEqual({
            childSentence: "This is the IRC follow-up that continues {link}",
            parentSentence: "{link} is the IRC follow-up that continues this run",
            edgeLabel: "continued by the IRC follow-up",
        })
    })

    it("scan_parent — per the ENFORCED _DEPENDENCY_ROLE_TO_PARENT_TYPE constraint (parent type == opt) and the 73 deployed edges, the parent is the opt that provided the geometry, the child is the scan itself; rubrics.py's docstring describing the parent as 'a scan' is wrong for this role", () => {
        expect(DEPENDENCY_ROLE_WORDING.scan_parent).toEqual({
            childSentence: "This scan started from the geometry of {link}",
            parentSentence: "{link} is a scan started from this geometry",
            edgeLabel: "geometry for the scan",
        })
    })

    it("arkane_source — parent is the calc whose result fed Arkane, child is what drew it in", () => {
        expect(DEPENDENCY_ROLE_WORDING.arkane_source).toEqual({
            childSentence: "This used {link} as an Arkane source",
            parentSentence: "{link} used this as an Arkane source",
            edgeLabel: "source for Arkane",
        })
    })

    it("carries all seven CalculationDependencyRole values -- no more, no fewer", () => {
        expect(Object.keys(DEPENDENCY_ROLE_WORDING).sort()).toEqual(
            [
                "arkane_source",
                "freq_on",
                "irc_followup",
                "irc_start",
                "optimized_from",
                "scan_parent",
                "single_point_on",
            ].sort(),
        )
    })
})

/** Every role the backend enum can send is bespoke now (all seven), so
 * only a genuinely unrecognised role -- one this backend enum has never
 * shipped -- exercises the fallback path. Pinned literally through that
 * same fallback, so it stays honest about what a reader actually sees
 * for a future role this table has not caught up to yet. */
describe("fallback wording for a role with no bespoke entry, pinned literally", () => {
    it("some_future_role falls back to the raw, spaced role token everywhere", () => {
        expect(roleLabel("some_future_role")).toBe("some future role")
        expect(dependencyEdgeLabel("some_future_role")).toBe("some future role")
        expect(dependencyChildSentenceTemplate("some_future_role")).toBe("This — some future role — {link}")
        expect(dependencyParentSentenceTemplate("some_future_role")).toBe("{link} — some future role")
    })
})

describe("splitLinkTemplate", () => {
    it("splits a template's exact before/after text around {link}, for a real bespoke template", () => {
        expect(splitLinkTemplate("This is the fine optimisation; its starting geometry came from {link}")).toEqual({
            before: "This is the fine optimisation; its starting geometry came from ", after: "",
        })
        expect(splitLinkTemplate("{link} is a scan started from this geometry")).toEqual({
            before: "", after: " is a scan started from this geometry",
        })
    })

    it("splits a fallback template's em-dash wording the same way", () => {
        expect(splitLinkTemplate("This — some future role — {link}")).toEqual({
            before: "This — some future role — ", after: "",
        })
    })
})
