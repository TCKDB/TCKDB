import type { ReactNode } from "react"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { calculationTypeCounts, conformerLabel, geometryConvergence } from "../domain/conformerEvidence"

/**
 * Answers the owner's own confusion directly: "they see 2 distinct Geoms
 * but then 4 conformers... 14 calculation rows but only 12 I see (opt 4/4,
 * freq 4/4, sp 3/4)." Every one of those numbers is correct; they are
 * three different UNITS printed as if they were comparable. This panel
 * draws the shape those units actually stand in: observations fan out to
 * calculation rows (several per observation), calculation rows converge
 * on shared geometries (several calculations, one stored coordinate set).
 * No number here is recomputed or merged -- each is read straight off the
 * same `evidence_summary` fields the conformer card already renders; this
 * panel only makes the units and the linkage between them explicit.
 */
export function ConformerEvidenceLinkage({ conformer }: { conformer: ConformerProjection }) {
    const total = conformer.observations_summary.total
    const evidence = conformer.evidence_summary
    const coverage = evidence.evidence_coverage
    const typeCounts = calculationTypeCounts(conformer)
    const convergence = geometryConvergence(conformer)
    const label = conformerLabel(conformer)

    return (
        <section className="evidence-linkage" aria-labelledby="evidence-linkage-heading">
            <p className="eyebrow">How this evidence connects</p>
            <h2 id="evidence-linkage-heading">Evidence for {label}</h2>
            <p className="evidence-linkage-note">
                Three different units, not three counts of the same thing: an observation is a deposited sighting
                of this basin; a calculation row is one piece of evidence attached to an observation; a stored
                geometry is a coordinate set several calculation rows can share.
            </p>
            <div className="linkage-flow">
                <LinkageStep
                    count={total}
                    unit={total === 1 ? "deposited observation" : "deposited observations"}
                    detail="each a separate sighting of this basin"
                />
                <LinkageConnector />
                <LinkageStep
                    count={evidence.calculation_count}
                    unit={evidence.calculation_count === 1 ? "calculation row" : "calculation rows"}
                    detail={typeCounts.length > 0
                        ? `${typeCounts.map(({ type, count }) => `${count} ${type}`).join(" · ")}, in `
                            + `${evidence.optimization_chain_count} optimization chain${evidence.optimization_chain_count === 1 ? "" : "s"}`
                            + " (a staged coarse-then-fine reoptimization counts as one chain)"
                        : "no calculation rows recorded"}
                />
                <LinkageConnector />
                <LinkageStep
                    count={evidence.geometry_count}
                    unit={evidence.geometry_count === 1 ? "distinct stored geometry" : "distinct stored geometries"}
                >
                    {/* Plain text, not links -- the Geometry tab below already links each
                        of these refs (`ConformerGeometryTab`); repeating them as links
                        here would give two same-named links their own separate targets
                        for assistive tech to disambiguate, for no navigational benefit
                        this summary needs to provide. */}
                    {convergence.length > 0 && (
                        <ul className="linkage-geometry-list">
                            {convergence.map((entry) => (
                                <li key={entry.geometryRef}>
                                    <code>{entry.geometryRef}</code>
                                    {` — ${entry.calculationCount} calculation output${entry.calculationCount === 1 ? "" : "s"}`}
                                </li>
                            ))}
                        </ul>
                    )}
                </LinkageStep>
            </div>
            <p className="linkage-coverage">
                <strong>Stage coverage</strong> — of the {total} observation{total === 1 ? "" : "s"}: opt{" "}
                {coverage.opt}/{total} · freq {coverage.freq}/{total} · sp {coverage.sp}/{total}. This counts which
                observations have at least one calculation of that stage, not the number of calculation rows.
            </p>
        </section>
    )
}

function LinkageStep({ count, unit, detail, children }: {
    count: number
    unit: string
    detail?: string
    children?: ReactNode
}) {
    return (
        <div className="linkage-step">
            <span className="linkage-step-count">{count}</span>
            <span className="linkage-step-unit">{unit}</span>
            {detail && <span className="linkage-step-detail">{detail}</span>}
            {children}
        </div>
    )
}

function LinkageConnector() {
    return <span className="linkage-connector" aria-hidden="true">→</span>
}
