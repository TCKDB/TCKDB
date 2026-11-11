import { Link } from "react-router-dom"

/** A short link to the guide entry for a concept the page no longer explains; `to` is the entry's anchor on /guide. */
export function GuideLink({ to, children = "What is this?" }: { to: string; children?: string }) {
    return <span className="note guide-link"> <Link to={`/guide#${to}`}>{children}</Link></span>
}
