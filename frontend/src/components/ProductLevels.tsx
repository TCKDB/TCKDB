import { lotLabel } from "../api/scientificSchemas"
import { productLevelsAgree, type ProductLevels } from "../domain/productLevels"

// ---------------------------------------------------------------------------
// A statmech or thermo record can carry different levels of theory for its
// optimised geometry, its frequencies, and its electronic energy — see
// `domain/productLevels.ts` for how those three are resolved. This module
// is the ONE rendering of that fact, shared by `EntryStatmechSection.tsx`
// and `EntryThermoSection.tsx` (record cards, "Records in this group"
// tables, and the identical-values group's own shared body) so a record
// with mixed levels reads identically wherever it appears.
//
// Owner decision (2026-09): on a page where every level happened to be the
// same (B3LYP/def2-TZVP throughout), the old rule collapsed all three to
// one "Level of theory: X" fact — "I don't see the separation in LoT". This
// module ALWAYS renders three labelled facts/columns, "Geometry"/
// "Frequencies"/"Energy", each through the same `lotLabel()` compact
// formatter, whether or not the three actually agree — never collapsed.
// A muted note sits under Energy explaining WHY it is what it is when
// that's not self-evident from the label alone, and `ProductLevelsFact`
// (record cards only, not the tables) adds an optional muted "all at the
// same level" note under the block when all three genuinely do agree.
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
 * every caller already owns the wrapping list). ALWAYS renders three
 * facts — Geometry/Frequencies/Energy — never collapsed to one, even when
 * all three agree; when they do agree, an optional muted note ("all at the
 * same level") renders under the block, on its own full-width row
 * (`.kv-list--wide`, `design-system.css`), rather than folding the three
 * facts back into one.
 */
export function ProductLevelsFact({ levels }: { levels: ProductLevels }) {
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
            {productLevelsAgree(levels) && (
                <div className="kv-list--wide">
                    <div className="note">all at the same level</div>
                </div>
            )}
        </>
    )
}

/** Header cells for the Geometry/Frequencies/Energy table columns — ALWAYS
 *  three, never collapsed to one "Level of theory" column even when every
 *  row agrees. Meant to sit inside an existing `<tr>` in a table's
 *  `<thead>`, alongside this table's other `<th>`s. */
export function ProductLevelsTableHead() {
    return (
        <>
            <th scope="col">Geometry</th>
            <th scope="col">Frequencies</th>
            <th scope="col">Energy</th>
        </>
    )
}

/**
 * Data cells for one row's levels — ALWAYS three (Geometry/Frequencies/
 * Energy), matching `ProductLevelsTableHead`'s own always-three columns.
 * Nowrap via `.data`, per the `.data-table` convention every other
 * identifier-shaped cell on this table already follows
 * (`design-system.css`'s `.data-table td .data` rule), so a level of
 * theory never breaks mid-token inside a scrolling table.
 *
 * The Energy note is a PER-ROW decision that simply follows this row's own
 * `energy_source === "sp"` — every sp-sourced row gets it, regardless of
 * whether that row's geometry/frequency/energy happen to agree with each
 * other. (An earlier review fixed a bug where the note was emitted for
 * every sp row in a table forced into three columns by some OTHER row's
 * disagreement, even rows that individually agreed with themselves; the
 * fix at the time was to gate the note on that row's own agreement. Now
 * that every table always shows three columns, "this row's own agreement"
 * is no longer a meaningful distinction for the note — there is no longer
 * a differing row to single out, so the note is just "was this row's own
 * energy sourced from a single point", full stop.)
 */
export function ProductLevelsTableCells({ levels }: { levels: ProductLevels }) {
    const note = energySourceNote(levels.energy_source)
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
