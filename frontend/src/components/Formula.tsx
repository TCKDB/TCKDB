import { Fragment } from "react"
import { formulaTokens } from "../domain/chemistryFormat"

/**
 * A molecular formula with element counts set as subscripts -- e.g. "H2O"
 * renders as H, then a subscript 2, then O. Ported behaviour, not ported
 * code, from `backend/app/api/landing.py`'s `formulaNode`: see
 * `../domain/chemistryFormat.ts` for why the port is by rule rather than by
 * file.
 *
 * A string that does not parse as element-symbol/count pairs (or does not
 * round-trip back to itself once parsed) renders exactly as it arrived,
 * with no subscripts -- guessing at chemistry is worse than plain text.
 */
export function Formula({ value }: { value: string }) {
    const tokens = formulaTokens(value)
    if (!tokens) return <>{value}</>
    return <>{tokens.map((token, index) => (
        <Fragment key={index}>
            {token.element}
            {token.count && <sub>{token.count}</sub>}
        </Fragment>
    ))}</>
}
