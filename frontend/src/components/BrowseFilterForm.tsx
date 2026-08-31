import type { BrowseFilters, BrowseKind, TriState } from "../api/browseApi"

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

                {kind !== "transition_state" && <CompositionFields filters={filters} onChange={onChange} />}
                {kind === "transition_state" && <EvidenceFields filters={filters} onChange={onChange} />}
            </div>
        </form>
    )
}

function CompositionFields({ filters, onChange }: { filters: BrowseFilters; onChange: (patch: Partial<BrowseFilters>) => void }) {
    return <>
        <TextField label="Formula" onChange={(value) => onChange({ formula: value })} value={filters.formula} />
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

function EvidenceFields({ filters, onChange }: { filters: BrowseFilters; onChange: (patch: Partial<BrowseFilters>) => void }) {
    return <>
        <SelectField
            label="Status"
            onChange={(value) => onChange({ status: value })}
            options={[["", "Any"], ...TS_STATUSES.map((status): [string, string] => [status, token(status)])]}
            value={filters.status}
        />
        <TextField label="Method" onChange={(value) => onChange({ method: value })} value={filters.method} />
        <TextField label="Basis" onChange={(value) => onChange({ basis: value })} value={filters.basis} />
        <TextField label="Software" onChange={(value) => onChange({ software: value })} value={filters.software} />
        <TextField label="Software version" onChange={(value) => onChange({ softwareVersion: value })} value={filters.softwareVersion} />
        <TextField label="Workflow tool" onChange={(value) => onChange({ workflowTool: value })} value={filters.workflowTool} />
        <TextField label="Workflow tool version" onChange={(value) => onChange({ workflowToolVersion: value })} value={filters.workflowToolVersion} />
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
