import type { ReactNode } from "react"

/**
 * Renders an identifier (a `tse_…`/`lot_…`/`calc_…`-shaped ref) with a
 * `<wbr>` inserted after every `_` -- design/foundations PR E ("record-
 * page residuals" re-review), SHOULD-FIX-3.
 *
 * MEASURED: `.kv-list dd`'s `overflow-wrap: anywhere` broke a long ref
 * at an arbitrary character once its column narrowed even slightly
 * ("tse_aq5ktxlu27nvul3hmdwp / uyuz4e", one orphaned character on its
 * own line). `design-system.css`'s companion fix widens the column and
 * switches that rule to `overflow-wrap: normal; word-break: keep-all` --
 * which stops breaking ANYWHERE, but a non-CJK run with no break
 * opportunity at all (an underscore is not one; a hyphen already is,
 * which is why an InChIKey wrapped cleanly at its own `-` before this
 * file existed) would then simply overflow instead of wrapping. This is
 * the other half of that fix: a `<wbr>` after each `_` gives the browser
 * exactly the break points a ref-shaped token actually has, the same
 * technique `RecordIdentityHeader.tsx`'s own `withSmilesBreaks` already
 * uses for `>>`/`.` boundaries in an unmapped SMILES string.
 *
 * Not applied to every ref in the app -- only where a `.kv-list`/`.data`
 * cell renders one on a page this PR owns (calc/geometry/TS/conformer
 * pages). `RefsDisclosure.tsx`'s own ref rows are a different primitive
 * (`.ref-item-value`, fixed by `refs-disclosure.css`'s own type-scale
 * pass) and are not touched here.
 */
export function refWithBreaks(value: string): ReactNode {
    const parts = value.split(/(_)/)
    const nodes: ReactNode[] = []
    parts.forEach((part, index) => {
        nodes.push(part)
        if (part === "_") nodes.push(<wbr key={`ref-wbr-${index}`} />)
    })
    return nodes
}
