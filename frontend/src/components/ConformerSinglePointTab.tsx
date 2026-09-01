import { Fragment } from "react"
import { Link } from "react-router-dom"
import "../conformer-group.css"
import "../entry-science.css"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { lotLabel } from "../api/scientificSchemas"
import { conformerLabel } from "../domain/conformerEvidence"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { SectionHeading } from "./PageSections"

/**
 * One row per deposited observation, never merged: an observation with two
 * single-point calculations shows both, an observation with none says so
 * plainly rather than being silently skipped.
 */
export function ConformerSinglePointTab({ conformer }: { conformer: ConformerProjection }) {
    const observations = conformer.observations ?? []

    return (
        <section className="ledger-section" aria-labelledby="sp-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Evidence for this conformer</p>
                <SectionHeading id="sp-heading">Single-point energy</SectionHeading>
                <p>Single-point energy evidence for {conformerLabel(conformer)}, one row per deposited observation.</p>
            </div>
            {observations.length === 0 ? (
                <p className="empty-projection">No deposited observations are projected for this conformer.</p>
            ) : (
                observations.map((observation) => {
                    const ref = observation.conformer_observation.conformer_observation_ref
                    const spCalculations = (observation.calculations ?? []).filter((calculation) => calculation.type === "sp")
                    return (
                        <article key={ref} className="science-record">
                            <div className="science-record-heading">
                                <h3><Link to={`/conformer-observations/${ref}`}>{ref}</Link></h3>
                            </div>
                            {spCalculations.length === 0 ? (
                                <p className="empty-projection">No single-point calculation recorded for this observation.</p>
                            ) : (
                                <dl className="kv-list">
                                    {spCalculations.map((calculation) => (
                                        <Fragment key={calculation.calculation_ref}>
                                            <div>
                                                <dt>Calculation</dt>
                                                <dd><Link to={`/calculations/${calculation.calculation_ref}`}>{calculation.calculation_ref}</Link></dd>
                                            </div>
                                            <div>
                                                <dt>Level of theory</dt>
                                                <dd>{calculation.level_of_theory ? lotLabel(calculation.level_of_theory) : "not recorded"}</dd>
                                            </div>
                                            <div><dt>Software</dt><dd>{softwareLabel(calculation.software_release) ?? "not recorded"}</dd></div>
                                            <div><dt>Workflow</dt><dd>{toolReleaseLabel(calculation.workflow_tool_release) ?? "not recorded"}</dd></div>
                                        </Fragment>
                                    ))}
                                </dl>
                            )}
                        </article>
                    )
                })
            )}
        </section>
    )
}
