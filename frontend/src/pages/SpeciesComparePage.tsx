import { useMemo } from "react"
import { Link, useParams } from "react-router-dom"
import "../data-table.css"
import "../species-overview.css"
import { BROWSE_KIND_PATHS } from "../api/browseApi"
import type { SpeciesOverview } from "../api/speciesOverviewApi"
import { Breadcrumbs } from "../components/Breadcrumbs"
import { CompareTable, type CompareTableColumn } from "../components/CompareTable"
import { DataTable, type DataColumn } from "../components/DataTable"
import { Formula } from "../components/Formula"
import { ListNotes, WalkStatus } from "../components/NumbersTable"
import { OrderNote } from "../components/OrderNote"
import { SectionHeading } from "../components/PageSections"
import { PageShell } from "../components/PageShell"
import { ReviewChip } from "../components/ReviewChip"
import { ValueLink } from "../components/ValueLink"
import { levelParts, numbersOrderSentence } from "../domain/entryNumbers"
import { versionFacts } from "../domain/entryVersions"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import {
    compareColumns, cpRows, sortNumber, thermoValueRows, valueRows,
    type CompareColumn, type CompareRow,
} from "../domain/speciesCompare"
import { valueRecordLinks } from "../domain/speciesValues"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import { useSpeciesOverview } from "../hooks/useSpeciesOverview"
import { useSpeciesValues } from "../hooks/useSpeciesValues"

const KIND_WORDS = { thermo: "Thermochemistry", statmech: "Statistical mechanics" } as const

export default function SpeciesComparePage() {
    const { speciesRef = "" } = useParams<{ speciesRef: string }>()
    const state = useSpeciesOverview(speciesRef)
    const ready = state.status === "ready" && state.speciesRef === speciesRef
    useDocumentTitle("Compare", ready ? (state.species.formula ?? state.species.canonical_smiles) : null)

    if (state.speciesRef !== speciesRef || state.status === "loading") return <State title="Loading molecule…" busy />
    if (state.status === "missing") return <State title="Molecule not found" message="No molecule with this stable reference is available in this archive projection." />
    if (state.status === "rate-limited") {
        return <State title="Archive is busy" alert message={`The archive is receiving too many requests right now. Wait ${formatWaitSeconds(state.retryAfterSeconds)} and reload the page.`} />
    }
    if (state.status !== "ready") return <State title="Molecule unavailable" alert message="The archive response could not be read. Try again later." />
    return <CompareDocument species={state.species} />
}

function State({ title, busy, alert, message }: { title: string; busy?: boolean; alert?: boolean; message?: string }) {
    return (
        <section className="record-placeholder" aria-busy={busy} role={alert ? "alert" : undefined}>
            <p className="eyebrow">Archive record</p>
            <h1>{title}</h1>
            <p>{message ?? "Retrieving the molecule and its records."}</p>
        </section>
    )
}

function Head({ column, versions }: { column: CompareColumn; versions: Map<string, string> }) {
    const { row } = column
    const { to } = valueRecordLinks({ ...row, entryRef: column.entryRef })
    const ref = <code className="t-preserve-case">{column.ref}</code>
    return (
        <span className="compare-head">
            <span className="compare-head-key">{column.entryRef ? (versions.get(column.entryRef) ?? "Another version") : "Version not stated by the archive"}</span>
            {to ? <Link to={to}>{ref}</Link> : ref}
            {row.levels.length > 0
                ? <span>{levelParts(row.levels).map((part) => <span key={part.label} className="compare-level"><span className="t-preserve-case">{part.label}</span> ({part.roles.join(", ")}) </span>)}</span>
                : <span>No level of theory recorded</span>}
            {row.programs.length > 0 && <span className="t-preserve-case">{row.programs.join(", ")}</span>}
            <ReviewChip status={row.review} explain={false} />
        </span>
    )
}

function DataView({ columns, rows, versions, order, kind }: {
    columns: readonly CompareColumn[]
    rows: readonly CompareRow[]
    versions: Map<string, string>
    order: string
    kind: "thermo" | "statmech"
}) {
    const entries = useMemo(() => columns.map((column, index) => ({ column, index })), [columns])
    type Entry = (typeof entries)[number]
    const cells: DataColumn<Entry>[] = rows.map((row) => ({
        key: row.key,
        name: row.label,
        header: <>{row.label}{row.unit && <>, <span className="t-preserve-case">{row.unit}</span></>}</>,
        sortValue: ({ index }) => {
            const cell = row.cells[index]
            return sortNumber(cell) ?? (cell.kind === "value" && cell.value.value !== null && cell.value.kind !== "number" ? cell.value.value : null)
        },
        cell: ({ column, index }) => {
            const cell = row.cells[index]
            if (cell.kind === "absent") return <span className="compare-absent">{cell.words}</span>
            return cell.value.kind === "number"
                ? <ValueLink value={cell.value.value} to={valueRecordLinks({ ...column.row, entryRef: column.entryRef }).to} />
                : <>{cell.value.value}</>
        },
    }))
    const all: DataColumn<Entry>[] = [
        { key: "record", name: "record", header: "Record", rowHeader: true, sortValue: ({ column }) => column.ref, cell: ({ column }) => <code className="t-preserve-case">{column.ref}</code> },
        {
            key: "version", name: "version", header: "Version",
            sortValue: ({ column }) => (column.entryRef ? (versions.get(column.entryRef) ?? "Another version") : null),
            cell: ({ column }) => (column.entryRef ? (versions.get(column.entryRef) ?? "Another version") : "Not stated by the archive"),
        },
        { key: "review", name: "review status", header: "Review", sortValue: ({ column }) => column.row.review, cell: ({ column }) => <ReviewChip status={column.row.review} explain={false} /> },
        ...cells,
    ]
    return (
        <DataTable<Entry>
            caption={`${KIND_WORDS[kind]} records, one row per record`}
            label={`${KIND_WORDS[kind]} records as a list`}
            rows={entries}
            rowKey={({ column }) => column.key}
            columns={all}
            order={order}
        />
    )
}

function CompareDocument({ species }: { species: SpeciesOverview }) {
    const state = useSpeciesValues(species.species_ref)
    const data = state.status === "ready" ? state.data : null
    const columns = useMemo(() => (data ? compareColumns(data) : null), [data])
    const versions = useMemo(() => new Map(species.entries.map((entry) => [entry.species_entry_ref, versionFacts(entry)])), [species])
    const name = species.formula ? <Formula value={species.formula} /> : species.canonical_smiles
    const thermoRows = useMemo(() => (columns ? [...thermoValueRows(columns.thermo), ...cpRows(columns.thermo)] : []), [columns])
    const statmechRows = useMemo(() => (columns ? valueRows(columns.statmech) : []), [columns])
    const tableColumns = (list: readonly CompareColumn[]): CompareTableColumn[] => list.map((column) => ({
        key: column.key,
        header: <Head column={column} versions={versions} />,
        to: valueRecordLinks({ ...column.row, entryRef: column.entryRef }).to,
    }))
    const order = data
        ? numbersOrderSentence((["thermo", "statmech"] as const).filter((kind) => data[kind].records.length > 0).map((kind) => ({ kind, sort: data[kind].sort })))
        : ""
    const total = columns ? columns.thermo.length + columns.statmech.length : 0
    return (
        <section className="species-overview">
            <PageShell
                identity={<>
                    <Breadcrumbs crumbs={[
                        { label: "Molecules", to: BROWSE_KIND_PATHS.species },
                        { label: name, to: `/species/${species.species_ref}` },
                        { label: "Compare" },
                    ]} />
                    <h1>Compare values for {name}</h1>
                    <p className="note" role="note">
                        Values computed at different levels of theory or with different programs are listed side by side for reading, not ranked.
                        This page never subtracts values across levels or programs, and never fills a value in from another record.
                    </p>
                </>}
            >
                <WalkStatus
                    state={state}
                    loading="Loading the records for this molecule…"
                    failed="The records for this molecule could not be loaded. Each version's page still shows its own."
                />
                {data && columns && (
                    <>
                        {total === 0 && <p className="empty-projection">No thermochemistry or statistical mechanics records are deposited for this molecule.</p>}
                        {total === 1 && <p className="note">Only one record is deposited, so there is nothing to compare it with.</p>}
                        {total > 0 && <OrderNote order={`Columns follow the archive's display order. ${order}`} />}
                        <ListNotes
                            lists={[["thermochemistry", data.thermo], ["statistical mechanics", data.statmech]]}
                            rest={() => "Each version's page lists its own records."}
                        />
                        <section aria-labelledby="compare-thermo">
                            <SectionHeading id="compare-thermo" intro="Enthalpy and entropy as deposited, with their uncertainties. Heat capacity is shown at each temperature where at least two records have a deposited point, and only inside each record's own temperature range.">
                                Thermochemistry
                            </SectionHeading>
                            {columns.thermo.length === 0
                                ? <p className="empty-projection">No thermochemistry records are deposited for this molecule.</p>
                                : <CompareTable caption="Thermochemistry, one column per record" label="Thermochemistry compared" columns={tableColumns(columns.thermo)} rows={thermoRows} />}
                        </section>
                        <section aria-labelledby="compare-statmech">
                            <SectionHeading id="compare-statmech" intro="The scalar values each statistical mechanics record states. Frequencies, torsions and conformers are on each record's own page.">
                                Statistical mechanics
                            </SectionHeading>
                            {columns.statmech.length === 0
                                ? <p className="empty-projection">No statistical mechanics records are deposited for this molecule.</p>
                                : <CompareTable caption="Statistical mechanics, one column per record" label="Statistical mechanics compared" columns={tableColumns(columns.statmech)} rows={statmechRows} />}
                        </section>
                        {total > 0 && (
                            <section aria-labelledby="compare-lists">
                                <SectionHeading id="compare-lists" intro="The same values with one row per record. Sorting is yours to choose; it does not rank the records.">
                                    Records as lists
                                </SectionHeading>
                                {columns.thermo.length > 0 && <DataView kind="thermo" columns={columns.thermo} rows={thermoRows} versions={versions} order={numbersOrderSentence([{ kind: "thermo", sort: data.thermo.sort }])} />}
                                {columns.statmech.length > 0 && <DataView kind="statmech" columns={columns.statmech} rows={statmechRows} versions={versions} order={numbersOrderSentence([{ kind: "statmech", sort: data.statmech.sort }])} />}
                            </section>
                        )}
                    </>
                )}
            </PageShell>
        </section>
    )
}
