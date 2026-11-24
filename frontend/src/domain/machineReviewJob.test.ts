import { describe, expect, it } from "vitest"
import { elapsedSeconds, parseApiInstant, progressAnnouncement, stepDuration, stepWords } from "./machineReviewJob"
import * as fx from "../test/machineReviewFixtures"
import { JobEventSchema, JobSchema } from "../types/machineReviewJob"

describe("API timestamps", () => {
    it("reads a timestamp with no offset as UTC", () => {
        expect(parseApiInstant("2026-11-24T10:00:00")?.toISOString()).toBe("2026-11-24T10:00:00.000Z")
        expect(parseApiInstant("2026-11-24T10:00:00Z")?.toISOString()).toBe("2026-11-24T10:00:00.000Z")
        expect(parseApiInstant("2026-11-24T12:00:00+02:00")?.toISOString()).toBe("2026-11-24T10:00:00.000Z")
        expect(parseApiInstant(null)).toBeNull()
        expect(parseApiInstant("not a date")).toBeNull()
    })

    // Also run under TZ=Asia/Jerusalem: read as local time, the result below would be 7200 s out.
    it("measures elapsed time from a naive timestamp without the timezone offset", () => {
        const job = JobSchema.parse(fx.job({ status: "running", created_at: "2026-11-24T10:00:00", started_at: "2026-11-24T10:00:05", finished_at: null }))
        const now = new Date("2026-11-24T10:00:35Z")
        expect(elapsedSeconds(job, now)).toBe(30)
    })

    it("counts from the request while the job is queued and stops at the end", () => {
        const queued = JobSchema.parse(fx.job({ status: "queued", created_at: "2026-11-24T10:00:00", started_at: null, finished_at: null }))
        expect(elapsedSeconds(queued, new Date("2026-11-24T10:00:10Z"))).toBe(10)
        const done = JobSchema.parse(fx.job({ created_at: "2026-11-24T10:00:00", started_at: "2026-11-24T10:00:01", finished_at: "2026-11-24T10:00:04" }))
        expect(elapsedSeconds(done, new Date("2026-11-24T11:00:00Z"))).toBe(3)
    })
})

describe("steps", () => {
    it("words a step from its kind and names the tool of a lookup", () => {
        expect(stepWords(JobEventSchema.parse(fx.jobEvent({ kind: "context_built" })))).toBe("reading the submission")
        expect(stepWords(JobEventSchema.parse(fx.jobEvent({ kind: "tool_call", tool_name: "get_record" })))).toBe("looking something up (get_record)")
        expect(stepWords(JobEventSchema.parse(fx.jobEvent({ kind: "something_new" })))).toBe("something_new")
    })

    it("shows a duration only when one was recorded", () => {
        expect(stepDuration(JobEventSchema.parse(fx.jobEvent({ duration_ms: null })))).toBeNull()
        expect(stepDuration(JobEventSchema.parse(fx.jobEvent({ duration_ms: 1234 })))).toBe("1.2 s")
        expect(stepDuration(JobEventSchema.parse(fx.jobEvent({ duration_ms: 80 })))).toBe("80 ms")
    })

    it("announces by status and step, so an unchanged step gives the same sentence", () => {
        const last = JobEventSchema.parse(fx.jobEvent({ seq: 2, kind: "model_turn" }))
        const at2 = JobSchema.parse(fx.job({ status: "running", step_count: 2 }))
        const last3 = JobEventSchema.parse(fx.jobEvent({ seq: 3, kind: "tool_call", tool_name: "get_record" }))
        expect(progressAnnouncement(at2, last)).toBe(progressAnnouncement({ ...at2 }, last))
        expect(progressAnnouncement(at2, last3)).not.toBe(progressAnnouncement(at2, last))
        expect(progressAnnouncement(JobSchema.parse(fx.job({ status: "queued", step_count: 0 })), null)).toBe("Machine review is queued.")
    })
})
