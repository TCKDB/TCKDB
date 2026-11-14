import { useMemo } from "react"
import { modelKindWord } from "../domain/kineticsWords"
import { recordWords, versionWords } from "../domain/recordWords"
import { RecordLink } from "../components/RecordLink"
import { RecordPlaceholder } from "../components/RecordPlaceholder"
import { useParams } from "react-router-dom"
import "../data-table.css"
import { BROWSE_KIND_PATHS } from "../api/browseApi"
import { loadReactionOverview, type ReactionOverviewRecord } from "../api/reactionOverviewApi"
import type { EnergyProfileRead } from "../api/reactionEnergyProfileApi"
import { loadReactionKinetics } from "../api/reactionKineticsApi"
import { lotLabel } from "../api/scientificSchemas"
import { Breadcrumbs } from "../components/Breadcrumbs"
import { CompareCellView, CompareTable, type CompareTableGroup } from "../components/CompareTable"
import { DataTable, type DataColumn } from "../components/DataTable"
import { Profiles } from "../components/EnergyProfile"
import { LevelDetails, LevelLabel } from "../components/LevelLabel"
import { ListNotes, WalkStatus } from "../components/NumbersTable"
import { OrderNote } from "../components/OrderNote"
import { SectionHeading } from "../components/PageSections"
import { PageShell } from "../components/PageShell"
import { ReactionEquation } from "../components/ReactionEquation"
import { ReviewChip } from "../components/ReviewChip"
import { inlineQualifiers, levelLine, type LevelKey, type Qualifier } from "../domain/levelLabel"
import { orderWords } from "../domain/paging"
import { originWord } from "../domain/kineticsWords"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import { equationNeedsStructure, equationText } from "../domain/reactionEquation"
import {
    DELTA_FORMULA, deltaNames, groupDifference, groupKinetics, kineticsColumns, MAX_COLUMNS_PER_GROUP, MAX_PROFILE_ENTRIES, profilesOfGroup, programFromSource, reactionRows,
    type KineticsColumn, type KineticsGroup,
} from "../domain/reactionCompare"
import { displayLabel, sortNumber, type CompareRow } from "../domain/speciesCompare"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import { useEnergyProfiles } from "../hooks/useEnergyProfiles"
import { useScientificRecord } from "../hooks/useScientificRecord"
import { useWalked } from "../hooks/useWalked"

const BANNER = {
    methods: "These use different methods. Differences reflect the method; neither is an error.",
    programs: "These use different programs. Differences reflect the program; neither is an error.",
    both: "These use different methods and programs. Differences mix both effects; neither is an error.",
} as const

const NOT_STATED_LEVEL = "Energy level of theory not recorded"
const NOT_STATED_PROGRAM = "Program not recorded"

export default function ReactionComparePage() {
    const { reactionRef = "" } = useParams<{ reactionRef: string }>()
    const state = useScientificRecord(reactionRef, loadReactionOverview)
    const first = state.status === "ready" ? state.record.records[0] : undefined
    useDocumentTitle("Compare", first ? equationText(first.reactants, first.products, first.reversible) : null)

    if (state.status === "loading" || state.status === "retrying") return <State title="Loading reaction…" busy />
    if (state.status === "missing" || (state.status === "ready" && !first)) {
        return <State title="Reaction not found" message="No reaction with this stable reference is available in this archive projection." />
    }
    if (state.status === "rate-limited") {
        return <State title="Archive is busy" alert message={`The archive is receiving too many requests right now. Wait ${formatWaitSeconds(state.retryAfterSeconds)} and reload the page.`} />
    }
    if (state.status !== "ready" || !first) return <State title="Reaction unavailable" alert message="The archive response could not be read. Try again later." />
    return <CompareDocument reactionRef={reactionRef} first={first} labels={speciesLabels(state.record.records)} />
}

function State({ title, busy, alert, message }: { title: string; busy?: boolean; alert?: boolean; message?: string }) {
    return (
        <RecordPlaceholder busy={busy} alert={alert}>
            <p className="eyebrow">Archive record</p>
            <h1>{title}</h1>
            <p>{message ?? "Retrieving the reaction and its rate records."}</p>
        </RecordPlaceholder>
    )
}

function speciesLabels(records: readonly ReactionOverviewRecord[]): Map<string, string> {
    const labels = new Map<string, string>()
    for (const record of records) {
        for (const participant of [...record.reactants, ...record.products]) labels.set(participant.species_entry_ref, participant.formula ?? participant.smiles)
    }
    return labels
}

const recordPath = (column: KineticsColumn) => `/reaction-entries/${column.entryRef}#kinetics-${column.ref}`
const levelText = (group: KineticsGroup) => (group.level ? `${lotLabel(group.level)}${group.levelDeclared ? " (declared)" : ""}` : NOT_STATED_LEVEL)
const groupText = (group: KineticsGroup) => `${levelText(group)}, ${group.program?.text ?? NOT_STATED_PROGRAM}`

function GroupLevel({ group }: { group: KineticsGroup }) {
    return group.level
        ? <><LevelLabel levelOfTheory={group.level} program={group.program?.text ?? null} />{group.levelDeclared && " (declared by the depositor)"}</>
        : <>{NOT_STATED_LEVEL}{group.program && ` · ${group.program.text}`}</>
}

function GroupHead({ group }: { group: KineticsGroup }) {
    return (
        <span className="compare-head">
            <span className="compare-head-key"><GroupLevel group={group} /></span>
            {!group.program && <span>{NOT_STATED_PROGRAM}</span>}
            {group.program && programFromSource(group) && <span>Program of the source calculation</span>}
        </span>
    )
}

/** A group's level and program as plain text, for a heading; qualifiers that tell it from another group's are inline. */
function groupHeading(group: KineticsGroup, inline: ReadonlyMap<LevelKey, Qualifier[]>): string {
    if (!group.level) return `${NOT_STATED_LEVEL}${group.program ? ` · ${group.program.text}` : ""}`
    return `${levelLine(group.level, inline, group.program?.text ?? null)}${group.levelDeclared ? " (declared)" : ""}`
}

const versionName = (versions: ReadonlyMap<string, number>, entryRef: string) => (versions.has(entryRef) ? versionWords(versions.get(entryRef)!) : "Another version")

function Head({ column, position, versions }: { column: KineticsColumn; position: number; versions: ReadonlyMap<string, number> }) {
    const origin = originWord(column.record.scientific_origin)
    const kind = modelKindWord(column.record.model_kind) ?? column.record.model_kind.replaceAll("_", " ")
    return (
        <span className="compare-head">
            <RecordLink kind="kinetics" to={`/reaction-entries/${column.entryRef}`} hash={`kinetics-${column.ref}`} label={recordWords(position)} detail={kind} />
            <span>Origin: {origin ?? column.record.scientific_origin.replaceAll("_", " ")}</span>
            <ReviewChip status={column.record.review.status} explain={false} />
            <span><RecordLink kind="reaction-entry" to={`/reaction-entries/${column.entryRef}`} label={versionName(versions, column.entryRef)} /></span>
        </span>
    )
}

/** True when the row's values carry more than one unit; such a row is shown but not sorted. */
function unitsDiffer(row: CompareRow): boolean {
    return row.unit === null && new Set(row.cells.flatMap((cell) => (cell.kind === "value" && cell.value.kind === "number" ? [cell.value.unit] : []))).size > 1
}

type Entry = { column: KineticsColumn; group: KineticsGroup; index: number }

function DataView({ entries, rows, order, versions }: { entries: Entry[]; rows: readonly CompareRow[]; order: string; versions: ReadonlyMap<string, number> }) {
    const cells: DataColumn<Entry>[] = rows.map((row) => ({
        key: row.key,
        name: row.label,
        header: (
            <span className="data-table-head">
                <span>{displayLabel(row.label)}</span>{" "}
                {row.unit && <span className="data-table-unit t-preserve-case">{row.unit}</span>}
                {unitsDiffer(row) && <span className="data-table-unit">units differ in this row, so it is not sortable</span>}
            </span>
        ),
        // Values in different units are not ordered against each other, and are not converted.
        sortValue: unitsDiffer(row) ? undefined : ({ index }) => {
            const cell = row.cells[index]
            return sortNumber(cell) ?? (cell.kind === "value" && cell.value.value !== null && cell.value.kind !== "number" ? cell.value.value : null)
        },
        cell: ({ column, index }) => <CompareCellView cell={row.cells[index]} to={recordPath(column)} showUnit={row.unit === null} />,
    }))
    const all: DataColumn<Entry>[] = [
        { key: "record", name: "record", header: "Record", rowHeader: true, sortValue: ({ index }) => index, cell: ({ column, index }) => <RecordLink kind="kinetics" to={`/reaction-entries/${column.entryRef}`} hash={`kinetics-${column.ref}`} label={recordWords(index + 1)} context={versionName(versions, column.entryRef)} /> },
        { key: "group", name: "group", header: "Level of theory and program", sortValue: ({ group }) => groupText(group), cell: ({ group }) => <span className="level-label-cell"><GroupLevel group={group} /></span> },
        { key: "review", name: "review status", header: "Review", sortValue: ({ column }) => column.record.review.status, cell: ({ column }) => <ReviewChip status={column.record.review.status} explain={false} /> },
        ...cells,
    ]
    return (
        <DataTable<Entry>
            caption="Rate records, one row per record"
            label="Rate records as a list"
            rows={entries}
            rowKey={({ column }) => column.key}
            columns={all}
            order={order}
        />
    )
}

function GroupProfiles({ group, position, reads, readRefs, labels, versions, inline }: {
    versions: ReadonlyMap<string, number>
    inline: ReadonlyMap<LevelKey, Qualifier[]>
    group: KineticsGroup
    position: number
    reads: ReadonlyMap<string, EnergyProfileRead | null>
    readRefs: readonly string[]
    labels: ReadonlyMap<string, string>
}) {
    const heading = groupHeading(group, inline)
    const unread = group.entryRefs.filter((ref) => !readRefs.includes(ref))
    const failed = group.entryRefs.filter((ref) => readRefs.includes(ref) && !reads.get(ref))
    const matching = profilesOfGroup(group, group.entryRefs.flatMap((ref) => reads.get(ref)?.profiles ?? []))
    let body
    if (!group.level || !group.program) {
        body = <p className="empty-projection">These records do not state both an energy level of theory and a program, so no energy profile can be matched to them.</p>
    } else if (matching.length > 0) {
        body = <Profiles read={{ profiles: matching }} labels={labels} showLevel={false} />
    } else {
        body = <p className="empty-projection">No complete energy profile is available at this level of theory from this program for the reaction entries of this group.</p>
    }
    return (
        <>
            <SectionHeading id={`compare-profile-${position}`} subsection label={heading}>{heading}</SectionHeading>
            {group.level && <LevelDetails levelOfTheory={group.level} program={group.program?.text ?? null} />}
            {group.level && group.levelDeclared && <p className="note">The level of theory is declared by the depositor.</p>}
            {unread.length > 0 && <p className="note" role="note">Not read here: {unread.map((ref) => versionName(versions, ref)).join(", ")}. Open the entry's own page for its profile.</p>}
            {failed.length > 0 && <p className="note" role="alert">The energy profile of {failed.map((ref) => versionName(versions, ref)).join(", ")} could not be loaded.</p>}
            {body}
        </>
    )
}

function CompareDocument({ reactionRef, first, labels }: { reactionRef: string; first: ReactionOverviewRecord; labels: ReadonlyMap<string, string> }) {
    const state = useWalked(reactionRef, loadReactionKinetics)
    const data = state.status === "ready" ? state.data : null
    const versions = useMemo(() => new Map((data?.records ?? []).map((row) => [row.entryRef, row.entryPosition] as const)), [data])
    const equation = <ReactionEquation reactants={first.reactants} products={first.products} reversible={first.reversible} formulaOnly={!equationNeedsStructure(first.reactants, first.products)} />
    const entryOrder = useMemo(() => [...new Set((data?.records ?? []).map((row) => row.entryRef))], [data])
    const readRefs = useMemo(() => entryOrder.slice(0, MAX_PROFILE_ENTRIES), [entryOrder])
    const profiles = useEnergyProfiles(readRefs)
    const model = useMemo(() => {
        if (!data) return null
        const full = groupKinetics(kineticsColumns(data.records))
        const shown = full.map((group) => ({ ...group, columns: group.columns.slice(0, MAX_COLUMNS_PER_GROUP) }))
        const entries: Entry[] = full.flatMap((group) => group.columns.map((column) => ({ column, group, index: 0 }))).map((entry, index) => ({ ...entry, index }))
        const positions = new Map(entries.map((entry) => [entry.column.key, entry.index + 1] as const))
        const inline = inlineQualifiers(full.flatMap((group) => (group.level ? [group.level] : [])))
        return { full, shown, entries, positions, inline, rows: reactionRows(shown), listRows: reactionRows(full), cut: shown.some((group, index) => group.columns.length < full[index].columns.length), total: entries.length }
    }, [data])
    const tableGroups: CompareTableGroup[] = (model?.shown ?? []).map((group) => ({
        key: group.key,
        header: <GroupHead group={group} />,
        columns: group.columns.map((column) => ({ key: column.key, header: <Head column={column} position={model?.positions.get(column.key) ?? 1} versions={versions} />, to: recordPath(column) })),
        ...(group.columns.length === 2 && { delta: { header: <span className="compare-head"><span className="compare-head-key"><span aria-hidden="true">Δ</span> {deltaNames(group, (column) => recordWords(model?.positions.get(column.key) ?? 1))?.join(" − ")}</span><span>Difference within this group</span></span> } }),
    }))
    const difference = model ? groupDifference(model.full) : null
    const order = `Grouped by level and program; ${orderWords(data?.sort ?? null) ?? "the archive did not state an order"}.`
    return (
        <section className="species-overview">
            <PageShell
                identity={<>
                    <Breadcrumbs crumbs={[
                        { label: "Reactions", to: BROWSE_KIND_PATHS.reaction },
                        { label: equation, to: `/reactions/${reactionRef}` },
                        { label: "Compare" },
                    ]} />
                    <h1>Compare rate coefficients for {equation}</h1>
                    <p className="note" role="note">Side by side, not ranked. Differences only within one group; nothing is filled in from another record.</p>
                </>}
            >
                <WalkStatus
                    state={state}
                    loading="Loading the rate records for this reaction…"
                    failed="The rate records for this reaction could not be loaded. Each version's page still shows its own."
                />
                {data && model && (
                    <>
                        {model.total === 0 && <p className="empty-projection">No rate records are deposited for this reaction.</p>}
                        {model.total > 0 && <OrderNote order={order} />}
                        <ListNotes lists={[["rate", data]]} rest={() => "Each version's page lists its own."} />
                        {model.total >= 1 && (
                            <section aria-labelledby="compare-rates">
                                <SectionHeading id="compare-rates" intro="As deposited; k only within each fitted range, tunnelling only if the fit had it.">
                                    Rate coefficients
                                </SectionHeading>
                                {difference && <p className="note" role="note">{BANNER[difference]}</p>}
                                {model.total === 1 && <p className="note">Only one rate record is deposited, so there is nothing to compare it with.</p>}
                                {model.cut && <p className="note">Showing the first {MAX_COLUMNS_PER_GROUP} records of each group here. The list below has every record.</p>}
                                {tableGroups.some((group) => group.delta) && <p className="note">{DELTA_FORMULA}</p>}
                                <CompareTable
                                    caption="Rate records, one column per record, grouped by energy level of theory and program"
                                    label="Rate records compared"
                                    groups={tableGroups}
                                    rows={model.rows}
                                    groupNoun="group"
                                />
                            </section>
                        )}
                        {model.total >= 1 && (
                            <section aria-labelledby="compare-profiles">
                                <SectionHeading id="compare-profiles" intro="Each group's profile has its own axis. Profiles of different groups are never drawn together.">
                                    Energy profiles by group
                                </SectionHeading>
                                {entryOrder.length > readRefs.length && <p className="note" role="note">Profiles were read for the first {readRefs.length} of {entryOrder.length} reaction entries.</p>}
                                {profiles.status === "retrying" && <p className="note" role="status" aria-busy="true">The archive is busy. Retrying in {formatWaitSeconds(profiles.retryAfterSeconds)}.</p>}
                                {profiles.status === "loading" && <p className="note" role="status" aria-busy="true">Loading the energy profiles.</p>}
                                {profiles.status === "ready" && model.full.map((group, position) => (
                                    <GroupProfiles key={group.key} group={group} position={position} reads={profiles.reads} readRefs={readRefs} labels={labels} versions={versions} inline={model.inline} />
                                ))}
                            </section>
                        )}
                        {model.total > 1 && (
                            <section aria-labelledby="compare-lists">
                                <SectionHeading id="compare-lists" intro="One row per record. Sorting does not rank.">
                                    Records as a list
                                </SectionHeading>
                                <DataView entries={model.entries} rows={model.listRows} order="Same as the table above." versions={versions} />
                            </section>
                        )}
                    </>
                )}
            </PageShell>
        </section>
    )
}
