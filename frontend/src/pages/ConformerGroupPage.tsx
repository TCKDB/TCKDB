import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import type { ConformerGroup } from "../api/conformerGroupApi"
import { useConformerGroup } from "../hooks/useConformerGroup"

const lotLabel = (value: { method: string; basis?: string | null; display?: string }) => (
    value.display ?? (value.basis ? `${value.method}/${value.basis}` : value.method)
)
const statusLabel = (status: string) => status.replaceAll("_", " ")
type Observation = NonNullable<ConformerGroup["observations"]>[number]

export default function ConformerGroupPage() {
    const { groupRef = "" } = useParams<{ groupRef: string }>()
    const state = useConformerGroup(groupRef)
    if (state.status === "loading") return <State title="Loading conformer basin" busy />
    if (state.status !== "ready") return <State title={state.status === "missing" ? "Conformer basin not found" : "Conformer basin unavailable"} ref={groupRef} alert={state.status !== "missing"} />
    return <Ledger group={state.group} />
}

function State({ title, ref, busy, alert }: { title: string; ref?: string; busy?: boolean; alert?: boolean }) {
    const message = busy ? "Retrieving the conformer basin and its deposited evidence." : alert ? "The archive response could not be read. Try again later." : "No conformer group with this stable reference is available in this archive projection."
    return <section className="record-placeholder" aria-busy={busy} role={alert ? "alert" : undefined}>
        <p className="eyebrow">Archive record</p><h1>{title}</h1>{ref && <code>{ref}</code>}<p>{message}</p>
    </section>
}

function Ledger({ group }: { group: ConformerGroup }) {
    const { conformer_group: basin, species, observations_summary: summary, evidence_summary: evidence } = group
    const observations = group.observations ?? []
    const geometries = [...new Map((group.geometries ?? []).map((item) => [item.geometry.geometry_ref, item])).values()]
    return <section className="conformer-page">
        <header className="basin-header">
            <p className="eyebrow">Conformer basin · evidence ledger</p>
            <div className="basin-title"><h1>{basin.label ?? basin.conformer_group_ref}</h1><span className="review-badge">{statusLabel(basin.review.status)}</span></div>
            <p className="basin-intro">One torsional basin, shown through its deposited observations. Calculation rows are evidence attached to those observations; they are not separate conformers.</p>
            <dl className="basin-context">
                <div><dt>Group ref</dt><dd>{basin.conformer_group_ref}</dd></div>
                <div><dt>Species entry</dt><dd><Link to={`/species-entries/${species.species_entry_ref}`}>{species.species_entry_label ?? species.species_entry_ref}</Link></dd></div>
                <div><dt>Structure</dt><dd>{species.canonical_smiles ?? "Not projected"}</dd></div>
            </dl>
        </header>
        <section className="ledger-summary" aria-label="Basin evidence summary">
            <Metric label="Deposited observations" value={summary.total} />
            <Metric label="Calculation rows" value={evidence.calculation_count} detail={`${evidence.optimization_chain_count} optimisation chains`} />
            <Metric label="Distinct stored geometries" value={evidence.geometry_count} />
            <div className="coverage-card"><span>Observation coverage</span><strong>opt {evidence.evidence_coverage.opt}/{summary.total} · freq {evidence.evidence_coverage.freq}/{summary.total} · sp {evidence.evidence_coverage.sp}/{summary.total}</strong><p>Coverage says which observations have a stage, not whether methods are comparable.</p></div>
        </section>
        <section className="ledger-section" aria-labelledby="observation-ledger">
            <div className="ledger-heading"><p className="eyebrow">Deposited provenance</p><h2 id="observation-ledger">Observation-scoped evidence</h2><p>Methods remain on their actual calculation rows so differing levels of theory stay visible.</p></div>
            {observations.length ? <div className="observation-list">{observations.map((observation) => <ObservationCard key={observation.conformer_observation.conformer_observation_ref} observation={observation} />)}</div> : <p className="empty-projection">No deposited observations were returned for this conformer basin.</p>}
        </section>
        <section className="ledger-section geometry-ledger" aria-labelledby="geometry-ledger">
            <p className="eyebrow">Stored coordinates</p><h2 id="geometry-ledger">Geometry records</h2><p>These are stored geometry objects linked from calculation output. Their count is not a conformer count.</p>
            <div className="geometry-links">{geometries.map(({ geometry, calculation_ref }) => <Link key={geometry.geometry_ref} to={`/geometries/${geometry.geometry_ref}`}><strong>{geometry.geometry_ref}</strong><span>from {calculation_ref}{geometry.natoms != null ? ` · ${geometry.natoms} atoms` : ""}</span></Link>)}</div>
        </section>
    </section>
}

function Metric({ label, value, detail }: { label: string; value: number; detail?: string }) {
    return <div className="metric"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>
}

function ObservationCard({ observation }: { observation: Observation }) {
    const core = observation.conformer_observation
    const calculations = observation.calculations ?? []
    const geometries = observation.geometries ?? []
    return <article className="observation-card">
        <header><div><span className="ledger-kicker">Observation</span><Link to={`/conformer-observations/${core.conformer_observation_ref}`}>{core.conformer_observation_ref}</Link></div><div><span className="review-badge">{statusLabel(core.review.status)}</span><small>{core.scientific_origin ?? "origin not recorded"}</small></div></header>
        {core.note && <p className="observation-note">{core.note}</p>}
        {calculations.length ? <table className="stage-table" aria-label={`Calculations for ${core.conformer_observation_ref}`}><thead><tr><th scope="col">Stage</th><th scope="col">Level of theory</th><th scope="col">Software / workflow</th><th scope="col">Record</th></tr></thead><tbody>{calculations.map((calculation) => <tr key={calculation.calculation_ref}><td>{calculation.type}</td><td>{calculation.level_of_theory ? lotLabel(calculation.level_of_theory) : "Not recorded"}</td><td>{calculation.software_release?.name ?? "Not recorded"}{calculation.workflow_tool_release?.name ? ` · ${calculation.workflow_tool_release.name}` : ""}</td><td><Link to={`/calculations/${calculation.calculation_ref}`}>{calculation.calculation_ref}</Link></td></tr>)}</tbody></table> : <p className="empty-stage">No calculation rows were returned for this observation.</p>}
        {geometries.length > 0 && <p className="observation-geometries">Geometry output: {geometries.map(({ geometry }) => <Link key={geometry.geometry_ref} to={`/geometries/${geometry.geometry_ref}`}>{geometry.geometry_ref}</Link>)}</p>}
    </article>
}
