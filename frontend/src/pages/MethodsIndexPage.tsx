import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import "../conformer-group.css"
import "../methods.css"
import { loadLevelOfTheoryBrowse, type LevelOfTheoryRecord } from "../api/methodsApi"
import { loadSoftwareNames, loadWorkflowToolNames, type VocabEntry } from "../api/vocabApi"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { levelOfTheoryPath } from "../domain/methodsLinks"
import { words } from "../domain/provenanceFormat"

type LoadState<T> = { status: "loading" } | { status: "error" } | { status: "ready"; data: T }

function useLoad<T>(load: (signal: AbortSignal) => Promise<T>): LoadState<T> {
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
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])
    return state
}

/**
 * `/methods` -- the archive's provenance-vocabulary index (methods-surface
 * plan §4.1, `plan-methods-surface-v2`, not committed to this repo).
 * Replaces `RecordPlaceholderPage kind="Methods"`. Three sections, each
 * its own `.data-table`: levels of theory (grouped by
 * `level_of_theory_ref`, per ruling 1 in §0 of that plan -- NEVER by
 * method/basis display text, which two distinct levels can share while
 * differing only in dispersion, solvent, or spin treatment), software,
 * and workflow tools -- unchanged in shape from what a bare vocabulary
 * index would show; what changed is where a level-of-theory row links
 * (§4.2's real record page, not a thin anchor).
 */
export default function MethodsIndexPage() {
    const lotState = useLoad(loadLevelOfTheoryBrowse)
    const softwareState = useLoad((signal) => loadSoftwareNames(undefined, signal))
    const workflowToolState = useLoad((signal) => loadWorkflowToolNames(undefined, signal))

    return (
        <section className="conformer-page methods-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Methods</span>
            </nav>
            <PageShell>
                <p className="eyebrow">Archive index</p>
                <h1 className="t-display-1">Methods</h1>
                <p className="t-body methods-index-intro">
                    The provenance vocabulary this archive records: the levels of theory, software, and workflow
                    tools attached to at least one deposited calculation. Levels of theory below link to their
                    correction parameters and observed software, where any are deposited.
                </p>

                <section className="ledger-section" aria-labelledby="methods-lot-heading">
                    <SectionHeading
                        id="methods-lot-heading"
                        kicker="Provenance vocabulary"
                        intro="One row per distinct level-of-theory identity — two levels can share a method and basis while differing in dispersion, solvent, or spin treatment, so this table never groups on that display text."
                    >
                        Levels of theory
                    </SectionHeading>
                    <LevelOfTheoryTable state={lotState} />
                </section>

                <section className="ledger-section" aria-labelledby="methods-software-heading">
                    <SectionHeading id="methods-software-heading" kicker="Provenance vocabulary" intro="Software packages observed running at least one calculation in this archive.">
                        Software
                    </SectionHeading>
                    <VocabTable state={softwareState} nameLabel="Software" emptyText="No software usage is recorded in this archive." />
                </section>

                <section className="ledger-section" aria-labelledby="methods-workflow-heading">
                    <SectionHeading id="methods-workflow-heading" kicker="Provenance vocabulary" intro="Workflow tools observed orchestrating at least one calculation in this archive.">
                        Workflow tools
                    </SectionHeading>
                    <VocabTable state={workflowToolState} nameLabel="Workflow tool" emptyText="No workflow-tool usage is recorded in this archive." />
                </section>
            </PageShell>
        </section>
    )
}

function LevelOfTheoryTable({ state }: { state: LoadState<{ records: LevelOfTheoryRecord[] }> }) {
    if (state.status === "loading") return <p className="note" role="status">Loading levels of theory…</p>
    if (state.status === "error") return <p className="empty-projection" role="alert">The archive service could not load this list. Try again later.</p>
    const records = state.data.records
    if (records.length === 0) return <p className="empty-projection">No levels of theory have been deposited in this archive yet.</p>
    return (
        <div className="table-scroll">
            <table className="data-table" aria-label="Levels of theory">
                <thead>
                    <tr>
                        <th scope="col">Method</th>
                        <th scope="col">Basis</th>
                        <th scope="col">Dispersion</th>
                        <th scope="col">Solvent</th>
                        <th scope="col">Calculations</th>
                    </tr>
                </thead>
                <tbody>
                    {records.map((record) => (
                        <tr key={record.level_of_theory.level_of_theory_ref}>
                            <td data-label="Method">
                                <Link className="data" to={levelOfTheoryPath(record.level_of_theory.level_of_theory_ref)}>
                                    {record.level_of_theory.method}
                                </Link>
                            </td>
                            <td data-label="Basis">{record.level_of_theory.basis ?? "not recorded"}</td>
                            <td data-label="Dispersion">{record.level_of_theory.dispersion ?? "not recorded"}</td>
                            <td data-label="Solvent">{record.level_of_theory.solvent ?? "not recorded"}</td>
                            <td data-label="Calculations" className="num">{record.evidence_summary.calculation_usage_count}</td>
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
