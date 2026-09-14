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

/**
 * The shared "how is a species presented" primitive (owner ruling, filed
 * after `/reactions/rxn_fktlilofmrdaylunqva2hbltpq` rendered as
 * "CH3OS <=> CH3OS": a reactant `[CH2]SO` and a product `OC[S]` are two
 * different structures sharing one formula, and formula-only presentation
 * made an isomerisation read as a species reacting to itself).
 *
 * SMILES leads (a data run, `code.data`, never uppercased -- see
 * `text-transform-scientific-guard.test.ts`); the formula, still typeset
 * through `Formula` for its subscripts, follows in parentheses only when
 * the archive actually computed one: "[CH2]SO (CH3OS)". Formula alone
 * (no SMILES to lead with) or nothing at all are the only other shapes --
 * never a bare formula standing in for a species that could share it with
 * something structurally different.
 */
export function SpeciesFace({ smiles, formula }: { smiles?: string | null; formula?: string | null }) {
    if (smiles) {
        return (
            <>
                <code className="data species-face-smiles">{smiles}</code>
                {formula && (
                    <>
                        {" "}
                        <span className="species-face-formula">(<Formula value={formula} />)</span>
                    </>
                )}
            </>
        )
    }
    if (formula) return <Formula value={formula} />
    return null
}
