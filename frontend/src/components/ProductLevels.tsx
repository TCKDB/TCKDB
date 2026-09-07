import { lotLabel } from "../api/scientificSchemas"
import { productLevelsAgree, type ProductLevels } from "../domain/productLevels"

// ---------------------------------------------------------------------------
// A statmech or thermo record can carry different levels of theory for its
// optimised geometry, its frequencies, and its electronic energy — see
// `domain/productLevels.ts` for how those three are resolved. This module
// is the ONE rendering of that fact, shared by `EntryStatmechSection.tsx`
// and `EntryThermoSection.tsx` (record cards, "Records in this group"
// tables, and the identical-values group's own shared body) so a record
// with mixed levels reads identically wherever it appears:
//   - all three the same level of theory -> one fact, "Level of theory: X",
//     exactly the single line this page showed before any of the three was
//     split out.
//   - otherwise -> three labelled facts, "Geometry"/"Frequencies"/"Energy",
//     each through the same `lotLabel()` compact formatter, with a muted
//     note under Energy explaining WHY it is what it is when that's not
//     self-evident from the label alone.
// ---------------------------------------------------------------------------

function levelText(level: ProductLevels["geometry"]): string {
    return level ? lotLabel(level) : "not recorded"
}

/** The reader-facing explanation for `energy_source`, or `null` when the
 *  label alone already says everything (`"opt"`: the energy line already
 *  reads identically to Geometry's, nothing more to add; an unrecognised
 *  future value renders the same way — no note, not a guessed one). */
function energySourceNote(source: ProductLevels["energy_source"]): string | null {
    return source === "sp" ? "single point on the optimised geometry" : null
}

/** `energy_source` values the backend can declare with no calculation
 *  evidence behind them at all — rendered as a pill (never a note, which
 *  would imply the archive derived them the normal way) so a composite or
 *  imported energy is never mistaken for one traced to a source
 *  calculation. */
function isEvidenceFreeEnergySource(source: ProductLevels["energy_source"]): boolean {
    return source === "composite" || source === "imported"
}

/**
 * `<dt>/<dd>` pairs for one record's levels, meant to sit directly inside
 * an existing `<dl className="kv-list">` (a fragment, not its own `<dl>` —
 * every caller already owns the wrapping list). Collapses to the single
 * historical "Level of theory" fact when `productLevelsAgree`, expands to
 * three otherwise.
 */
export function ProductLevelsFact({ levels }: { levels: ProductLevels }) {
    if (productLevelsAgree(levels)) {
        return (
            <div>
                <dt>Level of theory</dt>
                <dd>{levelText(levels.geometry)}</dd>
            </div>
        )
    }
    const note = energySourceNote(levels.energy_source)
    const evidenceFree = isEvidenceFreeEnergySource(levels.energy_source)
    return (
        <>
            <div><dt>Geometry</dt><dd>{levelText(levels.geometry)}</dd></div>
            <div><dt>Frequencies</dt><dd>{levelText(levels.frequency)}</dd></div>
            <div>
                <dt>Energy</dt>
                <dd>
                    {levelText(levels.energy)}
                    {evidenceFree && (
                        <>
                            {" "}
                            <span className="value-pill value-pill--muted">{levels.energy_source}</span>
                        </>
                    )}
                    {note && <div className="note">{note}</div>}
                </dd>
            </div>
        </>
    )
}

/** Header cells for a "Level of theory" table column — one column when
 *  every row agrees within itself, three (Geometry/Frequencies/Energy)
 *  otherwise. Meant to sit inside an existing `<tr>` in a table's
 *  `<thead>`, alongside this table's other `<th>`s. */
export function ProductLevelsTableHead({ showThree }: { showThree: boolean }) {
    if (showThree) {
        return (
            <>
                <th scope="col">Geometry</th>
                <th scope="col">Frequencies</th>
                <th scope="col">Energy</th>
            </>
        )
    }
    return <th scope="col">Level of theory</th>
}

/** Data cells for one row's levels, matching `ProductLevelsTableHead`'s own
 *  `showThree` decision for the whole table — nowrap via `.data`, per the
 *  `.data-table` convention every other identifier-shaped cell on this
 *  table already follows (`design-system.css`'s `.data-table td .data`
 *  rule), so a level of theory never breaks mid-token inside a scrolling
 *  table. */
export function ProductLevelsTableCells({ levels, showThree }: { levels: ProductLevels; showThree: boolean }) {
    if (!showThree) {
        return <td data-label="Level of theory"><LevelCell level={levels.geometry} /></td>
    }
    const note = energySourceNote(levels.energy_source)
    const evidenceFree = isEvidenceFreeEnergySource(levels.energy_source)
    return (
        <>
            <td data-label="Geometry"><LevelCell level={levels.geometry} /></td>
            <td data-label="Frequencies"><LevelCell level={levels.frequency} /></td>
            <td data-label="Energy">
                <LevelCell level={levels.energy} />
                {evidenceFree && (
                    <>
                        {" "}
                        <span className="value-pill value-pill--muted">{levels.energy_source}</span>
                    </>
                )}
                {note && <div className="note">{note}</div>}
            </td>
        </>
    )
}

function LevelCell({ level }: { level: ProductLevels["geometry"] }) {
    return level ? <span className="data">{lotLabel(level)}</span> : <>not recorded</>
}
