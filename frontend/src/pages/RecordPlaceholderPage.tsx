import { useParams } from "react-router-dom"

export type RecordRefParam = "speciesRef" | "entryRef" | "groupRef" | "observationRef" | "calculationRef" | "geometryRef" | "reactionRef"
type RecordRouteParam = RecordRefParam | "section"

export default function RecordPlaceholderPage({ kind, refParam }: { kind: string; refParam?: RecordRefParam }) {
    const params = useParams<RecordRouteParam>()
    const ref = refParam ? params[refParam] : undefined
    return <section className="record-placeholder"><p className="eyebrow">Archive record</p><h1>{kind}</h1>
        {ref && <code>{ref}</code>}{params.section && <p>Section: <code>{params.section}</code></p>}<p>This public record view is being prepared. It will show source-linked scientific data without inventing a result before the record projection is ready.</p></section>
}
