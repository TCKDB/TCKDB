import type { CorrectionTerm } from "../api/methodsApi"
import { ENERGY_DISPLAY_UNITS, energyUnitLabel, type EnergyDisplayUnit } from "../domain/energyUnits"

/**
 * The one rendering of an energy-correction scheme's own parameter table --
 * shared by `LevelOfTheoryPage` (the LOT record page's inline "Correction
 * schemes" section, methods-surface plan §4.2 item 3) and
 * `CorrectionSchemePage` (the standalone scheme page, §4.3), so an atom or
 * bond table cannot silently diverge in shape between the two places it
 * renders (plan §6: "Build one `CorrectionSchemeTable` component ... and
 * use it in both places").
 *
 * Kind-dispatched on `correction_kind` (`"atom"` / `"bond"` / `"component"`
 * -- the three child tables `EnergyCorrectionTermSummary` uniformly
 * projects, see that schema's own docstring): an atom-keyed scheme
 * (`atom_energy`, 8 rows on the live archive) renders one Element/Value
 * table; a bond-keyed scheme (`bac_petersson`, 45 rows) renders one
 * Bond/Value table; a component-keyed scheme (`bac_melius`, not yet
 * deposited anywhere) renders ONE table split into its own
 * `component_kind` groups (`atom_corr`/`bond_corr_length`/
 * `bond_corr_neighbor`/`mol_corr`), each its own `<h4>` sub-heading, since
 * the four sub-types are not comparable rows of one flat table.
 *
 * Every row renders — never summarised, truncated, or paginated (the
 * task brief's own acceptance criterion: "8 atom rows and 45 bond rows …
 * not summarised or truncated").
 */
export function CorrectionSchemeTable({ corrections, units }: {
    corrections: CorrectionTerm[]
    units?: string | null
}) {
    const unitLabel = formatUnits(units)
    const atomRows = corrections.filter((row) => row.correction_kind === "atom")
    const bondRows = corrections.filter((row) => row.correction_kind === "bond")
    const componentRows = corrections.filter((row) => row.correction_kind === "component")

    return (
        <>
            {atomRows.length > 0 && <SimpleParamTable caption="Element" rows={atomRows} unitLabel={unitLabel} />}
            {bondRows.length > 0 && <SimpleParamTable caption="Bond" rows={bondRows} unitLabel={unitLabel} />}
            {componentRows.length > 0 && <ComponentParamTables rows={componentRows} unitLabel={unitLabel} />}
        </>
    )
}

function formatUnits(units?: string | null): string | null {
    if (!units) return null
    if ((ENERGY_DISPLAY_UNITS as readonly string[]).includes(units)) return energyUnitLabel(units as EnergyDisplayUnit)
    // An unrecognised units string (a future `EnergyUnit` member this
    // client doesn't have a specific label for yet) is still shown, not
    // silently dropped -- see `api/scientificSchemas.ts`'s productLevels
    // `energy_source` for the same "unrecognised but still real" rule.
    return units
}

function SimpleParamTable({ caption, rows, unitLabel }: { caption: "Element" | "Bond"; rows: CorrectionTerm[]; unitLabel: string | null }) {
    return (
        <div className="table-scroll">
            <table className="data-table correction-param-table" aria-label={`${caption} correction parameters`}>
                <thead>
                    <tr>
                        <th scope="col">{caption}</th>
                        <th scope="col">{unitLabel ? `Value (${unitLabel})` : "Value"}</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row) => (
                        <tr key={`${row.correction_kind}-${row.target}`}>
                            <td data-label={caption}><span className="data">{row.target}</span></td>
                            <td data-label="Value" className="num">{row.value}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}

const COMPONENT_KIND_LABELS: Record<string, string> = {
    atom_corr: "Atom correction",
    bond_corr_length: "Bond correction (length)",
    bond_corr_neighbor: "Bond correction (neighbor)",
    mol_corr: "Molecule correction",
}

function ComponentParamTables({ rows, unitLabel }: { rows: CorrectionTerm[]; unitLabel: string | null }) {
    const byKind = new Map<string, CorrectionTerm[]>()
    for (const row of rows) {
        const kind = row.component_kind ?? "unspecified"
        const group = byKind.get(kind) ?? []
        group.push(row)
        byKind.set(kind, group)
    }
    return (
        <>
            {[...byKind.entries()].map(([kind, kindRows]) => (
                <div key={kind} className="correction-component-group">
                    <h4 className="t-heading-2">{COMPONENT_KIND_LABELS[kind] ?? kind.replaceAll("_", " ")}</h4>
                    <div className="table-scroll">
                        <table className="data-table correction-param-table" aria-label={`${COMPONENT_KIND_LABELS[kind] ?? kind} correction parameters`}>
                            <thead>
                                <tr>
                                    <th scope="col">Target</th>
                                    <th scope="col">{unitLabel ? `Value (${unitLabel})` : "Value"}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {kindRows.map((row) => (
                                    <tr key={`${kind}-${row.target}`}>
                                        <td data-label="Target"><span className="data">{row.target}</span></td>
                                        <td data-label="Value" className="num">{row.value}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            ))}
        </>
    )
}
