import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../geometry-detail.css"
import { GeometryViewer } from "../components/GeometryViewer"
import { RecordStatus } from "../components/RecordStatus"
import type { GeometryProvenanceCalcLink, GeometryRecord } from "../api/geometryApi"
import { useGeometry } from "../hooks/useGeometry"

// ---------------------------------------------------------------------------
// This page has no on-demand ("behind a disclosure") sections — see the
// shape notes atop `api/geometryApi.ts`. Every field on this endpoint is
// returned unconditionally in one request, so the only availability
// distinction that can ever fire is the same eager-section triple this
// project already uses on `CalculationDetailPage` /
// `ConformerObservationPage`: absence describes the request (a field
// silently dropped from a future response), null/[] describes genuinely
// empty data. There is no fourth "idle, not yet requested by the reader"
// state here, because nothing on this page is reader-gated.
// ---------------------------------------------------------------------------
type SectionAvailability = "not-requested" | "empty" | "populated"

function sectionAvailability<T>(value: T[] | null | undefined): SectionAvailability {
    if (value === undefined) return "not-requested"
    if (value === null || value.length === 0) return "empty"
    return "populated"
}

const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "Not recorded")

const CALC_TYPE_LABELS: Record<string, string> = {
    opt: "Optimisation",
    freq: "Frequency",
    sp: "Single-point",
    irc: "IRC",
    scan: "Scan",
    path_search: "Path search",
    conf: "Conformer",
}
const typeLabel = (type: string) => CALC_TYPE_LABELS[type] ?? type.replaceAll("_", " ")

/**
 * A simple Hill-order formula string derived only from the atom elements
 * already shown on this page (carbon first, hydrogen second, everything
 * else alphabetical) — a display convenience computed from data the reader
 * can already see in the coordinate table, not an inferred relationship.
 */
function hillFormula(elements: string[]): string {
    if (elements.length === 0) return ""
    const counts = new Map<string, number>()
    for (const symbol of elements) counts.set(symbol, (counts.get(symbol) ?? 0) + 1)
    const order = [...counts.keys()].sort((a, b) => {
        if (a === "C" || a === "H") return b === "C" || b === "H" ? (a === "C" ? -1 : b === "C" ? 1 : 0) : -1
        if (b === "C" || b === "H") return 1
        return a.localeCompare(b)
    })
    return order.map((el) => `${el}${(counts.get(el) ?? 0) > 1 ? counts.get(el) : ""}`).join("")
}

export default function GeometryDetailPage() {
    const { geometryRef = "" } = useParams<{ geometryRef: string }>()
    const state = useGeometry(geometryRef)

    if (state.status === "ready") {
        return <GeometryDetail key={state.record.geometry_ref} geometry={state.record} />
    }
    return (
        <RecordStatus
            state={state}
            ref={geometryRef}
            kind="geometry"
            loadingDetail="Retrieving the coordinate payload and its producer/consumer provenance."
        />
    )
}

function GeometryDetail({ geometry }: { geometry: GeometryRecord }) {
    const atomsAvailability = sectionAvailability(geometry.atoms)
    const atoms = geometry.atoms ?? []
    // `symbols`/`coords` are a parallel-array view of the same rows as
    // `atoms` (see the shape notes in api/geometryApi.ts) — this page reads
    // the richer `atoms` shape throughout rather than duplicating both.
    const formula = hillFormula(atoms.map((atom) => atom.element))

    const producedByAvailability = sectionAvailability(geometry.provenance?.produced_by)
    const usedAsInputByAvailability = sectionAvailability(geometry.provenance?.used_as_input_by)
    const producedBy = geometry.provenance?.produced_by ?? []
    const usedAsInputBy = geometry.provenance?.used_as_input_by ?? []

    return (
        <section className="conformer-page geometry-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Geometry</span>
            </nav>

            <header className="basin-header">
                <p className="eyebrow">Geometry · deposited evidence</p>
                <div className="basin-title">
                    <h1>{formula ? `${formula} geometry` : "Geometry"}</h1>
                </div>
                <p className="basin-intro">
                    One stored set of atomic coordinates: the exact positions a calculation consumed or produced.
                    This is not a species or a calculation — the same coordinates can be reused across more than
                    one calculation, in either direction.
                </p>
                <dl className="basin-context">
                    <div><dt>Geometry ref</dt><dd>{geometry.geometry_ref}</dd></div>
                    <div><dt>Atom count</dt><dd>{geometry.natoms}</dd></div>
                    <div><dt>Geometry hash</dt><dd>{geometry.geom_hash}</dd></div>
                    <div><dt>Format</dt><dd>{geometry.format}</dd></div>
                    <div><dt>Coordinate units</dt><dd>{geometry.coordinate_units}</dd></div>
                    <div><dt>Deposited</dt><dd>{isoDate(geometry.created_at)}</dd></div>
                </dl>
            </header>

            <section className="ledger-summary" aria-label="Geometry provenance summary">
                <Metric label="Producing calculations" value={producedBy.length} />
                <Metric label="Consuming calculations" value={usedAsInputBy.length} />
                <div className="coverage-card">
                    <span>Validation</span>
                    <strong>Not recorded on this endpoint</strong>
                    <p>
                        This geometry record carries no validation field of its own. A geometry-vs-formula check,
                        where one was recorded, lives on the calculation that produced or consumed it — see
                        "Geometry validation" on a linked calculation below, not here.
                    </p>
                </div>
            </section>

            <ViewerSection atoms={atoms} atomsAvailability={atomsAvailability} formula={formula} />

            <CoordinateTableSection
                atoms={atoms}
                atomsAvailability={atomsAvailability}
                geometryRef={geometry.geometry_ref}
            />

            <RawXyzSection xyzText={geometry.xyz_text ?? null} />

            <ProvenanceSection
                title="Produced by"
                description="Calculations that emitted this geometry as an output. A role, where recorded, describes which output slot on that calculation this geometry filled."
                links={producedBy}
                availability={producedByAvailability}
                showRole
                emptyText="No calculation is recorded as having produced this geometry."
            />

            <ProvenanceSection
                title="Used as input by"
                description="Calculations that consumed this geometry as an input. This list is never merged with, summed with, or inferred from the producer list above — the same geometry can appear in both, and a calculation can appear in both lists at once."
                links={usedAsInputBy}
                availability={usedAsInputByAvailability}
                showRole={false}
                emptyText="No calculation is recorded as having consumed this geometry as input."
            />
        </section>
    )
}

function Metric({ label, value }: { label: string; value: number }) {
    return (
        <div className="metric">
            <span>{label}</span>
            <strong>{value}</strong>
        </div>
    )
}

function SectionEmptyMessage({ availability, emptyText }: { availability: SectionAvailability; emptyText: string }) {
    if (availability === "not-requested") {
        return <p className="empty-projection">This section was not requested for this view.</p>
    }
    return <p className="empty-projection">{emptyText}</p>
}

function ViewerSection({ atoms, atomsAvailability, formula }: {
    atoms: GeometryRecord["atoms"]
    atomsAvailability: SectionAvailability
    formula: string
}) {
    const rows = atoms ?? []
    return (
        <section className="ledger-section" aria-labelledby="viewer-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Structure</p>
                <h2 id="viewer-heading">Structure projection</h2>
            </div>
            {atomsAvailability === "populated" ? (
                <GeometryViewer atoms={rows} formula={formula} />
            ) : (
                <SectionEmptyMessage
                    availability={atomsAvailability}
                    emptyText="No atom rows are recorded for this geometry, so no projection can be drawn."
                />
            )}
        </section>
    )
}

function CoordinateTableSection({ atoms, atomsAvailability, geometryRef }: {
    atoms: NonNullable<GeometryRecord["atoms"]>
    atomsAvailability: SectionAvailability
    geometryRef: string
}) {
    return (
        <section className="ledger-section" aria-labelledby="coordinates-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Deposited evidence</p>
                <h2 id="coordinates-heading">Coordinate table</h2>
                <p>
                    Every atom in this geometry, in the order the archive returned them. This table is the
                    accessible, selectable fallback for the projection above — it renders whether or not that
                    projection does.
                </p>
            </div>
            {atomsAvailability === "populated" ? (
                <div className="table-scroll">
                    <table className="stage-table" aria-label={`Coordinates for ${geometryRef}`}>
                        <thead>
                            <tr>
                                <th scope="col">Atom</th>
                                <th scope="col">Element</th>
                                <th scope="col">x</th>
                                <th scope="col">y</th>
                                <th scope="col">z</th>
                            </tr>
                        </thead>
                        <tbody>
                            {atoms.map((atom) => (
                                <tr key={atom.atom_index}>
                                    <td data-label="Atom">{atom.atom_index}</td>
                                    <td data-label="Element">{atom.element}</td>
                                    <td data-label="x">{atom.x}</td>
                                    <td data-label="y">{atom.y}</td>
                                    <td data-label="z">{atom.z}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <SectionEmptyMessage
                    availability={atomsAvailability}
                    emptyText="No coordinate rows are recorded for this geometry."
                />
            )}
        </section>
    )
}

function RawXyzSection({ xyzText }: { xyzText: string | null }) {
    return (
        <section className="ledger-section" aria-labelledby="xyz-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Raw</p>
                <h2 id="xyz-heading">Raw XYZ</h2>
                <p>The archive's own XYZ-format text block for this geometry, selectable as deposited.</p>
            </div>
            {xyzText ? (
                <pre className="xyz-block"><code>{xyzText}</code></pre>
            ) : (
                <p className="empty-projection">No raw XYZ text is recorded for this geometry.</p>
            )}
        </section>
    )
}

function ProvenanceSection({ title, description, links, availability, showRole, emptyText }: {
    title: string
    description: string
    links: GeometryProvenanceCalcLink[]
    availability: SectionAvailability
    showRole: boolean
    emptyText: string
}) {
    const headingId = `provenance-${title.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`
    return (
        <section className="ledger-section" aria-labelledby={headingId}>
            <div className="ledger-heading">
                <p className="eyebrow">Deposited provenance</p>
                <h2 id={headingId}>{title}</h2>
                <p>{description}</p>
            </div>
            {availability === "populated" ? (
                <table className="stage-table" aria-label={`${title} for this geometry`}>
                    <thead>
                        <tr>
                            <th scope="col">Calculation</th>
                            <th scope="col">Type</th>
                            {showRole && <th scope="col">Role</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {links.map((link, index) => (
                            <tr key={`${title}-${link.calculation_ref}-${index}`}>
                                <td data-label="Calculation">
                                    <Link to={`/calculations/${link.calculation_ref}`}>{link.calculation_ref}</Link>
                                </td>
                                <td data-label="Type">{typeLabel(link.calculation_type)}</td>
                                {showRole && <td data-label="Role">{link.role ? statusLabel(link.role) : "Not recorded"}</td>}
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : (
                <SectionEmptyMessage availability={availability} emptyText={emptyText} />
            )}
        </section>
    )
}
