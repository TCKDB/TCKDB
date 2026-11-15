import { Fragment, type ReactNode } from "react"
import { calculationDid } from "../domain/calculationTypeFormat"
import { formatHartree, spreadLines, spreadText, type ShapeCalculation, type ShapeEnergy, type ShapeRow } from "../domain/conformerGallery"
import type { SinglePointsState } from "../hooks/useSpeciesSinglePoints"
import { LevelLabel } from "./LevelLabel"
import { RecordLink } from "./RecordLink"
import { ReviewChip } from "./ReviewChip"
import { ValueLink } from "./ValueLink"

const KIND_WORDS = { sp: "Single-point energy", opt: "Final energy of the optimisation" } as const

/** Level and program as one run of text; a missing part is said, never filled in. */
function Where({ calculation }: { calculation: ShapeCalculation }) {
    if (calculation.level) {
        return <><LevelLabel levelOfTheory={calculation.level} program={calculation.program} />{!calculation.program && ", program not recorded"}</>
    }
    return <>level not recorded, {calculation.program ? <span className="t-preserve-case">{calculation.program}</span> : "program not recorded"}</>
}

type Member = { calculation: ShapeCalculation; deposit: number }

/** Calculations that share what a row says, in deposit order; each keeps its own link. */
function groupBy<T extends ShapeCalculation>(items: readonly { item: T; deposit: number }[], keyOf: (item: T) => string): { first: T; members: Member[] }[] {
    const groups = new Map<string, { first: T; members: Member[] }>()
    for (const { item, deposit } of items) {
        const key = keyOf(item)
        const group = groups.get(key) ?? { first: item, members: [] }
        group.members.push({ calculation: item, deposit })
        groups.set(key, group)
    }
    return [...groups.values()]
}

const levelKeyOf = (calculation: ShapeCalculation) => `${calculation.levelRef ?? calculation.levelLabel ?? "-"}\n${calculation.program ?? "-"}\n${calculation.workflowTool ?? "-"}`

/** "Calculation 1, 2, 3": the number is the deposit's place in the list, and each links to its own calculation. */
function CalculationLinks({ members }: { members: readonly Member[] }) {
    return (
        <>
            {members.length === 1 ? "Calculation " : "Calculations "}
            {members.map((member, index) => (
                <Fragment key={member.calculation.ref}>
                    {index > 0 && ", "}
                    <RecordLink kind="calculation" to={`/calculations/${member.calculation.ref}`} label={`Calculation ${member.deposit}`}>{member.deposit}</RecordLink>
                </Fragment>
            ))}
        </>
    )
}

function Region({ label, children }: { label: string; children: ReactNode }) {
    return <div className="table-scroll" tabIndex={0} role="region" aria-label={label}>{children}</div>
}

/**
 * The calculations and energies of one shape, in small tables. Calculations that say the same thing (same kind, level
 * and program, and for an energy the same value and review) share one row and each keeps its own link. Nothing is
 * picked or averaged; a spread is stated only between energies of one kind at one level and one program.
 */
export function ShapeEnergies({ rows, singlePoints, optimisations }: {
    rows: readonly ShapeRow[]
    singlePoints: SinglePointsState
    optimisations: SinglePointsState
}) {
    const found = groupBy(
        rows.flatMap((row, index) => row.foundBy.map((item) => ({ item, deposit: index + 1 }))),
        (item) => `${item.type}\n${levelKeyOf(item)}\n${item.review ?? "-"}`,
    )
    const energies = groupBy<ShapeEnergy>(
        rows.flatMap((row, index) => row.energies.map((item) => ({ item, deposit: index + 1 }))),
        (item) => `${item.kind}\n${item.hartree ?? "none"}\n${levelKeyOf(item)}\n${item.review ?? "-"}`,
    )
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
                                    <td><Where calculation={first} />{first.workflowTool && <>, run by <span className="t-preserve-case">{first.workflowTool}</span></>}</td>
                                    <td><CalculationLinks members={members} /></td>
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
                                    <td>{KIND_WORDS[first.kind]}</td>
                                    <td><Where calculation={first} /></td>
                                    <td><CalculationLinks members={members} /></td>
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
                                <tr key={`${line.kind}-${line.levelLabel}-${line.program}`}>
                                    <th scope="row">{line.level ? <LevelLabel levelOfTheory={line.level} program={line.program} /> : <span className="t-preserve-case">{line.levelLabel} · {line.program}</span>}</th>
                                    <td>{KIND_WORDS[line.kind]}</td>
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
