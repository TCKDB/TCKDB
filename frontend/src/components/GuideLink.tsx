import { Link } from "react-router-dom"
import { GLOSSARY, GUIDE_TOPICS, type GlossaryKey } from "../domain/glossary"
import "../guide-link.css"

/** The name of a guide entry: a topic's term, else a concept's, else the anchor itself. */
function guideEntryName(id: string): string {
    return GUIDE_TOPICS.find((topic) => topic.id === id)?.term ?? GLOSSARY[id as GlossaryKey]?.term ?? id
}

/** A short link to the guide entry for a concept the page no longer explains; `to` is the entry's anchor on /guide. */
export function GuideLink({ to, children = "What is this?" }: { to: string; children?: string }) {
    return (
        <span className="note guide-link"> <Link to={`/guide#${to}`}>{children}{" "}<span className="guide-link-for">({guideEntryName(to).toLowerCase()})</span></Link></span>
    )
}
