import type { ReactNode } from "react"
import "../page-shell.css"
import { PageSectionsProvider } from "./PageSections"
import { TableOfContents } from "./TableOfContents"
import { NARROW_QUERY, useMediaQuery } from "../hooks/useMediaQuery"

/**
 * The shared shell every record page renders its content through: an "On this page" column
 * (`TableOfContents`) whose column is always reserved and whose list appears once two or more
 * sections are mounted (`PageSections.tsx`). Page width stays in each page's own stylesheet.
 *
 * `identity` is the page's header block (formula, SMILES, reference). It renders first inside
 * `.page-shell-content`.
 *
 * Wide screens: the contents column follows the content in the markup and is placed on the left
 * with CSS `order`, so the skip link lands on the content, not on a dozen contents links.
 * Narrow screens: the single "Contents" disclosure comes first.
 */
export function PageShell({ identity, children }: { identity?: ReactNode; children: ReactNode }) {
    const narrow = useMediaQuery(NARROW_QUERY)
    return (
        <PageSectionsProvider>
            <div className="page-shell-layout">
                {narrow && <TableOfContents />}
                <div className="page-shell-content">
                    {identity !== undefined && <div className="page-shell-identity">{identity}</div>}
                    {children}
                </div>
                {!narrow && <TableOfContents />}
            </div>
        </PageSectionsProvider>
    )
}
