import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import "../record-blocks.css"

export type Crumb = { label: ReactNode; to?: string }

/** The record's real parents, outermost first; the last crumb is the page itself. */
export function Breadcrumbs({ crumbs }: { crumbs: readonly Crumb[] }) {
    if (crumbs.length === 0) return null
    const last = crumbs.length - 1
    return (
        <nav className="breadcrumbs" aria-label="Breadcrumb">
            <ol>
                {crumbs.map((crumb, index) => (
                    <li key={index}>
                        {index === last || !crumb.to
                            ? <span aria-current={index === last ? "page" : undefined}>{crumb.label}</span>
                            : <Link to={crumb.to}>{crumb.label}</Link>}
                    </li>
                ))}
            </ol>
        </nav>
    )
}
