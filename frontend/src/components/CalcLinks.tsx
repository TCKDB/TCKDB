import { RecordLink } from "./RecordLink"

/** Calculations as comma-separated links to their pages, numbered when there is more than one. */
export function CalcLinks({ refs }: { refs: readonly string[] }) {
    return (
        <>
            {refs.map((ref, index) => (
                <span key={ref}>{index > 0 && ", "}<RecordLink kind="calculation" to={`/calculations/${ref}`} label={refs.length > 1 ? `Calculation ${index + 1}` : "Calculation"} /></span>
            ))}
        </>
    )
}
