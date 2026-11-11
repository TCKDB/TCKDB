import { useMemo } from "react"
import { Link } from "react-router-dom"
import "../data-table.css"
import "../entry-numbers.css"
import type { SpeciesOverview } from "../api/speciesOverviewApi"
import { numbersOrderSentence } from "../domain/entryNumbers"
import { versionFacts } from "../domain/entryVersions"
import { speciesValueRows, valueRecordLinks, type SpeciesValueRow } from "../domain/speciesValues"
import { useSpeciesValues } from "../hooks/useSpeciesValues"
import { ListNotes, NumbersTable, WalkStatus } from "./NumbersTable"
import { OrderNote } from "./OrderNote"
import { SectionHeading } from "./PageSections"

const NO_LABELS = new Map<string, string>()

/** Every thermochemistry and statistical mechanics record of every version of the molecule, each row naming its version. */
export function SpeciesValues({ species }: { species: SpeciesOverview }) {
    const state = useSpeciesValues(species.species_ref)
    const data = state.status === "ready" ? state.data : null
    const rows = useMemo(() => (data ? speciesValueRows(data) : []), [data])
    const labels = useMemo(
        () => new Map(species.entries.map((entry) => [entry.species_entry_ref, versionFacts(entry)])),
        [species],
    )
    return (
        <section className="entry-numbers" aria-labelledby="species-values">
            <SectionHeading
                id="species-values"
                intro="Every thermochemistry and statistical mechanics record deposited for any version of this molecule, each with its version, levels of theory and programs. Values are never copied from one version to another. Transport records are on each version's page."
            >
                All values for this molecule
            </SectionHeading>
            <WalkStatus
                state={state}
                loading="Loading the values for this molecule…"
                failed="The values for this molecule could not be loaded. Each version's page still shows its own records."
            />
            {data && rows.length === 0 && (
                <p className="empty-projection">No thermochemistry or statistical mechanics records are deposited for this molecule.</p>
            )}
            {data && rows.length > 0 && (
                <>
                    <OrderNote order={numbersOrderSentence((["thermo", "statmech"] as const)
                        .filter((kind) => data[kind].records.length > 0)
                        .map((kind) => ({ kind, sort: data[kind].sort })))}
                    />
                    <ListNotes
                        lists={[["thermochemistry", data.thermo], ["statistical mechanics", data.statmech]]}
                        rest={() => "Each version's page lists its own records."}
                    />
                    {rows.length >= 2 && (
                        <p><Link className="compare-link" to={`/species/${species.species_ref}/compare`}>Compare these values</Link></p>
                    )}
                    <NumbersTable<SpeciesValueRow>
                        rows={rows}
                        caption="Values for this molecule, one row per record"
                        label="Table of values for this molecule"
                        groupLabels={NO_LABELS}
                        hrefs={valueRecordLinks}
                        version={(row) => (row.entryRef
                            ? <>
                                <span className="entry-numbers-key">Version:</span>{" "}
                                <Link to={`/species-entries/${row.entryRef}`} aria-describedby={`${row.kind}-${row.ref}-version-ref`}>{labels.get(row.entryRef) ?? "Another version"}</Link>{" "}
                                <code id={`${row.kind}-${row.ref}-version-ref`} className="t-preserve-case">{row.entryRef}</code>
                            </>
                            : <>Version not stated by the archive</>)}
                    />
                </>
            )}
        </section>
    )
}
