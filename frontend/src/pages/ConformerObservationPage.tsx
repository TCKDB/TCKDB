import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import type { ConformerObservation } from "../api/conformerObservationApi"
import { useConformerObservation } from "../hooks/useConformerObservation"

const lotLabel = (value: { method: string; basis?: string | null; display?: string }) => (
    value.display ?? (value.basis ? `${value.method}/${value.basis}` : value.method)
)
const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "Not recorded")
type CalculationEntry = NonNullable<ConformerObservation["calculations"]>[number]
type GeometryLink = NonNullable<ConformerObservation["geometries"]>[number]
type SiblingObservation = NonNullable<ConformerObservation["observations"]>[number]

export default function ConformerObservationPage() {
    const { observationRef = "" } = useParams<{ observationRef: string }>()
    const state = useConformerObservation(observationRef)

    if (state.status === "loading") return <State title="Loading conformer observation…" busy />
    if (state.status !== "ready") {
        const title = state.status === "missing"
            ? "Conformer observation not found"
            : "Conformer observation unavailable"
        return <State title={title} ref={observationRef} alert={state.status !== "missing"} />
    }
    return <ObservationDetail observation={state.observation} />
}

function State({ title, ref, busy, alert }: {
    title: string
    ref?: string
    busy?: boolean
    alert?: boolean
}) {
    const message = busy
        ? "Retrieving the deposited observation and its provenance boundary to derived calculations."
        : alert
            ? "The archive response could not be read. Try again later."
            : "No conformer observation with this stable reference is available in this archive projection."

    return (
        <section className="record-placeholder" aria-busy={busy} role={alert ? "alert" : undefined}>
            <p className="eyebrow">Archive record</p>
            <h1>{title}</h1>
            {ref && <code>{ref}</code>}
            <p>{message}</p>
        </section>
    )
}

function ObservationDetail({ observation }: { observation: ConformerObservation }) {
    const {
        conformer_observation: core,
        conformer_group: group,
        species,
        evidence_summary: evidence,
    } = observation
    const calculations = observation.calculations ?? []
    const geometries = groupGeometries(observation.geometries ?? [])
    const siblings = (observation.observations ?? [])
        .filter((sibling) => sibling.conformer_observation.conformer_observation_ref !== core.conformer_observation_ref)
    const reviewHistory = observation.review_history ?? []
    const selections = observation.selections ?? []
    const stages = Object.entries(evidence.levels_of_theory)

    return (
        <section className="conformer-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to={`/species-entries/${species.species_entry_ref}`}>
                    {species.species_entry_label ?? species.species_entry_ref}
                </Link>
                <span aria-hidden="true">/</span>
                <Link to={`/conformer-groups/${group.conformer_group_ref}`}>
                    {group.label ?? group.conformer_group_ref}
                </Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Observation</span>
            </nav>
            <header className="basin-header">
                <p className="eyebrow">Conformer observation · deposited evidence</p>
                <div className="basin-title">
                    <h1>{core.conformer_observation_ref}</h1>
                    <span className="review-badge">{statusLabel(core.review.status)}</span>
                </div>
                <p className="basin-intro">
                    One deposition of evidence for this torsional basin, and the provenance boundary to the
                    calculations and geometries derived from it. This is one observation, not the basin itself.
                </p>
                <dl className="basin-context">
                    <div><dt>Scientific origin</dt><dd>{core.scientific_origin ?? "Not recorded"}</dd></div>
                    <div><dt>Deposited</dt><dd>{isoDate(core.created_at)}</dd></div>
                    <div>
                        <dt>Conformer basin</dt>
                        <dd>
                            <Link to={`/conformer-groups/${group.conformer_group_ref}`}>
                                {group.label ?? group.conformer_group_ref}
                            </Link>
                        </dd>
                    </div>
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
                {core.note && <p className="observation-note">{core.note}</p>}
            </header>

            <section className="ledger-summary" aria-label="Observation evidence summary">
                <Metric label="Calculation rows" value={evidence.calculation_count} />
                <Metric label="Distinct stored geometries" value={evidence.geometry_count} />
                <Metric label="Other observations in this basin" value={siblings.length} />
                <div className="coverage-card">
                    <span>Evidence present on this observation</span>
                    <strong>
                        opt {evidence.has_opt ? "yes" : "no"} · freq {evidence.has_freq ? "yes" : "no"} · sp
                        {` ${evidence.has_sp ? "yes" : "no"}`} · geometry validation
                        {` ${evidence.has_geometry_validation ? "yes" : "no"}`} · SCF stability
                        {` ${evidence.has_scf_stability ? "yes" : "no"}`}
                    </strong>
                    <p>Presence says this observation carries that stage, not that it is comparable across siblings.</p>
                </div>
            </section>

            {stages.length > 0 && (
                <section className="ledger-section" aria-labelledby="lot-by-stage">
                    <div className="ledger-heading">
                        <p className="eyebrow">Deposited provenance</p>
                        <h2 id="lot-by-stage">Levels of theory by stage</h2>
                        <p>Each stage keeps its own method. Differing levels across stages are never flattened.</p>
                    </div>
                    <dl className="basin-context">
                        {stages.map(([stage, levels]) => (
                            <div key={stage}>
                                <dt>{stage}</dt>
                                <dd>{levels.map((level) => lotLabel(level)).join(", ") || "Not recorded"}</dd>
                            </div>
                        ))}
                    </dl>
                </section>
            )}

            <section className="ledger-section" aria-labelledby="calc-ledger">
                <div className="ledger-heading">
                    <p className="eyebrow">Machine detail</p>
                    <h2 id="calc-ledger">Calculation evidence</h2>
                    <p>
                        Rows are listed in the order the archive returned them. No dependency ordering is drawn
                        between rows here — that relationship is only shown when explicit dependency data backs it.
                    </p>
                </div>
                {calculations.length ? (
                    <CalculationTable calculations={calculations} observationRef={core.conformer_observation_ref} />
                ) : (
                    <p className="empty-projection">No calculation rows were returned for this observation.</p>
                )}
            </section>

            <section className="ledger-section geometry-ledger" aria-labelledby="geometry-ledger">
                <p className="eyebrow">Stored coordinates</p>
                <h2 id="geometry-ledger">Geometry records</h2>
                <p>
                    These are stored geometry objects linked from this observation's calculation output. Their
                    count is not a conformer count and is tracked separately from the calculation-row count above.
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
                        No stored geometry links were returned for this observation.
                    </p>
                )}
            </section>

            <section className="ledger-section" aria-labelledby="sibling-ledger">
                <div className="ledger-heading">
                    <p className="eyebrow">Deposited provenance</p>
                    <h2 id="sibling-ledger">Sibling observations</h2>
                    <p>Each sibling is an independent deposition; none of them is this observation.</p>
                </div>
                {siblings.length ? (
                    <ul className="observation-list">
                        {siblings.map((sibling) => (
                            <SiblingRow key={sibling.conformer_observation.conformer_observation_ref} sibling={sibling} />
                        ))}
                    </ul>
                ) : (
                    <p className="empty-projection">
                        No other deposited observations were returned for this basin.
                    </p>
                )}
            </section>

            <section className="ledger-section" aria-labelledby="review-ledger">
                <div className="ledger-heading">
                    <p className="eyebrow">Review &amp; trust</p>
                    <h2 id="review-ledger">Review history</h2>
                    <p>The current status is {statusLabel(core.review.status)}. This is the record of how it got there.</p>
                </div>
                {reviewHistory.length ? (
                    <table className="stage-table" aria-label={`Review history for ${core.conformer_observation_ref}`}>
                        <thead>
                            <tr>
                                <th scope="col">Status</th>
                                <th scope="col">Reviewed at</th>
                                <th scope="col">Note</th>
                            </tr>
                        </thead>
                        <tbody>
                            {reviewHistory.map((entry, index) => (
                                <tr key={`${entry.status}-${entry.reviewed_at ?? index}`}>
                                    <td data-label="Status">{statusLabel(entry.status)}</td>
                                    <td data-label="Reviewed at">{isoDate(entry.reviewed_at)}</td>
                                    <td data-label="Note">{entry.note ?? "Not recorded"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : (
                    <p className="empty-projection">No review history was returned for this observation.</p>
                )}
            </section>

            {selections.length > 0 && (
                <details className="ledger-section">
                    <summary>Curation selections ({selections.length})</summary>
                    <ul>
                        {selections.map((selection, index) => (
                            <li key={`${selection.selection_kind}-${index}`}>
                                {selection.selection_kind}
                                {selection.assignment_scheme ? ` · ${selection.assignment_scheme.name}` : ""}
                            </li>
                        ))}
                    </ul>
                </details>
            )}
        </section>
    )
}

function SiblingRow({ sibling }: { sibling: SiblingObservation }) {
    const core = sibling.conformer_observation
    return (
        <li>
            <Link to={`/conformer-observations/${core.conformer_observation_ref}`}>
                {core.conformer_observation_ref}
            </Link>
            <span> · {statusLabel(core.review.status)}</span>
        </li>
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

function Metric({ label, value }: { label: string; value: number }) {
    return (
        <div className="metric">
            <span>{label}</span>
            <strong>{value}</strong>
        </div>
    )
}

function CalculationTable({ calculations, observationRef }: {
    calculations: CalculationEntry[]
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
