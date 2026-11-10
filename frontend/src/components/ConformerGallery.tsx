import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import "../molecule-page.css"
import type { ListMeta } from "../api/listEnvelope"
import { loadConformerUsage, type ConformerUsage, type ConformerUsageGroup } from "../api/conformerUsageApi"
import { loadRelativeEnergies, relativeEnergyKey, type RelativeEnergies } from "../api/relativeEnergyApi"
import type { SpeciesCalculationEnergyRecord } from "../api/speciesCalculationsApi"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel, sortConformersForDisplay } from "../domain/conformerEvidence"
import {
    cardEnergy, depositorWrittenLabel, formatRelativeKj, levelChoices, outcomeNote, pictureGeometryRef, shapeCountsSentence,
    type CardEnergy, type LevelChoice,
} from "../domain/conformerGallery"
import { hiddenByDefaultNote, type PageRequest } from "../domain/paging"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import { useLoaded, type LoadedState } from "../hooks/useLoaded"
import { DepositorNote } from "./DepositorNote"
import { ListPager } from "./ListPager"
import { OrderNote } from "./OrderNote"
import { SectionHeading } from "./PageSections"
import { RefsDisclosure } from "./RefsDisclosure"
import { ShapePicture } from "./ShapePicture"
import { ValueLink } from "./ValueLink"

const SHAPE_ORDER =
    "Pages follow the archive's display order; the cards within a page are listed by conformer number, then label, then reference."

/** What a card or table cell says about one shape's energy; every state is words, never a blank or a zero. */
function EnergyText({ energy }: { energy: CardEnergy }) {
    switch (energy.kind) {
        case "pending":
            return <span className="shape-energy-absent">Not shown until you choose a level.</span>
        case "unread":
            return <span className="shape-energy-absent">Could not be read.</span>
        case "reference":
            return <>0 <span className="u t-preserve-case">kJ/mol</span> (the reference shape)</>
        case "value": {
            const refs = energy.calculationRefs
            return (
                <>
                    <ValueLink value={formatRelativeKj(energy.kjMol)} unit="kJ/mol" to={refs.length === 1 ? `/calculations/${refs[0]}` : null} />
                    {refs.length > 0 && (
                        <span className="shape-energy-from">
                            {" "}from{" "}
                            {refs.map((ref, index) => (
                                <span key={ref}>{index > 0 && ", "}<Link to={`/calculations/${ref}`}><code className="t-preserve-case">{ref}</code></Link></span>
                            ))}
                        </span>
                    )}
                </>
            )
        }
        case "disagree":
            return (
                <span className="shape-energy-absent">
                    Records disagree.{energy.valuesHartree.length > 0 && ` Stored values: ${energy.valuesHartree.map((value) => value.toFixed(6)).join(", ")} hartree.`}
                </span>
            )
        case "absent":
            return <span className="shape-energy-absent">No value at this level.{energy.reason && ` (${energy.reason})`}</span>
    }
}

type Energies = { levelRef: string | null; referenceRef: string; state: LoadedState<RelativeEnergies> }

function energyFor(energies: Energies, groupRef: string): CardEnergy {
    if (energies.levelRef === null) return { kind: "pending" }
    if (energies.state.status === "ready") return cardEnergy(energies.state.data, groupRef)
    if (energies.state.status === "failed" || energies.state.status === "rate-limited") {
        return { kind: "unread" }
    }
    return { kind: "pending" }
}

function UsedBy({ usage, group, entryRef, groupRef, labelOf }: {
    usage: LoadedState<ConformerUsage>
    group: ConformerUsageGroup | undefined
    entryRef: string
    groupRef: string
    labelOf: (ref: string) => string
}) {
    if (usage.status !== "ready") return null
    if (!group || (group.thermo.length === 0 && group.kinetics.length === 0 && !group.merged_into_conformer_group_ref)) {
        return <p className="shape-usedby"><span className="shape-usedby-key">Used by</span> No thermochemistry or rate coefficient names this shape.</p>
    }
    const merged = group.merged_into_conformer_group_ref
    return (
        <div className="shape-usedby">
            <span className="shape-usedby-key">Used by</span>
            {group.thermo.length > 0 && (
                <p>
                    Thermochemistry:{" "}
                    {group.thermo.map((record, index) => (
                        <span key={record.thermo_ref}>
                            {index > 0 && ", "}
                            <Link to={`/species-entries/${entryRef}/thermo?conformer=${encodeURIComponent(groupRef)}#thermo-heading-${record.thermo_ref}`}>
                                <code className="t-preserve-case">{record.thermo_ref}</code>
                            </Link>
                        </span>
                    ))}
                </p>
            )}
            {group.kinetics.length > 0 && (
                <p>
                    Rate coefficients:{" "}
                    {group.kinetics.map((record, index) => (
                        <span key={`${record.kinetics_ref}-${record.role ?? ""}-${index}`}>
                            {index > 0 && ", "}
                            <Link to={`/reaction-entries/${record.reaction_entry_ref}`}>
                                <code className="t-preserve-case">{record.kinetics_ref}</code>
                            </Link>
                            {record.role && ` (${record.role})`}
                        </span>
                    ))}
                </p>
            )}
            {merged && (
                <p>This shape was merged into <Link to={`/conformer-groups/${merged}`}>{labelOf(merged)}</Link>. Records that name it are listed here and were not moved.</p>
            )}
        </div>
    )
}

/**
 * One card per conformer group (one shape): picture, label, counts, relative energy at a level and reference the reader
 * picks, and the records that name it. Cards are buttons; choosing one drives the stage, the calculations and the
 * evidence tabs. The first card is only a default, never chosen by energy.
 */
export function ConformerGallery({
    entryRef, formula, conformers, allConformers, meta, onPage, pagingBusy, pagingFailed,
    selectedRef, selectedLabel, selectionIsDefault, defaultReferenceRef, energyRecords, onSelect,
}: {
    entryRef: string
    formula: string
    /** The page of cards on screen. */
    conformers: ConformerProjection[]
    /** Every group the page knows, for labels of shapes off this page. */
    allConformers: ConformerProjection[]
    meta: ListMeta
    onPage: (request: PageRequest) => void
    pagingBusy: boolean
    pagingFailed: boolean
    selectedRef: string | null
    selectedLabel: string | null
    selectionIsDefault: boolean
    /** The first card of the first page: the reference shape until the reader names another. */
    defaultReferenceRef: string | null
    energyRecords: SpeciesCalculationEnergyRecord[]
    onSelect: (conformerGroupRef: string) => void
}) {
    const total = meta.pagination.total
    const ordered = useMemo(() => sortConformersForDisplay(conformers), [conformers])
    const selectedOnPage = conformers.some((conformer) => conformer.conformer_group.conformer_group_ref === selectedRef)
    const labels = useMemo(
        () => new Map(allConformers.map((conformer) => [conformer.conformer_group.conformer_group_ref, conformerLabel(conformer)])),
        [allConformers],
    )
    const labelOf = (ref: string) => labels.get(ref) ?? ref
    const usage = useLoaded(loadConformerUsage, entryRef)
    const levels = useMemo(() => levelChoices(allConformers, energyRecords), [allConformers, energyRecords])
    const [chosenLevel, setChosenLevel] = useState<string | null>(null)
    const [chosenReference, setChosenReference] = useState<string | null>(null)
    const onlyLevel = levels.length === 1 ? levels[0].ref : null
    const levelRef = chosenLevel !== null && levels.some((level) => level.ref === chosenLevel) ? chosenLevel : onlyLevel
    const referenceRef = chosenReference ?? defaultReferenceRef
    const referenceIsDefault = chosenReference === null
    const energies = useLoaded(
        loadRelativeEnergies,
        levelRef !== null && referenceRef !== null ? relativeEnergyKey({ entryRef, referenceRef, levelRef }) : null,
    )
    const state: Energies = { levelRef, referenceRef: referenceRef ?? "", state: energies }
    const level: LevelChoice | undefined = levels.find((candidate) => candidate.ref === levelRef)
    const usageData = usage.status === "ready" ? usage.data : null
    const hidden = usageData ? hiddenByDefaultNote(usageData.hidden, "records") : null
    const referenceChoices = useMemo(() => sortConformersForDisplay(allConformers), [allConformers])
    const note = energies.status === "ready" ? outcomeNote(energies.data) : null

    return (
        <section className="conformer-picker" aria-labelledby="shapes-heading">
            <SectionHeading id="shapes-heading">Its 3D shapes</SectionHeading>
            {ordered.length === 0 ? (
                <p className="empty-projection">No conformers are recorded for this entry.</p>
            ) : (
                <>
                    <p className="conformer-picker-note">
                        {total === 1
                            ? "This entry has one deposited conformer basin. Its evidence is shown below."
                            : `This entry has ${total} deposited conformer basins. Each card is one shape of the molecule; choose one to see its picture, `
                                + "the calculations behind it and its evidence."}
                    </p>
                    <ListPager meta={meta} noun="conformer basin" onPage={onPage} busy={pagingBusy} label="Conformer basins paging" />
                    {pagingFailed && (
                        <p className="empty-projection" role="alert">That page of conformers could not be loaded; the previous page is still shown.</p>
                    )}
                    {selectedRef && selectedLabel && !selectedOnPage && (
                        <p className="conformer-picker-note conformer-offpage-note">{selectedLabel} is selected; it is on another page of this list.</p>
                    )}
                    {selectionIsDefault && selectedLabel && total > 1 && (
                        <p className="conformer-picker-note conformer-default-note">
                            {selectedLabel} is selected only because it is listed first. That is not a preference or a
                            recommendation; choose a shape to see its evidence instead.
                        </p>
                    )}
                    <OrderNote order={SHAPE_ORDER} />

                    <fieldset className="shape-energy-controls">
                        <legend>Energy relative to a reference shape</legend>
                        {levels.length === 0 ? (
                            <p className="note">No level of theory with energies is recorded for the shapes on this page, so no relative energies are shown.</p>
                        ) : (
                            <>
                                <p className="note">
                                    Electronic energy at one level of theory, in kJ/mol. A shape needs a stored check that it is a true minimum;
                                    one without it has no value. Levels are never mixed.
                                </p>
                                <div className="shape-energy-fields">
                                    <label>
                                        Level of theory
                                        <select value={levelRef ?? ""} onChange={(event) => setChosenLevel(event.target.value || null)}>
                                            {levelRef === null && <option value="">Choose a level</option>}
                                            {levels.map((choice) => <option key={choice.ref} value={choice.ref}>{choice.label}</option>)}
                                        </select>
                                    </label>
                                    <label>
                                        Reference shape
                                        <select value={referenceRef ?? ""} onChange={(event) => setChosenReference(event.target.value || null)}>
                                            {referenceChoices.map((choice) => (
                                                <option key={choice.conformer_group.conformer_group_ref} value={choice.conformer_group.conformer_group_ref}>
                                                    {conformerLabel(choice)}
                                                </option>
                                            ))}
                                        </select>
                                    </label>
                                </div>
                                {referenceRef !== null && referenceIsDefault && (
                                    <p className="note shape-energy-default">
                                        {labelOf(referenceRef)} is the reference only because it is the first card listed. It is not chosen by energy; pick another to measure from it.
                                    </p>
                                )}
                                {onlyLevel !== null && chosenLevel === null && (
                                    <p className="note">{levels[0].label} is the only level with energies here, so it is filled in.</p>
                                )}
                                {level && level.programs.length > 1 && (
                                    <p className="note">
                                        Calculations at this level used {level.programs.join(" and ")}. The comparison is made by level and does not separate programs.
                                    </p>
                                )}
                                <p className="note" role="status">
                                    {energies.status === "loading" && "Reading energies."}
                                    {energies.status === "failed" && "The relative energies could not be read."}
                                    {energies.status === "rate-limited" && `The archive is busy. Wait ${formatWaitSeconds(energies.retryAfterSeconds)} and choose the level again.`}
                                    {note}
                                </p>
                            </>
                        )}
                    </fieldset>

                    {usageData && usageData.ensembleThermoRefs.length > 0 && (
                        <p className="shape-ensemble">
                            Describes all shapes together:{" "}
                            {usageData.ensembleThermoRefs.map((ref, index) => (
                                <span key={ref}>
                                    {index > 0 && ", "}
                                    <Link to={`/species-entries/${entryRef}/thermo#thermo-heading-${ref}`}><code className="t-preserve-case">{ref}</code></Link>
                                </span>
                            ))}
                        </p>
                    )}
                    {hidden && <p className="note">{hidden}</p>}
                    {usage.status === "failed" && <p className="note">Which records use each shape could not be read.</p>}

                    <ul className="conformer-list" aria-label="Shapes of this entry">
                        {ordered.map((conformer) => {
                            const ref = conformer.conformer_group.conformer_group_ref
                            const isSelected = ref === selectedRef
                            const typed = depositorWrittenLabel(conformer)
                            return (
                                <li key={ref} className="conformer-card" data-selected={isSelected}>
                                    <button type="button" className="conformer-card-select" aria-pressed={isSelected} onClick={() => onSelect(ref)}>
                                        <ShapePicture geometryRef={pictureGeometryRef(conformer)} formula={formula} />
                                        <span className="conformer-card-label">{conformerLabel(conformer)}</span>
                                        <span className="conformer-card-meta">{shapeCountsSentence(conformer)}</span>
                                    </button>
                                    <p className="shape-card-energy"><span className="shape-card-key">Relative energy</span> <EnergyText energy={energyFor(state, ref)} /></p>
                                    <UsedBy usage={usage} group={usageData?.groups.get(ref)} entryRef={entryRef} groupRef={ref} labelOf={labelOf} />
                                    <DepositorNote>{typed}</DepositorNote>
                                    <RefsDisclosure inset refs={[{ label: "Conformer group", value: ref, to: `/conformer-groups/${ref}` }]} />
                                </li>
                            )
                        })}
                    </ul>

                    {levelRef !== null && energies.status === "ready" && (
                        <div className="table-scroll" tabIndex={0} role="region" aria-label="Relative energies table">
                            <table className="data-table shape-energy-table">
                                <caption>
                                    Energy of each shape on this page relative to {referenceRef ? labelOf(referenceRef) : "the reference"}
                                    {level ? ` at ${level.label}` : ""}
                                </caption>
                                <thead>
                                    <tr><th scope="col">Shape</th><th scope="col">Relative energy</th></tr>
                                </thead>
                                <tbody>
                                    {ordered.map((conformer) => (
                                        <tr key={conformer.conformer_group.conformer_group_ref}>
                                            <th scope="row">{conformerLabel(conformer)}</th>
                                            <td><EnergyText energy={energyFor(state, conformer.conformer_group.conformer_group_ref)} /></td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </>
            )}
        </section>
    )
}
