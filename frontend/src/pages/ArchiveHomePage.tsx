import { Link } from "react-router-dom"
import { IdentifierSearch } from "../components/IdentifierSearch"

// Each card's action text is the honest label for what clicking through
// actually finds, so the difference between a working index and a
// placeholder is visible before a reader ever leaves this page, not
// discovered after. "Browse reactions" earned its "Open index →" label the
// same way "Browse species" already had it: `/reactions` now renders the
// real reaction browse (per-kind browse paths change), not the
// `RecordPlaceholderPage` it used to. "Methods" earns the same label now,
// for the same reason: `/methods` renders the real methods index
// (`MethodsIndexPage`, methods-surface plan §4.1), not the placeholder it
// used to -- the third and last card this rule applied to.
const destinations = [
    ["Browse species", "Find stable species and species-entry records.", "/species", "⌬", "Open index →"],
    ["Browse reactions", "Follow reaction records and their scientific context.", "/reactions", "⇄", "Open index →"],
    ["Methods", "Read the computational methods attached to records.", "/methods", "▤", "Open index →"],
] as const

function ArchiveHomePage() {
    return <>
        <section className="archive-hero">
            <p className="eyebrow">Theoretical Chemical Kinetics Database</p>
            <h1>TCKDB</h1>
            <p className="tagline">A public archive for traceable <em>quantum-chemical</em> and experimental records in chemical kinetics.</p>
            <div className="accession-rail" aria-hidden="true"><span>species</span><i /><span>entry</span><i /><span>record</span></div>
            <IdentifierSearch />
        </section>
        <section className="destination-grid" aria-label="Archive destinations">
            {destinations.map(([title, detail, to, glyph, action]) => (
                <Link
                    className="destination card"
                    key={to}
                    to={to}
                >
                    <span className="destination-icon" aria-hidden="true">{glyph}</span><h2>{title}</h2><p>{detail}</p><span>{action}</span>
                </Link>
            ))}
        </section>
    </>
}

export default ArchiveHomePage
