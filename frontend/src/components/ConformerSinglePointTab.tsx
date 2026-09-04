import { useState } from "react"
import { Link } from "react-router-dom"
import "../conformer-group.css"
import "../entry-science.css"
import "../energy-display.css"
import type { ConformerProjection } from "../api/speciesEntryApi"
import type { SpeciesCalculationEnergyRecord } from "../api/speciesCalculationsApi"
import { lotLabel } from "../api/scientificSchemas"
import { conformerLabel } from "../domain/conformerEvidence"
import {
    ENERGY_DISPLAY_UNITS,
    energyUnitLabel,
    formatEnergyForDisplay,
    type EnergyDisplayUnit,
} from "../domain/energyUnits"
import { softwareLabel } from "../domain/provenanceFormat"
import { SectionHeading } from "./PageSections"

type Calculation = NonNullable<NonNullable<ConformerProjection["observations"]>[number]["calculations"]>[number]

// One row per single-point CALCULATION, never per observation: an
// observation that deposited two independent sp calculations (a real,
// fixture-covered case) gets two rows, not one row hiding a second value.
// An observation with no sp calculation at all still gets exactly one row
// -- absent evidence is a row that says so, never a row that disappears.
type Row = { key: string; observationRef: string; calculation: Calculation | null }

function buildRows(observations: NonNullable<ConformerProjection["observations"]>): Row[] {
    const rows: Row[] = []
    for (const observation of observations) {
        const ref = observation.conformer_observation.conformer_observation_ref
        const spCalculations = (observation.calculations ?? []).filter((calculation) => calculation.type === "sp")
        if (spCalculations.length === 0) {
            rows.push({ key: ref, observationRef: ref, calculation: null })
            continue
        }
        for (const calculation of spCalculations) {
            rows.push({ key: `${ref}-${calculation.calculation_ref}`, observationRef: ref, calculation })
        }
    }
    return rows
}

/**
 * A table, not a card per observation: this tab used to render one
 * `science-record` article per observation, each with its OWN hartree
 * value and its OWN five-button unit toggle -- eleven observations meant
 * eleven near-identical toggles for what is, underneath, one electronic
 * energy shown in one chosen unit at a time. The unit is now a single
 * control for the whole table (`unit` state below); every cell still
 * carries its unit on the value itself (`formatEnergyForDisplay` has no
 * "value only" mode -- see `domain/energyUnits.ts` -- so switching away
 * from hartree can never leave a bare, ambiguous number in a cell).
 *
 * `valueHartree` is joined from `spEnergies` (`api/speciesCalculationsApi.ts`)
 * by `calculation_ref`, since the calculation summaries this tab already
 * had (`conformer.observations[].calculations`) do not carry a result
 * value at all (see that API module's own docstring for why). A
 * calculation ref with no matching energy record (the enrichment fetch
 * failed, or genuinely has no parsed energy) still renders its row -- the
 * energy cell alone says "not recorded", never a silently dropped row. An
 * observation with no sp calculation at all ALSO keeps its row (`buildRows`
 * above) -- dropping it would make an 11-observation entry look like it
 * only ever deposited 7 sightings of single-point evidence.
 */
export function ConformerSinglePointTab({ conformer, spEnergies }: {
    conformer: ConformerProjection
    spEnergies: SpeciesCalculationEnergyRecord[]
}) {
    const [unit, setUnit] = useState<EnergyDisplayUnit>("hartree")
    const observations = conformer.observations ?? []
    const rows = buildRows(observations)
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
            {rows.length === 0 ? (
                <p className="empty-projection">No deposited observations are projected for this conformer.</p>
            ) : (
                <>
                    {/* ONE unit switcher for the whole table -- every row's
                        energy cell reads off this same `unit` state, rather
                        than each row owning its own toggle. */}
                    <fieldset className="energy-toggle" aria-label="Energy display unit for every row below">
                        <legend>Units</legend>
                        {ENERGY_DISPLAY_UNITS.map((candidate) => (
                            <button
                                key={candidate}
                                type="button"
                                aria-pressed={unit === candidate}
                                onClick={() => setUnit(candidate)}
                            >
                                {energyUnitLabel(candidate)}
                            </button>
                        ))}
                    </fieldset>
                    <div className="table-scroll table-scroll--compact">
                        <table className="data-table" aria-label="Single-point energies">
                            <thead>
                                <tr>
                                    <th scope="col">Observation</th>
                                    <th scope="col">Calculation</th>
                                    <th scope="col">Energy</th>
                                    <th scope="col">Level of theory</th>
                                    <th scope="col">Software</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row) => {
                                    const valueHartree = row.calculation
                                        ? energyByCalculationRef.get(row.calculation.calculation_ref) ?? null
                                        : null
                                    return (
                                        <tr key={row.key}>
                                            <td data-label="Observation">
                                                <Link className="data" to={`/conformer-observations/${row.observationRef}`}>{row.observationRef}</Link>
                                            </td>
                                            <td data-label="Calculation">
                                                {row.calculation
                                                    ? <Link className="data" to={`/calculations/${row.calculation.calculation_ref}`}>{row.calculation.calculation_ref}</Link>
                                                    : "not recorded"}
                                            </td>
                                            <td className="num" data-label="Energy">
                                                {!row.calculation ? (
                                                    <span className="energy-display-absent">no single-point calculation recorded</span>
                                                ) : valueHartree === null ? (
                                                    <span className="energy-display-absent">not recorded</span>
                                                ) : (
                                                    <span className="energy-display-value" data-testid="energy-display-value">
                                                        {formatEnergyForDisplay(valueHartree, unit)}
                                                    </span>
                                                )}
                                            </td>
                                            <td data-label="Level of theory">
                                                {row.calculation?.level_of_theory ? lotLabel(row.calculation.level_of_theory) : "not recorded"}
                                            </td>
                                            <td data-label="Software">
                                                {row.calculation ? (softwareLabel(row.calculation.software_release) ?? "not recorded") : "not recorded"}
                                            </td>
                                        </tr>
                                    )
                                })}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </section>
    )
}
