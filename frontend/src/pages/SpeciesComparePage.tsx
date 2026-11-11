import { useMemo } from "react"
import { RecordPlaceholder } from "../components/RecordPlaceholder"
import { Link, useParams } from "react-router-dom"
import "../data-table.css"
import "../species-overview.css"
import { BROWSE_KIND_PATHS } from "../api/browseApi"
import type { SpeciesOverview } from "../api/speciesOverviewApi"
import { Breadcrumbs } from "../components/Breadcrumbs"
import { CompareCellView, CompareTable, type CompareTableGroup } from "../components/CompareTable"
import { DataTable, type DataColumn } from "../components/DataTable"
import { Formula } from "../components/Formula"
import { ListNotes, WalkStatus } from "../components/NumbersTable"
import { OrderNote } from "../components/OrderNote"
import { SectionHeading } from "../components/PageSections"
import { PageShell } from "../components/PageShell"
import { ReviewChip } from "../components/ReviewChip"
import { levelParts, numbersOrderSentence } from "../domain/entryNumbers"
import { versionFacts } from "../domain/entryVersions"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import {
    compareColumns, cpRows, displayLabel, groupByVersion, MAX_COLUMNS_PER_VERSION, sortNumber, thermoValueRows, valueRows,
    type CompareColumn, type CompareRow, type VersionGroup,
} from "../domain/speciesCompare"
import { valueRecordLinks } from "../domain/speciesValues"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import { useSpeciesOverview } from "../hooks/useSpeciesOverview"
import { useSpeciesValues } from "../hooks/useSpeciesValues"

const KIND_WORDS = { thermo: "Thermochemistry", statmech: "Statistical mechanics" } as const
type Kind = keyof typeof KIND_WORDS

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
        <RecordPlaceholder busy={busy} alert={alert}>
            <p className="eyebrow">Archive record</p>
            <h1>{title}</h1>
            <p>{message ?? "Retrieving the molecule and its records."}</p>
        </RecordPlaceholder>
    )
}

function versionText(entryRef: string | null, versions: Map<string, string>): string {
    return entryRef ? (versions.get(entryRef) ?? "Another version") : "Version not stated by the archive"
}

function Head({ column }: { column: CompareColumn }) {
    const { row } = column
    const { to } = valueRecordLinks({ ...row, entryRef: column.entryRef })
    const ref = <code className="t-preserve-case">{column.ref}</code>
    return (
        <span className="compare-head">
            {to ? <Link to={to}>{ref}</Link> : ref}
            {row.levels.length > 0
                ? <span>{levelParts(row.levels).map((part) => <span key={part.label} className="compare-level"><span className="t-preserve-case">{part.label}</span> ({part.roles.join(", ")})</span>)}</span>
                : <span>No level of theory recorded</span>}
            {row.programs.length > 0 && <span className="t-preserve-case">{row.programs.join(", ")}</span>}
            <ReviewChip status={row.review} explain={false} />
        </span>
    )
}

function GroupHead({ group, versions }: { group: VersionGroup; versions: Map<string, string> }) {
    return (
        <span className="compare-head">
            <span className="compare-head-key">{versionText(group.entryRef, versions)}</span>
            {group.entryRef && <Link to={`/species-entries/${group.entryRef}`}><code className="t-preserve-case">{group.entryRef}</code></Link>}
        </span>
    )
}

type Entry = { column: CompareColumn; index: number }

function DataView({ columns, rows, versions, order, kind }: {
    columns: readonly CompareColumn[]
    rows: readonly CompareRow[]
    versions: Map<string, string>
    order: string
    kind: Kind
}) {
    const entries = useMemo<Entry[]>(() => columns.map((column, index) => ({ column, index })), [columns])
    const linkOf = (column: CompareColumn) => valueRecordLinks({ ...column.row, entryRef: column.entryRef }).to
    const cells: DataColumn<Entry>[] = rows.map((row) => ({
        key: row.key,
        name: row.label,
        header: (
            <span className="data-table-head">
                <span>{displayLabel(row.label)}</span>{" "}
                {row.unit && <span className="data-table-unit t-preserve-case">{row.unit}</span>}
            </span>
        ),
        sortValue: ({ index }) => {
            const cell = row.cells[index]
            return sortNumber(cell) ?? (cell.kind === "value" && cell.value.value !== null && cell.value.kind !== "number" ? cell.value.value : null)
        },
        cell: ({ column, index }) => <CompareCellView cell={row.cells[index]} to={linkOf(column)} />,
    }))
    const all: DataColumn<Entry>[] = [
        { key: "record", name: "record", header: "Record", rowHeader: true, sortValue: ({ column }) => column.ref, cell: ({ column }) => <code className="t-preserve-case">{column.ref}</code> },
        {
            key: "version", name: "version", header: "Version",
            sortValue: ({ column }) => versionText(column.entryRef, versions),
            cell: ({ column }) => versionText(column.entryRef, versions),
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
    const versions = useMemo(() => new Map(species.entries.map((entry) => [entry.species_entry_ref, versionFacts(entry)])), [species])
    const versionOrder = useMemo(() => species.entries.map((entry) => entry.species_entry_ref), [species])
    const name = species.formula ? <Formula value={species.formula} /> : species.canonical_smiles
    const model = useMemo(() => {
        if (!data) return null
        const columns = compareColumns(data)
        const build = (kind: Kind) => {
            const groups = groupByVersion(columns[kind], versionOrder)
            const full = groups.flatMap((group) => group.columns)
            const capped = groups.map((group) => ({ ...group, columns: group.columns.slice(0, MAX_COLUMNS_PER_VERSION) }))
            const shown = capped.flatMap((group) => group.columns)
            const rowsOf = (list: CompareColumn[]) => (kind === "thermo" ? [...thermoValueRows(list), ...cpRows(list)] : valueRows(list))
            return { groups: capped, full, shown, compareRows: rowsOf(shown), listRows: rowsOf(full), cut: full.length > shown.length }
        }
        return { thermo: build("thermo"), statmech: build("statmech") }
    }, [data, versionOrder])
    const tableGroups = (groups: VersionGroup[]): CompareTableGroup[] => groups.map((group) => ({
        key: group.key,
        header: <GroupHead group={group} versions={versions} />,
        columns: group.columns.map((column) => ({
            key: column.key,
            header: <Head column={column} />,
            to: valueRecordLinks({ ...column.row, entryRef: column.entryRef }).to,
        })),
    }))
    const orderOf = (kind: Kind) => (data ? numbersOrderSentence([{ kind, sort: data[kind].sort }]) : "")
    const total = model ? model.thermo.full.length + model.statmech.full.length : 0
    const section = (kind: Kind, intro: string) => {
        if (!model) return null
        const part = model[kind]
        const id = `compare-${kind}`
        return (
            <section aria-labelledby={id}>
                <SectionHeading id={id} intro={intro}>{KIND_WORDS[kind]}</SectionHeading>
                {part.full.length === 0 && <p className="empty-projection">No {KIND_WORDS[kind].toLowerCase()} records are deposited for this molecule.</p>}
                {part.full.length === 1 && <p className="note">Only one {KIND_WORDS[kind].toLowerCase()} record is deposited, so there is nothing to compare it with.</p>}
                {part.cut && <p className="note">Showing the first {MAX_COLUMNS_PER_VERSION} records of each version here. The list below has every record.</p>}
                {part.full.length > 0 && (
                    <CompareTable
                        caption={`${KIND_WORDS[kind]}, one column per record, grouped by version`}
                        label={`${KIND_WORDS[kind]} compared`}
                        groups={tableGroups(part.groups)}
                        rows={part.compareRows}
                    />
                )}
            </section>
        )
    }
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
                {data && model && (
                    <>
                        {total === 0 && <p className="empty-projection">No thermochemistry or statistical mechanics records are deposited for this molecule.</p>}
                        {total > 0 && <OrderNote order="Columns are grouped by version; within a version, the archive's display order." />}
                        <ListNotes
                            lists={[["thermochemistry", data.thermo], ["statistical mechanics", data.statmech]]}
                            rest={() => "Each version's page lists its own records."}
                        />
                        {section("thermo", "Enthalpy and entropy as deposited, with their uncertainties. Heat capacity is shown at each temperature where at least two records have a deposited point, and only inside each record's own temperature range.")}
                        {section("statmech", "The scalar values each statistical mechanics record states. Frequencies, torsions and conformers are on each record's own page.")}
                        {total > 0 && (
                            <section aria-labelledby="compare-lists">
                                <SectionHeading id="compare-lists" intro="The same values with one row per record, every record included. Sorting is yours to choose; it does not rank the records.">
                                    Records as lists
                                </SectionHeading>
                                {(["thermo", "statmech"] as const).map((kind) => model[kind].full.length > 0 && (
                                    <DataView
                                        key={kind}
                                        kind={kind}
                                        columns={model[kind].full}
                                        rows={model[kind].listRows}
                                        versions={versions}
                                        order={`Rows are grouped by version. ${orderOf(kind)}`}
                                    />
                                ))}
                            </section>
                        )}
                    </>
                )}
            </PageShell>
        </section>
    )
}
