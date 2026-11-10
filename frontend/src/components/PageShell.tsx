import type { ReactNode } from "react"
import "../page-shell.css"
import { PageSectionsProvider } from "./PageSections"
import { TableOfContents } from "./TableOfContents"
import { NARROW_QUERY, useMediaQuery } from "../hooks/useMediaQuery"

/**
 * The shared shell every record page renders its content through: an "On this page" column
 * (`TableOfContents`) whose list appears once two or more sections are mounted (`PageSections.tsx`).
 * `contents="auto"` (default) holds the column while sections register and removes it if none ever
 * appear; `contents="none"` is for pages that have no sections and renders no column at all.
 * Page width stays in each page's own stylesheet.
 *
 * `identity` is the page's header block (formula, SMILES, reference). It renders first inside
 * `.page-shell-content`.
 *
 * Wide screens: the contents column follows the content in the markup and is placed on the left
 * with CSS `order`, so the skip link lands on the content, not on a dozen contents links.
 * Narrow screens: the single "Contents" disclosure comes first.
 */
export function PageShell({ identity, children, contents = "auto" }: {
    identity?: ReactNode
    children: ReactNode
    contents?: "auto" | "none"
}) {
    const narrow = useMediaQuery(NARROW_QUERY)
    return (
        <PageSectionsProvider>
            <div className="page-shell-layout">
                {narrow && contents === "auto" && <TableOfContents />}
                <div className="page-shell-content">
                    {identity !== undefined && <div className="page-shell-identity">{identity}</div>}
                    {children}
                </div>
                {!narrow && contents === "auto" && <TableOfContents />}
            </div>
        </PageSectionsProvider>
    )
}
