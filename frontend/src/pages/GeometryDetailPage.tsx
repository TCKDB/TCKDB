import { useState } from "react"
import { calculationWords } from "../domain/recordWords"
import { RecordLink } from "../components/RecordLink"
import { useParams } from "react-router-dom"
import "../conformer-group.css"
import "../geometry-detail.css"
import { Breadcrumbs, type Crumb } from "../components/Breadcrumbs"
import { entryCrumbs } from "../components/entryCrumbs"
import { Formula } from "../components/Formula"
import { PageShell } from "../components/PageShell"
import { RecordMap } from "../components/RelationshipMap"
import { geometryNodes } from "../domain/recordMapNodes"
import { SectionHeading } from "../components/PageSections"
import { Stored } from "../components/QuantityValue"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { SectionErrorBoundary } from "../components/SectionErrorBoundary"
import { CopyButton } from "../components/RefsDisclosure"
import { ViewerStage } from "../components/ViewerStage"
import { BROWSE_KIND_PATHS } from "../api/browseApi"
import type { GeometryProvenanceCalcLink, GeometryRecord } from "../api/geometryApi"
import { typeLabel } from "../domain/calculationTypeFormat"
import { geometryLede } from "../domain/conformerLede"
import { producedByPhrase, shapeSummary } from "../domain/geometryShape"
import { hasIsotopeLabel, isotopeHillFormula, isotopeLabel } from "../domain/isotopeLabel"
import { identityFormula, identityFromGeometry, type RecordIdentity } from "../domain/recordIdentity"
import { useGeometry } from "../hooks/useGeometry"
import {
    ANGSTROM_TO_BOHR,
    angstromToBohr,
    atomicNumberForSymbol,
    type CoordinateUnitMode,
} from "../domain/geometryXyz"
import { useDocumentTitle } from "../hooks/useDocumentTitle"

type ElementDisplayMode = "symbol" | "number"

/** Six decimal places in either unit, so the columns line up; the stored ångström number is the hover value. */
function CoordinateValue({ valueAngstrom, unit }: { valueAngstrom: number; unit: CoordinateUnitMode }) {
    const shown = (unit === "angstrom" ? valueAngstrom : angstromToBohr(valueAngstrom)).toFixed(6)
    const text = Number(shown) === 0 ? shown.replace("-", "") : shown
    return unit === "angstrom" && Number(text) !== valueAngstrom
        ? <Stored full={String(valueAngstrom)}>{text}</Stored>
        : <>{text}</>
}

function elementDisplayValue(
    atom: { element: string; isotope_mass_number?: number | null },
    mode: ElementDisplayMode,
): string {
    if (mode === "symbol") return isotopeLabel(atom)
    const atomicNumber = atomicNumberForSymbol(atom.element)
    if (atomicNumber === null) return `unknown (${atom.element})`
    return atom.isotope_mass_number == null ? String(atomicNumber) : `${atomicNumber} (mass ${atom.isotope_mass_number})`
}

// Every field on this endpoint is returned in one request and no token gates any of them, so an absent key can only
// mean the archive dropped it, never that this client failed to ask.
type SectionAvailability = "absent" | "empty" | "populated"

function sectionAvailability<T>(value: T[] | null | undefined): SectionAvailability {
    if (value === undefined) return "absent"
    if (value === null || value.length === 0) return "empty"
    return "populated"
}

/** "3 calculations", or "Calculations" when the archive did not return the list, so no count is claimed. */
function countLead(availability: SectionAvailability, count: number): string {
    if (availability === "absent") return "Calculations"
    return `${count} ${count === 1 ? "calculation" : "calculations"}`
}

const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

/**
 * A Hill-order formula from the atoms shown on this page, used only when the archive's own identity block is absent or
 * ambiguous.
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

/** The owner's own parents, outermost first, ending at this page. */
function geometryCrumbs(identity: RecordIdentity): Crumb[] {
    if (identity.kind === "species_entry" && identity.speciesRef && identity.speciesEntryRef) {
        return [
            ...entryCrumbs({
                speciesRef: identity.speciesRef, speciesEntryRef: identity.speciesEntryRef, formula: identity.formula,
                smiles: identity.canonicalSmiles, facets: identity.facets,
            }),
            { label: "Geometry" },
        ]
    }
    if (identity.kind === "transition_state_entry" && identity.transitionStateEntryRef) {
        return [
            { label: "Transition states", to: BROWSE_KIND_PATHS.transition_state },
            { label: "This transition state", to: `/transition-state-entries/${identity.transitionStateEntryRef}` },
            { label: "Geometry" },
        ]
    }
    return [{ label: "Geometry" }]
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
    const clientFormula = hillFormula(atoms.map((atom) => atom.element))

    // The archive's own identity wins whenever it has a formula; the formula computed from the atoms is a labelled fallback.
    const identity = identityFromGeometry(geometry.identity)
    const servedFormula = identityFormula(identity)
    // An isotope-labelled geometry shows the formula of its own atoms: the entry's served formula is element-only.
    const isotopeLabelled = hasIsotopeLabel(atoms)
    const displayFormula = isotopeLabelled ? isotopeHillFormula(atoms) : (servedFormula ?? clientFormula)
    const formulaIsFallback = !isotopeLabelled && servedFormula === null && clientFormula !== ""
    useDocumentTitle("Geometry", displayFormula)

    // One unit for the coordinate table and the viewer's measurements.
    const [coordinateUnit, setCoordinateUnit] = useState<CoordinateUnitMode>("angstrom")

    const producedByAvailability = sectionAvailability(geometry.provenance?.produced_by)
    const usedAsInputByAvailability = sectionAvailability(geometry.provenance?.used_as_input_by)
    const producedBy = geometry.provenance?.produced_by ?? []
    const usedAsInputBy = geometry.provenance?.used_as_input_by ?? []
    const producedPhrase = producedByPhrase(producedBy)

    const mapNodes = geometryNodes({
        owner: identity.kind === "species_entry" && identity.speciesEntryRef
            ? { kind: "species_entry", entryRef: identity.speciesEntryRef, formula: identity.formula }
            : identity.kind === "transition_state_entry" && identity.transitionStateEntryRef
                ? { kind: "transition_state_entry", entryRef: identity.transitionStateEntryRef }
                : null,
        producedBy: geometry.provenance?.produced_by ? producedBy : null,
        usedAsInputBy: geometry.provenance?.used_as_input_by ? usedAsInputBy : null,
        typeLabel,
    })

    return (
        <section className="conformer-page geometry-page">
            <PageShell
                side={<RecordMap selfKind="geometry" selfLabel="This geometry" nodes={mapNodes} />}
                identity={(
                    <header className="basin-header">
                        <Breadcrumbs crumbs={geometryCrumbs(identity)} />
                        <RecordIdentityHeader
                            kicker="Geometry"
                            lede={geometryLede()}
                            title={<>
                                Geometry{displayFormula ? <> of <Formula value={displayFormula} /></> : null}
                                {producedPhrase ? ` (${producedPhrase})` : ""}
                            </>}
                            identity={identity}
                            submissionRef={geometry.submission_ref}
                            ownRef={{ label: "Geometry ref", value: geometry.geometry_ref }}
                        />
                        {isotopeLabelled && (
                            <p className="note">
                                Mass numbers shown (e.g. ²H) come from this geometry's atoms.
                            </p>
                        )}
                        {formulaIsFallback && (
                            <p className="note">
                                Formula worked out from the atoms; none was served.
                            </p>
                        )}
                    </header>
                )}
            >
                <StageSection atoms={atoms} atomsAvailability={atomsAvailability} formula={displayFormula} xyzText={geometry.xyz_text ?? null} coordinateUnit={coordinateUnit} />

                <CoordinateTableSection
                    atoms={atoms}
                    atomsAvailability={atomsAvailability}
                    natoms={geometry.natoms}
                    unit={coordinateUnit}
                    onUnitChange={setCoordinateUnit}
                />

                <RawXyzSection xyzText={geometry.xyz_text ?? null} />

                <ProvenanceSection
                    title="Produced by"
                    description={`${countLead(producedByAvailability, producedBy.length)} ${producedByAvailability === "absent" ? "that produced" : "produced"} this geometry.`}
                    links={producedBy}
                    availability={producedByAvailability}
                    showRole
                    emptyText="No calculation is recorded as having produced this geometry."
                />

                <ProvenanceSection
                    title="Used as input by"
                    description={`${countLead(usedAsInputByAvailability, usedAsInputBy.length)} ${usedAsInputByAvailability === "absent" ? "that used" : "used"} it as input. Listed separately from Produced by.`}
                    links={usedAsInputBy}
                    availability={usedAsInputByAvailability}
                    showRole={false}
                    emptyText="No calculation is recorded as having consumed this geometry as input."
                />

                <section className="ledger-section" aria-labelledby="details-heading">
                    <SectionHeading id="details-heading">Record details</SectionHeading>
                    <dl className="kv-list basin-context">
                        <div><dt>Atom count</dt><dd>{geometry.natoms}</dd></div>
                        <div><dt>Geometry hash</dt><dd><code>{geometry.geom_hash}</code></dd></div>
                        <div><dt>Format</dt><dd>{geometry.format}</dd></div>
                        <div><dt>Coordinate units</dt><dd>{geometry.coordinate_units}</dd></div>
                        <div><dt>Deposited</dt><dd>{isoDate(geometry.created_at)}</dd></div>
                        <div><dt>Validation</dt><dd>not recorded for this geometry</dd></div>
                    </dl>
                    {(producedBy.length > 0 || usedAsInputBy.length > 0) && (
                        <p className="note">Validation lives on the producing or consuming calculation.</p>
                    )}
                </section>
            </PageShell>
        </section>
    )
}

function SectionEmptyMessage({ availability, emptyText }: { availability: SectionAvailability; emptyText: string }) {
    if (availability === "absent") {
        return <p className="empty-projection">The archive did not return this field for this geometry.</p>
    }
    return <p className="empty-projection">{emptyText}</p>
}

/** The picture first, across the content column, with one sentence about its shape worked out from the coordinates. */
function StageSection({ atoms, atomsAvailability, formula, xyzText, coordinateUnit }: {
    atoms: GeometryRecord["atoms"]
    atomsAvailability: SectionAvailability
    formula: string
    xyzText: string | null
    coordinateUnit: CoordinateUnitMode
}) {
    const rows = atoms ?? []
    if (atomsAvailability !== "populated") {
        return (
            <SectionEmptyMessage
                availability={atomsAvailability}
                emptyText="No atom rows are recorded for this geometry, so no view can be drawn."
            />
        )
    }
    return (
        <figure className="viewer-figure" aria-label="Picture of this geometry">
            <SectionErrorBoundary
                fallback={(
                    <p className="empty-projection" role="alert">
                        This structure view could not be drawn. The coordinate table and raw XYZ
                        block below are unaffected.
                    </p>
                )}
            >
                <ViewerStage atoms={rows} formula={formula} xyzText={xyzText} coordinateUnitMode={coordinateUnit} />
            </SectionErrorBoundary>
            <figcaption>{shapeSummary(rows)}</figcaption>
        </figure>
    )
}

function CoordinateTableSection({ atoms, atomsAvailability, natoms, unit, onUnitChange }: {
    atoms: NonNullable<GeometryRecord["atoms"]>
    atomsAvailability: SectionAvailability
    natoms: number
    unit: CoordinateUnitMode
    onUnitChange: (unit: CoordinateUnitMode) => void
}) {
    // `natoms` (a stored column) and `atoms.length` (the rows returned) are separate numbers; a mismatch is a real
    // archive-side inconsistency, shown rather than hidden.
    const countMismatch = atomsAvailability === "populated" && natoms !== atoms.length
    const [elementDisplay, setElementDisplay] = useState<ElementDisplayMode>("symbol")
    const unitLabel = unit === "angstrom" ? "Å" : "bohr"
    return (
        <section className="ledger-section" aria-labelledby="coordinates-heading">
            <SectionHeading
                id="coordinates-heading"
                intro="Atoms in archive order."
            >
                Atoms and coordinates
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
                    <p className="note coordinate-toggle-note">
                        Stored in ångström; bohr is a display conversion (1 Å = {ANGSTROM_TO_BOHR.toFixed(10)} bohr). 6 decimals; hover for the stored value.
                    </p>
                </div>
            )}
            {atomsAvailability === "populated" ? (
                <div className="table-scroll" tabIndex={0} role="region" aria-label="Table of coordinates">
                    <table className="data-table coordinate-table" aria-label="Coordinates">
                        <thead>
                            <tr>
                                <th scope="col">Atom</th>
                                <th scope="col">Element</th>
                                {/* `data-column` is the hook for the numeric alignment rule and never changes with the unit;
                                    the visible header does. `.t-preserve-case` keeps "bohr" from being uppercased. */}
                                <th className="t-preserve-case" scope="col" data-column="x">{`x (${unitLabel})`}</th>
                                <th className="t-preserve-case" scope="col" data-column="y">{`y (${unitLabel})`}</th>
                                <th className="t-preserve-case" scope="col" data-column="z">{`z (${unitLabel})`}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {atoms.map((atom) => (
                                <tr key={atom.atom_index}>
                                    <td data-label="Atom">{atom.atom_index}</td>
                                    <td data-label="Element">{elementDisplayValue(atom, elementDisplay)}</td>
                                    {/* The stacked phone layout shows `data-label`, so it carries the active unit. */}
                                    <td data-column="x" data-label={`x (${unitLabel})`}><CoordinateValue valueAngstrom={atom.x} unit={unit} /></td>
                                    <td data-column="y" data-label={`y (${unitLabel})`}><CoordinateValue valueAngstrom={atom.y} unit={unit} /></td>
                                    <td data-column="z" data-label={`z (${unitLabel})`}><CoordinateValue valueAngstrom={atom.z} unit={unit} /></td>
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
            <SectionHeading id="xyz-heading">
                Raw XYZ
            </SectionHeading>
            {xyzText ? (
                <>
                    <div className="xyz-actions">
                        <CopyButton value={xyzText} label="raw XYZ" srLabel="text" />
                    </div>
                    <pre className="xyz-block" tabIndex={0}><code>{xyzText}</code></pre>
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
            <SectionHeading id={headingId} intro={description}>{title}</SectionHeading>
            {availability === "populated" ? (
                <div className="table-scroll" tabIndex={0} role="region" aria-label={`Table of ${title.toLowerCase()}`}>
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
                                        <RecordLink kind="calculation" to={`/calculations/${link.calculation_ref}`} label={calculationWords(link.calculation_type)} context={`${title} ${index + 1}`} />
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
