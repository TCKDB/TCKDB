import { useMemo } from "react"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import { loadConformerUsage } from "../api/conformerUsageApi"
import type { ConformerGroup } from "../api/conformerGroupApi"
import { Breadcrumbs } from "../components/Breadcrumbs"
import { CalcLinks } from "../components/CalcLinks"
import { ConformerStage } from "../components/ConformerStage"
import { ConformerUsedBy } from "../components/ConformerUsedBy"
import { DepositorNote } from "../components/DepositorNote"
import { entryCrumbs } from "../components/entryCrumbs"
import { EvidenceChecklist } from "../components/EvidenceChecklist"
import { Formula } from "../components/Formula"
import { EnergyNotices, ObservationEvidence } from "../components/ObservationEvidence"
import { OrderNote } from "../components/OrderNote"
import { PageShell } from "../components/PageShell"
import { RecordMap } from "../components/RelationshipMap"
import { conformerGroupNodes } from "../domain/recordMapNodes"
import { SectionHeading } from "../components/PageSections"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { ReviewChip } from "../components/ReviewChip"
import { SpeciesEntryLink } from "../components/SpeciesEntryLink"
import { conformerGroupName } from "../domain/conformerEvidence"
import { pictureGeometryFromLinks, shapeRows, spreadLines, type ShapeRow } from "../domain/conformerGallery"
import { groupLede } from "../domain/conformerLede"
import { words } from "../domain/provenanceFormat"
import { hiddenByDefaultNote } from "../domain/paging"
import { useConformerGroup } from "../hooks/useConformerGroup"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import { useLoaded } from "../hooks/useLoaded"
import { useSpeciesOptimisations, useSpeciesSinglePoints, type SinglePointsState } from "../hooks/useSpeciesSinglePoints"

type Observation = NonNullable<ConformerGroup["observations"]>[number]
type GeometryLink = NonNullable<ConformerGroup["geometries"]>[number]

/** Whether every stage is carried by every observation: with no observations nothing is covered. */
function stageCoverageSummary(coverage: { opt: number; freq: number; sp: number }, total: number): string {
    const stages = [coverage.opt, coverage.freq, coverage.sp]
    const covered = total > 0 ? stages.filter((count) => count === total).length : 0
    return `${covered} of ${stages.length} stages covered`
}

export default function ConformerGroupPage() {
    const { groupRef = "" } = useParams<{ groupRef: string }>()
    const state = useConformerGroup(groupRef)

    if (state.status === "ready") return <Ledger group={state.record} />
    return (
        <RecordStatus
            state={state}
            ref={groupRef}
            kind="conformer"
            loadingDetail="Retrieving the conformer and its deposited evidence."
        />
    )
}

function Ledger({ group }: { group: ConformerGroup }) {
    useDocumentTitle("Conformer", group.species?.formula)
    const { conformer_group: core, species, observations_summary: summary, evidence_summary: evidence } = group
    const observations = useMemo(() => group.observations ?? [], [group.observations])
    const geometries = groupGeometries(group.geometries ?? [])
    const name = conformerGroupName(core.label)
    const formula = species.formula ?? ""
    const entryRef = species.species_entry_ref

    const singlePoints = useSpeciesSinglePoints(entryRef, true)
    const optimisations = useSpeciesOptimisations(entryRef, true)
    const usage = useLoaded(loadConformerUsage, entryRef)
    const rows = useMemo(
        () => shapeRows({ observations }, singlePoints.status === "ready" ? singlePoints.records : [], optimisations.status === "ready" ? optimisations.records : []),
        [observations, singlePoints, optimisations],
    )
    const spreads = spreadLines(rows)
    const usageData = usage.status === "ready" ? usage.data : null
    const hidden = usageData ? hiddenByDefaultNote(usageData.hidden, "records") : null
    const depositorLabel = core.label && name === null && core.label.trim() !== "" ? core.label : null

    const usageGroup = usageData?.groups.get(core.conformer_group_ref)
    const mapNodes = conformerGroupNodes({
        entry: { ref: entryRef, formula: species.formula ?? null },
        observationCount: summary.total,
        onlyObservationRef: observations.length === 1 ? observations[0].conformer_observation.conformer_observation_ref : null,
        calculationCount: evidence.calculation_count,
        geometryCount: evidence.geometry_count,
        thermoCount: usageData ? new Set(usageGroup?.thermo.map((t) => t.thermo_ref)).size : null,
        // The read lists one row per interpretation assignment, so a record naming the group twice is counted once.
        kineticsCount: usageData ? new Set(usageGroup?.kinetics.map((k) => k.kinetics_ref)).size : null,
    })

    return (
        <section className="conformer-page">
            <PageShell
                side={<RecordMap selfKind="conformer-group" selfLabel="This conformer group" nodes={mapNodes} />}
                identity={(
                    <header className="basin-header">
                        <Breadcrumbs crumbs={[
                            ...entryCrumbs({
                                speciesRef: species.species_ref, speciesEntryRef: entryRef, formula: species.formula,
                                smiles: species.canonical_smiles, facets: species,
                            }),
                            { label: name ?? "Conformer" },
                        ]} />
                        <RecordIdentityHeader
                            kicker="Conformer"
                            glyph="conformer-group"
                            review={core.review.status}
                            lede={groupLede(summary.total, evidence.calculation_count)}
                            title={<>{name ?? "Conformer"}{formula ? <> of <Formula value={formula} /></> : null}</>}
                            ownRef={{ label: "Group ref", value: core.conformer_group_ref }}
                            ownFacts={(
                                <>
                                    <div>
                                        <dt>Species entry</dt>
                                        <dd>
                                            <SpeciesEntryLink speciesEntryRef={entryRef} smiles={species.canonical_smiles} formula={species.formula} facets={species} />
                                        </dd>
                                    </div>
                                    <div><dt>Structure</dt><dd>{species.canonical_smiles ? <code>{species.canonical_smiles}</code> : "not projected"}</dd></div>
                                </>
                            )}
                        />
                        <DepositorNote>{depositorLabel}</DepositorNote>
                        <DepositorNote>{core.note}</DepositorNote>
                    </header>
                )}
            >
                <ConformerStage
                    geometryRef={pictureGeometryFromLinks(group.geometries ?? [])}
                    formula={formula}
                    drawnFrom="First deposited final geometry."
                />

                <section className="ledger-section" aria-labelledby="observation-ledger">
                    <SectionHeading
                        id="observation-ledger"
                        intro={`${evidence.optimization_chain_count} optimisation ${evidence.optimization_chain_count === 1 ? "chain" : "chains"}.`}
                    >
                        Observations
                    </SectionHeading>
                    {observations.length === 0 ? (
                        <p className="empty-projection">No deposited observations were returned for this conformer.</p>
                    ) : (
                        <>
                            <EnergyNotices singlePoints={singlePoints} optimisations={optimisations} />
                            <OrderNote />
                            {spreads.length > 0 && (
                                <ul className="shape-spread">
                                    {spreads.map((line) => (
                                        <li key={`${line.kind}-${line.levelLabel}-${line.program}`}>
                                            {line.kind === "sp" ? "Single-point energies" : "Final energies of the optimisations"} at <span className="t-preserve-case">{line.levelLabel}</span> with{" "}
                                            <span className="t-preserve-case">{line.program}</span> differ by up to {line.kjMol.toFixed(2)} <span className="t-preserve-case">kJ/mol</span> across{" "}
                                            {line.count} calculations.
                                        </li>
                                    ))}
                                </ul>
                            )}
                            <ol className="observation-items">
                                {observations.map((observation, index) => (
                                    <ObservationItem
                                        key={observation.conformer_observation.conformer_observation_ref}
                                        observation={observation}
                                        position={index + 1}
                                        row={rows[index]}
                                        singlePoints={singlePoints}
                                        optimisations={optimisations}
                                    />
                                ))}
                            </ol>
                        </>
                    )}
                </section>

                <section className="ledger-section" aria-labelledby="used-by-heading">
                    <SectionHeading
                        id="used-by-heading"
                        intro="Records naming no conformer are not listed."
                    >
                        Used by
                    </SectionHeading>
                    {usage.status === "loading" && <p className="note" role="status" aria-busy="true">Reading which records name this conformer.</p>}
                    {(usage.status === "failed" || usage.status === "rate-limited") && <p className="note" role="alert">Which records name this conformer could not be read.</p>}
                    <ConformerUsedBy
                        usage={usage}
                        group={usageData?.groups.get(core.conformer_group_ref)}
                        entryRef={entryRef}
                        groupRef={core.conformer_group_ref}
                        labelOf={() => "another conformer"}
                        showLabel={false}
                    />
                    {hidden && <p className="note">{hidden}</p>}
                </section>

                <section className="ledger-summary ledger-summary--single" aria-label="Conformer evidence checklist">
                    <EvidenceChecklist
                        heading="Evidence on this conformer"
                        summary={stageCoverageSummary(evidence.evidence_coverage, summary.total)}
                        rows={[
                            { label: "Optimisation", value: `${evidence.evidence_coverage.opt} of ${summary.total} observations` },
                            { label: "Frequency", value: `${evidence.evidence_coverage.freq} of ${summary.total} observations` },
                            { label: "Single point", value: `${evidence.evidence_coverage.sp} of ${summary.total} observations` },
                        ]}
                        note="Coverage says which observations have a stage, not whether methods are comparable."
                    />
                </section>

                <section className="ledger-section" aria-labelledby="geometry-ledger">
                    <SectionHeading
                        id="geometry-ledger"
                        intro={`${evidence.geometry_count} ${evidence.geometry_count === 1 ? "geometry" : "geometries"}. This is not a count of conformers.`}
                    >
                        Geometries
                    </SectionHeading>
                    {geometries.length ? (
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
                        <p className="empty-projection">No stored geometry links were returned for this conformer.</p>
                    )}
                </section>
            </PageShell>
        </section>
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

function ObservationItem({ observation, position, row, singlePoints, optimisations }: {
    observation: Observation
    position: number
    row: ShapeRow | undefined
    singlePoints: SinglePointsState
    optimisations: SinglePointsState
}) {
    const core = observation.conformer_observation
    const geometries = observation.geometries ?? []
    return (
        <li className="observation-item">
            <p className="observation-item-head">
                <Link to={`/conformer-observations/${core.conformer_observation_ref}`}>Observation {position}</Link>{" "}
                <code className="data t-preserve-case">{core.conformer_observation_ref}</code>{" "}
                <ReviewChip status={core.review.status} explain={false} />{" "}
                <span className="observation-origin">Origin: {words(core.scientific_origin) ?? "not recorded"}</span>
            </p>
            {row ? (
                <ObservationEvidence row={row} singlePoints={singlePoints} optimisations={optimisations} />
            ) : (
                <p className="empty-projection">No calculation rows were returned for this observation.</p>
            )}
            {geometries.length > 0 && (
                <p className="observation-geometries">
                    Geometry output: {geometries.map(({ calculation_ref: calculationRef, geometry }) => (
                        <span key={`${calculationRef}:${geometry.geometry_ref}`}>
                            <Link to={`/geometries/${geometry.geometry_ref}`}>{geometry.geometry_ref}</Link>
                            {" from "}<Link to={`/calculations/${calculationRef}`}>{calculationRef}</Link>
                        </span>
                    ))}
                </p>
            )}
            <DepositorNote>{core.note}</DepositorNote>
        </li>
    )
}
