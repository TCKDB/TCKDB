import { useMemo } from "react"
import { Link } from "react-router-dom"
import "../data-table.css"
import "../entry-numbers.css"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel } from "../domain/conformerEvidence"
import { numbersOrderSentence, statmechRow, thermoRow, transportRow, type NumberKind } from "../domain/entryNumbers"
import { PREVIEW_ROWS } from "../api/entryNumbersApi"
import { useEntryNumbers } from "../hooks/useEntryNumbers"
import { useSpeciesOverview } from "../hooks/useSpeciesOverview"
import { useScrollToHash } from "../hooks/useScrollToHash"
import { SectionHeading } from "./PageSections"
import { OrderNote } from "./OrderNote"
import { ListNotes, NumbersTable, WalkStatus } from "./NumbersTable"

/** The link to the molecule's compare page: this entry holds two records of a kind, or one that another version also has. */
function CompareLink({ speciesRef, entryRef, thermo, statmech }: { speciesRef: string; entryRef: string; thermo: number; statmech: number }) {
    const state = useSpeciesOverview(speciesRef)
    const others = state.status === "ready" && state.speciesRef === speciesRef ? state.species.entries.filter((entry) => entry.species_entry_ref !== entryRef) : []
    const offered = thermo >= 2 || statmech >= 2
        || (thermo >= 1 && others.some((entry) => entry.availability.has_thermo))
        || (statmech >= 1 && others.some((entry) => entry.availability.has_statmech))
    if (!offered) return null
    return <p><Link className="compare-link" to={`/species/${speciesRef}/compare`}>Compare these values</Link></p>
}

/** Every thermo, statmech and transport record of this entry with its values, levels, programs and review status. */
export function EntryNumbers({ entryRef, entryPath, conformerQuery, conformers, speciesRef, compact = false }: {
    entryRef: string
    entryPath: string
    /** `?conformer=...` of the shape on screen, kept on same-page links so a link does not change the selection. */
    conformerQuery: string
    conformers: ConformerProjection[]
    /** The molecule, whose compare page is offered when this entry or another version has records to set beside each other. */
    speciesRef?: string
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
    return (
        <section className="entry-numbers" aria-labelledby="entry-numbers">
            <SectionHeading
                id="entry-numbers"
                subsection={compact}
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
                    {!compact && speciesRef && (
                        <CompareLink speciesRef={speciesRef} entryRef={entryRef} thermo={data.thermo.records.length} statmech={data.statmech.records.length} />
                    )}
                    {!compact && <ListNotes
                        lists={[["thermochemistry", data.thermo], ["statistical mechanics", data.statmech], ["transport", data.transport]]}
                        rest={(label) => `All in the ${label.charAt(0).toUpperCase()}${label.slice(1)} tab.`}
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

