import type { ReactNode } from "react"
import "../page-shell.css"
import { PageSectionsProvider } from "./PageSections"
import { TableOfContents } from "./TableOfContents"

/**
 * The shared shell every record page (species overview, species entry,
 * geometry/calculation/conformer-group/conformer-observation detail,
 * browse) renders its content through: a side table of contents that
 * appears the moment 4+ sections are mounted beneath it
 * (`TableOfContents`/`PageSections.tsx`), on any page, without either
 * component needing to know which page it is on.
 *
 * Page WIDTH is not this component's job -- each page keeps its own root
 * class and stylesheet (`.species-overview`, `.entry-page`, `.calc-page`,
 * `.conformer-page`, `.geometry-page`, `.browse-page`), all standardised
 * to the same `max-width: 100rem` the species entry page was already
 * widened to. `PageShell` only lays out what is INSIDE that width: the
 * ToC rail beside the content column.
 *
 * `identity` is a reserved, NAMED slot for the upcoming identity header
 * (species formula/SMILES/InChI + submission reference -- PR #321, still
 * in CI as of this shell). Every call site in this PR omits the prop, so
 * nothing renders in the slot at all -- not even an empty wrapper div --
 * rather than reserve visual space for content that does not exist yet.
 * It exists so that follow-up drops its header in without restructuring
 * this shell a second time.
 */
export function PageShell({ identity, children }: { identity?: ReactNode; children: ReactNode }) {
    return (
        <PageSectionsProvider>
            {identity !== undefined && <div className="page-shell-identity">{identity}</div>}
            <div className="page-shell-layout">
                <TableOfContents />
                <div className="page-shell-content">{children}</div>
            </div>
        </PageSectionsProvider>
    )
}
