import { useMemo } from "react"
import { Link } from "react-router-dom"
import "../entry-numbers.css"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel } from "../domain/conformerEvidence"
import { numbersOrderSentence, statmechRow, thermoRow, transportRow, type NumberKind } from "../domain/entryNumbers"
import { PREVIEW_ROWS } from "../api/entryNumbersApi"
import { useEntryNumbers } from "../hooks/useEntryNumbers"
import { useScrollToHash } from "../hooks/useScrollToHash"
import { SectionHeading } from "./PageSections"
import { OrderNote } from "./OrderNote"
import { ListNotes, NumbersTable, WalkStatus } from "./NumbersTable"

/** Every thermo, statmech and transport record of this entry with its values, levels, programs and review status. */
export function EntryNumbers({ entryRef, entryPath, conformerQuery, conformers, compact = false }: {
    entryRef: string
    entryPath: string
    /** `?conformer=...` of the shape on screen, kept on same-page links so a link does not change the selection. */
    conformerQuery: string
    conformers: ConformerProjection[]
    /** The short form for another page: a smaller heading, no grouping control, the first records only. */
    compact?: boolean
}) {
    useScrollToHash()
    const state = useEntryNumbers(entryRef, compact)
    const data = state.status === "ready" ? state.data : null
    const rows = useMemo(() => (data
        ? [...data.thermo.records.map(thermoRow), ...data.statmech.records.map(statmechRow), ...data.transport.records.map(transportRow)]
        : []), [data])
    const shownRows = compact ? rows.slice(0, PREVIEW_ROWS) : rows
    const totalRecords = data ? data.thermo.total + data.statmech.total + data.transport.total : 0
    const groupLabels = useMemo(() => new Map(conformers.map((conformer) => [conformer.conformer_group.conformer_group_ref, conformerLabel(conformer)])), [conformers])
    const tabPath = (kind: NumberKind) => `${entryPath.replace(/\/(geometry|sp|statmech|thermo|transport)$/, "")}/${kind}`
    const tabLink = (kind: "thermo" | "statmech") => `${tabPath(kind)}${conformerQuery}`
    return (
        <section className="entry-numbers" aria-labelledby="entry-numbers">
            <SectionHeading
                id="entry-numbers"
                subsection={compact}
                intro={compact ? undefined : <>
                    Every single value deposited for this one entry, each with the levels of theory and programs behind it. Values are never copied in from another entry.
                    Heat-capacity points, polynomial coefficients and frequencies are in the <Link to={tabLink("thermo")}>Thermochemistry</Link> and <Link to={tabLink("statmech")}>Statistical mechanics</Link> tabs below.
                </>}
            >
                Numbers for this entry
            </SectionHeading>
            <WalkStatus state={state} loading="Loading the numbers for this entry…" failed="The numbers for this entry could not be loaded. The tabs below still show each record." />
            {data && rows.length === 0 && (
                <p className="empty-projection">No thermochemistry, statistical mechanics or transport records are deposited for this entry.</p>
            )}
            {data && rows.length > 0 && (
                <>
                    <OrderNote order={numbersOrderSentence((["thermo", "statmech", "transport"] as const)
                        .filter((kind) => data[kind].records.length > 0)
                        .map((kind) => ({ kind, sort: data[kind].sort })))}
                    />
                    {!compact && <ListNotes
                        lists={[["thermochemistry", data.thermo], ["statistical mechanics", data.statmech], ["transport", data.transport]]}
                        rest={(label) => `The ${label.charAt(0).toUpperCase()}${label.slice(1)} tab below pages through all of them.`}
                    />}
                    {compact && totalRecords > shownRows.length && (
                        <p className="note">Showing the first {shownRows.length} of {totalRecords.toLocaleString("en-US")} records. <Link to={entryPath}>All records on the molecule page</Link></p>
                    )}
                    <NumbersTable
                        compact={compact}
                        rows={shownRows}
                        caption="Numbers for this entry, one row per record"
                        label="Table of numbers"
                        groupLabels={groupLabels}
                        hrefs={(row) => {
                            const query = row.conformerGroupRef ? `?conformer=${encodeURIComponent(row.conformerGroupRef)}` : conformerQuery
                            return { to: `${tabPath(row.kind)}${query}#${row.kind}-heading-${row.ref}`, entryPath }
                        }}
                    />
                </>
            )}
        </section>
    )
}

