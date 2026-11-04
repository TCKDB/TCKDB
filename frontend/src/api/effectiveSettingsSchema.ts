import { z } from "zod"

// `effective_settings` on a calculation (`include=parameters`) and `calculation_settings` on a thermo or statmech
// record (`include=calculation_settings`): what the grid and SCF convergence were, and how the archive knows.
// Strings stay plain on the wire so a future state or reason never fails to parse; `domain/effectiveSettings.ts`
// maps each to words from a closed table.

export const effectiveSettingSchema = z.object({
    setting: z.string(),
    state: z.string(),
    requested_value: z.string().nullable().optional(),
    requested_from: z.string().nullable().optional(),
    reported: z.array(z.object({ key: z.string(), value: z.string() }).passthrough()).nullable().optional(),
    assumed_value: z.string().nullable().optional(),
    conflicting: z.boolean().optional(),
}).passthrough()

export const roleCalculationSettingsSchema = z.object({
    role: z.string(),
    calculation_ref: z.string().nullable().optional(),
    calculation_type: z.string().nullable().optional(),
    absent_reason: z.string().nullable().optional(),
    settings: z.array(effectiveSettingSchema).optional(),
}).passthrough()

export type EffectiveSetting = z.infer<typeof effectiveSettingSchema>
export type RoleCalculationSettings = z.infer<typeof roleCalculationSettingsSchema>
