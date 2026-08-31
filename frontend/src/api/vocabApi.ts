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

export function loadSoftwareNames(signal?: AbortSignal): Promise<VocabEntry[]> {
    return loadVocab("/api/v1/scientific/meta/software", signal)
}

export function loadWorkflowToolNames(signal?: AbortSignal): Promise<VocabEntry[]> {
    return loadVocab("/api/v1/scientific/meta/workflow-tools", signal)
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
