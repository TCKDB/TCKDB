import { Link } from "react-router-dom"
import "../home.css"
import { BROWSE_KIND_PATHS } from "../api/browseApi"
import { HomeExample } from "../components/HomeExample"
import { IdentifierSearch } from "../components/IdentifierSearch"
import { RecordGlyph, type RecordKind } from "../components/RecordGlyph"
import { ReviewChip } from "../components/ReviewChip"
import { Term } from "../components/Term"
import { siteConfig } from "../config/siteConfig"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import { useHomeCounts, type HomeCounts } from "../hooks/useHomeCounts"

/** Each runs the exact-identifier search; an equation is written the way that search reads one. */
const TRY_EXAMPLES: { label: string; query: string }[] = [
    { label: "CH4", query: "CH4" },
    { label: "[OH]", query: "[OH]" },
    { label: "O=O", query: "O=O" },
    { label: "[CH3] + [OH] → CO", query: "[CH3],[OH] <> CO" },
]

const BROWSE_CARDS: { title: string; detail: string; to: string; glyph: RecordKind; count: keyof HomeCounts; unit: string }[] = [
    { title: "Molecules", detail: "Shapes, energies, vibrations and thermochemistry, each at one or more levels of theory.", to: BROWSE_KIND_PATHS.species, glyph: "species", count: "molecules", unit: "molecules" },
    { title: "Reactions", detail: "Transition states, barriers and rate coefficients for each reaction.", to: BROWSE_KIND_PATHS.reaction, glyph: "reaction", count: "reactions", unit: "reactions" },
    { title: "Methods", detail: "The levels of theory behind the values. Listed in no particular order of quality.", to: "/methods", glyph: "calculation", count: "levels", unit: "levels of theory" },
]

const COUNT_CELLS: { count: keyof HomeCounts; label: string }[] = [
    { count: "molecules", label: "Molecules" },
    { count: "reactions", label: "Reactions" },
    { count: "levels", label: "Levels of theory" },
]

function formatCount(value: number): string {
    return value.toLocaleString("en-US")
}

function ArchiveHomePage() {
    useDocumentTitle("Home")
    const counts = useHomeCounts()
    const available = COUNT_CELLS.filter((cell) => counts[cell.count] !== null)
    return <div className="home">
        <section className="home-hero" aria-labelledby="home-title">
            <h1 id="home-title">Computed thermochemistry and kinetics</h1>
            <p className="home-lede">
                Each value for a molecule or reaction shows the calculation it came from, its <Term term="level-of-theory">level of theory</Term> and its review status.
            </p>
            <IdentifierSearch />
            <div className="home-try">
                <span>Try</span>
                <ul>
                    {TRY_EXAMPLES.map((example) => (
                        <li key={example.query}>
                            <Link to={`/?q=${encodeURIComponent(example.query)}`}><code className="t-preserve-case">{example.label}</code></Link>
                        </li>
                    ))}
                </ul>
            </div>
        </section>

        {available.length > 0 && (
            <dl className="home-counts" aria-label="What the archive holds">
                {available.map((cell) => (
                    <div key={cell.count}>
                        <dt>{cell.label}</dt>
                        <dd>{formatCount(counts[cell.count] as number)}</dd>
                    </div>
                ))}
            </dl>
        )}

        <section className="home-section" aria-labelledby="home-browse">
            <h2 id="home-browse" className="home-section-title">Browse the database</h2>
            <div className="home-cards">
                {BROWSE_CARDS.map((card) => (
                    <div className="card home-card" key={card.to}>
                        <RecordGlyph kind={card.glyph} size={22} />
                        <h3><Link to={card.to}>{card.title}</Link></h3>
                        <p>{card.detail}</p>
                        {counts[card.count] !== null && <p className="home-card-count"><b>{formatCount(counts[card.count] as number)}</b> {card.unit}</p>}
                    </div>
                ))}
            </div>
        </section>

        <section className="home-section home-read" aria-labelledby="home-read">
            <h2 id="home-read" className="home-section-title">How to read a value</h2>
            <div className="home-read-body">
                <ol className="home-read-steps">
                    <li><span className="home-callout" aria-hidden="true">1</span> The number, its uncertainty and its unit. Every value on TCKDB is computed, not measured.</li>
                    <li><span className="home-callout" aria-hidden="true">2</span> The calculation it came from. Follow the link to its input, output files and settings.</li>
                    <li><span className="home-callout" aria-hidden="true">3</span> The level of theory: the method and basis set the calculation used. The same molecule can have values at several.</li>
                    <li><span className="home-callout" aria-hidden="true">4</span> The review status. “Not reviewed” means no curator has checked the value yet, not that it is wrong.</li>
                </ol>
                <figure className="home-read-sample">
                    <figcaption>Illustration with placeholder wording. It is not data.</figcaption>
                    <p className="home-read-quantity">Enthalpy of formation</p>
                    <p className="home-read-line"><span className="home-callout" aria-hidden="true">1</span> <span className="t-preserve-case">value ± uncertainty unit</span></p>
                    <p className="home-read-line"><span className="home-callout" aria-hidden="true">2</span> Source calculation <code className="t-preserve-case">calc_…</code></p>
                    <p className="home-read-line"><span className="home-callout" aria-hidden="true">3</span> Level of theory: method and basis set</p>
                    <p className="home-read-line"><span className="home-callout" aria-hidden="true">4</span> <ReviewChip status="not_reviewed" explain={false} /></p>
                </figure>
            </div>
        </section>

        <section className="home-section" aria-labelledby="home-example">
            <h2 id="home-example" className="home-section-title">Example record</h2>
            <HomeExample example={siteConfig.homeExample} />
        </section>
    </div>
}

export default ArchiveHomePage
