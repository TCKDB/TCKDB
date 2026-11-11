import { Link } from "react-router-dom"
import "../molecule-page.css"
import { calculationDid } from "../domain/calculationTypeFormat"
import { formatHartree, type ShapeCalculation, type ShapeRow } from "../domain/conformerGallery"
import type { SinglePointsState } from "../hooks/useSpeciesSinglePoints"
import { LevelOfTheoryLink } from "./LevelOfTheoryLink"
import { ReviewChip } from "./ReviewChip"
import { ValueLink } from "./ValueLink"
import { ValueSource } from "./ValueSource"

const KIND_WORDS = { sp: "single-point energy", opt: "final energy of the optimisation" } as const

function Where({ calculation }: { calculation: ShapeCalculation }) {
    return (
        <>
            {calculation.levelLabel ? (
                <>at <span className="t-preserve-case"><LevelOfTheoryLink levelOfTheory={{ method: calculation.levelLabel, label: calculation.levelLabel, level_of_theory_ref: calculation.levelRef ?? undefined }} /></span></>
            ) : "level not recorded"},{" "}
            {calculation.program ? <>with <span className="t-preserve-case">{calculation.program}</span></> : "program not recorded"}
            {calculation.workflowTool && <>, run by <span className="t-preserve-case">{calculation.workflowTool}</span></>}
        </>
    )
}

/** What the energy reads are still doing, in words; an energy shown as missing may not have arrived. */
export function EnergyNotices({ singlePoints, optimisations }: { singlePoints: SinglePointsState; optimisations: SinglePointsState }) {
    const waiting = singlePoints.status === "loading" || optimisations.status === "loading"
    const capped = (singlePoints.status === "ready" && singlePoints.capped) || (optimisations.status === "ready" && optimisations.capped)
    return (
        <>
            {waiting && <p className="note" role="status" aria-busy="true">The energies are still being read; an energy shown as missing may not have arrived yet.</p>}
            {singlePoints.status === "failed" && <p className="note" role="alert">The single-point energies could not be read.</p>}
            {optimisations.status === "failed" && <p className="note" role="alert">The final energies of the optimisations could not be read.</p>}
            {capped && <p className="note">This entry has more energies than this page reads. One shown as missing may not have been read.</p>}
        </>
    )
}

/**
 * One observation's evidence: the calculations that found its geometry, and every energy computed on it. Each energy
 * links to its calculation and says its level, program and review beneath it. Nothing is picked, averaged or compared
 * across observations.
 */
export function ObservationEvidence({ row, singlePoints, optimisations }: {
    row: ShapeRow
    singlePoints: SinglePointsState
    optimisations: SinglePointsState
}) {
    return (
        <dl className="kv-list observation-evidence">
            <div>
                <dt>Found by</dt>
                <dd>
                    {row.foundBy.length === 0 ? <span className="shape-energy-absent">No calculation recorded.</span> : (
                        <ul className="shape-cell-list">
                            {row.foundBy.map((calculation) => (
                                <li key={calculation.ref}>
                                    {calculationDid(calculation.type) ?? calculation.type} <Where calculation={calculation} />{" "}
                                    <Link to={`/calculations/${calculation.ref}`}><code className="t-preserve-case">{calculation.ref}</code></Link>
                                    {calculation.review && <>{" "}<ReviewChip status={calculation.review} explain={false} /></>}
                                </li>
                            ))}
                        </ul>
                    )}
                </dd>
            </div>
            <div>
                <dt>Energies on this geometry</dt>
                <dd>
                    {row.energies.length === 0 ? <span className="shape-energy-absent">No energy recorded.</span> : (
                        <ul className="shape-cell-list">
                            {row.energies.map((energy) => {
                                const state = energy.kind === "sp" ? singlePoints : optimisations
                                return (
                                    <li key={energy.ref} className="observation-energy">
                                        {energy.hartree === null
                                            ? <span className="shape-energy-absent">{state.status === "failed" ? "Could not be read" : "Energy not recorded"}</span>
                                            : <ValueLink value={formatHartree(energy.hartree)} full={String(energy.hartree)} unit="hartree" to={`/calculations/${energy.ref}`} />}
                                        {" "}({KIND_WORDS[energy.kind]})
                                        <br />
                                        <ValueSource
                                            source={{ recordType: "calculation", ref: energy.ref }}
                                            did={energy.type}
                                            levels={energy.levelLabel ? [{ role: "at", label: energy.levelLabel }] : []}
                                            programs={energy.program ? [energy.program] : []}
                                            review={energy.review}
                                        />
                                    </li>
                                )
                            })}
                        </ul>
                    )}
                </dd>
            </div>
        </dl>
    )
}
