import { z } from "zod"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

/**
 * The `/meta/*` vocabulary endpoints (`backend/app/api/routes/scientific/
 * meta.py`) -- distinct stored values for the exact-string filters on the
 * transition-state browse/search surface (method, basis, software,
 * workflow tool), each with a usage count. `BrowseFilterForm` uses these
 * to populate its dropdowns rather than asking a reader to guess a value
 * that happens to exist in the archive.
 *
 * `count` is genuinely informative for methods/basis-sets/versions (their
 * backing tables carry no uniqueness on the counted column alone -- see
 * `app/services/scientific_read/meta.py`'s module docstring), but for
 * software/workflow-tool names it is structurally always 1 (`UniqueConstraint
 * ("name")` on both `Software` and `WorkflowTool`, `app/db/models/software.py`
 * and `app/db/models/workflow.py`). Callers of `loadSoftwareNames` /
 * `loadWorkflowToolNames` must not render that constant as if it varied.
 */

const vocabEntrySchema = z.object({ value: z.string(), count: z.number() }).passthrough()
export type VocabEntry = z.infer<typeof vocabEntrySchema>

const vocabResponseSchema = z.object({ results: z.array(vocabEntrySchema) }).passthrough()

async function loadVocab(path: string, signal?: AbortSignal): Promise<VocabEntry[]> {
    const payload = await requestScientificJson(path, signal)
    const parsed = parseScientificResponse(vocabResponseSchema, payload, "vocabulary")
    return parsed.results
}

export function loadMethods(signal?: AbortSignal): Promise<VocabEntry[]> {
    return loadVocab("/api/v1/scientific/meta/methods", signal)
}

export function loadBasisSets(signal?: AbortSignal): Promise<VocabEntry[]> {
    return loadVocab("/api/v1/scientific/meta/basis-sets", signal)
}

/**
 * The calculation-owner granularity `/meta/software` and
 * `/meta/workflow-tools` narrow by -- "species" or "transition_state",
 * mirroring `CalculationRecordKind` (`app/db/models/common.py`). There is
 * no "vdw" value: a van der Waals complex is a `species_entry` row like
 * any other, so at the calculation-owner level it IS "species" -- see
 * `BrowseFilterForm`'s `recordKindFor` helper, which maps the browse
 * page's three-way `kind` down to this two-way scope.
 */
export type VocabRecordKind = "species" | "transition_state"

/**
 * `recordKind` narrows to software actually used by a calculation owned by
 * that kind of record (added in #308, `/meta/software`'s own doc comment
 * for the full rationale) -- omitted, the list is unscoped (any kind).
 */
export function loadSoftwareNames(recordKind?: VocabRecordKind, signal?: AbortSignal): Promise<VocabEntry[]> {
    const query = recordKind ? `?${new URLSearchParams({ record_kind: recordKind })}` : ""
    return loadVocab(`/api/v1/scientific/meta/software${query}`, signal)
}

/** Mirrors `loadSoftwareNames`'s `recordKind` scoping -- see there for the rationale. */
export function loadWorkflowToolNames(recordKind?: VocabRecordKind, signal?: AbortSignal): Promise<VocabEntry[]> {
    const query = recordKind ? `?${new URLSearchParams({ record_kind: recordKind })}` : ""
    return loadVocab(`/api/v1/scientific/meta/workflow-tools${query}`, signal)
}

/**
 * `software` is the SELECTED parent's name, verbatim -- never call this
 * with an empty string; the endpoint requires the parameter (422
 * `missing_version_parent`) and the caller (`useVersionVocabulary`) never
 * fetches without a parent for exactly that reason.
 */
export function loadSoftwareVersions(software: string, signal?: AbortSignal): Promise<VocabEntry[]> {
    const query = new URLSearchParams({ software })
    return loadVocab(`/api/v1/scientific/meta/software-versions?${query}`, signal)
}

export function loadWorkflowToolVersions(workflowTool: string, signal?: AbortSignal): Promise<VocabEntry[]> {
    const query = new URLSearchParams({ workflow_tool: workflowTool })
    return loadVocab(`/api/v1/scientific/meta/workflow-tool-versions?${query}`, signal)
}

/**
 * The bounded reaction-family vocabulary -- `/transition-states/browse`'s
 * `family` filter matches one of these exactly (`ReactionFamily.name`).
 * Reuses `loadVocab`/`vocabEntrySchema` like every other list here: the
 * endpoint's rows also carry `display_name`, passed through untouched by
 * `.passthrough()` but not surfaced as a separate type here, since no
 * caller needs it yet -- `value` is both the filter token and (for this
 * vocabulary) a reasonably readable label on its own.
 */
export function loadReactionFamilies(signal?: AbortSignal): Promise<VocabEntry[]> {
    return loadVocab("/api/v1/scientific/meta/reaction-families", signal)
}
