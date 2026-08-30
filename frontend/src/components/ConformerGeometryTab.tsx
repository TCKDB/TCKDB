import { Link } from "react-router-dom"
import "../conformer-group.css"
import "../entry-science.css"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel } from "../domain/conformerEvidence"

/**
 * Reads straight off the conformer projection already loaded for the
 * picker -- no extra request. Distinct stored geometries stay distinct
 * (never collapsed into "how much evidence"), and every observation is
 * listed even when several share the same final geometry.
 */
export function ConformerGeometryTab({ conformer }: { conformer: ConformerProjection }) {
    const geometryLinks = conformer.geometries ?? []
    const uniqueGeometries = [...new Map(geometryLinks.map((link) => [link.geometry.geometry_ref, link])).values()]
    const observations = conformer.observations ?? []

    return (
        <section className="ledger-section" aria-labelledby="geometry-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Evidence for this conformer</p>
                <h2 id="geometry-heading">Geometry</h2>
                <p>
                    {uniqueGeometries.length} distinct stored geometr{uniqueGeometries.length === 1 ? "y" : "ies"} from{" "}
                    {geometryLinks.length} calculation output{geometryLinks.length === 1 ? "" : "s"} for {conformerLabel(conformer)}.
                </p>
            </div>
            {uniqueGeometries.length === 0 ? (
                <p className="empty-projection">No stored geometry is projected for this conformer.</p>
            ) : (
                <ul className="geometry-list">
                    {uniqueGeometries.map(({ geometry }) => (
                        <li key={geometry.geometry_ref} className="geometry-item">
                            <Link to={`/geometries/${geometry.geometry_ref}`}>{geometry.geometry_ref}</Link>
                            <span className="geometry-meta">
                                {geometry.role ? `${geometry.role} · ` : ""}
                                {geometry.natoms ?? "?"} atoms
                                {geometry.geom_hash ? ` · ${geometry.geom_hash.slice(0, 12)}…` : ""}
                            </span>
                        </li>
                    ))}
                </ul>
            )}

            <h3 className="model-block-heading">By observation</h3>
            {observations.length === 0 ? (
                <p className="empty-projection">No deposited observations are projected for this conformer.</p>
            ) : (
                <ul className="checklist">
                    {observations.map((observation) => {
                        const ref = observation.conformer_observation.conformer_observation_ref
                        const optCalcs = (observation.calculations ?? []).filter((calculation) => calculation.type === "opt")
                        return (
                            <li key={ref}>
                                <Link to={`/conformer-observations/${ref}`}>{ref}</Link>
                                {" — "}{optCalcs.length} optimization calculation{optCalcs.length === 1 ? "" : "s"}
                            </li>
                        )
                    })}
                </ul>
            )}
        </section>
    )
}
