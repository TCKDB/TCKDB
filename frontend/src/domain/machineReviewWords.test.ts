import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
    adminDate,
    adminDateTime,
    RECOMMENDATION_STATUSES,
    recommendationFor,
    RECORD_TYPE_OPTIONS,
    wordsFor,
    type WordKind,
} from "./machineReviewWords"

const GOLDEN = import.meta.glob("../../../backend/tests/api/golden/openapi.json", {
    import: "default",
    eager: true,
}) as Record<string, { components: { schemas: Record<string, { enum?: string[] }> } }>

const schemas = Object.values(GOLDEN)[0].components.schemas

const KINDS: Array<[WordKind, string]> = [
    ["status", "app__db__models__common__MachineReviewStatus"],
    ["severity", "app__db__models__common__MachineReviewSeverity"],
    ["category", "MachineReviewCategory"],
    ["unmappedReason", "UnmappedReason"],
    ["priority", "CuratorPriority"],
    ["findingMatch", "CuratorTaskFindingMatch"],
    ["submissionStatus", "SubmissionStatus"],
    ["submissionKind", "SubmissionKind"],
    ["auditEventKind", "SubmissionAuditEventKind"],
    ["actorKind", "SubmissionActorKind"],
    ["recordType", "SubmissionRecordType"],
    ["taskState", "MachineReviewCuratorTaskState"],
    ["jobStatus", "MachineReviewJobStatus"],
    ["jobFailure", "MachineReviewJobFailure"],
    ["availability", "MachineReviewAvailability"],
    ["jobEventKind", "MachineReviewJobEventKind"],
    ["providerKey", "MachineReviewProviderKey"],
]

describe("machine-review wording", () => {
    it("found the golden OpenAPI", () => {
        expect(Object.keys(GOLDEN)).toHaveLength(1)
    })

    it.each(KINDS)("every %s value has a label that is not its raw token", (kind, schemaName) => {
        const values = schemas[schemaName]?.enum
        expect(values, `${schemaName} enum`).toBeDefined()
        for (const value of values!) {
            const { label, meaning } = wordsFor(kind, value)
            expect(label.length, `${kind}:${value}`).toBeGreaterThan(0)
            expect(label, `${kind}:${value}`).not.toMatch(/_/)
            if (value.includes("_")) expect(label).not.toBe(value)
            expect(typeof meaning).toBe("string")
        }
    })

    it("an unknown token is reported as itself, never as another value's wording", () => {
        const { label, known } = wordsFor("status", "machine_screened_nonsense")
        expect(known).toBe(false)
        expect(label).toBe("machine_screened_nonsense")
        expect(wordsFor("severity", "critical").known).toBe(true)
    })

    it("formats dates deterministically in UTC", () => {
        expect(adminDate("2026-09-14T23:59:59Z")).toBe("2026-09-14")
        expect(adminDateTime("2026-09-14T09:05:59Z")).toBe("2026-09-14 09:05 UTC")
        expect(adminDateTime("2026-09-14T23:30:00-05:00")).toBe("2026-09-15 04:30 UTC")
        expect(adminDate(null)).toBe("")
    })

    describe("a timestamp with no offset, in a zone east of UTC", () => {
            beforeEach(() => {
            vi.stubEnv("TZ", "Asia/Jerusalem")
        })
        afterEach(() => {
        vi.unstubAllEnvs()
        })

        it("is read as UTC, not as local time", () => {
            expect(adminDateTime("2026-11-24T08:22:02.484672")).toBe("2026-11-24 08:22 UTC")
            expect(adminDate("2026-11-24T23:30:00")).toBe("2026-11-24")
        })
    })

    it("offers every record type as a filter option with a plain label", () => {
        const values = RECORD_TYPE_OPTIONS.map((o) => o.value)
        expect(new Set(values)).toEqual(new Set(schemas["SubmissionRecordType"].enum))
        for (const option of RECORD_TYPE_OPTIONS) expect(option.label).not.toMatch(/_/)
    })

    it("has a recommendation for exactly the three screened statuses", () => {
        const statuses = schemas["app__db__models__common__MachineReviewStatus"].enum!
        const screened = statuses.filter((v) => v.startsWith("machine_screened_"))
        expect(new Set(RECOMMENDATION_STATUSES)).toEqual(new Set(screened))
        for (const status of screened) expect(recommendationFor(status)).toMatch(/\(advisory\)$/)
        expect(recommendationFor("machine_screened_pass")).toBe("Machine reviewer recommends approval (advisory)")
        expect(recommendationFor("machine_screened_warning")).toBe("Machine reviewer recommends approval after reading its notes (advisory)")
        expect(recommendationFor("machine_screened_needs_attention")).toBe("Machine reviewer does not recommend approval yet (advisory)")
    })

    it("gives a failed review the words for its failure code, and no recommendation to not_run or an unknown status", () => {
        expect(recommendationFor("machine_review_failed", "provider_timeout")).toBe("Machine review did not finish: the reviewer took too long")
        expect(recommendationFor("machine_review_failed", null)).toBe("Machine review did not finish")
        expect(recommendationFor("not_run")).toBeNull()
        expect(recommendationFor("machine_screened_nonsense")).toBeNull()
    })
})
