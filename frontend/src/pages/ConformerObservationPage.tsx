import { useMemo } from "react"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import type { ConformerObservation, ConformerObservationSibling } from "../api/conformerObservationApi"
import { Breadcrumbs } from "../components/Breadcrumbs"
import { CalcLinks } from "../components/CalcLinks"
import { ConformerStage } from "../components/ConformerStage"
import { DepositorNote } from "../components/DepositorNote"
import { Disclosure } from "../components/Disclosure"
import { entryCrumbs } from "../components/entryCrumbs"
import { EvidenceChecklist } from "../components/EvidenceChecklist"
import { Formula } from "../components/Formula"
import { EnergyNotices, ObservationEvidence } from "../components/ObservationEvidence"
import { OrderNote } from "../components/OrderNote"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { ReviewChip } from "../components/ReviewChip"
import { SpeciesEntryLink } from "../components/SpeciesEntryLink"
import { conformerGroupName } from "../domain/conformerEvidence"
import { pictureGeometryFromLinks, shapeRows } from "../domain/conformerGallery"
import { observationLede } from "../domain/conformerLede"
import { useConformerObservation } from "../hooks/useConformerObservation"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import { useSpeciesOptimisations, useSpeciesSinglePoints } from "../hooks/useSpeciesSinglePoints"

const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")
type GeometryLink = NonNullable<ConformerObservation["geometries"]>[number]
type SiblingObservation = ConformerObservationSibling

// An include-gated section is "not-requested" when its key is absent, "empty" when it is null or [] and "populated"
// otherwise, so a section that vanished from a response is never reported as returned and empty.
type SectionAvailability = "not-requested" | "empty" | "populated"

function sectionAvailability<T>(value: T[] | null | undefined): SectionAvailability {
    if (value === undefined) return "not-requested"
    if (value === null || value.length === 0) return "empty"
    return "populated"
}

export default function ConformerObservationPage() {
    const { observationRef = "" } = useParams<{ observationRef: string }>()
    const state = useConformerObservation(observationRef)

    if (state.status === "ready") return <ObservationDetail observation={state.record} />
    return (
        <RecordStatus
            state={state}
            ref={observationRef}
            kind="conformer observation"
            loadingDetail="Retrieving the deposited observation and its provenance boundary to derived calculations."
        />
    )
}

function ObservationDetail({ observation }: { observation: ConformerObservation }) {
    useDocumentTitle("Conformer observation", observation.species?.formula)
    const {
        conformer_observation: core,
        conformer_group: group,
        species,
        evidence_summary: evidence,
        available_sections: available,
    } = observation

    const calculationsAvailability = sectionAvailability(observation.calculations)
    const calculations = useMemo(() => observation.calculations ?? [], [observation.calculations])
    const geometriesAvailability = sectionAvailability(observation.geometries)
    const geometries = groupGeometries(observation.geometries ?? [])
    const observationsAvailability = sectionAvailability(observation.observations)
    const siblings = (observation.observations ?? [])
        .filter((sibling) => sibling.conformer_observation.conformer_observation_ref !== core.conformer_observation_ref)
    const reviewAvailability = sectionAvailability(observation.review_history)
    const reviewHistory = observation.review_history ?? []
    // `has_selections` is always false on this surface, so the array itself is the only signal.
    const selections = observation.selections ?? []
    // A placeholder row mirroring the current status (no date, no note) is not an event worth a table.
    const hasReviewEvents = reviewHistory.some((entry) => entry.reviewed_at != null || entry.note != null)
    const showReviewTable = reviewAvailability === "populated" && hasReviewEvents

    const entryRef = species.species_entry_ref
    const formula = species.formula ?? ""
    const groupName = group ? conformerGroupName(group.label) : null
    const singlePoints = useSpeciesSinglePoints(entryRef, true)
    const optimisations = useSpeciesOptimisations(entryRef, true)
    const row = useMemo(
        () => shapeRows(
            { observations: [{ conformer_observation: core, calculations }] },
            singlePoints.status === "ready" ? singlePoints.records : [],
            optimisations.status === "ready" ? optimisations.records : [],
        )[0],
        [core, calculations, singlePoints, optimisations],
    )

    return (
        <section className="conformer-page">
            <PageShell
                identity={(
                    <header className="basin-header">
                        <Breadcrumbs crumbs={[
                            ...entryCrumbs({
                                speciesRef: species.species_ref, speciesEntryRef: entryRef, formula: species.formula,
                                smiles: species.canonical_smiles, facets: species,
                            }),
                            ...(group ? [{ label: groupName ?? "Conformer", to: `/conformer-groups/${group.conformer_group_ref}` }] : []),
                            { label: "Observation" },
                        ]} />
                        <RecordIdentityHeader
                            kicker="Conformer observation"
                            glyph="conformer-group"
                            review={core.review.status}
                            lede={observationLede()}
                            title={<>
                                {groupName ? `Observation of ${groupName}` : "Conformer observation"}
                                {formula ? <> of <Formula value={formula} /></> : null}
                            </>}
                            ownRef={{ label: "Observation ref", value: core.conformer_observation_ref }}
                            ownFacts={(
                                <>
                                    <div><dt>Scientific origin</dt><dd>{core.scientific_origin ?? "not recorded"}</dd></div>
                                    <div><dt>Deposited</dt><dd>{isoDate(core.created_at)}</dd></div>
                                    {/* Absent (not "unknown") when the read is curated and the conformer is rejected or deprecated. */}
                                    {group && (
                                        <div>
                                            <dt>Conformer</dt>
                                            <dd>
                                                <Link to={`/conformer-groups/${group.conformer_group_ref}`}>{groupName ?? "This conformer"}</Link>
                                            </dd>
                                        </div>
                                    )}
                                    <div>
                                        <dt>Species entry</dt>
                                        <dd>
                                            <SpeciesEntryLink speciesEntryRef={entryRef} smiles={species.canonical_smiles} formula={species.formula} facets={species} />
                                        </dd>
                                    </div>
                                    <div><dt>Species ref</dt><dd><code className="data">{species.species_ref}</code></dd></div>
                                    <div><dt>Structure</dt><dd>{species.canonical_smiles ? <code>{species.canonical_smiles}</code> : "not projected"}</dd></div>
                                    {species.inchi_key && <div><dt>InChIKey</dt><dd><code>{species.inchi_key}</code></dd></div>}
                                    {(species.charge !== null && species.charge !== undefined
                                        && species.multiplicity !== null && species.multiplicity !== undefined) && (
                                        <div><dt>Charge / multiplicity</dt><dd>{species.charge} / {species.multiplicity}</dd></div>
                                    )}
                                </>
                            )}
                        />
                        <DepositorNote>{core.note}</DepositorNote>
                    </header>
                )}
            >
                <ConformerStage
                    geometryRef={pictureGeometryFromLinks(observation.geometries ?? [])}
                    formula={formula}
                    drawnFrom="Final geometry of this observation."
                />

                <section className="ledger-section" aria-labelledby="calc-ledger">
                    <SectionHeading
                        id="calc-ledger"
                        intro={`${evidence.calculation_count} ${evidence.calculation_count === 1 ? "calculation" : "calculations"}, in archive order (not dependency order).`}
                    >
                        Calculations and energies
                    </SectionHeading>
                    {calculationsAvailability === "populated" ? (
                        <>
                            <EnergyNotices singlePoints={singlePoints} optimisations={optimisations} />
                            <ObservationEvidence row={row} singlePoints={singlePoints} optimisations={optimisations} />
                        </>
                    ) : (
                        <SectionEmptyMessage
                            availability={calculationsAvailability}
                            emptyText="No calculation rows were returned for this observation."
                            contradicted={calculationsAvailability === "empty" && available.has_calculations}
                        />
                    )}
                </section>

                <section className="ledger-summary ledger-summary--single" aria-label="Observation evidence checklist">
                    <EvidenceChecklist
                        heading="Evidence on this observation"
                        rows={[
                            { label: "Optimisation", value: evidence.has_opt ? "present" : "absent", tone: evidence.has_opt ? "pill" : "pill-muted" },
                            { label: "Frequency", value: evidence.has_freq ? "present" : "absent", tone: evidence.has_freq ? "pill" : "pill-muted" },
                            { label: "Single point", value: evidence.has_sp ? "present" : "absent", tone: evidence.has_sp ? "pill" : "pill-muted" },
                            {
                                label: "Geometry validation",
                                value: evidence.has_geometry_validation ? "recorded" : "not recorded",
                                tone: evidence.has_geometry_validation ? "pill" : "pill-muted",
                            },
                            {
                                label: "SCF stability",
                                value: evidence.has_scf_stability ? "recorded" : "not recorded",
                                tone: evidence.has_scf_stability ? "pill" : "pill-muted",
                            },
                        ]}
                        note='Presence says this observation carries that check, not that the result was favourable. "SCF stability recorded" means a stability test ran, not that the wavefunction was stable.'
                    />
                </section>

                <section className="ledger-section" aria-labelledby="geometry-ledger">
                    <SectionHeading
                        id="geometry-ledger"
                        intro={`${evidence.geometry_count} ${evidence.geometry_count === 1 ? "geometry" : "geometries"}. This is not a count of conformers.`}
                    >
                        Geometries
                    </SectionHeading>
                    {geometriesAvailability === "populated" ? (
                        <div className="geometry-links">
                            {geometries.map(({ geometry, calculationRefs }) => (
                                <div className="geometry-link" key={geometry.geometry_ref}>
                                    <Link to={`/geometries/${geometry.geometry_ref}`}>{geometry.geometry_ref}</Link>
                                    <span>
                                        produced by <CalcLinks refs={calculationRefs} />
                                        {geometry.natoms != null ? ` · ${geometry.natoms} atoms` : ""}
                                    </span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <SectionEmptyMessage
                            availability={geometriesAvailability}
                            emptyText="No stored geometry links were returned for this observation."
                            contradicted={geometriesAvailability === "empty" && available.has_geometries}
                        />
                    )}
                </section>

                {/* The siblings are this conformer's other observations: with the conformer withheld (a curated read of a
                    rejected or deprecated group) naming them would name it, so the whole list goes with it. */}
                {group && (
                    <section className="ledger-section" aria-labelledby="sibling-ledger">
                        <SectionHeading
                            id="sibling-ledger"
                            intro={observationsAvailability === "populated" && siblings.length > 0 ? "Review shown only where it differs." : undefined}
                        >
                            Other observations of this conformer
                        </SectionHeading>
                        {observationsAvailability === "populated" && siblings.length > 0 ? (
                            <>
                                <OrderNote />
                                <ul className="observation-list">
                                    {siblings.map((sibling) => (
                                        <SiblingRow
                                            key={sibling.conformer_observation.conformer_observation_ref}
                                            sibling={sibling}
                                            currentStatus={core.review.status}
                                        />
                                    ))}
                                </ul>
                            </>
                        ) : (
                            <SectionEmptyMessage
                                availability={observationsAvailability === "not-requested" ? "not-requested" : "empty"}
                                emptyText="No other deposited observations were returned for this conformer."
                            />
                        )}
                    </section>
                )}

                <section className="ledger-section" aria-labelledby="review-ledger">
                    <SectionHeading
                        id="review-ledger">
                        Review history
                    </SectionHeading>
                    {showReviewTable ? (
                        <div className="table-scroll" tabIndex={0} role="region" aria-label="Table of review history">
                            <table className="data-table" aria-label={`Review history for ${core.conformer_observation_ref}`}>
                                <thead>
                                    <tr>
                                        <th scope="col">Status</th>
                                        <th scope="col">Reviewed at</th>
                                        <th scope="col">Note</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {reviewHistory.map((entry, index) => (
                                        <tr key={`review-entry-${index}`}>
                                            <td data-label="Status">{statusLabel(entry.status)}</td>
                                            <td data-label="Reviewed at">{isoDate(entry.reviewed_at)}</td>
                                            <td data-label="Note">{entry.note ?? "not recorded"}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        <SectionEmptyMessage
                            availability={reviewAvailability === "populated" ? "empty" : reviewAvailability}
                            emptyText="No review events are recorded for this observation."
                            contradicted={reviewAvailability === "empty" && available.has_review}
                        />
                    )}
                </section>

                {selections.length > 0 && (
                    <section className="ledger-section">
                        <SectionHeading id="curation-selections" label={`Curation selections (${selections.length})`}>
                            Curation selections ({selections.length})
                        </SectionHeading>
                        <Disclosure
                            id="curation-selections-disclosure"
                            summary={`${selections.length} selection${selections.length === 1 ? "" : "s"}`}
                        >
                            <ul>
                                {selections.map((selection, index) => (
                                    <li key={`${selection.selection_kind}-${index}`}>
                                        {selection.selection_kind}
                                        {selection.assignment_scheme ? ` · ${selection.assignment_scheme.name}` : ""}
                                    </li>
                                ))}
                            </ul>
                        </Disclosure>
                    </section>
                )}
            </PageShell>
        </section>
    )
}

function SiblingRow({ sibling, currentStatus }: { sibling: SiblingObservation; currentStatus: string }) {
    const core = sibling.conformer_observation
    // A sibling shows its own review only when it differs from this record's, shown once in the header.
    const statusDiffers = core.review.status !== currentStatus
    return (
        <li>
            <Link to={`/conformer-observations/${core.conformer_observation_ref}`}>
                {core.conformer_observation_ref}
            </Link>
            {statusDiffers && <>{" "}<ReviewChip status={core.review.status} explain={false} /></>}
        </li>
    )
}

function SectionEmptyMessage({ availability, emptyText, contradicted }: {
    availability: SectionAvailability
    emptyText: string
    contradicted?: boolean
}) {
    if (availability === "not-requested") {
        return <p className="empty-projection">This section was not requested for this view.</p>
    }
    return (
        <p className="empty-projection">
            {emptyText}
            {contradicted
                ? " The archive marks this observation as having recorded evidence here; this view did not return it."
                : ""}
        </p>
    )
}

function groupGeometries(links: GeometryLink[]) {
    const byRef = new Map<string, { geometry: GeometryLink["geometry"]; calculationRefs: string[] }>()
    for (const { calculation_ref: calculationRef, geometry } of links) {
        const current = byRef.get(geometry.geometry_ref)
        if (current) {
            if (!current.calculationRefs.includes(calculationRef)) current.calculationRefs.push(calculationRef)
        } else {
            byRef.set(geometry.geometry_ref, { geometry, calculationRefs: [calculationRef] })
        }
    }
    return [...byRef.values()]
}
