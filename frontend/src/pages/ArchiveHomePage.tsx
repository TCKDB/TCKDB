import { Link } from "react-router-dom"
import { IdentifierSearch } from "../components/IdentifierSearch"
import { StructureSearch } from "../components/StructureSearch"

const destinations = [
    ["Browse species", "Find stable species and species-entry records.", "/species", "⌬"],
    ["Browse reactions", "Follow reaction records and their scientific context.", "/reactions", "⇄"],
    ["Methods", "Read the computational methods attached to records.", "/methods", "▤"],
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
        <StructureSearch />
        <section className="destination-grid" aria-label="Archive destinations">
            {destinations.map(([title, detail, to, glyph]) => <Link className="destination" key={to} to={to}>
                <span className="destination-icon" aria-hidden="true">{glyph}</span><h2>{title}</h2><p>{detail}</p><span>Open index →</span>
            </Link>)}
        </section>
    </>
}

export default ArchiveHomePage
