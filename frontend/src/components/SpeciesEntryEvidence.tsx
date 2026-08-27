import { Link } from "react-router-dom"
import type { ConformerProjection } from "../api/speciesEntryApi"

function levelLabel(lot: { method: string; basis?: string | null; display?: string }) {
    return lot.display ?? (lot.basis ? `${lot.method}/${lot.basis}` : lot.method)
}

export function LineageSection({ conformers }: { conformers: ConformerProjection[] }) {
    return <section className="lineage-section" aria-labelledby="lineage-title">
        <div className="section-heading">
            <p className="eyebrow">Provenance lineage</p>
            <h2 id="lineage-title">From basin to stored geometry</h2>
            <p>
                Observations are deposited provenance; calculation rows are stages. Stored geometries remain distinct
                objects, even when coordinates are near-identical.
            </p>
        </div>
        {conformers.length === 0
            ? <p className="empty-projection">No conformer basins are projected for this entry.</p>
            : conformers.map((group) => <LineageRail group={group} key={group.conformer_group.conformer_group_ref} />)}
    </section>
}

function LineageRail({ group }: { group: ConformerProjection }) {
    const geometryLinks = group.geometries ?? []
    const uniqueGeometries = [...new Map(geometryLinks.map((link) => [link.geometry.geometry_ref, link])).values()]
    const coverage = group.evidence_summary.evidence_coverage
    const observations = group.observations ?? []
    const calculations = group.calculations ?? []
    const observationCount = group.observations_summary.total
    const chainSummary = `${group.evidence_summary.calculation_count} rows · `
        + `${group.evidence_summary.optimization_chain_count} chains`
    const coverageText = `opt ${coverage.opt}/${observationCount} · freq ${coverage.freq}/${observationCount}`
        + ` · sp ${coverage.sp}/${observationCount}`
    return <article className="lineage-rail">
        <div className="rail-node basin">
            <span>Conformer basin</span>
            <Link to={`/conformer-groups/${group.conformer_group.conformer_group_ref}`}>
                {group.conformer_group.label ?? group.conformer_group.conformer_group_ref}
            </Link>
            <small>{group.conformer_group.conformer_group_ref}</small>
        </div>
        <div className="rail-arrow" aria-hidden="true" />
        <div className="rail-node">
            <span>Deposited observations</span><strong>{group.observations_summary.total}</strong>
            <RecordLinks records={observations.map((item) => ({
                ref: item.conformer_observation.conformer_observation_ref,
                to: `/conformer-observations/${item.conformer_observation.conformer_observation_ref}`,
            }))} />
        </div>
        <div className="rail-arrow" aria-hidden="true" />
        <div className="rail-node">
            <span>Calculation stages</span>
            <strong>{chainSummary}</strong>
            <div className="coverage">{coverageText}</div>
            <RecordLinks records={calculations.map((calculation) => ({
                ref: `${calculation.type} · ${calculation.calculation_ref}`,
                to: `/calculations/${calculation.calculation_ref}`,
            }))} />
        </div>
        <div className="rail-arrow" aria-hidden="true" />
        <div className="rail-node geometry-node">
            <span>Distinct stored geometries</span><strong>{uniqueGeometries.length}</strong>
            <p>{geometryLinks.length} output links</p>
            <RecordLinks records={uniqueGeometries.map(({ geometry }) => ({
                ref: `${geometry.geometry_ref} ${geometry.geom_hash ?? ""}`,
                to: `/geometries/${geometry.geometry_ref}`,
            }))} />
        </div>
    </article>
}

function RecordLinks({ records }: { records: Array<{ ref: string; to: string }> }) {
    return <div className="rail-links">{records.map((record) => (
        <Link key={record.to} to={record.to}>{record.ref}</Link>
    ))}</div>
}

export function LevelsOfTheorySection({ conformers }: { conformers: ConformerProjection[] }) {
    const levels = conformers.flatMap((group) => (
        Object.entries(group.evidence_summary.levels_of_theory).map(([type, values]) => ({
            groupRef: group.conformer_group.conformer_group_ref,
            type,
            values,
        }))
    ))
    return <section className="entry-panel">
        <p className="eyebrow">Method inventory</p>
        <h2>Levels of theory by calculation type</h2>
        <p>
            Each type keeps a list, so multiple recorded levels stay visible rather than being collapsed to one
            representative.
        </p>
        {levels.length === 0
            ? <p className="empty-projection">No calculation levels are projected for this entry.</p>
            : <div className="lot-grid">
                {levels.map(({ groupRef, type, values }) => <div key={`${groupRef}-${type}`}>
                    <strong>{type}</strong>
                    <ul>{values.map((level, index) => (
                        <li key={`${level.method}-${level.basis ?? ""}-${index}`}>{levelLabel(level)}</li>
                    ))}</ul>
                </div>)}
            </div>}
    </section>
}
