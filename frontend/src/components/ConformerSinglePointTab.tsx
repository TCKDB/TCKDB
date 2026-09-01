import { Fragment } from "react"
import { Link } from "react-router-dom"
import "../conformer-group.css"
import "../entry-science.css"
import type { ConformerProjection } from "../api/speciesEntryApi"
import type { SpeciesCalculationEnergyRecord } from "../api/speciesCalculationsApi"
import { lotLabel } from "../api/scientificSchemas"
import { conformerLabel } from "../domain/conformerEvidence"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { EnergyDisplay } from "./EnergyDisplay"
import { SectionHeading } from "./PageSections"

/**
 * One row per deposited observation, never merged: an observation with two
 * single-point calculations shows both, an observation with none says so
 * plainly rather than being silently skipped.
 *
 * The owner's own complaint this fixes: "i kinda expect the single point
 * energy to also show on the species entries page rather than just being a
 * link… oh wait it does in Result but it doesnt catch the eye". The energy
 * VALUE, not just the calculation ref link, now renders directly on this
 * row — joined from `spEnergies` (`api/speciesCalculationsApi.ts`) by
 * `calculation_ref`, since the calculation summaries this tab already had
 * (`conformer.observations[].calculations`) do not carry a result value at
 * all (see that API module's own docstring for why). A calculation ref with
 * no matching energy record (the enrichment fetch failed, or genuinely has
 * no parsed energy) still renders its row — the value cell alone says "not
 * recorded", never a silently dropped row.
 */
export function ConformerSinglePointTab({ conformer, spEnergies }: {
    conformer: ConformerProjection
    spEnergies: SpeciesCalculationEnergyRecord[]
}) {
    const observations = conformer.observations ?? []
    const energyByCalculationRef = new Map(
        spEnergies.map((record) => [record.calculation.calculation_ref, record.energy?.energy_hartree ?? null]),
    )

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
                                            <div className="single-point-energy-row">
                                                <dt>Electronic energy</dt>
                                                <dd>
                                                    <EnergyDisplay
                                                        valueHartree={energyByCalculationRef.get(calculation.calculation_ref) ?? null}
                                                        size="inline"
                                                    />
                                                </dd>
                                            </div>
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
