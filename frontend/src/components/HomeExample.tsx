import { Link } from "react-router-dom"
import "../home.css"
import type { HomeExampleState } from "../hooks/useHomeExample"
import { EntryNumbers } from "./EntryNumbers"
import { Formula, SpeciesFace } from "./Formula"
import { ReactionEquation } from "./ReactionEquation"
import { RecordGlyph } from "./RecordGlyph"

function examplePath(state: Extract<HomeExampleState, { ref: string }>): string {
    return state.status === "molecule" ? `/species-entries/${state.ref}` : `/reaction-entries/${state.ref}`
}

/** The line under the search that opens the example record. */
export function SeeExample({ state }: { state: HomeExampleState }) {
    if (state.status !== "molecule" && state.status !== "reaction") return null
    const name = state.status === "molecule" && state.formula
        ? <>: <Formula value={state.formula} /></>
        : state.status === "reaction" ? " reaction" : ""
    return <p className="home-see-example"><Link to={examplePath(state)}>See an example{name} →</Link></p>
}

/**
 * The example record the operator named. Only a record the archive returns under its current visibility is shown;
 * when there is none, a link to the molecule index stands in.
 */
export function HomeExample({ state }: { state: HomeExampleState }) {
    if (state.status === "loading") return null
    if (state.status === "none") {
        return <section className="home-section" aria-label="Browse molecules"><p className="home-example-browse"><Link to="/species">Browse molecules</Link></p></section>
    }
    const path = examplePath(state)
    return (
        <section className="home-section" aria-labelledby="home-example">
            <h2 id="home-example" className="home-section-title">Example record</h2>
            <p className="home-example-copy">Chosen by the editors as an example. It is not a recommendation.</p>
            <article className="card home-example-card" aria-label="Example record">
                <header className="home-example-head">
                    <RecordGlyph kind={state.status === "molecule" ? "species-entry" : "reaction"} size={18} />
                    <span className="home-example-name">
                        {state.status === "molecule"
                            ? <SpeciesFace smiles={state.smiles} formula={state.formula} />
                            : <ReactionEquation reactants={state.record.species?.reactants ?? []} products={state.record.species?.products ?? []} reversible={state.record.reaction_entry.reversible} />}
                    </span>
                    <Link className="home-example-open" to={path}>{state.status === "molecule" ? "Open this molecule" : "Open this reaction"}</Link>
                </header>
                {state.status === "molecule" && <EntryNumbers entryRef={state.ref} entryPath={path} conformerQuery="" conformers={[]} compact />}
            </article>
        </section>
    )
}
