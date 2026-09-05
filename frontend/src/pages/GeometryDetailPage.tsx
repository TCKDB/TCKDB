import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../geometry-detail.css"
import { GeometryViewer } from "../components/GeometryViewer"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { SectionErrorBoundary } from "../components/SectionErrorBoundary"
import { CopyButton } from "../components/RefsDisclosure"
import type { GeometryProvenanceCalcLink, GeometryRecord } from "../api/geometryApi"
import { identityFormula, identityFromGeometry } from "../domain/recordIdentity"
import { useGeometry } from "../hooks/useGeometry"
import {
    ANGSTROM_TO_BOHR,
    angstromToBohr,
    atomicNumberForSymbol,
    type CoordinateUnitMode,
} from "../domain/geometryXyz"

type ElementDisplayMode = "symbol" | "number"

function formatCoordinate(valueAngstrom: number, unit: CoordinateUnitMode): string {
    // Angstrom keeps this page's own pre-existing numeric rendering
    // (`String(valueAngstrom)`, unchanged from before this toggle
    // existed) of the parsed `atoms[].x/y/z` JSON numbers — bohr is the
    // one display-only conversion, rounded to 6dp for a stable column
    // width rather than trailing whatever float noise
    // `* ANGSTROM_TO_BOHR` happens to produce (the coordinate-toggle-note
    // below the table says so).
    //
    // NOT claimed as fidelity to the archive's raw text: `String(-0)` is
    // `"0"` in JS, but the Raw XYZ block renders the archive's own
    // `xyz_text` string verbatim, which for the same atom can read
    // `-0.000000000000` (however the server formatted it). So this
    // ångström column and the Raw XYZ block below can print the same
    // atom's coordinate two different ways on the same page. That is
    // pre-existing (this toggle did not introduce it, and did not fix
    // it) — noted here so this comment does not overclaim a round-trip
    // guarantee the code does not have.
    if (unit === "angstrom") return String(valueAngstrom)
    return angstromToBohr(valueAngstrom).toFixed(6)
}

function elementDisplayValue(symbol: string, mode: ElementDisplayMode): string {
    if (mode === "symbol") return symbol
    const atomicNumber = atomicNumberForSymbol(symbol)
    return atomicNumber === null ? `unknown (${symbol})` : String(atomicNumber)
}

// ---------------------------------------------------------------------------
// This page has no on-demand ("behind a disclosure") sections — see the
// shape notes atop `api/geometryApi.ts`. Every field on this endpoint is
// returned unconditionally in one request, so the internal three-state
// shape below is the same eager-section triple `CalculationDetailPage` /
// `ConformerObservationPage` use, but its *wording* cannot be borrowed
// from them: on those surfaces a token really does gate the field, so an
// absent key can honestly be read as "not requested". Here every field is
// always requested (this page sends `include=provenance` unconditionally,
// see `api/geometryApi.ts`) and no token gates anything at all — an
// absent key can *only* mean the archive dropped the field from its own
// response, never that this client failed to ask for it. Saying "not
// requested" here would blame the request for something the request
// provably did not cause, so the copy below says the archive-side thing
// instead. There is no fourth "idle, not yet requested by the reader"
// state here either, because nothing on this page is reader-gated.
// ---------------------------------------------------------------------------
type SectionAvailability = "absent" | "empty" | "populated"

function sectionAvailability<T>(value: T[] | null | undefined): SectionAvailability {
    if (value === undefined) return "absent"
    if (value === null || value.length === 0) return "empty"
    return "populated"
}

const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

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
 *
 * This is now a FALLBACK only, used when the archive's own `identity`
 * block (#321) is absent or ambiguous — never the primary source when a
 * served formula exists. See `GeometryDetail`'s `displayFormula` /
 * `formulaIsFallback`.
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
    const clientFormula = hillFormula(atoms.map((atom) => atom.element))

    // The archive's own served identity (#321) wins whenever it has a
    // formula to offer. Only when identity is absent/ambiguous (or the
    // owning entry's formula itself failed to parse server-side) does
    // this page fall back to the client-derived Hill formula above — and
    // when it does, it says so, rather than presenting a computed value
    // as if the archive had served it.
    const identity = identityFromGeometry(geometry.identity)
    const servedFormula = identityFormula(identity)
    const displayFormula = servedFormula ?? clientFormula
    const formulaIsFallback = servedFormula === null && clientFormula !== ""

    // Lifted out of `CoordinateTableSection` (where it lived alone until
    // now) so `GeometryViewer`'s atom-picking measurements can follow the
    // same Å/bohr toggle as the coordinate table beside it — see
    // `GeometryViewer.tsx`'s module docstring for the units decision this
    // serves. One piece of state, two controlled consumers below, rather
    // than two independent toggles that could disagree about which unit
    // is "current" for the same page.
    const [coordinateUnit, setCoordinateUnit] = useState<CoordinateUnitMode>("angstrom")

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

            <PageShell
                identity={(
                    <header className="basin-header">
                        {/* SHOULD-FIX-7 (PR B review): the intro sentence
                            now goes through `RecordIdentityHeader`'s own
                            `intro` prop -- kicker -> h1 -> intro -> identity
                            is the one order every record page's header
                            follows now, rather than each page picking its
                            own position for this sentence relative to
                            identity (this page used to put it AFTER the
                            whole header/identity block; `ConformerGroupPage`/
                            `ConformerObservationPage`'s hand-matched headers
                            put it BEFORE identity -- three different
                            orders for the same fact). */}
                        <RecordIdentityHeader
                            kicker="Geometry · deposited evidence"
                            title={displayFormula ? `${displayFormula} geometry` : "Geometry"}
                            intro="One stored set of atomic coordinates: the exact positions a calculation consumed or produced. This is not a species or a calculation — the same coordinates can be reused across more than one calculation, in either direction."
                            identity={identity}
                            submissionRef={geometry.submission_ref}
                        />
                        {formulaIsFallback && (
                            // Only reachable when the archive's own identity is absent
                            // or ambiguous — the honest label the brief asks for,
                            // rather than presenting a client-computed value as if it
                            // came from the archive.
                            <p className="note">
                                Formula computed on this page from atom symbols — the archive did not serve a formula
                                for this geometry's owning entry.
                            </p>
                        )}
                        <dl className="kv-list basin-context">
                            <div><dt>Geometry ref</dt><dd><code>{geometry.geometry_ref}</code></dd></div>
                            <div><dt>Atom count</dt><dd>{geometry.natoms}</dd></div>
                            <div><dt>Geometry hash</dt><dd><code>{geometry.geom_hash}</code></dd></div>
                            <div><dt>Format</dt><dd>{geometry.format}</dd></div>
                            <div><dt>Coordinate units</dt><dd>{geometry.coordinate_units}</dd></div>
                            <div><dt>Deposited</dt><dd>{isoDate(geometry.created_at)}</dd></div>
                        </dl>
                    </header>
                )}
            >
            <section className="ledger-summary geometry-summary" aria-label="Geometry provenance summary">
                <Metric label="Producing calculations" value={producedBy.length} />
                <Metric label="Consuming calculations" value={usedAsInputBy.length} />
                {/* `.card`, not `--derived`: this states a single fact about ONE
                    geometry ("no validation field of its own"), not a computed
                    aggregate -- see the `.card--derived` decision in this PR's
                    body. */}
                <div className="card validation-card">
                    <span className="t-label">Validation</span>
                    {/* Finding #9: "on this endpoint" was API jargon leaking into a
                        chemist-facing page — "for this geometry" says the same thing
                        (this record has no validation field of its own) without it. */}
                    <strong>Not recorded for this geometry</strong>
                    <p className="note">
                        This geometry record carries no validation field of its own. A geometry-vs-formula check,
                        where one was recorded, lives on the calculation that produced or consumed it, not here.
                    </p>
                    {/* Finding #13: the panel used to repeat every producing/consuming
                        calculation ref inline (twelve on the live CH3 record, comma-joined
                        under one caption) with no visual link affordance -- the same refs
                        are already a proper linked table further down this page, under
                        "Produced by" / "Used as input by". Pointing here instead of
                        repeating the list keeps this card short and gives the reader ONE
                        place to click through, not two lists that can drift out of sync. */}
                    {/* No `#section-geometry-validation` fragment: this app has no fragment-scroll
                        handling (no ScrollRestoration, no hash effect) and a react-router `<Link>`
                        does a pushState navigation the browser does not scroll for anyway — and the
                        id sits inside a closed `<details>` whose content never loads until opened.
                        A fragment that silently does nothing is worse than a plain link to the right
                        page, so this points at the "Produced by" / "Used as input by" tables further
                        down this same page, not at a fragment.

                        Previously repeated every producing/consuming calculation ref inline here too
                        (a `<dl>` of named pointers, one row per relationship) — up to twelve refs,
                        comma-joined, on the live CH3 record, reported rendering all-caps despite the
                        DOM holding the lowercase ref. Those are the SAME refs the "Produced by" /
                        "Used as input by" tables below already render as real, correctly-cased links
                        (`ProvenanceSection` below; no `text-transform` on that table's `td`s at all).
                        Rather than hunt the specific inherited-uppercase source in THIS card's own
                        rules, this removes the duplicate identifier list altogether — one pointer
                        sentence to the tables that already render these refs correctly, so the two
                        lists can no longer drift and there is nothing left in this card for a
                        caption-styled ancestor to mis-transform. */}
                    {(producedBy.length > 0 || usedAsInputBy.length > 0) && (
                        <p className="note">
                            See "Geometry validation" on the producing or consuming calculations, listed with links
                            in the "Produced by" and "Used as input by" tables below.
                        </p>
                    )}
                </div>
            </section>

            <ViewerSection
                atoms={atoms}
                atomsAvailability={atomsAvailability}
                formula={displayFormula}
                xyzText={geometry.xyz_text ?? null}
                coordinateUnit={coordinateUnit}
            />

            <CoordinateTableSection
                atoms={atoms}
                atomsAvailability={atomsAvailability}
                geometryRef={geometry.geometry_ref}
                natoms={geometry.natoms}
                unit={coordinateUnit}
                onUnitChange={setCoordinateUnit}
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
            </PageShell>
        </section>
    )
}

function Metric({ label, value }: { label: string; value: number }) {
    return (
        <div className="card metric">
            <span>{label}</span>
            <strong>{value}</strong>
        </div>
    )
}

function SectionEmptyMessage({ availability, emptyText }: { availability: SectionAvailability; emptyText: string }) {
    if (availability === "absent") {
        // Not "not requested" — see the module docstring above. This page
        // always requests every field, so an absent key can only mean the
        // archive did not return it, never that this client failed to ask.
        return <p className="empty-projection">The archive did not return this field for this geometry.</p>
    }
    return <p className="empty-projection">{emptyText}</p>
}

function ViewerSection({ atoms, atomsAvailability, formula, xyzText, coordinateUnit }: {
    atoms: GeometryRecord["atoms"]
    atomsAvailability: SectionAvailability
    formula: string
    xyzText: string | null
    coordinateUnit: CoordinateUnitMode
}) {
    const rows = atoms ?? []
    return (
        <section className="ledger-section" aria-labelledby="viewer-heading">
            {/* Post-review pass (item 4): "Structure" was correctly dropped
                as a kicker (SHOULD-FIX-6) for restating "Structure view",
                but that left this page 3-of-5 kickered -- MEASURED and
                flagged as inconsistent ("either every section on a page
                has a category kicker or none does"). "Deposited evidence"
                fits for real (not a restatement of "Structure view", and
                accurate: this section, the coordinate table below, and
                the raw XYZ block further down all render the SAME
                deposited atom coordinates in three different forms) --
                matching `.coordinates-heading`'s own kicker below rather
                than inventing a fourth wording for the same category. */}
            <SectionHeading id="viewer-heading" kicker="Deposited evidence">Structure view</SectionHeading>
            {atomsAvailability === "populated" ? (
                <SectionErrorBoundary
                    fallback={(
                        <p className="empty-projection" role="alert">
                            This structure view could not be drawn. The coordinate table and raw XYZ
                            block below are unaffected.
                        </p>
                    )}
                >
                    <GeometryViewer atoms={rows} formula={formula} xyzText={xyzText} coordinateUnitMode={coordinateUnit} />
                </SectionErrorBoundary>
            ) : (
                <SectionEmptyMessage
                    availability={atomsAvailability}
                    emptyText="No atom rows are recorded for this geometry, so no view can be drawn."
                />
            )}
        </section>
    )
}

function CoordinateTableSection({ atoms, atomsAvailability, geometryRef, natoms, unit, onUnitChange }: {
    atoms: NonNullable<GeometryRecord["atoms"]>
    atomsAvailability: SectionAvailability
    geometryRef: string
    natoms: number
    unit: CoordinateUnitMode
    onUnitChange: (unit: CoordinateUnitMode) => void
}) {
    // `natoms` and `atoms.length` are two separately-sourced numbers on the
    // wire (see `app/services/scientific_read/geometry.py` — one is a
    // stored column, the other is `len(atoms)` freshly queried) and are
    // never reconciled by this page's rendering. A mismatch is a real
    // archive-side inconsistency worth surfacing, not a client bug to hide.
    const countMismatch = atomsAvailability === "populated" && natoms !== atoms.length
    // `unit` is now controlled by `GeometryDetail` (see its own comment) so
    // `GeometryViewer`'s measurements can share it — this section no longer
    // owns the state, only the two buttons that change it.
    const [elementDisplay, setElementDisplay] = useState<ElementDisplayMode>("symbol")
    const unitLabel = unit === "angstrom" ? "Å" : "bohr"
    return (
        <section className="ledger-section" aria-labelledby="coordinates-heading">
            <SectionHeading
                id="coordinates-heading"
                kicker="Deposited evidence"
                intro="Every atom in this geometry, in the order the archive returned them. This table is the accessible, selectable fallback for the view above — it renders whether or not that view does."
            >
                Coordinate table
            </SectionHeading>
            {countMismatch && (
                <p className="empty-projection" role="alert">
                    The declared atom count ({natoms}) does not match the number of coordinate rows returned
                    ({atoms.length}). Showing the rows the archive actually returned.
                </p>
            )}
            {atomsAvailability === "populated" && (
                <div className="coordinate-controls">
                    <fieldset className="coordinate-toggle">
                        <legend>Units</legend>
                        <button type="button" aria-pressed={unit === "angstrom"} onClick={() => onUnitChange("angstrom")}>Å</button>
                        <button type="button" aria-pressed={unit === "bohr"} onClick={() => onUnitChange("bohr")}>bohr</button>
                    </fieldset>
                    <fieldset className="coordinate-toggle">
                        <legend>Elements</legend>
                        <button type="button" aria-pressed={elementDisplay === "symbol"} onClick={() => setElementDisplay("symbol")}>Symbol</button>
                        <button type="button" aria-pressed={elementDisplay === "number"} onClick={() => setElementDisplay("number")}>Number</button>
                    </fieldset>
                    {/* Owner's own words: "plain toggle but starts with angstrom
                        cause that's how it's stored" — no "converted" badge next
                        to a value, and this note names the wire truth (angstrom
                        is what `coordinate_units` says and what the archive
                        holds) rather than let the bohr column imply otherwise.
                        Also names the rounding: bohr values are computed then
                        `.toFixed(6)`'d for a stable column width (see
                        `formatCoordinate`), so a reader comparing this column
                        against their own conversion is not surprised by a
                        7th-digit difference this note never mentioned.

                        Finding #9: plain wording, no `coordinate_units`-on-the-wire
                        jargon — the guarantee (stored in ångström; bohr is a display
                        conversion) and the rounding/conversion-factor detail both stay,
                        just said in words a chemist reads without needing to know this
                        app's field names. */}
                    <p className="note coordinate-toggle-note">
                        Stored in ångström; bohr here is a display conversion only, rounded to 6 decimal
                        places, at 1 Å = {ANGSTROM_TO_BOHR.toFixed(10)} bohr (CODATA 2018 Bohr radius).
                    </p>
                </div>
            )}
            {atomsAvailability === "populated" ? (
                <div className="table-scroll">
                    <table className="data-table coordinate-table" aria-label={`Coordinates for ${geometryRef}`}>
                        <thead>
                            <tr>
                                <th scope="col">Atom</th>
                                <th scope="col">Element</th>
                                {/* `data-column` is the CSS/test hook for "this is
                                    the x/y/z column" — stable across unit toggles,
                                    unlike the visible header text below it (which
                                    intentionally changes to name the active unit;
                                    see geometry-detail.css's numeric-alignment rule
                                    for why the two must not be the same attribute). */}
                                <th scope="col" data-column="x">{`x (${unitLabel})`}</th>
                                <th scope="col" data-column="y">{`y (${unitLabel})`}</th>
                                <th scope="col" data-column="z">{`z (${unitLabel})`}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {atoms.map((atom) => (
                                <tr key={atom.atom_index}>
                                    <td data-label="Atom">{atom.atom_index}</td>
                                    <td data-label="Element">{elementDisplayValue(atom.element, elementDisplay)}</td>
                                    {/* `data-label` here carries the ACTIVE UNIT
                                        (`x (Å)` / `x (bohr)`), not a bare axis
                                        letter — this is what the mobile stacked
                                        view's `td::before { content:
                                        attr(data-label) }` (conformer-group.css)
                                        actually shows a reader below the 680px
                                        breakpoint. Without the unit in this
                                        string, a phone reader in bohr mode saw
                                        "X / 2.038933" with no unit anywhere on
                                        the value — a wrong-unit-reading hazard.
                                        `data-column` (unit-independent) is the
                                        separate hook the desktop alignment CSS
                                        keys on instead, so that rule does not
                                        silently stop matching the moment this
                                        label's text changes. */}
                                    <td data-column="x" data-label={`x (${unitLabel})`}>{formatCoordinate(atom.x, unit)}</td>
                                    <td data-column="y" data-label={`y (${unitLabel})`}>{formatCoordinate(atom.y, unit)}</td>
                                    <td data-column="z" data-label={`z (${unitLabel})`}>{formatCoordinate(atom.z, unit)}</td>
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
            {/* Post-review pass (item 4): "Raw" was correctly dropped as a
                kicker (SHOULD-FIX-6) for restating "Raw XYZ" -- see the
                matching comment on `viewer-heading` above for why
                "Deposited evidence" (not a restatement of "Raw XYZ", and
                the same category the Structure-view/Coordinate-table
                sections above share -- all three render the same
                deposited coordinates) is what makes this page's kickers
                consistent again rather than removing the two that were
                never redundant in the first place. */}
            <SectionHeading id="xyz-heading" kicker="Deposited evidence" intro="The archive's own XYZ-format text block for this geometry, selectable as deposited.">
                Raw XYZ
            </SectionHeading>
            {xyzText ? (
                <>
                    <div className="xyz-actions">
                        <CopyButton value={xyzText} label="raw XYZ" srLabel="text" />
                    </div>
                    <pre className="xyz-block"><code>{xyzText}</code></pre>
                </>
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
            <SectionHeading id={headingId} kicker="Deposited provenance" intro={description}>{title}</SectionHeading>
            {availability === "populated" ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label={`${title} for this geometry`}>
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
                                    {showRole && <td data-label="Role">{link.role ? statusLabel(link.role) : "not recorded"}</td>}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <SectionEmptyMessage availability={availability} emptyText={emptyText} />
            )}
        </section>
    )
}
