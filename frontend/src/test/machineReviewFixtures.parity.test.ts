import { describe, expect, it } from "vitest"
import * as fx from "./machineReviewFixtures"

// The backend's own OpenAPI snapshot. `import.meta.glob` (no Node types under src/); an empty match fails rather than passing with nothing to compare.
const GOLDEN = import.meta.glob("../../../backend/tests/api/golden/openapi.json", {
    import: "default",
    eager: true,
}) as Record<string, { components: { schemas: Record<string, { properties: Record<string, unknown> }> } }>

const files = Object.values(GOLDEN)

const CASES: Array<[string, Record<string, unknown>]> = [
    ["AdminSubmissionMachineReviewInspectionResponse", fx.inspection()],
    ["AdminMachineReviewRecordInspection", fx.recordInspection()],
    ["AdminMachineReviewRecordReviewRead", fx.recordReview()],
    ["AdminMachineReviewFindingRead", fx.finding()],
    ["AdminMachineReviewRunRead", fx.run()],
    ["AdminMachineReviewUnmappedFindingRead", fx.unmappedFinding()],
    ["AdminCuratorTaskResponse", fx.curatorTask()],
    ["SubmissionMachineReviewOverviewRead", fx.overviewRow()],
    ["ReviewQueueRecordRead", fx.reviewQueueRecord()],
    ["ReviewQueueMachineFindingsRead", fx.machineFindings()],
    ["MachineReviewRecordSummary", fx.summary()],
    ["SubmissionMachineReviewLatestRunRead", (fx.overviewRow().machine_review as { latest_run: Record<string, unknown> }).latest_run],
    ["SubmissionMachineReviewStateRead", fx.overviewRow().machine_review as Record<string, unknown>],
    ["SubmissionMachineReviewOpenJobRead", fx.openJob()],
    ["MachineReviewJobRead", fx.job()],
    ["MachineReviewJobDetailRead", fx.jobDetail()],
    ["MachineReviewJobEventRead", fx.jobEvent()],
    ["MachineReviewProviderRead", (fx.providers().providers as Array<Record<string, unknown>>)[0]],
    ["MachineReviewSettingsRead", fx.settings()],
    ["SubmissionRead", fx.submission()],
    ["SubmissionAuditEventRead", fx.auditEvent()],
    ["SubmissionRecordLinkRead", fx.recordLink()],
]

describe("machine-review fixtures match the API contract", () => {
    it("found exactly one golden OpenAPI file", () => {
        expect(files).toHaveLength(1)
    })

    it.each(CASES)("%s", (name, fixture) => {
        const schema = files[0].components.schemas[name]
        expect(schema, `${name} is not in the golden OpenAPI`).toBeDefined()
        expect(Object.keys(fixture).sort()).toEqual(Object.keys(schema.properties).sort())
    })
})
