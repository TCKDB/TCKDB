import { tokenWords } from "./apiVocabulary"

// The words for the curator panels: structure-evidence findings and reproducibility assessments. The token lists mirror the
// backend and `curationWording.parity.test.ts` fails when they drift. A token this build has not been taught reads as its
// own words (`tokenWords`), never as the raw token. Refusals are worded from the error code and its context values; the
// server's own `detail` text is never shown.

export const FINDING_KIND: Record<string, string> = {
    identity_incompatibility: "Identity incompatibility",
    state_incompatibility: "State incompatibility",
    path_incompatibility: "Path incompatibility",
    role_invalidation: "Role invalidation",
    contradictory_characterization: "Contradictory characterisation",
    adjudication: "Adjudication",
}

export const FINDING_VERDICT: Record<string, string> = {
    invalidates: "Invalidates the evidence",
    does_not_invalidate: "Does not invalidate the evidence",
    unresolved: "Unresolved",
}

export const SOURCE_ROLE: Record<string, string> = {
    energy: "Energy",
    geometry_optimization: "Geometry optimisation",
    curvature: "Curvature",
    correction: "Correction",
    connectivity: "Connectivity",
    alternative_characterization: "Alternative characterisation",
}

export const FINDING_SCOPE: Record<string, string> = {
    geometry: "geometry",
    calculation: "calculation",
    determination: "structure determination",
}

export const FINDING_AUTHORITY: Record<string, string> = {
    authorized_adjudication: "Ruling by a curator",
    producer_assertion: "Asserted by the depositor, not a curator ruling",
}

const words = (table: Record<string, string>, token: string) => (Object.hasOwn(table, token) ? table[token] : tokenWords(token))

export const kindWords = (kind: string) => words(FINDING_KIND, kind)
export const verdictWords = (verdict: string) => words(FINDING_VERDICT, verdict)
/** The role in running text; the form lists the same words in sentence case. */
export const roleWords = (role: string) => words(SOURCE_ROLE, role).toLowerCase()
export const scopeWords = (scope: string) => words(FINDING_SCOPE, scope)
export const authorityWords = (authority: string) => words(FINDING_AUTHORITY, authority)

/**
 * What a finding in force does to the evidence, from its applied kind, role and verdict. A ruling that does not
 * invalidate, and an adjudication head, have no effect of their own, so they say nothing.
 */
export function appliedEffect(kind: string | null, role: string | null, verdict: string): string | null {
    if (!kind || kind === "adjudication" || verdict === "does_not_invalidate") return null
    const base = kindWords(kind).toLowerCase()
    const what = role ? `${/^[aeiou]/.test(base) ? "an" : "a"} ${base} of the ${roleWords(role)} role` : `${/^[aeiou]/.test(base) ? "an" : "a"} ${base}`
    return verdict === "unresolved" ? `Applied as ${what} while the verdict is unresolved.` : `Applied as ${what}.`
}

/** The sentence a form field shows for a rule a curator broke; no request is sent. */
export const FORM_ERROR = {
    rationale: "Give the reason for this finding.",
    rationaleLong: "The rationale is limited to 2000 characters.",
    role: "Choose the role this finding invalidates.",
    target: "Choose the finding it corrects.",
    calculation: "A calculation reference starts with calc_.",
    literature: "A literature reference starts with lit_.",
    dismissal: "A ruling that does not invalidate must be an adjudication that corrects an earlier finding.",
} as const

export const CHECK_SENTENCE: Record<string, string> = {
    target_identity: "The record can be pointed at by a public reference.",
    scientific_context: "The scientific context the record belongs to is recorded.",
    source_attribution: "The record says where it came from: its origin and, when it is derived, the calculations or paper behind it.",
    record_type_audit_policy: "Reproducibility can be audited for this kind of record.",
    calculation_metadata: "The calculation names its level of theory and the exact program release that ran it.",
    typed_output_evidence: "The calculation keeps its results as typed values, not only as a file.",
    composite_inputs_auditable: "Every input of the composite energy is itself auditable.",
    composite_total_follows_from_inputs: "The composite total can be recomputed from its inputs.",
    verified_output_artifact_bytes: "A stored output file was read back and matched its recorded checksum.",
    source_role_preservation: "Each supporting calculation keeps the role it played.",
    composite_inputs_rerunnable: "Every input of the composite energy could be rerun.",
    preserved_input_artifacts: "The input files of the calculation are kept.",
    execution_parameter_snapshot: "The settings the program ran with are recorded.",
    upstream_dependency_snapshot: "The calculations this one depends on are recorded.",
    evidence_warnings_clear: "No stored file raised an integrity warning.",
}

export const CHECK_LEVEL: Record<string, string> = {
    described: "Described",
    auditable: "Auditable",
    rerunnable: "Rerunnable",
}
export const LEVEL_ORDER = ["described", "auditable", "rerunnable"]
export const levelWords = (level: string) => words(CHECK_LEVEL, level)
export const checkSentence = (name: string) => (Object.hasOwn(CHECK_SENTENCE, name) ? CHECK_SENTENCE[name] : `Check: ${tokenWords(name)}.`)

export const WARNING_SENTENCE: Record<string, string> = {
    artifact_integrity_failed: "A stored file no longer matches its recorded checksum.",
    artifact_object_missing: "A stored file is recorded but its content could not be found.",
    artifact_storage_unavailable: "File storage could not be reached, so a stored file was not checked.",
    artifact_verification_size_limit: "A stored file was too large to check.",
    artifact_verification_count_budget: "There were too many stored files to check them all.",
    artifact_verification_aggregate_budget: "The stored files together were too large to check them all.",
}
export const warningSentence = (code: string) => (Object.hasOwn(WARNING_SENTENCE, code) ? WARNING_SENTENCE[code] : `Warning: ${tokenWords(code)}.`)

type Context = Record<string, unknown> | undefined

/** A context value that is safe to quote: a short public reference or prefix, never free text. */
function quotable(context: Context, key: string): string | null {
    const value = context?.[key]
    return typeof value === "string" && /^[A-Za-z0-9_]{1,64}$/.test(value) ? value : null
}

const FIELD_NAME: Record<string, string> = {
    source_calculation_ref: "source calculation",
    literature_ref: "literature reference",
    supersedes_finding_ref: "corrected finding",
}
const fieldName = (context: Context) => {
    const field = quotable(context, "field")
    return field && Object.hasOwn(FIELD_NAME, field) ? FIELD_NAME[field] : "reference"
}

export const REFUSAL_SENTENCE: Record<string, (context: Context) => string> = {
    structure_finding_rationale_required: () => "A finding needs a rationale: say why the evidence is or is not invalidated.",
    structure_finding_role_mismatch: () => "Name a role for a role invalidation, and only for a role invalidation.",
    structure_finding_adjudication_needs_target: () => "An adjudication corrects an earlier finding: choose which one.",
    structure_finding_correction_needs_adjudication: () => "Only an adjudication can correct an earlier finding.",
    structure_finding_dismissal_needs_target: () => FORM_ERROR.dismissal,
    structure_finding_literature_unidentified: () => "That paper has no DOI or ISBN, so it cannot be cited as a source.",
    structure_finding_reference_invalid: (c) => {
        const prefix = quotable(c, "expected_prefix")
        return `The ${fieldName(c)} is not a valid reference${prefix ? `; it should start with ${prefix}` : ""}.`
    },
    structure_finding_reference_not_found: (c) => {
        const ref = quotable(c, "ref")
        return `No record was found for the ${fieldName(c)}${ref ? ` ${ref}` : ""}.`
    },
    structure_finding_subject_not_found: () => "The record this finding is about could not be found.",
    structure_finding_already_settled: (c) => {
        const by = quotable(c, "settled_by_finding_ref")
        return by ? `That finding has already been corrected by ${by}. Correct that one instead.` : "That finding has already been corrected. Reload to see the latest."
    },
    structure_finding_correction_subject_mismatch: () => "The finding being corrected is about a different record.",
    structure_finding_scope_subject_mismatch: (c) => {
        const prefix = quotable(c, "expected_prefix")
        return `This kind of finding needs a reference that starts with ${prefix ?? "another prefix"}.`
    },
}

/** The sentence for a failed curation request, from its status, code and context. */
export function refusalSentence(error: { status?: number; code?: string; context?: Record<string, unknown> }): string {
    if (error.code && Object.hasOwn(REFUSAL_SENTENCE, error.code)) return REFUSAL_SENTENCE[error.code](error.context)
    if (error.status === 401) return "Sign in again to continue; your session has ended."
    if (error.status === 403) return "Only curators and admins can do this."
    return "The request could not be completed. Try again, and reload the page if it keeps failing."
}
