import { useState, type ReactNode } from "react"
import "../page-shell.css"
import { PageSectionsProvider } from "./PageSections"
import { TableOfContents } from "./TableOfContents"
import { Breadcrumbs, type Crumb } from "./Breadcrumbs"
import { NARROW_QUERY, useMediaQuery } from "../hooks/useMediaQuery"
import { createLiveViewerSlot, LiveViewerSlotContext } from "../hooks/useLiveViewerSlot"

/**
 * The shared shell every page renders its content through, and the one owner of page width: a fixed side column
 * (14rem) left of the main column, one `--page-max`, one padding. The side column always exists on wide screens, so
 * the main column never moves, whether the page is loading, failed or loaded.
 *
 * The side column holds the "On this page" list (`TableOfContents`, which fills once two or more sections are
 * mounted; `contents="none"` leaves it out) and then `side`, an optional block such as the relationship map or a
 * list page's filters.
 *
 * `crumbs` is the trail of real parents; `identity` is the page's header block (formula, SMILES, reference).
 * Both render first inside `.page-shell-content`.
 *
 * Wide screens: the side column follows the content in the markup and is placed on the left by the grid, so the
 * skip link lands on the content, not on a dozen contents links.
 * Narrow screens: the single "Contents" disclosure comes first and `side` follows the content.
 * The shell also owns the page's one live 3D viewer slot (`ViewerStage`).
 */
export function PageShell({ crumbs, identity, children, contents = "list", side }: {
    crumbs?: readonly Crumb[]
    identity?: ReactNode
    children: ReactNode
    contents?: "list" | "none"
    side?: ReactNode
}) {
    const narrow = useMediaQuery(NARROW_QUERY)
    const [viewerSlot] = useState(createLiveViewerSlot)
    return (
        <LiveViewerSlotContext.Provider value={viewerSlot}>
            <PageSectionsProvider>
                <div className="page-shell">
                    <div className="page-shell-layout">
                        {narrow && contents === "list" && <TableOfContents />}
                        <div className="page-shell-content">
                            {(crumbs !== undefined || identity !== undefined) && (
                                <div className="page-shell-identity">
                                    {crumbs !== undefined && <Breadcrumbs crumbs={crumbs} />}
                                    {identity}
                                </div>
                            )}
                            {children}
                        </div>
                        {(!narrow || side !== undefined) && (
                            <aside className="page-shell-side">
                                {!narrow && contents === "list" && <TableOfContents />}
                                {side}
                            </aside>
                        )}
                    </div>
                </div>
            </PageSectionsProvider>
        </LiveViewerSlotContext.Provider>
    )
}
