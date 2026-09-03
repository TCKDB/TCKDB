import { Link } from "react-router-dom"
import { IdentifierSearch } from "../components/IdentifierSearch"

// Two of these three cards used to end in the same "Open index →" action
// text as "Browse species", then land on a page reading "This public
// record view is being prepared..." -- the owner's report: two of three
// top-level destinations were presented as equals of a working index when
// neither one is. The action text is now the honest label for what
// clicking through actually finds, so the difference is visible before a
// reader ever leaves this page, not discovered after. Still real links
// (never removed, per the fix instruction) -- "coming soon" is a status,
// not a dead end.
const destinations = [
    ["Browse species", "Find stable species and species-entry records.", "/species", "⌬", "Open index →"],
    ["Browse reactions", "Follow reaction records and their scientific context.", "/reactions", "⇄", "Coming soon"],
    ["Methods", "Read the computational methods attached to records.", "/methods", "▤", "Coming soon"],
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
                    className="destination"
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
