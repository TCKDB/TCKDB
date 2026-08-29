import { Link } from "react-router-dom"
import { lotLabel } from "../api/scientificSchemas"

const statusLabel = (status: string) => status.replaceAll("_", " ")

/**
 * The `source_calculations` row shape is structurally identical between
 * `StatmechSourceCalculationSummary` (`api/statmechApi.ts`) and
 * `TransportSourceCalculationSummary` (`api/transportApi.ts`) — both are
 * `role, calculation_ref, calculation_type, quality, created_at, review,
 * level_of_theory, software_release, workflow_tool_release`, read off the
 * same-shaped backend summary. One shared table renders both rather than
 * two byte-identical copies drifting apart.
 */
export interface SourceCalculationRow {
    role: string
    calculation_ref: string
    calculation_type: string
    review: { status: string }
    level_of_theory?: { method: string; basis?: string | null; display?: string } | null
}

/**
 * Extracted into its own module (not an inline render-prop body) so it can
 * be `vi.mock`'d — see `EntryStatmechSection.errorBoundary.test.tsx` /
 * `EntryTransportSection.errorBoundary.test.tsx`, which mock this exact
 * export to prove the real `StatmechLazySection`/`TransportLazySection`
 * wiring isolates a throwing row, the same way
 * `GeometryDetailPage.errorBoundary.test.tsx` mocks `GeometryViewer`. An
 * inline arrow function passed as a `children` render prop cannot be
 * targeted by `vi.mock` — only a named export of its own module can.
 */
export function SourceCalculationsTable({ rows }: { rows: SourceCalculationRow[] | null | undefined }) {
    if (!rows || rows.length === 0) {
        return <p className="empty-projection">The archive returned no source-calculation rows.</p>
    }
    return (
        <div className="table-scroll table-scroll--compact">
            <table className="stage-table" aria-label="Source calculations">
                <thead>
                    <tr>
                        <th scope="col">Role</th>
                        <th scope="col">Calculation</th>
                        <th scope="col">Type</th>
                        <th scope="col">Level of theory</th>
                        <th scope="col">Review</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, index) => (
                        <tr key={`source-${row.calculation_ref}-${index}`}>
                            <td data-label="Role">{statusLabel(row.role)}</td>
                            <td data-label="Calculation"><Link to={`/calculations/${row.calculation_ref}`}>{row.calculation_ref}</Link></td>
                            <td data-label="Type">{statusLabel(row.calculation_type)}</td>
                            <td data-label="Level of theory">{row.level_of_theory ? lotLabel(row.level_of_theory) : "Not recorded"}</td>
                            <td data-label="Review">{statusLabel(row.review.status)}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
