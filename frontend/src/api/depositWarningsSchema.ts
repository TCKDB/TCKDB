import { z } from "zod"

// Deposit warnings, as the read envelopes carry them (`record_warnings` and `deposit_warnings`; see
// backend/app/schemas/reads/scientific_deposit_warnings.py). The API sends no severity, so nothing here groups by one.
// `message` and `field` are TCKDB's words, rendered from the code and values. `depositor_message` and `depositor_field`
// are served only to the deposit's owner and to curators; their presence is the access signal the panel reads.
// `context.depositor_supplied`, when present, is depositor-typed text.

export const depositWarningItemSchema = z.object({
    code: z.string(),
    message: z.string(),
    field: z.string(),
    register_entry: z.number().nullable().optional(),
    context: z.record(z.string(), z.unknown()).nullable().optional(),
    check_version: z.number(),
    depositor_message: z.string().nullable().optional(),
    depositor_field: z.string().nullable().optional(),
    retired_at: z.string().nullable().optional(),
    retired_reason: z.string().nullable().optional(),
}).passthrough()

export const recordWarningsSchema = z.object({
    record_ref: z.string(),
    // Read as a plain string so a status this client has not been taught never fails the page.
    status: z.string(),
    items: z.array(depositWarningItemSchema),
}).passthrough()

export const submissionWarningsSchema = z.object({
    submission_ref: z.string().nullable().optional(),
    items: z.array(depositWarningItemSchema),
}).passthrough()

/** The two envelope fields, spread into a response schema. Absent on an older deployment: both default to empty. */
export const depositWarningsEnvelopeShape = {
    record_warnings: z.array(recordWarningsSchema).optional().default([]),
    deposit_warnings: z.array(submissionWarningsSchema).optional().default([]),
}

export const depositWarningsEnvelopeSchema = z.object(depositWarningsEnvelopeShape).passthrough()

export type DepositWarningItem = z.infer<typeof depositWarningItemSchema>
export type RecordWarnings = z.infer<typeof recordWarningsSchema>
export type SubmissionWarnings = z.infer<typeof submissionWarningsSchema>
export type DepositWarnings = { record_warnings: RecordWarnings[]; deposit_warnings: SubmissionWarnings[] }

export function emptyDepositWarnings(): DepositWarnings {
    return { record_warnings: [], deposit_warnings: [] }
}

/** Pull the envelope's warnings off a parsed response. */
export function pickDepositWarnings(parsed: { record_warnings?: RecordWarnings[]; deposit_warnings?: SubmissionWarnings[] }): DepositWarnings {
    return { record_warnings: parsed.record_warnings ?? [], deposit_warnings: parsed.deposit_warnings ?? [] }
}

/** Concatenate several envelopes' warnings, in order. */
export function mergeDepositWarnings(...all: Array<DepositWarnings | null | undefined>): DepositWarnings {
    const merged = emptyDepositWarnings()
    for (const warnings of all) {
        if (!warnings) continue
        merged.record_warnings.push(...warnings.record_warnings)
        merged.deposit_warnings.push(...warnings.deposit_warnings)
    }
    return merged
}

/** True when no block carries an item. */
export function hasNoWarningItems(warnings: DepositWarnings): boolean {
    return warnings.record_warnings.every((block) => block.items.length === 0)
        && warnings.deposit_warnings.every((block) => block.items.length === 0)
}
