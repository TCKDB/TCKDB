// What a calculation used for its DFT grid and SCF convergence, and how the archive knows (computed on read,
// `backend/app/services/scientific_read/effective_settings.py`). This module maps the server's closed tokens to the
// words a reader sees. Nothing here prints a depositor-typed string (house rule, owner decision 2026-11-03): a value
// is printed only when it belongs to a closed set below, and every key and state is looked up in a fixed table.

export type EffectiveSettingName = "grid" | "scf_convergence"
export type EffectiveSettingState =
    | "reported"
    | "requested"
    | "assumed_default"
    | "assumed_default_no_output_log"
    | "not_stated"

const SETTING_LABELS: Record<EffectiveSettingName, string> = {
    grid: "DFT grid",
    scf_convergence: "SCF convergence",
}

export function settingLabel(setting: string): string {
    return SETTING_LABELS[setting as EffectiveSettingName] ?? "Setting"
}

// Short labels for the pills; the full meaning is in `STATE_MEANINGS` (the pill's tooltip and the footnote).
const STATE_LABELS: Record<EffectiveSettingState, string> = {
    reported: "Reported",
    requested: "Requested",
    assumed_default: "Assumed default",
    assumed_default_no_output_log: "Assumed default (no log)",
    not_stated: "Not stated",
}

const STATE_MEANINGS: Record<EffectiveSettingState, string> = {
    reported: "Reported by the program: its output log prints the value it used.",
    requested: "Requested: the input asked for this value; the program did not report what it used.",
    assumed_default: "Assumed program default: the log neither reports nor echoes it, so the program's documented default is shown.",
    assumed_default_no_output_log:
        "Assumed program default (no output log): no output log was deposited, so the program's documented default is shown.",
    not_stated: "Not stated: nothing states it, and no program default can be assumed.",
}

/** The reader-facing name of a state; an unrecognised future state reads "Not stated", never the raw token. */
export function settingStateLabel(state: string): string {
    return STATE_LABELS[state as EffectiveSettingState] ?? STATE_LABELS.not_stated
}

/** The full sentence behind a pill, for its tooltip. */
export function settingStateMeaning(state: string): string {
    return STATE_MEANINGS[state as EffectiveSettingState] ?? STATE_MEANINGS.not_stated
}

/** `true` for the two states whose value is the program's documented default, not something observed. */
export function isAssumedState(state: string): boolean {
    return state === "assumed_default" || state === "assumed_default_no_output_log"
}

const REQUESTED_FROM_LABELS: Record<string, string> = {
    output_log: "echoed in the output log",
    input: "input file",
    typed: "stated by the depositor",
}

/** Where a requested value was stated, or `null` when the source is not one of the three the server names. */
export function requestedFromLabel(requestedFrom: string | null | undefined): string | null {
    return requestedFrom ? (REQUESTED_FROM_LABELS[requestedFrom] ?? null) : null
}

// The canonical tokens the parsers emit (`backend/app/services/parameter_closed_values.py`, the ORCA and Gaussian
// parameter parsers). A grid or convergence value outside these (a depositor can type anything into `canonical_value`)
// is not printed.
const GRID_TOKENS = new Set([
    "coarse", "sg1", "fine", "ultrafine", "superfine", "pass0grid",
    "defgrid1", "defgrid2", "defgrid3", "grid4", "grid5", "grid6", "grid7",
])
const SCF_TOKENS: Record<string, string> = {
    loose: "loose",
    normal: "normal",
    tight: "tight",
    very_tight: "very tight",
}
const GRID_NUMBER = /^\d{1,7}$/

/** A grid or SCF-convergence value, in words, or `null` when it is not in the closed set (the page then says "recorded"). */
export function publicSettingValue(setting: string, value: string | null | undefined): string | null {
    if (value == null) return null
    const token = value.trim().toLowerCase()
    if (setting === "grid") return GRID_TOKENS.has(token) || GRID_NUMBER.test(token) ? token : null
    if (setting === "scf_convergence") return SCF_TOKENS[token] ?? null
    return null
}

const REPORTED_KEY_LABELS: Record<string, string> = {
    "grid.integration_accuracy": "integration accuracy",
    "grid.angular_max": "largest angular grid",
    "scf.tol_energy_eh": "energy tolerance (Eh)",
    "scf.tol_orbital_gradient": "orbital gradient tolerance",
    "scf.tol_orbital_rotation": "orbital rotation tolerance",
    "scf.tol_diis_error": "DIIS error tolerance",
    "scf.tol_rms_density": "RMS density tolerance",
    "scf.tol_max_density": "max density tolerance",
    // Gaussian prints an internal grid code whose correspondence to a named grid is not documented.
    IRadAn: "Gaussian IRadAn code",
}

const NUMERIC = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/

/** One value a log printed: "largest angular grid 302". A key outside the table reads "a recorded value"; a non-numeric value "recorded". */
export function reportedEntryText(key: string, value: string): string {
    const label = REPORTED_KEY_LABELS[key]
    if (!label) return "a recorded value"
    return `${label} ${NUMERIC.test(value.trim()) ? value.trim() : "recorded"}`
}

export type SettingEvidenceKind = "reported" | "requested" | "assumed" | "not_stated"

export interface SettingSummaryInput {
    setting: string
    state: string
    requested_value?: string | null
    requested_from?: string | null
    reported?: { key: string; value: string }[] | null
    assumed_value?: string | null
    conflicting?: boolean
}

export interface SettingDisplay {
    setting: string
    settingLabel: string
    kind: SettingEvidenceKind
    stateLabel: string
    /** What was used or asked for, in words; `null` when the state names no value (not stated, a conflict, a report). */
    valueText: string | null
    /** `true` when `valueText` is the placeholder "recorded" (a value outside the closed set): draw it as a note, not a value. */
    valueIsPlaceholder: boolean
    /** The full sentence behind the state pill. */
    stateMeaning: string
    /** Where a requested value was stated, in words. */
    sourceText: string | null
    /** The numbers a log printed, for a reported state. */
    reportedTexts: string[]
    conflicting: boolean
    assumed: boolean
}

/** Everything a row needs to render one effective setting, with each string from a closed table. */
export function describeSetting(summary: SettingSummaryInput): SettingDisplay {
    const state = summary.state
    const assumed = isAssumedState(state)
    const kind: SettingEvidenceKind =
        state === "reported" ? "reported" : state === "requested" ? "requested" : assumed ? "assumed" : "not_stated"
    const conflicting = summary.conflicting === true
    let valueText: string | null = null
    if (kind === "requested" && !conflicting) {
        valueText = publicSettingValue(summary.setting, summary.requested_value) ?? "recorded"
    } else if (kind === "assumed") {
        valueText = publicSettingValue(summary.setting, summary.assumed_value) ?? "recorded"
    }
    return {
        setting: summary.setting,
        settingLabel: settingLabel(summary.setting),
        kind,
        // Two different requested values: say so in the state itself, with no value to show.
        stateLabel: conflicting && kind === "requested" ? "Conflicting requests" : settingStateLabel(state),
        stateMeaning:
            conflicting && kind === "requested"
                ? "Conflicting requests: the input states two different values, so none is shown."
                : settingStateMeaning(state),
        valueText,
        valueIsPlaceholder: valueText === "recorded",
        sourceText: kind === "requested" ? requestedFromLabel(summary.requested_from) : null,
        reportedTexts:
            kind === "reported" ? (summary.reported ?? []).map((item) => reportedEntryText(item.key, item.value)) : [],
        conflicting,
        assumed,
    }
}

// --- the calculation page's "Parsed parameters" table ------------------------------------------------------------

const SETTING_OF_KEY: Record<string, string> = { "grid.quality": "grid", "scf.convergence": "scf_convergence" }

/**
 * The Value cell for a recognised parameter key. The grid and SCF-convergence settings print their closed-set token
 * (safe even when a depositor typed it); anything outside the set reads "recorded". Every other key keeps the rule of
 * `fallback` (`publicParameterValue`: numbers and booleans only).
 */
export function parameterValueText(
    canonicalKey: string,
    canonicalValue: string | null | undefined,
    fallback: (value: string | null | undefined) => string,
): string {
    const setting = SETTING_OF_KEY[canonicalKey]
    if (!setting) return fallback(canonicalValue)
    return publicSettingValue(setting, canonicalValue) ?? "recorded"
}

export interface ParameterStatementInput {
    source?: string | null
    artifact_kind?: string | null
    statement_kind?: string | null
}

export interface ParameterStatement {
    kind: "reported" | "requested" | "curated" | "unknown"
    label: string
    /** The pill's modifier: reported and requested statements carry their own; curated and unknown are neutral. */
    pillClass: "reported" | "requested" | "neutral"
}

/** Requested or reported, and from where: "Reported · output log", "Requested · input file", "Requested · depositor". */
export function parameterStatement(row: ParameterStatementInput): ParameterStatement {
    if (row.statement_kind === "reported") return { kind: "reported", label: "Reported · output log", pillClass: "reported" }
    if (row.source === "curated") return { kind: "curated", label: "Curated", pillClass: "neutral" }
    if (row.source === "upload") return { kind: "requested", label: "Requested · depositor", pillClass: "requested" }
    if (row.source === "parser") {
        if (row.artifact_kind === "output_log") return { kind: "requested", label: "Requested · echoed in output log", pillClass: "requested" }
        if (row.artifact_kind === "input") return { kind: "requested", label: "Requested · input file", pillClass: "requested" }
        return { kind: "requested", label: "Requested", pillClass: "requested" }
    }
    return { kind: "unknown", label: "Source not recorded", pillClass: "neutral" }
}

// --- thermo and statmech: the three levels ------------------------------------------------------------------------

export type LevelRole = "geometry" | "frequency" | "energy"
export const LEVEL_ROLES: readonly LevelRole[] = ["geometry", "frequency", "energy"]

const ROLE_LABELS: Record<LevelRole, string> = { geometry: "Geometry", frequency: "Frequencies", energy: "Energy" }

export function levelRoleLabel(role: string): string {
    return ROLE_LABELS[role as LevelRole] ?? "Level"
}

const ABSENT_TEXT: Record<string, string> = {
    not_linked: "No calculation linked",
    ambiguous: "Linked energy calculations disagree on level of theory",
    composite_recipe: "Set by a composite method's recipe, not a deposited calculation",
    // The server leaves out a level whose calculations the review profile hides; the page says so.
    not_shown: "Calculation not shown under the current review profile",
}

export function absentReasonText(reason: string | null | undefined): string {
    return (reason ? ABSENT_TEXT[reason] : null) ?? "No calculation linked"
}
