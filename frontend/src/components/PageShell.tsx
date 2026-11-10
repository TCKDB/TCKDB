import type { ReactNode } from "react"
import "../page-shell.css"
import { PageSectionsProvider } from "./PageSections"
import { TableOfContents } from "./TableOfContents"

/**
 * The shared shell every record page renders its content through: a left
 * "On this page" column (`TableOfContents`) whose column is always reserved
 * and whose list appears once two or more sections are mounted
 * (`PageSections.tsx`). Page width stays in each page's own stylesheet.
 *
 * `identity` is the page's header block (formula, SMILES, reference). It
 * renders first inside `.page-shell-content`, level with the contents column.
 * The contents column comes first in the markup, so it is the first stop
 * after the page's breadcrumbs, and on phones it sits above the content.
 */
export function PageShell({ identity, children }: { identity?: ReactNode; children: ReactNode }) {
    return (
        <PageSectionsProvider>
            <div className="page-shell-layout">
                <TableOfContents />
                <div className="page-shell-content">
                    {identity !== undefined && <div className="page-shell-identity">{identity}</div>}
                    {children}
                </div>
            </div>
        </PageSectionsProvider>
    )
}
