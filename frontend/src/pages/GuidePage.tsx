import { RecordGlyph, type RecordKind } from "../components/RecordGlyph"
import { GLOSSARY, GUIDE_TOPICS, type GlossaryKey } from "../domain/glossary"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import "../guide.css"

// The concepts in the order a record builds on the one before it.
const CONCEPTS: { key: GlossaryKey; glyph?: RecordKind; heading?: string }[] = [
    { key: "species", glyph: "species", heading: "Molecule (species)" },
    { key: "species-entry", glyph: "species-entry", heading: "Molecule version (species entry)" },
    { key: "conformer", glyph: "conformer-group" },
    { key: "observation" },
    { key: "calculation", glyph: "calculation" },
    { key: "level-of-theory" },
    { key: "reaction", glyph: "reaction" },
    { key: "reaction-entry" },
    { key: "transition-state", glyph: "transition-state" },
    { key: "network", glyph: "network" },
]

function GuidePage() {
    useDocumentTitle("Guide")
    return (
        <PageShell>
            <article className="guide-page">
                <h1>Guide</h1>
                <p className="guide-lede">
                    TCKDB stores the results of computer calculations on molecules and reactions, and keeps the calculation behind every number.
                    The kinds of record come first, then how to read them.
                </p>
                <ol className="guide-concepts">
                    {CONCEPTS.map(({ key, glyph, heading }) => (
                        <li key={key}>
                            <SectionHeading id={key} label={heading ?? GLOSSARY[key].term}>
                                {glyph && <RecordGlyph kind={glyph} size={18} />}
                                <dfn>{heading ?? GLOSSARY[key].term}</dfn>
                            </SectionHeading>
                            <p>{GLOSSARY[key].definition}</p>
                        </li>
                    ))}
                </ol>
                <ol className="guide-concepts guide-topics">
                    {GUIDE_TOPICS.map((topic) => (
                        <li key={topic.id}>
                            <SectionHeading id={topic.id} label={topic.term}><dfn>{topic.term}</dfn></SectionHeading>
                            <p>{topic.text}</p>
                        </li>
                    ))}
                </ol>
            </article>
        </PageShell>
    )
}

export default GuidePage
