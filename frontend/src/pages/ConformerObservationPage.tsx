import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import type { ConformerObservation } from "../api/conformerObservationApi"
import { lotLabel } from "../api/scientificSchemas"
import { RecordStatus } from "../components/RecordStatus"
import { useConformerObservation } from "../hooks/useConformerObservation"

const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")
const originTitle = (origin?: string | null) => (
    origin ? `${origin.charAt(0).toUpperCase()}${origin.slice(1)} observation` : "Conformer observation"
)
type CalculationEntry = NonNullable<ConformerObservation["calculations"]>[number]
type GeometryLink = NonNullable<ConformerObservation["geometries"]>[number]
type SiblingObservation = NonNullable<ConformerObservation["observations"]>[number]

// Three states an include-gated section can be in, kept distinct per the
// house rule: absence describes the request, null describes the data.
// - "not-requested": the key was absent from the payload. This client
//   always requests every section, so in practice this should never
//   fire — but the type says `T[] | null | undefined`, and a section
//   that silently vanished from a future response must not be reported
//   as "returned and empty".
// - "empty": the key was present, and null or [] — the archive was
//   asked and had nothing to say.
// - "populated": at least one item came back.
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
    const {
        conformer_observation: core,
        conformer_group: group,
        species,
        evidence_summary: evidence,
        available_sections: available,
    } = observation

    const calculationsAvailability = sectionAvailability(observation.calculations)
    const calculations = observation.calculations ?? []

    const geometriesAvailability = sectionAvailability(observation.geometries)
    const geometries = groupGeometries(observation.geometries ?? [])

    const observationsAvailability = sectionAvailability(observation.observations)
    const siblings = (observation.observations ?? [])
        .filter((sibling) => sibling.conformer_observation.conformer_observation_ref !== core.conformer_observation_ref)

    const reviewAvailability = sectionAvailability(observation.review_history)
    const reviewHistory = observation.review_history ?? []

    // has_selections is hardcoded false on this surface even when
    // selections were returned (backend `get_conformer_observation`
    // always passes `selection_count=0`), so it cannot gate anything —
    // the array itself is the only trustworthy signal here.
    const selections = observation.selections ?? []

    const stages = Object.entries(evidence.levels_of_theory)

    return (
        <section className="conformer-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to={`/species/${species.species_ref}`}>Species</Link>
                <span aria-hidden="true">/</span>
                <Link to={`/species-entries/${species.species_entry_ref}`}>Species entry</Link>
                <span aria-hidden="true">/</span>
                <Link to={`/conformer-groups/${group.conformer_group_ref}`}>Conformer basin</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Observation</span>
            </nav>
            <header className="basin-header">
                <p className="eyebrow">Conformer observation · deposited evidence</p>
                <div className="basin-title">
                    <h1>{originTitle(core.scientific_origin)}</h1>
                    <span className="review-badge">{statusLabel(core.review.status)}</span>
                </div>
                <p className="basin-intro">
                    One deposition of evidence for this torsional basin, and the provenance boundary to the
                    calculations and geometries derived from it. This is one observation, not the basin itself.
                </p>
                <dl className="basin-context">
                    <div><dt>Observation ref</dt><dd>{core.conformer_observation_ref}</dd></div>
                    <div><dt>Scientific origin</dt><dd>{core.scientific_origin ?? "not recorded"}</dd></div>
                    <div><dt>Deposited</dt><dd>{isoDate(core.created_at)}</dd></div>
                    <div>
                        <dt>Conformer basin</dt>
                        <dd>
                            <Link to={`/conformer-groups/${group.conformer_group_ref}`}>
                                {group.label ?? group.conformer_group_ref}
                            </Link>
                        </dd>
                    </div>
                    <div><dt>Group ref</dt><dd>{group.conformer_group_ref}</dd></div>
                    <div>
                        <dt>Species entry</dt>
                        <dd>
                            <Link to={`/species-entries/${species.species_entry_ref}`}>
                                {species.species_entry_label ?? species.species_entry_ref}
                            </Link>
                        </dd>
                    </div>
                    <div><dt>Species ref</dt><dd>{species.species_ref}</dd></div>
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
                        {` ${evidence.has_geometry_validation ? "recorded" : "not recorded"}`} · SCF stability
                        {` ${evidence.has_scf_stability ? "recorded" : "not recorded"}`}
                    </strong>
                    <p>
                        Presence says this observation carries that check, not that the result was favourable —
                        "SCF stability recorded" means a stability test ran, not that the wavefunction was stable.
                    </p>
                </div>
            </section>

            <section className="ledger-section" aria-labelledby="lot-by-stage">
                <div className="ledger-heading">
                    <p className="eyebrow">Deposited provenance</p>
                    <h2 id="lot-by-stage">Levels of theory by stage</h2>
                    <p>Each stage keeps its own method. Differing levels across stages are never flattened.</p>
                </div>
                {stages.length ? (
                    <dl className="basin-context">
                        {stages.map(([stage, levels]) => (
                            <div key={stage}>
                                <dt>{stage}</dt>
                                <dd>{levels.map((level) => lotLabel(level)).join(", ") || "not recorded"}</dd>
                            </div>
                        ))}
                    </dl>
                ) : (
                    <p className="empty-projection">No levels of theory were recorded for this observation.</p>
                )}
            </section>

            <section className="ledger-section" aria-labelledby="calc-ledger">
                <div className="ledger-heading">
                    <p className="eyebrow">Machine detail</p>
                    <h2 id="calc-ledger">Calculation evidence</h2>
                    <p>
                        Rows are listed in the order the archive returned them. No dependency ordering is drawn
                        between rows here — that relationship is only shown when explicit dependency data backs it.
                    </p>
                </div>
                {calculationsAvailability === "populated" ? (
                    <CalculationTable calculations={calculations} observationRef={core.conformer_observation_ref} />
                ) : (
                    <SectionEmptyMessage
                        availability={calculationsAvailability}
                        emptyText="No calculation rows were returned for this observation."
                        contradicted={calculationsAvailability === "empty" && available.has_calculations}
                    />
                )}
            </section>

            <section className="ledger-section geometry-ledger" aria-labelledby="geometry-ledger">
                <p className="eyebrow">Stored coordinates</p>
                <h2 id="geometry-ledger">Geometry records</h2>
                <p>
                    These are stored geometry objects linked from this observation's calculation output. Their
                    count is not a conformer count and is tracked separately from the calculation-row count above.
                </p>
                {geometriesAvailability === "populated" ? (
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
                    <SectionEmptyMessage
                        availability={geometriesAvailability}
                        emptyText="No stored geometry links were returned for this observation."
                        contradicted={geometriesAvailability === "empty" && available.has_geometries}
                    />
                )}
            </section>

            <section className="ledger-section" aria-labelledby="sibling-ledger">
                <div className="ledger-heading">
                    <p className="eyebrow">Deposited provenance</p>
                    <h2 id="sibling-ledger">Sibling observations</h2>
                    <p>Each sibling is an independent deposition; none of them is this observation.</p>
                </div>
                {observationsAvailability === "populated" && siblings.length > 0 ? (
                    <ul className="observation-list">
                        {siblings.map((sibling) => (
                            <SiblingRow key={sibling.conformer_observation.conformer_observation_ref} sibling={sibling} />
                        ))}
                    </ul>
                ) : (
                    <SectionEmptyMessage
                        availability={observationsAvailability === "not-requested" ? "not-requested" : "empty"}
                        emptyText="No other deposited observations were returned for this basin."
                    />
                )}
            </section>

            <section className="ledger-section" aria-labelledby="review-ledger">
                <div className="ledger-heading">
                    <p className="eyebrow">Review &amp; trust</p>
                    <h2 id="review-ledger">Review history</h2>
                    <p>The current status is {statusLabel(core.review.status)}. This is the record of how it got there.</p>
                </div>
                {reviewAvailability === "populated" ? (
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
                                <tr key={`review-entry-${index}`}>
                                    <td data-label="Status">{statusLabel(entry.status)}</td>
                                    <td data-label="Reviewed at">{isoDate(entry.reviewed_at)}</td>
                                    <td data-label="Note">{entry.note ?? "not recorded"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : (
                    <SectionEmptyMessage
                        availability={reviewAvailability}
                        emptyText="No review history was returned for this observation."
                        contradicted={reviewAvailability === "empty" && available.has_review}
                    />
                )}
            </section>

            {selections.length > 0 && (
                <details className="ledger-section">
                    <summary><h2>Curation selections ({selections.length})</h2></summary>
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
            <span className="review-badge">{statusLabel(core.review.status)}</span>
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
                                : "not recorded"}
                        </td>
                        <td data-label="Software / workflow">
                            {calculation.software_release?.software ?? "not recorded"}
                            {calculation.workflow_tool_release?.workflow_tool
                                ? ` · ${calculation.workflow_tool_release.workflow_tool}`
                                : ""}
                        </td>
                        <td data-label="Review">
                            {calculation.review
                                ? statusLabel(calculation.review.status)
                                : "not recorded"}
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
