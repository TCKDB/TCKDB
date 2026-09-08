import type { ReactNode } from "react"
import { LevelOfTheoryLink } from "./LevelOfTheoryLink"
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

/**
 * `levelNode`'s output is what every existing caller used to render as
 * plain `lotLabel()` text -- now the SAME text, wrapped in a
 * `LevelOfTheoryLink` when the level carries a `level_of_theory_ref`, so
 * `EntryStatmechSection.tsx`/`EntryThermoSection.tsx` (this module's two
 * callers) get a working `/methods/:lotRef` link on every Geometry/
 * Frequencies/Energy fact without either file touching `lotLabel()`
 * directly (methods-surface plan §2.4/§6, `plan-methods-surface-v2`, not
 * committed to this repo).
 */
function levelNode(level: ProductLevels["geometry"]): ReactNode {
    return level ? <LevelOfTheoryLink levelOfTheory={level} /> : "not recorded"
}

/** The Energy fact's own value node -- `levelNode(level)` when there IS a
 *  level to show, else `null` when `energy_source` names a real (if
 *  evidence-free) classification for the absence (`isOtherEnergySource`,
 *  e.g. `"composite"`/`"imported"`/an unrecognised future value) rather
 *  than plain "not recorded" -- the pill this record's `energy_source`
 *  renders already says what IS known about it; "not recorded" would
 *  misstate that as nothing being known at all. */
function energyValueNode(level: ProductLevels["energy"], source: ProductLevels["energy_source"]): ReactNode | null {
    if (level) return levelNode(level)
    return isOtherEnergySource(source) ? null : "not recorded"
}

/** The reader-facing explanation for `energy_source`, or `null` when the
 *  label alone already says everything (`"opt"`: the energy line already
 *  reads identically to Geometry's, nothing more to add). Shown on EVERY
 *  sp-sourced row/fact, agreeing or not -- Geometry/Frequencies/Energy are
 *  always rendered separately now (see this module's own header comment),
 *  so there is no longer a "collapsed" case for this to be exclusive to. */
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
 * all three agree; when they do agree AND at least one is actually
 * recorded, an optional muted note ("all at the same level") renders
 * under the block, on its own full-width row (`.kv-list--wide`,
 * `design-system.css`), rather than folding the three facts back into
 * one. A record with no levels recorded at all does NOT get this note —
 * "not recorded" three times over is not the same claim as "compared and
 * found equal".
 */
export function ProductLevelsFact({ levels }: { levels: ProductLevels }) {
    const note = energySourceNote(levels.energy_source)
    const otherSource = isOtherEnergySource(levels.energy_source)
    const energyNode = energyValueNode(levels.energy, levels.energy_source)
    return (
        <>
            <div><dt>Geometry</dt><dd>{levelNode(levels.geometry)}</dd></div>
            <div><dt>Frequencies</dt><dd>{levelNode(levels.frequency)}</dd></div>
            <div>
                <dt>Energy</dt>
                <dd>
                    {energyNode}
                    {otherSource && (
                        <>
                            {energyNode !== null && " "}
                            <span className="value-pill value-pill--muted">{levels.energy_source}</span>
                        </>
                    )}
                    {note && <div className="note">{note}</div>}
                </dd>
            </div>
            {/* `productLevelsAgree` alone treats all-three-`null` as
                agreeing (one shared "not recorded" fact) -- correct for
                that function's OTHER callers, but wrong here: a record
                with no levels recorded at all (e.g. a literature-origin
                thermo record, `EntryThermoSection.tsx`'s own
                `thermoRecordProductLevels` fallback) would otherwise print
                "not recorded" three times followed by "all at the same
                level", which claims a fact (they were compared and found
                equal) that was never established. The extra `!= null`
                guard requires an actual recorded level before this note
                is safe to show. */}
            {productLevelsAgree(levels) && levels.geometry != null && (
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
    const energyNode = energyValueNode(levels.energy, levels.energy_source)
    return (
        <>
            <td data-label="Geometry"><LevelCell level={levels.geometry} /></td>
            <td data-label="Frequencies"><LevelCell level={levels.frequency} /></td>
            <td data-label="Energy">
                {energyNode !== null ? <span className="data">{energyNode}</span> : null}
                {otherSource && (
                    <>
                        {energyNode !== null && " "}
                        <span className="value-pill value-pill--muted">{levels.energy_source}</span>
                    </>
                )}
                {note && <div className="note">{note}</div>}
            </td>
        </>
    )
}

function LevelCell({ level }: { level: ProductLevels["geometry"] }) {
    return level ? <span className="data"><LevelOfTheoryLink levelOfTheory={level} /></span> : <>not recorded</>
}
