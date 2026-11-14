import { GuideLink } from "../components/GuideLink"
import { useEffect, useMemo, useState } from "react"
import { Link } from "react-router-dom"
import "../browse.css"
import "../conformer-group.css"
import "../methods.css"
import { loadLevelOfTheoryBrowse, type CoreTreatmentValue, type LevelOfTheoryRecord } from "../api/methodsApi"
import { loadSoftwareNames, loadWorkflowToolNames, type VocabEntry } from "../api/vocabApi"
import { CompositeSchemeLink } from "../components/CompositeSchemeLink"
import { LevelLabel } from "../components/LevelLabel"
import { ListPager } from "../components/ListPager"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import {
    CORE_TREATMENT_OPTIONS,
    EMPTY_LOT_FILTER,
    filterLevelOfTheoryRecords,
    isLotFilterActive,
    type LevelOfTheoryFilter,
} from "../domain/levelOfTheoryFilter"
import { levelOfTheoryPath } from "../domain/methodsLinks"
import type { PageRequest } from "../domain/paging"
import { words } from "../domain/provenanceFormat"
import { useDocumentTitle } from "../hooks/useDocumentTitle"

type LoadState<T> = { status: "loading" } | { status: "error" } | { status: "ready"; data: T }

function useLoad<T>(load: (signal: AbortSignal) => Promise<T>, key = ""): LoadState<T> {
    const [state, setState] = useState<LoadState<T>>({ status: "loading" })
    useEffect(() => {
        let mounted = true
        const controller = new AbortController()
        setState({ status: "loading" })
        load(controller.signal)
            .then((data) => { if (mounted) setState({ status: "ready", data }) })
            .catch((error: unknown) => {
                if (!mounted) return
                if (error instanceof DOMException && error.name === "AbortError") return
                setState({ status: "error" })
            })
        return () => { mounted = false; controller.abort() }
        // `key` is the only input that re-runs a load (a server-side filter
        // choice); `load` itself is a fresh closure every render.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key])
    return state
}

/** `/methods`: levels of theory (one row per level, never grouped by method and basis text), software and workflow tools. */
export default function MethodsIndexPage() {
    useDocumentTitle("Methods")
    const [lotFilter, setLotFilter] = useState<LevelOfTheoryFilter>(EMPTY_LOT_FILTER)
    // The core-treatment choice is the API's own `core_treatment` filter, so
    // changing it re-queries; the other facets narrow what is already here.
    const [lotPage, setLotPage] = useState<{ filterKey: string; request: PageRequest | null }>({ filterKey: "", request: null })
    const lotRequest = lotPage.filterKey === lotFilter.coreTreatment ? lotPage.request : null
    const lotState = useLoad(
        (signal) => loadLevelOfTheoryBrowse(signal, { coreTreatment: lotFilter.coreTreatment }, lotRequest ?? undefined),
        `${lotFilter.coreTreatment}#${lotRequest ? `${lotRequest.offset}:${lotRequest.limit}` : ""}`,
    )
    const softwareState = useLoad((signal) => loadSoftwareNames(undefined, signal))
    const workflowToolState = useLoad((signal) => loadWorkflowToolNames(undefined, signal))

    return (
        <section className="conformer-page methods-page">
            <PageShell>
                <h1 className="t-display-2">Methods</h1>
                <p className="t-body section-intro">
                    Levels of theory, software and workflow tools behind deposited calculations.<GuideLink to="level-of-theory" />
                </p>

                <section className="ledger-section" aria-labelledby="methods-lot-heading">
                    <SectionHeading id="methods-lot-heading">Levels of theory</SectionHeading>
                    {/* Stays visible while a server-side filter is chosen, even when
                        it matches nothing: hiding the control that caused an empty
                        list would leave no way back. */}
                    {((lotState.status === "ready" && lotState.data.records.length > 0) || lotFilter.coreTreatment !== "") && (
                        <LevelOfTheoryFilterFields filter={lotFilter} onChange={setLotFilter} />
                    )}
                    {lotState.status === "ready" && (
                        <ListPager
                            meta={lotState.data.meta}
                            noun="level of theory"
                            nounPlural="levels of theory"
                            onPage={(request) => setLotPage({ filterKey: lotFilter.coreTreatment, request })}
                            label="Levels of theory paging"
                        />
                    )}
                    <LevelOfTheoryTable filter={lotFilter} state={lotState} />
                </section>

                <section className="ledger-section" aria-labelledby="methods-software-heading">
                    <SectionHeading id="methods-software-heading">
                        Software
                    </SectionHeading>
                    <VocabTable state={softwareState} nameLabel="Software" emptyText="No software usage is recorded in this archive." />
                </section>

                <section className="ledger-section" aria-labelledby="methods-workflow-heading">
                    <SectionHeading id="methods-workflow-heading">
                        Workflow tools
                    </SectionHeading>
                    <VocabTable state={workflowToolState} nameLabel="Workflow tool" emptyText="No workflow-tool usage is recorded in this archive." />
                </section>
            </PageShell>
        </section>
    )
}

const NO_DISPERSION_TEXT = "none"
const NO_SOLVENT_TEXT = "gas phase"

/** A small filter: method or basis text, two evidence facets and the core-treatment choice. */
function LevelOfTheoryFilterFields({ filter, onChange }: {
    filter: LevelOfTheoryFilter
    onChange: (filter: LevelOfTheoryFilter) => void
}) {
    return (
        <div className="browse-filters">
            <div className="browse-filter-grid">
                <div className="browse-filter-field">
                    <label htmlFor="methods-lot-filter-query">Method or basis</label>
                    <input
                        id="methods-lot-filter-query"
                        onChange={(event) => onChange({ ...filter, query: event.target.value })}
                        placeholder="b3lyp, def2tzvp…"
                        type="text"
                        value={filter.query}
                    />
                </div>
                <fieldset className="browse-filter-evidence-group">
                    <legend>Show only levels with…</legend>
                    <div className="browse-filter-evidence-checks">
                        <label className="browse-filter-evidence-check">
                            <input
                                checked={filter.hasCorrectionSchemes}
                                onChange={(event) => onChange({ ...filter, hasCorrectionSchemes: event.target.checked })}
                                type="checkbox"
                            />
                            correction schemes
                        </label>
                        <label className="browse-filter-evidence-check">
                            <input
                                checked={filter.hasFrequencyScaleFactors}
                                onChange={(event) => onChange({ ...filter, hasFrequencyScaleFactors: event.target.checked })}
                                type="checkbox"
                            />
                            frequency scale factors
                        </label>
                    </div>
                </fieldset>
                <div className="browse-filter-field">
                    <label htmlFor="methods-lot-filter-core">Core treatment</label>
                    <select
                        id="methods-lot-filter-core"
                        onChange={(event) => onChange({ ...filter, coreTreatment: event.target.value as CoreTreatmentValue | "" })}
                        value={filter.coreTreatment}
                    >
                        {CORE_TREATMENT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                </div>
            </div>
            {filter.coreTreatment !== "" && (
                <p className="note">Only levels that state their core treatment are listed.</p>
            )}
        </div>
    )
}

function LevelOfTheoryTable({ state, filter }: { state: LoadState<{ records: LevelOfTheoryRecord[] }>; filter: LevelOfTheoryFilter }) {
    // Computed unconditionally, before any status-based early return below,
    // so this hook's call order never changes across renders.
    const records = useMemo(
        () => filterLevelOfTheoryRecords(state.status === "ready" ? state.data.records : [], filter),
        [state, filter],
    )

    if (state.status === "loading") return <p className="note" role="status">Loading levels of theory…</p>
    if (state.status === "error") return <p className="empty-projection" role="alert">The archive service could not load this list. Try again later.</p>
    if (records.length === 0 && isLotFilterActive(filter)) {
        return <p className="empty-projection">No levels of theory match this filter.</p>
    }
    if (state.data.records.length === 0) return <p className="empty-projection">No levels of theory have been deposited in this archive yet.</p>
    return (
        <div className="table-scroll">
            <table className="data-table" aria-label="Levels of theory">
                <thead>
                    <tr>
                        <th scope="col">Level of theory</th>
                        <th scope="col">Core treatment</th>
                        <th scope="col">Spin treatment</th>
                        <th scope="col">Calculations</th>
                        <th scope="col">Dispersion</th>
                        <th scope="col">Solvent</th>
                    </tr>
                </thead>
                <tbody>
                    {records.map((record) => (
                        <tr key={record.level_of_theory.level_of_theory_ref}>
                            <td data-label="Level of theory" className="composite-label">
                                <LevelLabel levelOfTheory={record.level_of_theory} />
                                {record.level_of_theory.composite_scheme && (
                                    <div className="note"><CompositeSchemeLink scheme={record.level_of_theory.composite_scheme} /></div>
                                )}
                            </td>
                            <td data-label="Core treatment">{record.level_of_theory.core_treatment ? words(record.level_of_theory.core_treatment) : "not recorded"}</td>
                            <td data-label="Spin treatment">{record.level_of_theory.spin_treatment ? words(record.level_of_theory.spin_treatment) : "not recorded"}</td>
                            <td data-label="Calculations" className="num"><Link to={`${levelOfTheoryPath(record.level_of_theory.level_of_theory_ref)}#lot-usage-heading`}>{record.evidence_summary.calculation_usage_count}</Link></td>
                            <td data-label="Dispersion">{record.level_of_theory.dispersion ?? NO_DISPERSION_TEXT}</td>
                            <td data-label="Solvent">{record.level_of_theory.solvent ?? NO_SOLVENT_TEXT}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}

function VocabTable({ state, nameLabel, emptyText }: { state: LoadState<VocabEntry[]>; nameLabel: string; emptyText: string }) {
    if (state.status === "loading") return <p className="note" role="status">Loading…</p>
    if (state.status === "error") return <p className="empty-projection" role="alert">The archive service could not load this list. Try again later.</p>
    const rows = state.data
    if (rows.length === 0) return <p className="empty-projection">{emptyText}</p>
    return (
        <div className="table-scroll">
            <table className="data-table" aria-label={nameLabel}>
                <thead>
                    <tr>
                        <th scope="col">{nameLabel}</th>
                        <th scope="col">Calculations</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row) => (
                        <tr key={row.value}>
                            <td data-label={nameLabel}>{row.display_name ?? words(row.value)}</td>
                            <td data-label="Calculations" className="num">{row.count}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
