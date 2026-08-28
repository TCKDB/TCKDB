import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import type { ConformerGroup } from "../api/conformerGroupApi"
import { useConformerGroup } from "../hooks/useConformerGroup"

const lotLabel = (value: { method: string; basis?: string | null; display?: string }) => (
    value.display ?? (value.basis ? `${value.method}/${value.basis}` : value.method)
)
const statusLabel = (status: string) => status.replaceAll("_", " ")
type Observation = NonNullable<ConformerGroup["observations"]>[number]
type GeometryLink = NonNullable<ConformerGroup["geometries"]>[number]

export default function ConformerGroupPage() {
    const { groupRef = "" } = useParams<{ groupRef: string }>()
    const state = useConformerGroup(groupRef)

    if (state.status === "loading") return <State title="Loading conformer basin…" busy />
    if (state.status !== "ready") {
        const title = state.status === "missing"
            ? "Conformer basin not found"
            : "Conformer basin unavailable"
        return <State title={title} ref={groupRef} alert={state.status !== "missing"} />
    }
    return <Ledger group={state.group} />
}

function State({ title, ref, busy, alert }: {
    title: string
    ref?: string
    busy?: boolean
    alert?: boolean
}) {
    const message = busy
        ? "Retrieving the conformer basin and its deposited evidence."
        : alert
            ? "The archive response could not be read. Try again later."
            : "No conformer group with this stable reference is available in this archive projection."

    return (
        <section className="record-placeholder" aria-busy={busy} role={alert ? "alert" : undefined}>
            <p className="eyebrow">Archive record</p>
            <h1>{title}</h1>
            {ref && <code>{ref}</code>}
            <p>{message}</p>
        </section>
    )
}

function Ledger({ group }: { group: ConformerGroup }) {
    const {
        conformer_group: basin,
        species,
        observations_summary: summary,
        evidence_summary: evidence,
    } = group
    const observations = group.observations ?? []
    const geometries = groupGeometries(group.geometries ?? [])

    return (
        <section className="conformer-page">
            <header className="basin-header">
                <p className="eyebrow">Conformer basin · evidence ledger</p>
                <div className="basin-title">
                    <h1>{basin.label ?? basin.conformer_group_ref}</h1>
                    <span className="review-badge">{statusLabel(basin.review.status)}</span>
                </div>
                <p className="basin-intro">
                    One torsional basin, shown through its deposited observations. Calculation rows
                    are evidence attached to those observations; they are not separate conformers.
                </p>
                <dl className="basin-context">
                    <div><dt>Group ref</dt><dd>{basin.conformer_group_ref}</dd></div>
                    <div>
                        <dt>Species entry</dt>
                        <dd>
                            <Link to={`/species-entries/${species.species_entry_ref}`}>
                                {species.species_entry_label ?? species.species_entry_ref}
                            </Link>
                        </dd>
                    </div>
                    <div><dt>Structure</dt><dd>{species.canonical_smiles ?? "Not projected"}</dd></div>
                </dl>
            </header>
            <section className="ledger-summary" aria-label="Basin evidence summary">
                <Metric label="Deposited observations" value={summary.total} />
                <Metric
                    label="Calculation rows"
                    value={evidence.calculation_count}
                    detail={`${evidence.optimization_chain_count} optimisation chains`}
                />
                <Metric label="Distinct stored geometries" value={evidence.geometry_count} />
                <div className="coverage-card">
                    <span>Observation coverage</span>
                    <strong>
                        opt {evidence.evidence_coverage.opt}/{summary.total} · freq
                        {` ${evidence.evidence_coverage.freq}/${summary.total}`} · sp
                        {` ${evidence.evidence_coverage.sp}/${summary.total}`}
                    </strong>
                    <p>Coverage says which observations have a stage, not whether methods are comparable.</p>
                </div>
            </section>
            <section className="ledger-section" aria-labelledby="observation-ledger">
                <div className="ledger-heading">
                    <p className="eyebrow">Deposited provenance</p>
                    <h2 id="observation-ledger">Observation-scoped evidence</h2>
                    <p>Methods remain on their actual calculation rows so differing levels stay visible.</p>
                </div>
                {observations.length ? (
                    <div className="observation-list">
                        {observations.map((observation) => (
                            <ObservationCard
                                key={observation.conformer_observation.conformer_observation_ref}
                                observation={observation}
                            />
                        ))}
                    </div>
                ) : (
                    <p className="empty-projection">
                        No deposited observations were returned for this conformer basin.
                    </p>
                )}
            </section>
            <section className="ledger-section geometry-ledger" aria-labelledby="geometry-ledger">
                <p className="eyebrow">Stored coordinates</p>
                <h2 id="geometry-ledger">Geometry records</h2>
                <p>
                    These are stored geometry objects linked from calculation output. Their count is
                    not a conformer count.
                </p>
                {geometries.length ? (
                    <div className="geometry-links">
                        {geometries.map(({ geometry, calculationRefs }) => (
                            <div className="geometry-link" key={geometry.geometry_ref}>
                                <Link to={`/geometries/${geometry.geometry_ref}`}>
                                    {geometry.geometry_ref}
                                </Link>
                                <span>
                                    produced by {calculationRefs.join(", ")}
                                    {geometry.natoms != null ? ` · ${geometry.natoms} atoms` : ""}
                                </span>
                            </div>
                        ))}
                    </div>
                ) : (
                    <p className="empty-projection">
                        No stored geometry links were returned for this conformer basin.
                    </p>
                )}
            </section>
        </section>
    )
}

function groupGeometries(links: GeometryLink[]) {
    const byRef = new Map<string, {
        geometry: GeometryLink["geometry"]
        calculationRefs: string[]
    }>()

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

function Metric({ label, value, detail }: { label: string; value: number; detail?: string }) {
    return (
        <div className="metric">
            <span>{label}</span>
            <strong>{value}</strong>
            {detail && <small>{detail}</small>}
        </div>
    )
}

function ObservationCard({ observation }: { observation: Observation }) {
    const core = observation.conformer_observation
    const calculations = observation.calculations ?? []
    const geometries = observation.geometries ?? []

    return (
        <article className="observation-card">
            <header>
                <div>
                    <span className="ledger-kicker">Observation</span>
                    <Link to={`/conformer-observations/${core.conformer_observation_ref}`}>
                        {core.conformer_observation_ref}
                    </Link>
                </div>
                <div>
                    <span className="review-badge">{statusLabel(core.review.status)}</span>
                    <small>{core.scientific_origin ?? "origin not recorded"}</small>
                </div>
            </header>
            {core.note && <p className="observation-note">{core.note}</p>}
            {calculations.length ? (
                <CalculationTable
                    calculations={calculations}
                    observationRef={core.conformer_observation_ref}
                />
            ) : (
                <p className="empty-stage">No calculation rows were returned for this observation.</p>
            )}
            {geometries.length > 0 && (
                <p className="observation-geometries">
                    Geometry output: {geometries.map(({ calculation_ref: calculationRef, geometry }) => (
                        <span key={`${calculationRef}:${geometry.geometry_ref}`}>
                            <Link to={`/geometries/${geometry.geometry_ref}`}>{geometry.geometry_ref}</Link>
                            {` from ${calculationRef}`}
                        </span>
                    ))}
                </p>
            )}
        </article>
    )
}

function CalculationTable({ calculations, observationRef }: {
    calculations: NonNullable<Observation["calculations"]>
    observationRef: string
}) {
    return (
        <table className="stage-table" aria-label={`Calculations for ${observationRef}`}>
            <thead>
                <tr>
                    <th scope="col">Stage</th>
                    <th scope="col">Level of theory</th>
                    <th scope="col">Software / workflow</th>
                    <th scope="col">Review</th>
                    <th scope="col">Record</th>
                </tr>
            </thead>
            <tbody>
                {calculations.map((calculation) => (
                    <tr key={calculation.calculation_ref}>
                        <td data-label="Stage">{calculation.type}</td>
                        <td data-label="Level of theory">
                            {calculation.level_of_theory
                                ? lotLabel(calculation.level_of_theory)
                                : "Not recorded"}
                        </td>
                        <td data-label="Software / workflow">
                            {calculation.software_release?.software ?? "Not recorded"}
                            {calculation.workflow_tool_release?.workflow_tool
                                ? ` · ${calculation.workflow_tool_release.workflow_tool}`
                                : ""}
                        </td>
                        <td data-label="Review">
                            {calculation.review
                                ? statusLabel(calculation.review.status)
                                : "Not recorded"}
                        </td>
                        <td data-label="Record">
                            <Link to={`/calculations/${calculation.calculation_ref}`}>
                                {calculation.calculation_ref}
                            </Link>
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
    )
}
