import { Fragment, type ReactNode } from "react"
import { calculationDid } from "../domain/calculationTypeFormat"
import { formatHartree, spreadLines, spreadText, type ShapeCalculation, type ShapeEnergy, type ShapeRow } from "../domain/conformerGallery"
import { energyKey, foundByKey, groupBy, numberCalculations, type CalculationNumbers } from "../domain/shapeGrouping"
import { words } from "../domain/provenanceFormat"
import type { SinglePointsState } from "../hooks/useSpeciesSinglePoints"
import { LevelLabel } from "./LevelLabel"
import { RecordLink } from "./RecordLink"
import { ReviewChip } from "./ReviewChip"
import { ValueLink } from "./ValueLink"

const KIND_WORDS = { sp: "Single-point energy", opt: "Final energy of the optimisation" } as const
const USUAL_ENERGY_KIND = { sp: "electronic_energy", opt: "final_energy" } as const

/** Level and program as one run of text; a missing part is said, never filled in. A workflow tool is named when there is one. */
function Where({ calculation }: { calculation: ShapeCalculation }) {
    return (
        <>
            {calculation.level
                ? <><LevelLabel levelOfTheory={calculation.level} program={calculation.program} />{!calculation.program && ", program not recorded"}</>
                : <>level not recorded, {calculation.program ? <span className="t-preserve-case">{calculation.program}</span> : "program not recorded"}</>}
            {calculation.workflowTool && <>, run by <span className="t-preserve-case">{calculation.workflowTool}</span></>}
        </>
    )
}

/** The kind of energy in words; the archive's own kind is added when it is not the usual one for the calculation's type. */
function EnergyKindWords({ energy }: { energy: Pick<ShapeEnergy, "kind" | "energyKind"> }) {
    const unusual = energy.energyKind && energy.energyKind !== USUAL_ENERGY_KIND[energy.kind] ? words(energy.energyKind) : null
    return <>{KIND_WORDS[energy.kind]}{unusual && <> ({unusual})</>}</>
}

/** "Calculation 1, 2, 3": each number links to its own calculation. */
function CalculationLinks({ members, numbers }: { members: readonly ShapeCalculation[]; numbers: CalculationNumbers }) {
    const ordered = [...members].sort((a, b) => (numbers.get(a.ref) ?? 0) - (numbers.get(b.ref) ?? 0))
    return (
        <>
            {ordered.length === 1 ? "Calculation " : "Calculations "}
            {ordered.map((member, index) => (
                <Fragment key={member.ref}>
                    {index > 0 && ", "}
                    <RecordLink kind="calculation" to={`/calculations/${member.ref}`} label={`Calculation ${numbers.get(member.ref)}`}>{numbers.get(member.ref)}</RecordLink>
                </Fragment>
            ))}
        </>
    )
}

function Region({ label, children }: { label: string; children: ReactNode }) {
    return <div className="table-scroll" tabIndex={0} role="region" aria-label={label}>{children}</div>
}

/**
 * The calculations and energies of one shape, in small tables. Calculations that say the same thing share one row and
 * each keeps its own link: same kind, level (by ref), program, workflow tool and review, and for an energy the same
 * value and kind of energy too. Each calculation has one number, the same in every table. Nothing is picked or
 * averaged; a spread is stated only between energies of one kind at one level and one program.
 */
export function ShapeEnergies({ rows, singlePoints, optimisations }: {
    rows: readonly ShapeRow[]
    singlePoints: SinglePointsState
    optimisations: SinglePointsState
}) {
    const numbers = numberCalculations(rows)
    const found = groupBy(rows.flatMap((row) => row.foundBy), foundByKey)
    const energies = groupBy<ShapeEnergy>(rows.flatMap((row) => row.energies), energyKey)
    const spreads = spreadLines(rows)
    return (
        <>
            {found.length > 0 && (
                <Region label="Calculations that found this geometry">
                    <table className="data-table shape-energies-table">
                        <caption className="molecule-page-visually-hidden">Calculations that found this geometry, one row per kind and level</caption>
                        <thead>
                            <tr>
                                <th scope="col">Kind</th>
                                <th scope="col">Level of theory</th>
                                <th scope="col">Calculations</th>
                                <th scope="col">Review</th>
                            </tr>
                        </thead>
                        <tbody>
                            {found.map(({ first, members }) => (
                                <tr key={`${first.type}-${first.ref}`}>
                                    <th scope="row">{calculationDid(first.type) ?? first.type}</th>
                                    <td><Where calculation={first} /></td>
                                    <td><CalculationLinks members={members} numbers={numbers} /></td>
                                    <td>{first.review ? <ReviewChip status={first.review} explain={false} /> : <span className="shape-energy-absent">Not recorded</span>}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </Region>
            )}
            {rows.some((row) => row.energies.length > 0) ? (
                <Region label="Energies on this geometry">
                    <table className="data-table shape-energies-table">
                        <caption className="molecule-page-visually-hidden">Energies on this geometry, one row per value and level</caption>
                        <thead>
                            <tr>
                                <th scope="col">Energy (hartree)</th>
                                <th scope="col">Kind</th>
                                <th scope="col">Level of theory</th>
                                <th scope="col">Calculations</th>
                                <th scope="col">Review</th>
                            </tr>
                        </thead>
                        <tbody>
                            {energies.map(({ first, members }) => (
                                <tr key={`${first.kind}-${first.ref}`}>
                                    <th scope="row">
                                        {first.hartree === null
                                            ? <span className="shape-energy-absent">{(first.kind === "sp" ? singlePoints : optimisations).status === "failed" ? "Could not be read" : "Energy not recorded"}</span>
                                            : <ValueLink value={formatHartree(first.hartree)} full={String(first.hartree)} to={`/calculations/${first.ref}`} />}
                                    </th>
                                    <td><EnergyKindWords energy={first} /></td>
                                    <td><Where calculation={first} /></td>
                                    <td><CalculationLinks members={members} numbers={numbers} /></td>
                                    <td>{first.review ? <ReviewChip status={first.review} explain={false} /> : <span className="shape-energy-absent">Not recorded</span>}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </Region>
            ) : (
                <p className="empty-projection">No energy recorded.</p>
            )}
            {spreads.length > 0 && (
                <Region label="Spread of energies at one level and program">
                    <table className="data-table shape-energies-table">
                        <caption className="molecule-page-visually-hidden">Largest difference between energies of one kind at one level and program</caption>
                        <thead>
                            <tr>
                                <th scope="col">Level of theory</th>
                                <th scope="col">Kind</th>
                                <th scope="col">Calculations</th>
                                <th scope="col">Spread (<span className="t-preserve-case">kJ/mol</span>)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {spreads.map((line) => (
                                <tr key={`${line.kind}-${line.energyKind}-${line.levelLabel}-${line.program}`}>
                                    <th scope="row">{line.level ? <LevelLabel levelOfTheory={line.level} program={line.program} /> : <span className="t-preserve-case">{line.levelLabel} · {line.program}</span>}</th>
                                    <td><EnergyKindWords energy={line} /></td>
                                    <td>{line.count}</td>
                                    <td className="t-preserve-case">{spreadText(line.kjMol)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </Region>
            )}
        </>
    )
}
