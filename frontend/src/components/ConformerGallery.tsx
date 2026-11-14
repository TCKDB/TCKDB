import { GuideLink } from "./GuideLink"
import { RecordLink } from "./RecordLink"
import { usedByWords } from "../domain/recordWords"
import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import "../molecule-page.css"
import type { ListMeta } from "../api/listEnvelope"
import { loadConformerUsage } from "../api/conformerUsageApi"
import { loadRelativeEnergies, relativeEnergyKey, type RelativeEnergies } from "../api/relativeEnergyApi"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel, sortConformersForDisplay } from "../domain/conformerEvidence"
import {
    cardEnergy, depositorWrittenLabel, shapeOrderSentence, formatHartree, formatRelativeKj, levelChoices, outcomeNote, pictureGeometryRef,
    programIndex, referenceProgram, shapeCountsSentence,
    type CardEnergy, type LevelChoice, type ProgramIndex,
} from "../domain/conformerGallery"
import { hiddenByDefaultNote, type PageRequest } from "../domain/paging"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import { useAnnounce } from "../hooks/useAnnounce"
import { useLoaded, type LoadedState } from "../hooks/useLoaded"
import type { SinglePointsState } from "../hooks/useSpeciesSinglePoints"
import { DepositorNote } from "./DepositorNote"
import { ListPager } from "./ListPager"
import { OrderNote } from "./OrderNote"
import { SectionHeading } from "./PageSections"
import { RefsDisclosure } from "./RefsDisclosure"
import { ConformerUsedBy } from "./ConformerUsedBy"
import { ShapeCardHead } from "./ShapePicture"
import { ValueLink } from "./ValueLink"

/** Pictures load by themselves for this many cards; later cards wait to be asked. */
const AUTO_PICTURES = 12

function CalculationLinks({ refs }: { refs: readonly string[] }) {
    return (
        <>
            {refs.map((ref, index) => (
                <span key={ref}>{index > 0 && ", "}<RecordLink kind="calculation" to={`/calculations/${ref}`} label={refs.length > 1 ? `Calculation ${index + 1}` : "Calculation"} /></span>
            ))}
        </>
    )
}

/** What a card or table cell says about one shape's energy; every state is words, never a blank or a zero. */
function EnergyText({ energy }: { energy: CardEnergy }) {
    switch (energy.kind) {
        case "pending":
            return <span className="shape-energy-absent">Not shown until you choose a level.</span>
        case "unread":
            return <span className="shape-energy-absent">Could not be read.</span>
        case "reference":
            return (
                <>
                    0 <span className="u t-preserve-case">kJ/mol</span> (the reference shape{energy.program ? `, ${energy.program}` : ""})
                    {energy.calculationRefs.length > 0 && <span className="shape-energy-from">{" "}from <CalculationLinks refs={energy.calculationRefs} /></span>}
                </>
            )
        case "value": {
            const refs = energy.calculationRefs
            return (
                <>
                    <ValueLink value={formatRelativeKj(energy.kjMol)} unit="kJ/mol" to={refs.length === 1 ? `/calculations/${refs[0]}` : null} />
                    <span className="shape-energy-from">{" "}with <span className="t-preserve-case">{energy.program}</span>, from <CalculationLinks refs={refs} /></span>
                </>
            )
        }
        case "disagree":
            return (
                <span className="shape-energy-absent">
                    Records disagree.
                    {energy.values.length > 0 && (
                        <>
                            {" "}Stored values:{" "}
                            {energy.values.map((value, index) => (
                                <span key={value.determinationRef}>{index > 0 && ", "}{formatHartree(value.hartree)} <span className="u t-preserve-case">hartree</span> {`(determination ${index + 1})`}</span>
                            ))}
                            .
                        </>
                    )}
                </span>
            )
        case "note":
            return <span className="shape-energy-absent">{energy.text}</span>
    }
}

type Energies = { levelRef: string | null; state: LoadedState<RelativeEnergies>; programs: ProgramIndex }

function energyFor(energies: Energies, groupRef: string): CardEnergy {
    if (energies.levelRef === null) return { kind: "pending" }
    if (energies.state.status === "ready") return cardEnergy(energies.state.data, groupRef, energies.programs)
    if (energies.state.status === "failed" || energies.state.status === "rate-limited") return { kind: "unread" }
    return { kind: "pending" }
}

/**
 * One card per conformer group (one shape): picture, label, counts, relative energy at a level and reference the reader
 * picks, and the records that name it. Cards are buttons; choosing one drives the stage, the calculations and the
 * evidence tabs. The first card is only a default, never chosen by energy. A difference is shown only when it and the
 * reference come from one program.
 */
export function ConformerGallery({
    entryRef, formula, conformers, allConformers, meta, onPage, pagingBusy, pagingFailed,
    selectedRef, selectedLabel, selectionIsDefault, defaultReferenceRef, singlePoints, optimisations, onSelect,
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
    singlePoints: SinglePointsState
    optimisations: SinglePointsState
    onSelect: (conformerGroupRef: string) => void
}) {
    const announce = useAnnounce()
    const total = meta.pagination.total
    const ordered = useMemo(() => sortConformersForDisplay(conformers), [conformers])
    const selectedOnPage = conformers.some((conformer) => conformer.conformer_group.conformer_group_ref === selectedRef)
    const labels = useMemo(
        () => new Map(allConformers.map((conformer) => [conformer.conformer_group.conformer_group_ref, conformerLabel(conformer)])),
        [allConformers],
    )
    const labelOf = (ref: string) => labels.get(ref) ?? ref
    const usage = useLoaded(loadConformerUsage, entryRef)
    const records = useMemo(
        () => [...(singlePoints.status === "ready" ? singlePoints.records : []), ...(optimisations.status === "ready" ? optimisations.records : [])],
        [singlePoints, optimisations],
    )
    const settled = (state: SinglePointsState) => state.status === "ready" || state.status === "failed"
    const energiesSettled = settled(singlePoints) && settled(optimisations)
    const levels = useMemo(() => (energiesSettled ? levelChoices(allConformers, records) : []), [energiesSettled, allConformers, records])
    const programs = useMemo(() => programIndex(allConformers, records), [allConformers, records])
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
    const state: Energies = { levelRef, state: energies, programs }
    const level: LevelChoice | undefined = levels.find((candidate) => candidate.ref === levelRef)
    const usageData = usage.status === "ready" ? usage.data : null
    const hidden = usageData ? hiddenByDefaultNote(usageData.hidden, "records") : null
    const referenceChoices = useMemo(() => sortConformersForDisplay(allConformers), [allConformers])
    const note = energies.status === "ready" ? outcomeNote(energies.data) : null
    const tableProgram = energies.status === "ready" ? referenceProgram(energies.data, programs) : null
    const select = (ref: string) => {
        onSelect(ref)
        announce(`${labelOf(ref)} selected; its picture and calculations are below.`)
    }

    return (
        <section className="conformer-picker" aria-labelledby="shapes-heading">
            <SectionHeading id="shapes-heading">Its 3D shapes</SectionHeading>
            {ordered.length === 0 ? (
                <p className="empty-projection">No conformers are recorded for this entry.</p>
            ) : (
                <>
                    {total !== 1 && <p className="conformer-picker-note">{total} shapes. Choose one.</p>}
                    <ListPager meta={meta} noun="shape" onPage={onPage} busy={pagingBusy} label="Shapes paging" hideOrder />
                    {pagingFailed && (
                        <p className="empty-projection" role="alert">That page of conformers could not be loaded; the previous page is still shown.</p>
                    )}
                    {selectedRef && selectedLabel && !selectedOnPage && (
                        <p className="conformer-picker-note conformer-offpage-note">{selectedLabel} is selected; it is on another page of this list.</p>
                    )}
                    {selectionIsDefault && selectedLabel && total > 1 && (
                        <p className="conformer-picker-note conformer-default-note">
                            {selectedLabel} is selected only because it is listed first.
                        </p>
                    )}
                    {total > 1 && <OrderNote order={shapeOrderSentence(meta.sort)} />}

                    {total > 1 && <fieldset className="shape-energy-controls">
                        <legend>Energy relative to a reference shape</legend>
                        {!energiesSettled ? (
                            <p className="note" role="status" aria-busy="true">Reading the energies on this entry.</p>
                        ) : levels.length === 0 ? (
                            <p className="note">No level of theory with energies is recorded for the shapes on this page, so no relative energies are shown.</p>
                        ) : (
                            <>
                                <p className="note">
                                    Electronic energy in kJ/mol, one level and one program. Only confirmed minima get a number.<GuideLink to="relative-energy" />
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
                                        {labelOf(referenceRef)} is the reference only because it is listed first.
                                    </p>
                                )}
                                {onlyLevel !== null && chosenLevel === null && (
                                    <p className="note">Only level with energies.</p>
                                )}
                                {level && level.programs.length > 1 && (
                                    <p className="note">Calculations at this level used {joinPrograms(level.programs)}.</p>
                                )}
                                <p className="note" role="status">
                                    {energies.status === "loading" && "Reading energies."}
                                    {energies.status === "failed" && "The relative energies could not be read."}
                                    {energies.status === "rate-limited" && `The archive is busy. Wait ${formatWaitSeconds(energies.retryAfterSeconds)} and choose the level again.`}
                                    {note}
                                </p>
                            </>
                        )}
                    </fieldset>}

                    {usageData && usageData.ensembleThermoRefs.length > 0 && (
                        <p className="shape-ensemble">
                            Describes all shapes together:{" "}
                            {usageData.ensembleThermoRefs.map((ref, index) => (
                                <span key={ref}>
                                    {index > 0 && ", "}
                                    <RecordLink kind="thermo" to={`/species-entries/${entryRef}/thermo${selectedRef ? `?conformer=${encodeURIComponent(selectedRef)}` : ""}`} hash={`thermo-heading-${ref}`} label={usedByWords("Thermo record", index, usageData.ensembleThermoRefs.length)} />
                                </span>
                            ))}
                        </p>
                    )}
                    {hidden && <p className="note">{hidden}</p>}
                    {usage.status === "failed" && <p className="note">Which records use each shape could not be read.</p>}

                    <ul className="conformer-list" aria-label="Shapes of this entry">
                        {ordered.map((conformer, index) => {
                            const ref = conformer.conformer_group.conformer_group_ref
                            const typed = depositorWrittenLabel(conformer)
                            return (
                                <li key={ref} className="conformer-card" data-selected={ref === selectedRef}>
                                    <ShapeCardHead
                                        geometryRef={pictureGeometryRef(conformer)}
                                        formula={formula}
                                        auto={index < AUTO_PICTURES}
                                        selected={ref === selectedRef}
                                        label={conformerLabel(conformer)}
                                        counts={shapeCountsSentence(conformer)}
                                        onSelect={() => select(ref)}
                                    />
                                    {total > 1 && <p className="shape-card-energy"><span className="shape-card-key">Relative energy</span> <EnergyText energy={energyFor(state, ref)} /></p>}
                                    <ConformerUsedBy usage={usage} group={usageData?.groups.get(ref)} entryRef={entryRef} groupRef={ref} labelOf={labelOf} />
                                    <DepositorNote>{typed}</DepositorNote>
                                    <p className="shape-card-3d"><Link to={`/conformer-groups/${ref}`}>Open in 3D{" "}<span className="molecule-page-visually-hidden">of {conformerLabel(conformer)}</span></Link></p>
                                    <RefsDisclosure inset refs={[{ label: "Conformer group", value: ref, to: `/conformer-groups/${ref}` }]} />
                                </li>
                            )
                        })}
                    </ul>

                    {total > 1 && levelRef !== null && energies.status === "ready" && (
                        <div className="table-scroll" tabIndex={0} role="region" aria-label="Relative energies table">
                            <table className="data-table shape-energy-table">
                                <caption>
                                    Energy of each shape on this page relative to {referenceRef ? labelOf(referenceRef) : "the reference"}
                                    {level ? ` at ${level.label}` : ""}{tableProgram ? ` with ${tableProgram}` : ""}
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

const joinPrograms = (programs: readonly string[]) => programs.join(" and ")
