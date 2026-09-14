import { describe, expect, it } from "vitest"
import {
    ALLOWED_TRANSITIONS,
    ALL_STATUSES,
    allowedTransitions,
    statusClass,
    statusLabel,
    statusMeaning,
    type RecordReviewStatus,
} from "./recordReview"

/**
 * `ALLOWED_TRANSITIONS` mirrors `_ALLOWED_TRANSITIONS` in
 * `backend/app/services/record_review.py`, and this suite can only check
 * it against itself.
 *
 * What these tests DO prove: the table is internally coherent, every
 * status is accounted for, and the three transitions the policy
 * deliberately withholds are absent.
 *
 * What they do NOT prove: that the backend still agrees. Reading the
 * Python was tried for the record-type table in #483 and abandoned --
 * vite refuses to load a file outside its root, and widening
 * `server.fs.allow` would let the dev server (which binds 0.0.0.0) serve
 * backend source over the network. Task #255 covers putting that guard
 * where it belongs.
 *
 * Drift is survivable here in a way it is not for a route table: the
 * server refuses a transition it does not allow, and
 * `ReviewQueuePage.test.tsx` pins that the refusal is shown verbatim. A
 * drifted copy costs a curator one failed click and an accurate message,
 * not a wrong write.
 */

/** The three the backend withholds so a reversal is recorded as a re-review. */
const DELIBERATELY_WITHHELD: ReadonlyArray<[RecordReviewStatus, RecordReviewStatus]> = [
    ["approved", "rejected"],
    ["rejected", "approved"],
    ["deprecated", "rejected"],
]

describe("the transition table is internally coherent", () => {
    it("has an entry for every status", () => {
        expect(Object.keys(ALLOWED_TRANSITIONS).sort()).toEqual([...ALL_STATUSES].sort())
    })

    it("names only real statuses as targets", () => {
        for (const from of ALL_STATUSES) {
            for (const to of allowedTransitions(from)) {
                expect(ALL_STATUSES).toContain(to)
            }
        }
    })

    it("never offers a transition to the state already held", () => {
        for (const from of ALL_STATUSES) {
            expect(allowedTransitions(from)).not.toContain(from)
        }
    })

    it("leaves every status with somewhere to go", () => {
        // A status with no transitions would render a row a curator can
        // see and never act on. None of the five is terminal today.
        for (const from of ALL_STATUSES) {
            expect(allowedTransitions(from).length).toBeGreaterThan(0)
        }
    })

    it.each(DELIBERATELY_WITHHELD)(
        "does not offer %s -> %s, which must route through under_review",
        (from, to) => {
            // Not an oversight: reversing a judgement in one step would
            // leave no record that it was re-reviewed.
            expect(allowedTransitions(from)).not.toContain(to)
        },
    )

    it("reaches under_review from every other status", () => {
        // The re-review route has to be available from everywhere, or the
        // withheld transitions above would be dead ends rather than
        // detours.
        for (const from of ALL_STATUSES) {
            if (from === "under_review") continue
            expect(allowedTransitions(from)).toContain("under_review")
        }
    })
})

describe("what the page tells a curator about each status", () => {
    it.each(ALL_STATUSES)("%s has a label that is not the raw token", (status) => {
        const label = statusLabel(status)
        expect(label).toBeTruthy()
        if (status.includes("_")) expect(label).not.toBe(status)
    })

    it.each(ALL_STATUSES)("%s says what it asserts about the record", (status) => {
        // Every status a curator can pick must explain itself: this axis
        // decides what readers are told, and "approved" vs "deprecated"
        // is not self-evident from the word.
        expect(statusMeaning(status).length).toBeGreaterThan(20)
    })

    it("gives the five statuses five distinct meanings", () => {
        const meanings = new Set(ALL_STATUSES.map(statusMeaning))
        expect(meanings.size).toBe(ALL_STATUSES.length)
    })

    it("only says 'trust' about the one status that creates it", () => {
        const trusting = ALL_STATUSES.filter((s) => /trusted/i.test(statusMeaning(s)))
        expect(trusting).toEqual(["approved"])
    })
})

describe("status styling", () => {
    it.each(ALL_STATUSES)("%s maps to a css class", (status) => {
        expect(statusClass(status)).toBe(`review-status-${status.replace(/_/g, "-")}`)
    })

    it("returns null for a status this build does not know", () => {
        // Guessing a class would paint an unfamiliar state as one of the
        // five, which states a judgement nobody made.
        expect(statusClass("provisionally_endorsed")).toBeNull()
    })
})
