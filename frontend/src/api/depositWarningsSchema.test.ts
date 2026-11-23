import { describe, expect, it } from "vitest"
import { depositWarningsEnvelopeSchema, emptyDepositWarnings, hasNoWarningItems, mergeDepositWarnings, pickDepositWarnings } from "./depositWarningsSchema"

const item = { code: "missing_irc_for_ts", message: "No IRC.", field: "calculations[*]", register_entry: 19, context: { n: 1 }, check_version: 1 }

describe("deposit warnings envelope", () => {
    it("parses recorded and not_recorded blocks and an unknown status", () => {
        const parsed = depositWarningsEnvelopeSchema.parse({
            record_warnings: [
                { record_ref: "a", status: "recorded", items: [item] },
                { record_ref: "b", status: "not_recorded", items: [] },
                { record_ref: "c", status: "some_new_status", items: [item] },
            ],
        })
        expect(parsed.record_warnings.map((b) => b.status)).toEqual(["recorded", "not_recorded", "some_new_status"])
        expect(parsed.deposit_warnings).toEqual([])
    })

    it("parses a deposit block with and without a submission ref", () => {
        const parsed = depositWarningsEnvelopeSchema.parse({
            deposit_warnings: [{ submission_ref: "sub_1", items: [item] }, { items: [item] }],
        })
        expect(parsed.deposit_warnings[0].submission_ref).toBe("sub_1")
        expect(parsed.deposit_warnings[1].submission_ref).toBeUndefined()
    })

    it("parses depositor fields, retirement fields and a null register entry", () => {
        const parsed = depositWarningsEnvelopeSchema.parse({
            record_warnings: [{ record_ref: "a", status: "recorded", items: [
                { ...item, depositor_message: "typed", depositor_field: "f", retired_at: "2026-10-01T00:00:00", retired_reason: "artifact_attached" },
                { ...item, register_entry: null },
                { code: "x", message: "m", field: "f", check_version: 2 },
            ] }],
        })
        const [first, second, third] = parsed.record_warnings[0].items
        expect(first.depositor_message).toBe("typed")
        expect(first.retired_reason).toBe("artifact_attached")
        expect(second.register_entry).toBeNull()
        expect(third.depositor_message).toBeUndefined()
    })

    it("picks, merges and detects emptiness", () => {
        const a = pickDepositWarnings({ record_warnings: [{ record_ref: "a", status: "recorded", items: [item] }] })
        const b = pickDepositWarnings({})
        expect(b).toEqual(emptyDepositWarnings())
        expect(mergeDepositWarnings(a, b, null).record_warnings).toHaveLength(1)
        expect(hasNoWarningItems(a)).toBe(false)
        expect(hasNoWarningItems({ record_warnings: [{ record_ref: "a", status: "not_recorded", items: [] }], deposit_warnings: [] })).toBe(true)
    })
})
