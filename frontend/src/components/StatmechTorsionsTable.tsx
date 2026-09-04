import { Link } from "react-router-dom"
import type { StatmechRecord } from "../api/statmechApi"

const statusLabel = (status: string) => status.replaceAll("_", " ")

/**
 * Extracted into its own module (not an inline render-prop body) so it can
 * be `vi.mock`'d — see `EntryStatmechSection.errorBoundary.test.tsx`, which
 * mocks this exact export to prove the real `StatmechLazySection` /
 * `LazyRowBody` wiring isolates a throwing row, the same way
 * `GeometryDetailPage.errorBoundary.test.tsx` mocks `GeometryViewer`. An
 * inline arrow function passed as a `children` render prop cannot be
 * targeted by `vi.mock` — only a named export of its own module can.
 *
 * `invalidated_reason` is its own column, not folded into "Treatment" or
 * dropped — a torsion the archive has marked invalid (a scan that did not
 * close, e.g.) must never look identical to a sound one. `top_description`
 * is rendered for the same reason: both are parsed by `api/statmechApi.ts`
 * and were previously silently dropped from this table.
 */
export function TorsionsTable({ rows }: { rows: StatmechRecord["torsions"] }) {
    if (!rows || rows.length === 0) {
        return <p className="empty-projection">The archive returned no torsion rows.</p>
    }
    return (
        <div className="table-scroll">
            <table className="data-table" aria-label="Torsions">
                <thead>
                    <tr>
                        <th scope="col">Index</th>
                        <th scope="col">Treatment</th>
                        <th scope="col">Top</th>
                        <th scope="col">Dimension</th>
                        <th scope="col">Symmetry number</th>
                        <th scope="col">Source scan</th>
                        <th scope="col">Invalidated</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row) => (
                        <tr key={`torsion-${row.torsion_index}`}>
                            <td className="num" data-label="Index">{row.torsion_index}</td>
                            <td data-label="Treatment">{row.treatment_kind ? statusLabel(row.treatment_kind) : "not recorded"}</td>
                            <td data-label="Top">{row.top_description ?? "not recorded"}</td>
                            <td className="num" data-label="Dimension">{row.dimension}</td>
                            <td className="num" data-label="Symmetry number">{row.symmetry_number ?? "not recorded"}</td>
                            <td data-label="Source scan">
                                {row.source_scan_calculation_ref
                                    ? <Link className="data" to={`/calculations/${row.source_scan_calculation_ref}`}>{row.source_scan_calculation_ref}</Link>
                                    : "not recorded"}
                            </td>
                            <td data-label="Invalidated">{row.invalidated_reason ?? "not invalidated"}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
