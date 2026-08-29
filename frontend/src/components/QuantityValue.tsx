import { fillValue, type FillableValue } from "../domain/quantityFormat"

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
                {filled.value}
                {filled.exponent && (
                    <>
                        {" × 10"}
                        <sup>{filled.exponent}</sup>
                    </>
                )}
                {filled.unit && <span className="unit"> {filled.unit}</span>}
            </>
        )
    }
    return <>{filled.text}</>
}
