import { Link } from "react-router-dom"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel } from "../domain/conformerEvidence"
import { calculationDid } from "../domain/calculationTypeFormat"
import { shapeRows, spreadLines, type ShapeCalculation } from "../domain/conformerGallery"
import type { SinglePointsState } from "../hooks/useSpeciesSinglePoints"
import { OrderNote } from "./OrderNote"
import { SectionHeading } from "./PageSections"
import { ReviewChip } from "./ReviewChip"
import { ValueLink } from "./ValueLink"

function Where({ calculation }: { calculation: ShapeCalculation }) {
    const parts = [
        calculation.levelLabel ? <>at <span className="t-preserve-case">{calculation.levelLabel}</span></> : "level not recorded",
        calculation.program ? <>with <span className="t-preserve-case">{calculation.program}</span></> : "program not recorded",
    ]
    return <>{parts[0]}, {parts[1]}</>
}

/**
 * One row per observation of the selected shape: how its geometry was found, and every single-point energy computed on
 * it. Nothing is picked or averaged; a spread is stated only between energies at one level and one program.
 */
export function ShapeCalculations({ conformer, spEnergies }: { conformer: ConformerProjection; spEnergies: SinglePointsState }) {
    const records = spEnergies.status === "ready" ? spEnergies.records : []
    const rows = shapeRows(conformer, records)
    const spreads = spEnergies.status === "ready" ? spreadLines(rows) : []
    return (
        <section className="shape-calculations" aria-labelledby="shape-calculations">
            <SectionHeading
                id="shape-calculations"
                intro={`One row for each time ${conformerLabel(conformer)} was deposited: how its geometry was found and every single-point energy computed on it.`}
            >
                Calculations of this shape
            </SectionHeading>
            {spEnergies.status !== "ready" && (
                <p className="note" role="status" aria-busy="true">The energies are still being read; an energy shown as missing may not have arrived yet.</p>
            )}
            {spEnergies.status === "ready" && spEnergies.capped && (
                <p className="note">This entry has more single-point energies than this page reads ({spEnergies.records.length} were read). One shown as missing may not have been read.</p>
            )}
            {rows.length === 0 ? (
                <p className="empty-projection">No deposits of this shape are listed.</p>
            ) : (
                <>
                    <OrderNote />
                    {spreads.length > 0 && (
                        <ul className="shape-spread">
                            {spreads.map((line) => (
                                <li key={`${line.levelLabel}-${line.program}`}>
                                    Single-point energies at <span className="t-preserve-case">{line.levelLabel}</span> with <span className="t-preserve-case">{line.program}</span> differ
                                    by up to {line.kjMol.toFixed(2)} <span className="t-preserve-case">kJ/mol</span> across {line.count} calculations.
                                </li>
                            ))}
                        </ul>
                    )}
                    <div className="table-scroll" tabIndex={0} role="region" aria-label="Table of calculations">
                        <table className="data-table shape-calculations-table">
                            <caption className="visually-hidden">Calculations of {conformerLabel(conformer)}, one row per deposit</caption>
                            <thead>
                                <tr>
                                    <th scope="col">Deposit</th>
                                    <th scope="col">How it was found</th>
                                    <th scope="col">Single-point energies on this geometry</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row) => (
                                    <tr key={row.observationRef}>
                                        <th scope="row"><Link to={`/conformer-observations/${row.observationRef}`}><code className="t-preserve-case">{row.observationRef}</code></Link></th>
                                        <td>
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
                                        <td>
                                            {row.energies.length === 0 ? <span className="shape-energy-absent">No single-point energy recorded.</span> : (
                                                <ul className="shape-cell-list">
                                                    {row.energies.map((energy) => (
                                                        <li key={energy.ref}>
                                                            {energy.hartree === null
                                                                ? <span className="shape-energy-absent">Energy not recorded</span>
                                                                : <ValueLink value={energy.hartree.toFixed(6)} unit="hartree" to={`/calculations/${energy.ref}`} />}
                                                            {" "}<Where calculation={energy} />{" "}
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
