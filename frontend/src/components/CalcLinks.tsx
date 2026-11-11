import { Link } from "react-router-dom"

/** Calculation refs as comma-separated links to their pages. */
export function CalcLinks({ refs }: { refs: readonly string[] }) {
    return (
        <>
            {refs.map((ref, index) => (
                <span key={ref}>{index > 0 && ", "}<Link to={`/calculations/${ref}`}>{ref}</Link></span>
            ))}
        </>
    )
}
