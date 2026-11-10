import { Link } from "react-router-dom"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel } from "../domain/conformerEvidence"
import { calculationDid } from "../domain/calculationTypeFormat"
import { formatHartree, shapeRows, spreadLines, type ShapeCalculation } from "../domain/conformerGallery"
import type { SinglePointsState } from "../hooks/useSpeciesSinglePoints"
import { OrderNote } from "./OrderNote"
import { SectionHeading } from "./PageSections"
import { ReviewChip } from "./ReviewChip"
import { ValueLink } from "./ValueLink"

function Where({ calculation }: { calculation: ShapeCalculation }) {
    return (
        <>
            {calculation.levelLabel ? <>at <span className="t-preserve-case">{calculation.levelLabel}</span></> : "level not recorded"},{" "}
            {calculation.program ? <>with <span className="t-preserve-case">{calculation.program}</span></> : "program not recorded"}
        </>
    )
}

const KIND_WORDS = { sp: "single-point energy", opt: "final energy of the optimisation" } as const

/**
 * One row per observation of the selected shape: how its geometry was found, and every energy computed on it
 * (single-point energies and the final energy of each optimisation). Nothing is picked or averaged; a spread is stated
 * only between energies of one kind at one level and one program.
 */
export function ShapeCalculations({ conformer, singlePoints, optimisations }: {
    conformer: ConformerProjection
    singlePoints: SinglePointsState
    optimisations: SinglePointsState
}) {
    const spRecords = singlePoints.status === "ready" ? singlePoints.records : []
    const optRecords = optimisations.status === "ready" ? optimisations.records : []
    const rows = shapeRows(conformer, spRecords, optRecords)
    const spreads = spreadLines(rows)
    const waiting = singlePoints.status === "loading" || optimisations.status === "loading"
    const capped = (singlePoints.status === "ready" && singlePoints.capped) || (optimisations.status === "ready" && optimisations.capped)
    const label = conformerLabel(conformer)
    return (
        <section className="shape-calculations" aria-labelledby="shape-calculations">
            <SectionHeading
                id="shape-calculations"
                intro={`One row for each time ${label} was deposited: how its geometry was found and every energy computed on it.`}
            >
                Calculations of this shape
            </SectionHeading>
            {waiting && (
                <p className="note" role="status" aria-busy="true">The energies are still being read; an energy shown as missing may not have arrived yet.</p>
            )}
            {singlePoints.status === "failed" && <p className="note" role="alert">The single-point energies could not be read.</p>}
            {optimisations.status === "failed" && <p className="note" role="alert">The final energies of the optimisations could not be read.</p>}
            {capped && (
                <p className="note">This entry has more energies than this page reads. One shown as missing may not have been read.</p>
            )}
            {rows.length === 0 ? (
                <p className="empty-projection">No deposits of this shape are listed.</p>
            ) : (
                <>
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
                    <div className="table-scroll" tabIndex={0} role="region" aria-label="Table of calculations">
                        <table className="data-table shape-calculations-table">
                            <caption className="molecule-page-visually-hidden">Calculations of {label}, one row per deposit</caption>
                            <thead>
                                <tr>
                                    <th scope="col">Deposit</th>
                                    <th scope="col">Optimisation and frequency calculations</th>
                                    <th scope="col">Energies on this geometry</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row) => (
                                    <tr key={row.observationRef}>
                                        <th scope="row"><Link to={`/conformer-observations/${row.observationRef}`}><code className="t-preserve-case">{row.observationRef}</code></Link></th>
                                        <td data-label="Optimisation and frequency calculations">
                                            {row.foundBy.length === 0 ? <span className="shape-energy-absent">No calculation recorded.</span> : (
                                                <ul className="shape-cell-list">
                                                    {row.foundBy.map((calculation) => (
                                                        <li key={calculation.ref}>
                                                            {calculationDid(calculation.type) ?? calculation.type} <Where calculation={calculation} />{" "}
                                                            <Link to={`/calculations/${calculation.ref}`}><code className="t-preserve-case">{calculation.ref}</code></Link>
                                                        </li>
                                                    ))}
                                                </ul>
                                            )}
                                        </td>
                                        <td data-label="Energies on this geometry">
                                            {row.energies.length === 0 ? <span className="shape-energy-absent">No energy recorded.</span> : (
                                                <ul className="shape-cell-list">
                                                    {row.energies.map((energy) => (
                                                        <li key={energy.ref}>
                                                            {energy.hartree === null
                                                                ? <span className="shape-energy-absent">{(energy.kind === "sp" ? singlePoints : optimisations).status === "failed" ? "Could not be read" : "Energy not recorded"}</span>
                                                                : <ValueLink value={formatHartree(energy.hartree)} unit="hartree" to={`/calculations/${energy.ref}`} />}
                                                            {" "}({KIND_WORDS[energy.kind]}) <Where calculation={energy} />{" "}
                                                            <Link to={`/calculations/${energy.ref}`}><code className="t-preserve-case">{energy.ref}</code></Link>
                                                            {energy.review && <>{" "}<ReviewChip status={energy.review} explain={false} /></>}
                                                        </li>
                                                    ))}
                                                </ul>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </section>
    )
}
