import { useParams } from "react-router-dom"

export default function RecordPlaceholderPage({ kind }: { kind: string }) {
    const params = useParams()
    const ref = Object.values(params).find(Boolean)
    return <section className="record-placeholder"><p className="eyebrow">Archive record</p><h1>{kind}</h1>
        {ref && <code>{ref}</code>}<p>This public record view is being prepared. It will show source-linked scientific data without inventing a result before the record projection is ready.</p></section>
}
