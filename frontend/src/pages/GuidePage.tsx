import { RecordGlyph, type RecordKind } from "../components/RecordGlyph"
import { GLOSSARY, type GlossaryKey } from "../domain/glossary"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import "../guide.css"

// The eight concepts in the order a record builds on the one before it.
const CONCEPTS: { key: GlossaryKey; glyph?: RecordKind; heading?: string }[] = [
    { key: "species", glyph: "species", heading: "Molecule (species)" },
    { key: "species-entry", glyph: "species-entry", heading: "Molecule version (species entry)" },
    { key: "conformer", glyph: "conformer-group" },
    { key: "calculation", glyph: "calculation" },
    { key: "level-of-theory" },
    { key: "reaction", glyph: "reaction" },
    { key: "transition-state", glyph: "transition-state" },
    { key: "network", glyph: "network" },
]

function GuidePage() {
    useDocumentTitle("Guide")
    return <article className="guide-page">
        <h1>Guide</h1>
        <p className="guide-lede">
            TCKDB stores the results of computer calculations on molecules and reactions, and keeps the calculation behind every number.
            These are the eight kinds of record you will meet.
        </p>
        <ol className="guide-concepts">
            {CONCEPTS.map(({ key, glyph, heading }) => (
                <li key={key} id={key}>
                    <h2>
                        {glyph && <RecordGlyph kind={glyph} size={18} />}
                        <dfn>{heading ?? GLOSSARY[key].term}</dfn>
                    </h2>
                    <p>{GLOSSARY[key].definition}</p>
                </li>
            ))}
        </ol>
        <p className="guide-order" id="order">A list's order is never a recommendation.</p>
    </article>
}

export default GuidePage
