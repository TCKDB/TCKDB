import type { ReactNode } from "react"
import { fillValue, type FillableValue } from "../domain/quantityFormat"

/** A rounded number with the stored value as machine-readable value and hover title; a number never rounded stays bare. */
export function Stored({ full, children }: { full?: string; children: ReactNode }) {
    return full === undefined ? <>{children}</> : <data value={full} title={full}>{children}</data>
}

/** The line under a table or section whose numbers are rounded. */
export const ROUNDED_NOTE = "6 significant figures; hover for the stored value."

/** The same line under a computed value, which no record stores. */
/** The line under a table that mixes stored and computed values. */
export const MIXED_ROUNDED_NOTE = "6 significant figures; hover for the full value."

export const EVALUATED_NOTE = "6 significant figures; hover for the value as computed."

/**
 * Renders whatever `fillValue` (`../domain/quantityFormat.ts`) decided a
 * value is: absent, a named absence, a `{value, unit, exponent}` quantity,
 * or a plain string/number. Ported behaviour, not ported code, from
 * `landing.py`'s `fillValue`, which built the same four shapes directly
 * into a DOM node — see the module docstring on `quantityFormat.ts`.
 *
 * The `absent`/`named-absence` cases both render in the `.absent` class,
 * matching `landing.py`: a reader must not be able to mistake either for a
 * value the record actually holds, so both are drawn the same way for the
 * same reason.
 *
 * The exponent, when present, is a real `<sup>` element (not a Unicode
 * superscript character) so it stays selectable/copyable text and reads
 * correctly to assistive technology, matching `landing.py`'s own
 * `make("sup", ...)`.
 */
export function QuantityValue({ value }: { value: FillableValue }) {
    const filled = fillValue(value)
    if (filled.kind === "absent" || filled.kind === "named-absence") {
        return <span className="absent">{filled.text}</span>
    }
    if (filled.kind === "quantity") {
        return (
            <>
                <Stored full={filled.full}>
                    {filled.value}
                    {filled.exponent && (
                        <>
                            {" × 10"}
                            <sup>{filled.exponent}</sup>
                        </>
                    )}
                </Stored>
                {filled.unit && <span className="unit"> {filled.unit}</span>}
            </>
        )
    }
    return <>{filled.text}</>
}
