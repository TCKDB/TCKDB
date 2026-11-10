import type { ReactNode } from "react"
import "../page-shell.css"
import { PageSectionsProvider } from "./PageSections"
import { TableOfContents } from "./TableOfContents"
import { NARROW_QUERY, useMediaQuery } from "../hooks/useMediaQuery"

/**
 * The shared shell every record page renders its content through: an "On this page" column
 * (`TableOfContents`) whose list appears once two or more sections are mounted (`PageSections.tsx`).
 * `contents="list"` (default) always reserves the column, so nothing shifts when sections register or
 * a tab changes; `contents="none"` is for a page whose section count is fixed below
 * `MIN_SECTIONS_FOR_LIST` and renders no column at all.
 * Page width stays in each page's own stylesheet.
 *
 * `identity` is the page's header block (formula, SMILES, reference). It renders first inside
 * `.page-shell-content`.
 *
 * Wide screens: the contents column follows the content in the markup and is placed on the left
 * with CSS `order`, so the skip link lands on the content, not on a dozen contents links.
 * Narrow screens: the single "Contents" disclosure comes first.
 */
export function PageShell({ identity, children, contents = "list" }: {
    identity?: ReactNode
    children: ReactNode
    contents?: "list" | "none"
}) {
    const narrow = useMediaQuery(NARROW_QUERY)
    return (
        <PageSectionsProvider>
            <div className="page-shell-layout">
                {narrow && contents === "list" && <TableOfContents />}
                <div className="page-shell-content">
                    {identity !== undefined && <div className="page-shell-identity">{identity}</div>}
                    {children}
                </div>
                {!narrow && contents === "list" && <TableOfContents />}
            </div>
        </PageSectionsProvider>
    )
}
