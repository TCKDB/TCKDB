import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import "../home.css"
import { loadReactionEntry } from "../api/reactionEntryApi"
import { loadSpeciesEntry } from "../api/speciesEntryApi"
import type { HomeExample as HomeExampleRef } from "../config/siteConfig"
import { useLoaded } from "../hooks/useLoaded"
import { EntryNumbers } from "./EntryNumbers"
import { SpeciesFace } from "./Formula"
import { ReactionEquation } from "./ReactionEquation"
import { RecordGlyph } from "./RecordGlyph"

const BROWSE_MOLECULES = <p className="home-example-browse"><Link to="/species">Browse molecules</Link></p>

/**
 * The example record the site was built with. It shows only a record the archive returns under its
 * current visibility; unset, not found, hidden or unreadable, a link to the molecule index stands in.
 */
export function HomeExample({ example }: { example: HomeExampleRef | null }) {
    if (!example) return BROWSE_MOLECULES
    return example.kind === "molecule" ? <MoleculeExample entryRef={example.ref} /> : <ReactionExample entryRef={example.ref} />
}

function Frame({ copy, children }: { copy: boolean; children?: ReactNode }) {
    if (!copy) return BROWSE_MOLECULES
    return (
        <>
            <p className="home-example-copy">An example record, chosen by the site editors to show how a record reads. It is not a recommendation.</p>
            {children}
        </>
    )
}

function MoleculeExample({ entryRef }: { entryRef: string }) {
    const state = useLoaded(loadSpeciesEntry, entryRef)
    if (state.status === "loading" || state.status === "idle") return <p className="note" role="status" aria-busy="true">Loading the example record…</p>
    if (state.status !== "ready" || state.data === null) return <Frame copy={false} />
    const entry = state.data
    const path = `/species-entries/${entryRef}`
    return (
        <Frame copy>
            <article className="card home-example-card" aria-label="Example record">
                <header className="home-example-head">
                    <RecordGlyph kind="species-entry" size={18} />
                    <span className="home-example-name"><SpeciesFace smiles={entry.canonicalSmiles} formula={entry.formula} /></span>
                    <code className="t-preserve-case home-example-ref">{entryRef}</code>
                    <Link className="home-example-open" to={path}>Open this molecule</Link>
                </header>
                <EntryNumbers entryRef={entryRef} entryPath={path} conformerQuery="" conformers={[]} compact />
            </article>
        </Frame>
    )
}

function ReactionExample({ entryRef }: { entryRef: string }) {
    const state = useLoaded(loadReactionEntry, entryRef)
    if (state.status === "loading" || state.status === "idle") return <p className="note" role="status" aria-busy="true">Loading the example record…</p>
    if (state.status !== "ready") return <Frame copy={false} />
    const entry = state.data.reaction_entry
    const species = state.data.species ?? { reactants: [], products: [] }
    return (
        <Frame copy>
            <article className="card home-example-card" aria-label="Example record">
                <header className="home-example-head">
                    <RecordGlyph kind="reaction" size={18} />
                    <span className="home-example-name">
                        <ReactionEquation reactants={species.reactants} products={species.products} reversible={entry.reversible} />
                    </span>
                    <code className="t-preserve-case home-example-ref">{entry.reaction_entry_ref}</code>
                    <Link className="home-example-open" to={`/reaction-entries/${entry.reaction_entry_ref}`}>Open this reaction</Link>
                </header>
            </article>
        </Frame>
    )
}
