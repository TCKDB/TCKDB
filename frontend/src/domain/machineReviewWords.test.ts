import { describe, expect, it } from "vitest"
import {
    adminDate,
    adminDateTime,
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

    it("offers every record type as a filter option with a plain label", () => {
        const values = RECORD_TYPE_OPTIONS.map((o) => o.value)
        expect(new Set(values)).toEqual(new Set(schemas["SubmissionRecordType"].enum))
        for (const option of RECORD_TYPE_OPTIONS) expect(option.label).not.toMatch(/_/)
    })
})
