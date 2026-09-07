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

/** The Energy fact's own value text -- `levelText(level)` when there IS a
 *  level to show, else `null` when `energy_source` names a real (if
 *  evidence-free) classification for the absence (`isOtherEnergySource`,
 *  e.g. `"composite"`/`"imported"`/an unrecognised future value) rather
 *  than plain "not recorded" -- the pill this record's `energy_source`
 *  renders already says what IS known about it; "not recorded" would
 *  misstate that as nothing being known at all. */
function energyValueText(level: ProductLevels["energy"], source: ProductLevels["energy_source"]): string | null {
    if (level) return levelText(level)
    return isOtherEnergySource(source) ? null : "not recorded"
}

/** The reader-facing explanation for `energy_source`, or `null` when the
 *  label alone already says everything (`"opt"`: the energy line already
 *  reads identically to Geometry's, nothing more to add). Only ever shown
 *  when this record's own three levels actually disagree — see the
 *  `agree` guard at each call site; an agreeing record never gets this
 *  note, matching the collapsed single-fact display it renders instead. */
function energySourceNote(source: ProductLevels["energy_source"]): string | null {
    return source === "sp" ? "single point on the optimised geometry" : null
}

/** `energy_source` values that are neither of the two calculation-derived
 *  cases (`"opt"`/`"sp"`) nor absent (`null`) -- today `"composite"`/
 *  `"imported"`, and any future value the backend declares that this
 *  client doesn't have a specific note for yet (kept as a plain string on
 *  the wire for exactly this reason — see `api/scientificSchemas.ts`'s
 *  `productLevelsSchema`). Rendered as a pill (never folded into a note,
 *  which would imply the archive derived it the normal way, and never
 *  silently dropped just because this client doesn't recognise the exact
 *  word) so an energy with no ordinary calculation evidence behind it is
 *  never mistaken for one traced to a source calculation. */
function isOtherEnergySource(source: ProductLevels["energy_source"]): boolean {
    return source != null && source !== "opt" && source !== "sp"
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
    const otherSource = isOtherEnergySource(levels.energy_source)
    const energyText = energyValueText(levels.energy, levels.energy_source)
    return (
        <>
            <div><dt>Geometry</dt><dd>{levelText(levels.geometry)}</dd></div>
            <div><dt>Frequencies</dt><dd>{levelText(levels.frequency)}</dd></div>
            <div>
                <dt>Energy</dt>
                <dd>
                    {energyText}
                    {otherSource && (
                        <>
                            {energyText && " "}
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

/**
 * Data cells for one row's levels, matching `ProductLevelsTableHead`'s own
 * `showThree` decision for the whole table — nowrap via `.data`, per the
 * `.data-table` convention every other identifier-shaped cell on this
 * table already follows (`design-system.css`'s `.data-table td .data`
 * rule), so a level of theory never breaks mid-token inside a scrolling
 * table.
 *
 * `showThree` is a TABLE-WIDE decision (does ANY row disagree with
 * itself), but the Energy note is a PER-ROW one: a table forced into
 * three columns by one disagreeing row still holds rows that agree with
 * themselves (e.g. every other record deposited at one uniform level,
 * still citing a real `sp` role) -- those rows get no note, exactly as
 * `ProductLevelsFact` would render them collapsed. Emitting the note for
 * every `energy_source === "sp"` row regardless of that row's own
 * agreement (the bug this fixes) buried the one row that actually
 * differs under six identical explanatory notes that added nothing.
 */
export function ProductLevelsTableCells({ levels, showThree }: { levels: ProductLevels; showThree: boolean }) {
    if (!showThree) {
        return <td data-label="Level of theory"><LevelCell level={levels.geometry} /></td>
    }
    const note = productLevelsAgree(levels) ? null : energySourceNote(levels.energy_source)
    const otherSource = isOtherEnergySource(levels.energy_source)
    const energyText = energyValueText(levels.energy, levels.energy_source)
    return (
        <>
            <td data-label="Geometry"><LevelCell level={levels.geometry} /></td>
            <td data-label="Frequencies"><LevelCell level={levels.frequency} /></td>
            <td data-label="Energy">
                {energyText ? <span className="data">{energyText}</span> : null}
                {otherSource && (
                    <>
                        {energyText && " "}
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
