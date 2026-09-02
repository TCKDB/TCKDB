import { useMemo } from "react"
import type { BrowseFilters, BrowseKind, TriState } from "../api/browseApi"
import { loadBasisSets, loadMethods, loadSoftwareNames, loadSoftwareVersions, loadWorkflowToolNames, loadWorkflowToolVersions } from "../api/vocabApi"
import type { VocabRecordKind } from "../api/vocabApi"
import type { VersionVocabularyState, VocabularyState } from "../hooks/useVocabulary"
import { useVersionVocabulary, useVocabulary } from "../hooks/useVocabulary"

const REVIEW_STATUSES = ["not_reviewed", "under_review", "approved", "deprecated", "rejected"]
const TS_STATUSES = ["guess", "optimized", "validated", "rejected"]
const ELECTRONIC_STATES = ["ground", "excited"]

function token(value: string) {
    return value.replaceAll("_", " ")
}

function fieldId(label: string) {
    return `browse-filter-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`
}

/**
 * Every field applies IMMEDIATELY on change -- no separate "Apply" step.
 * `BrowsePage` resets pagination back to offset 0 on every `onChange`
 * (see `updateFilters`), so a filter edit and a fresh first page always
 * arrive together. This mirrors `ConformerSelector`'s click-to-apply
 * pattern rather than `IdentifierSearch`'s submit-on-Enter form: a
 * catalogue narrowing is closer to "pick a facet" than "submit a query".
 */
export function BrowseFilterForm({ kind, filters, onChange }: {
    kind: BrowseKind
    filters: BrowseFilters
    onChange: (patch: Partial<BrowseFilters>) => void
}) {
    return (
        <form aria-label="Narrow this listing" className="browse-filters" onSubmit={(event) => event.preventDefault()}>
            <div className="browse-filter-grid">
                {/*
                 * Structure and formula lead the grid -- they are how a
                 * reader FINDS a molecule; charge, review status and the
                 * rest are refinements applied afterwards ("why isn't
                 * smiles and formula the first in the filter?" -- the
                 * owner). `CompositionFields` already opens with Formula
                 * then the structure controls (see its own body below), so
                 * moving the whole group first is enough -- no need to
                 * flatten it.
                 */}
                {kind !== "transition_state" && <CompositionFields filters={filters} onChange={onChange} />}

                <TextField label="Charge" onChange={(value) => onChange({ charge: value })} value={filters.charge} />
                <TextField label="Multiplicity" onChange={(value) => onChange({ multiplicity: value })} value={filters.multiplicity} />
                <SelectField
                    label="Minimum review status"
                    onChange={(value) => onChange({ minReviewStatus: value })}
                    options={[["", "Any"], ...REVIEW_STATUSES.map((status): [string, string] => [status, token(status)])]}
                    value={filters.minReviewStatus}
                />
                <CheckField label="Include rejected" checked={filters.includeRejected} onChange={(checked) => onChange({ includeRejected: checked })} />
                <CheckField label="Include deprecated" checked={filters.includeDeprecated} onChange={(checked) => onChange({ includeDeprecated: checked })} />

                <ProvenanceFields filters={filters} kind={kind} onChange={onChange} />
                {kind === "transition_state" && <EvidenceFields filters={filters} onChange={onChange} />}
            </div>
        </form>
    )
}

function CompositionFields({ filters, onChange }: { filters: BrowseFilters; onChange: (patch: Partial<BrowseFilters>) => void }) {
    return <>
        <TextField label="Formula" onChange={(value) => onChange({ formula: value })} value={filters.formula} />
        <StructureField filters={filters} onChange={onChange} />
        <TextField label="Elements" onChange={(value) => onChange({ elements: value })} placeholder="C,N,S" value={filters.elements} />
        <SelectField
            label="Element match"
            onChange={(value) => onChange({ elemMode: value as "all" | "any" })}
            options={[["all", "All listed elements"], ["any", "Any listed element"]]}
            value={filters.elemMode}
        />
        <TextField label="Min heavy atoms" onChange={(value) => onChange({ minHeavyAtoms: value })} value={filters.minHeavyAtoms} />
        <TextField label="Max heavy atoms" onChange={(value) => onChange({ maxHeavyAtoms: value })} value={filters.maxHeavyAtoms} />
        <SelectField
            label="Electronic state"
            onChange={(value) => onChange({ electronicStateKind: value })}
            options={[["", "Any"], ...ELECTRONIC_STATES.map((state): [string, string] => [state, token(state)])]}
            value={filters.electronicStateKind}
        />
    </>
}

const STRUCTURE_MODES: [string, string][] = [
    ["substructure", "Substructure"],
    ["similarity", "Similarity"],
    ["exact", "Exact"],
]

/**
 * The browse-page home for structure search ("just make the struct and
 * smiles search part of the browser-filters class" -- the owner, after
 * an earlier pass put it on the front page instead). Reuses
 * `/scientific/species/structure-search`'s own vocabulary
 * (`query_smiles` / `query_smarts` / `mode` / `similarity_threshold`,
 * now also accepted by `/species/browse` -- see `browseApi.ts`'s
 * `buildSpeciesBrowseQuery`), composed with every other field in this
 * grid rather than a second, separate search box: one text input plus a
 * "treat as SMARTS" checkbox (never both a SMILES box and a SMARTS box
 * at once), a mode select, and a similarity threshold that appears ONLY
 * under mode=similarity -- a control with no effect in the other two
 * modes would be a dead one left on screen, and a value typed into it
 * while on `substructure`/`exact` must never travel silently as if it
 * applied.
 */
function StructureField({ filters, onChange }: { filters: BrowseFilters; onChange: (patch: Partial<BrowseFilters>) => void }) {
    const structureLabel = filters.queryIsSmarts && filters.structureMode === "substructure" ? "Structure (SMARTS)" : "Structure (SMILES)"
    return <>
        <TextField
            label={structureLabel}
            onChange={(value) => onChange({ queryStructure: value })}
            placeholder={filters.queryIsSmarts && filters.structureMode === "substructure" ? "[#6]-[#8]" : "CCO"}
            value={filters.queryStructure}
        />
        {filters.structureMode === "substructure" && (
            <CheckField
                label="Treat structure as SMARTS"
                checked={filters.queryIsSmarts}
                onChange={(checked) => onChange({ queryIsSmarts: checked })}
            />
        )}
        <SelectField
            label="Structure search mode"
            onChange={(value) => {
                const nextMode = value as BrowseFilters["structureMode"]
                // SMARTS is only valid under substructure (the backend
                // 422s otherwise, invalid_structure_query) -- clearing
                // the toggle on the way out is what keeps a reader from
                // switching to similarity/exact with a SMARTS pattern
                // still typed and getting an inexplicable refusal.
                onChange(nextMode === "substructure" ? { structureMode: nextMode } : { structureMode: nextMode, queryIsSmarts: false })
            }}
            options={STRUCTURE_MODES}
            value={filters.structureMode}
        />
        {filters.structureMode === "similarity" && (
            <TextField
                label="Similarity threshold"
                onChange={(value) => onChange({ similarityThreshold: value })}
                placeholder="0.5"
                value={filters.similarityThreshold}
            />
        )}
    </>
}

/**
 * "species" and "vdw" both map to the "species" calculation-owner scope --
 * a van der Waals complex is a `species_entry` row like any other, so it
 * has no separate `CalculationRecordKind` value (see `VocabRecordKind`'s
 * doc comment in `api/vocabApi.ts`). Only "transition_state" gets its own
 * scope.
 */
function recordKindFor(kind: BrowseKind): VocabRecordKind {
    return kind === "transition_state" ? "transition_state" : "species"
}

/**
 * The six provenance selects -- Method, Basis, Software (+version),
 * Workflow tool (+version) -- rendered for EVERY browse kind. Both
 * underlying browse endpoints accept all six query parameters (see
 * `buildSpeciesBrowseQuery` / `buildTransitionStateBrowseQuery` in
 * `api/browseApi.ts`); this section used to be folded into
 * `EvidenceFields` and mount only for "transition_state", which hid a
 * capability `/species/browse` has genuinely had all along.
 *
 * Software and Workflow tool are further scoped by `record_kind` --
 * narrowing the offered names to ones actually used by a calculation
 * owned by a record of THIS kind (see `loadSoftwareNames`'s doc comment
 * for why). Method and Basis are NOT scoped: `/meta/methods` and
 * `/meta/basis-sets` take no such parameter, so scoping the other four
 * would be inventing an inconsistency across the six selects that the
 * backend does not support -- this mirrors what the API can actually do,
 * not a design choice made here.
 */
function ProvenanceFields({ kind, filters, onChange }: {
    kind: BrowseKind
    filters: BrowseFilters
    onChange: (patch: Partial<BrowseFilters>) => void
}) {
    const recordKind = recordKindFor(kind)
    // Fetched once per mount of this section -- none of the four depend on
    // any filter value, unlike the two version vocabularies below. Each is
    // its own independent request/effect, so one failing degrades only its
    // own select (see `useVocabulary`'s doc comment) and none of them
    // block the listing itself, which fetches through the unrelated
    // `useBrowse`.
    const methodVocab = useVocabulary(loadMethods)
    const basisVocab = useVocabulary(loadBasisSets)
    // `useVocabulary` re-fetches only when the LOADER reference changes
    // (see its own doc comment), so these are memoized on `recordKind` --
    // a fresh closure every render would refire the fetch every render;
    // stable per `recordKind` refires it only on a genuine species<->TS
    // switch (never on a species<->vdw switch, since both resolve to the
    // same "species" scope).
    const loadScopedSoftwareNames = useMemo(
        () => (signal?: AbortSignal) => loadSoftwareNames(recordKind, signal),
        [recordKind],
    )
    const loadScopedWorkflowToolNames = useMemo(
        () => (signal?: AbortSignal) => loadWorkflowToolNames(recordKind, signal),
        [recordKind],
    )
    const softwareVocab = useVocabulary(loadScopedSoftwareNames)
    const workflowToolVocab = useVocabulary(loadScopedWorkflowToolNames)
    const softwareVersionVocab = useVersionVocabulary(filters.software, loadSoftwareVersions)
    const workflowToolVersionVocab = useVersionVocabulary(filters.workflowTool, loadWorkflowToolVersions)

    return <>
        <VocabField label="Method" onChange={(value) => onChange({ method: value })} value={filters.method} vocab={methodVocab} />
        <VocabField label="Basis" onChange={(value) => onChange({ basis: value })} value={filters.basis} vocab={basisVocab} />
        <VocabField label="Software" onChange={(value) => onChange({ software: value, softwareVersion: "" })} value={filters.software} vocab={softwareVocab} />
        <VersionField
            label="Software version"
            onChange={(value) => onChange({ softwareVersion: value })}
            parent={filters.software}
            parentLabel="software"
            value={filters.softwareVersion}
            vocab={softwareVersionVocab}
        />
        <VocabField label="Workflow tool" onChange={(value) => onChange({ workflowTool: value, workflowToolVersion: "" })} value={filters.workflowTool} vocab={workflowToolVocab} />
        <VersionField
            label="Workflow tool version"
            onChange={(value) => onChange({ workflowToolVersion: value })}
            parent={filters.workflowTool}
            parentLabel="workflow tool"
            value={filters.workflowToolVersion}
            vocab={workflowToolVersionVocab}
        />
    </>
}

/**
 * `Status` and the seven `has_*` evidence flags -- transition-state ONLY.
 * `/species/browse` accepts none of these (unlike the six provenance
 * fields above, which it does accept), so this section stays gated on
 * `kind === "transition_state"` in `BrowseFilterForm`.
 */
function EvidenceFields({ filters, onChange }: { filters: BrowseFilters; onChange: (patch: Partial<BrowseFilters>) => void }) {
    return <>
        <SelectField
            label="Status"
            onChange={(value) => onChange({ status: value })}
            options={[["", "Any"], ...TS_STATUSES.map((status): [string, string] => [status, token(status)])]}
            value={filters.status}
        />
        <TriField label="Has optimization" onChange={(value) => onChange({ hasOpt: value })} value={filters.hasOpt} />
        <TriField label="Has frequency" onChange={(value) => onChange({ hasFreq: value })} value={filters.hasFreq} />
        <TriField label="Has single point" onChange={(value) => onChange({ hasSp: value })} value={filters.hasSp} />
        <TriField label="Has IRC" onChange={(value) => onChange({ hasIrc: value })} value={filters.hasIrc} />
        <TriField label="Has path search" onChange={(value) => onChange({ hasPathSearch: value })} value={filters.hasPathSearch} />
        <TriField label="Has geometry validation" onChange={(value) => onChange({ hasGeometryValidation: value })} value={filters.hasGeometryValidation} />
        <TriField label="Has SCF stability" onChange={(value) => onChange({ hasScfStability: value })} value={filters.hasScfStability} />
    </>
}

function TextField({ label, value, onChange, placeholder }: {
    label: string
    value: string
    onChange: (value: string) => void
    placeholder?: string
}) {
    const id = fieldId(label)
    return (
        <div className="browse-filter-field">
            <label htmlFor={id}>{label}</label>
            <input id={id} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} value={value} />
        </div>
    )
}

function SelectField({ label, value, onChange, options }: {
    label: string
    value: string
    onChange: (value: string) => void
    options: [string, string][]
}) {
    const id = fieldId(label)
    return (
        <div className="browse-filter-field">
            <label htmlFor={id}>{label}</label>
            <select id={id} onChange={(event) => onChange(event.target.value)} value={value}>
                {options.map(([optionValue, optionLabel]) => <option key={optionValue} value={optionValue}>{optionLabel}</option>)}
            </select>
        </div>
    )
}

/**
 * An unscoped vocabulary select (method, basis, software, workflow tool),
 * backed by `useVocabulary`. Option VALUES are the archive's stored
 * strings verbatim -- never deduped, normalised, or run through `token()`
 * -- because two values that merely look alike (e.g. Gaussian's `"16"`
 * and `"Gaussian 16, Revision C.02"`) can match different record subsets;
 * collapsing them would hide a real data inconsistency (issue #305) behind
 * a tidy control.
 *
 * No option renders a count. The vocabulary endpoints do return one, but
 * it means different things per endpoint -- structurally always 1 for
 * software/workflow-tool (both carry `UniqueConstraint("name")`), a real
 * tally elsewhere -- and a number beside an option reads as a record
 * count regardless. A figure whose meaning changes between two adjacent
 * dropdowns is worse than no figure.
 */
function VocabField({ label, value, onChange, vocab }: {
    label: string
    value: string
    onChange: (value: string) => void
    vocab: VocabularyState
}) {
    const id = fieldId(label)
    const entries = vocab.status === "ready" ? vocab.entries : []
    return (
        <div className="browse-filter-field">
            <label htmlFor={id}>{label}</label>
            <select disabled={vocab.status === "loading"} id={id} onChange={(event) => onChange(event.target.value)} value={value}>
                <option value="">Any</option>
                {entries.map((entry) => (
                    <option key={entry.value} value={entry.value}>{entry.value}</option>
                ))}
            </select>
            {vocab.status === "loading" && <p className="browse-filter-hint">Loading {label.toLowerCase()} list…</p>}
            {vocab.status === "unavailable" && <p className="browse-filter-hint">Could not load {label.toLowerCase()} list.</p>}
        </div>
    )
}

/**
 * A parent-scoped version select (software version, workflow tool
 * version), backed by `useVersionVocabulary`. Disabled in every state
 * except "ready" -- there is nothing valid to pick before the parent is
 * chosen, while its vocabulary is loading, or if the fetch failed -- so a
 * reader can never leave a version selected that no longer matches the
 * current parent. `onChange` here only ever carries THIS field's own
 * value; clearing the value on a parent change is `ProvenanceFields`'s job
 * (each `Software`/`Workflow tool` `VocabField`'s `onChange` clears its
 * paired version field in the same patch), not this component's.
 */
function VersionField({ label, parentLabel, parent, value, onChange, vocab }: {
    label: string
    parentLabel: string
    parent: string
    value: string
    onChange: (value: string) => void
    vocab: VersionVocabularyState
}) {
    const id = fieldId(label)
    const entries = vocab.status === "ready" ? vocab.entries : []
    return (
        <div className="browse-filter-field">
            <label htmlFor={id}>{label}</label>
            <select disabled={vocab.status !== "ready"} id={id} onChange={(event) => onChange(event.target.value)} value={value}>
                <option value="">Any</option>
                {entries.map((entry) => (
                    <option key={entry.value} value={entry.value}>{entry.value}</option>
                ))}
            </select>
            {vocab.status === "no-parent" && <p className="browse-filter-hint">Choose a {parentLabel} first.</p>}
            {vocab.status === "loading" && <p className="browse-filter-hint">Loading versions…</p>}
            {vocab.status === "ready" && entries.length === 0 && <p className="browse-filter-hint">No versions recorded for {parent}.</p>}
            {vocab.status === "unavailable" && <p className="browse-filter-hint">Could not load versions.</p>}
        </div>
    )
}

function TriField({ label, value, onChange }: { label: string; value: TriState; onChange: (value: TriState) => void }) {
    return (
        <SelectField
            label={label}
            onChange={(next) => onChange(next as TriState)}
            options={[["", "Any"], ["true", "Yes"], ["false", "No"]]}
            value={value}
        />
    )
}

function CheckField({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
    const id = fieldId(label)
    return (
        <div className="browse-filter-field browse-filter-field-check">
            <input checked={checked} id={id} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
            <label htmlFor={id}>{label}</label>
        </div>
    )
}
