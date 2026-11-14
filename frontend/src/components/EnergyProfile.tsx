import { Link } from "react-router-dom"
import { GuideLink } from "./GuideLink"
import { Fragment, useId, useRef, useState } from "react"
import "../energy-profile.css"
import {
    loadReactionEnergyProfile,
    type EnergyProfileCitedCalculation,
    type EnergyProfileMoleculePoint,
    type EnergyProfileRead,
    type EnergyProfileRecord,
} from "../api/reactionEnergyProfileApi"
import {
    citedSubjectRef,
    energyTypeWord,
    hartreeText,
    hiddenNotes,
    isMixed,
    levelsOf,
    profileLabels,
    profileScale,
    programText,
    relativeKjText,
    roleWord,
    transitionStateNamer,
    UNAVAILABLE_WORDS,
} from "../domain/energyProfile"
import { recordWords } from "../domain/recordWords"
import { useOverflows } from "../hooks/useOverflows"
import { useScientificRecord } from "../hooks/useScientificRecord"
import { Disclosure } from "./Disclosure"
import { LevelLabel } from "./LevelLabel"
import { RecordLink } from "./RecordLink"
import { OrderNote } from "./OrderNote"
import { SectionHeading } from "./PageSections"
import { ReviewChip } from "./ReviewChip"
import { ValueLink } from "./ValueLink"

const INTRO = "kJ/mol relative to the reactants. One level and program each; none is chosen for you."

const HILL_NOTE = "TCKDB's comparison at one level and program; the depositor's check may use other energies."

const NO_ZPE = "Electronic energy, no zero-point correction."

/** The kind of energy stays visible; the rest of the served definition sits in a closed disclosure. */
function ProfileDefinition({ definition }: { definition: string }) {
    if (!definition.includes(NO_ZPE)) return <p className="note energy-profile-definition">{definition}</p>
    const rest = definition.replace(NO_ZPE, "").replace(/\s+/g, " ").trim()
    return (
        <div className="energy-profile-definition">
            <p className="note">{NO_ZPE}</p>
            {rest && <Disclosure summary="How these energies are chosen"><p className="note">{rest}</p></Disclosure>}
        </div>
    )
}

const W = 500
const H = 240
const TOP = 30
const BOTTOM = 40
const AXIS_X = 52
const BAR_W = 100
const CENTRES = [122, 272, 422]

function ProfileFigure({ profile, captionId, markPeak }: { profile: EnergyProfileRecord; captionId: string; markPeak: boolean }) {
    const levels = levelsOf(profile)
    const scale = profileScale(levels.map((level) => level.kj))
    const plot = H - TOP - BOTTOM
    const y = (kj: number) => TOP + ((scale.max - kj) / (scale.max - scale.min)) * plot
    return (
        <svg className="energy-profile-svg" viewBox={`0 0 ${W} ${H}`} role="group" aria-labelledby={captionId}>
            {scale.ticks.map((tick) => (
                <g key={tick}>
                    <line className={tick === 0 ? "energy-profile-zero" : "energy-profile-grid"} x1={AXIS_X} x2={W - 6} y1={y(tick)} y2={y(tick)} />
                    <text className="energy-profile-tick t-preserve-case" x={AXIS_X - 6} y={y(tick) + 5} textAnchor="end">{tick === 0 ? "0" : relativeKjText(tick).replace(/\.0$/, "")}</text>
                </g>
            ))}
            <text className="energy-profile-axis-title t-preserve-case" x={AXIS_X} y={14}>kJ/mol relative to reactants</text>
            {levels.slice(1).map((level, index) => (
                <line
                    key={level.key}
                    className="energy-profile-connector"
                    x1={CENTRES[index] + BAR_W / 2}
                    y1={y(levels[index].kj)}
                    x2={CENTRES[index + 1] - BAR_W / 2}
                    y2={y(level.kj)}
                />
            ))}
            {levels.map((level, index) => (
                <g key={level.key}>
                    <line className={markPeak && level.key === "transition_state" ? "energy-profile-bar energy-profile-peak" : "energy-profile-bar"} x1={CENTRES[index] - BAR_W / 2} x2={CENTRES[index] + BAR_W / 2} y1={y(level.kj)} y2={y(level.kj)} />
                    <text className="energy-profile-value t-preserve-case" x={CENTRES[index]} y={y(level.kj) - 8} textAnchor="middle">
                        {relativeKjText(level.kj)} kJ/mol
                    </text>
                    <text className="energy-profile-name" x={CENTRES[index]} y={H - 14} textAnchor="middle">
                        {markPeak && level.key === "transition_state" ? "This transition state" : level.label}
                    </text>
                </g>
            ))}
        </svg>
    )
}

function SpeciesCell({ point, labels }: { point: EnergyProfileMoleculePoint; labels: ReadonlyMap<string, string> }) {
    const label = labels.get(point.species_entry_ref)
    return (
        <>
            <RecordLink kind="species-entry" to={`/species-entries/${point.species_entry_ref}`} label={label ?? "Species"}>
                <span className="t-preserve-case">{label ?? "Species"}</span>
            </RecordLink>
            {point.count > 1 && <> <span className="t-preserve-case">× {point.count}</span></>}
        </>
    )
}

type PointShape = {
    calculation_ref: string
    energy_type: string
    electronic_energy_hartree: number
    software: { program: string; version?: string | null }
    review_status: string
    convergence_unrecorded: boolean
}

function PointCells({ point }: { point: PointShape }) {
    return (
        <>
            <td data-label="Electronic energy">
                <ValueLink value={hartreeText(point.electronic_energy_hartree)} unit="hartree" to={`/calculations/${point.calculation_ref}`} />
            </td>
            <td data-label="Kind of energy">
                {energyTypeWord(point.energy_type)}
                {point.convergence_unrecorded && (
                    <span className="energy-profile-flag"><span aria-hidden="true">† </span>convergence not recorded</span>
                )}
            </td>
            <td data-label="Program">{programText(point.software)}</td>
            <td data-label="Review"><ReviewChip status={point.review_status} explain={false} /></td>
        </>
    )
}

function RelativeCell({ kj, note }: { kj: number | null; note?: string }) {
    return (
        <td className="num" data-label="Relative to reactants">
            {kj === null ? <span className="energy-profile-absent">{note}</span> : <><span>{relativeKjText(kj)}</span> <span className="u t-preserve-case">kJ/mol</span></>}
        </td>
    )
}

function GroupRows({ title, points, sum, kj, labels }: {
    title: string
    points: EnergyProfileMoleculePoint[]
    sum: number
    kj: number
    labels: ReadonlyMap<string, string>
}) {
    if (points.length === 1 && points[0].count === 1) {
        const point = points[0]
        return (
            <tr>
                <th scope="row">{title}: <SpeciesCell point={point} labels={labels} /></th>
                <PointCells point={point} />
                <RelativeCell kj={kj} />
            </tr>
        )
    }
    return (
        <>
            <tr className="energy-profile-sum">
                <th scope="row">{title}, summed</th>
                <td data-label="Electronic energy"><span className="value-link-number">{hartreeText(sum)}</span> <span className="u t-preserve-case">hartree</span></td>
                <td data-label="Kind of energy" colSpan={3}><span className="energy-profile-absent">each molecule counted once per occurrence</span></td>
                <RelativeCell kj={kj} />
            </tr>
            {points.map((point) => (
                <tr key={point.species_entry_ref}>
                    <th scope="row" className="energy-profile-member">{title}: <SpeciesCell point={point} labels={labels} /></th>
                    <PointCells point={point} />
                    <RelativeCell kj={null} note={`part of the ${title === "Reactants" ? "reactant" : "product"} sum above`} />
                </tr>
            ))}
        </>
    )
}

function ProfileTable({ profile, labels, single, tsWords }: { profile: EnergyProfileRecord; labels: ReadonlyMap<string, string>; single: boolean; tsWords: (ref: string) => string }) {
    const ts = profile.transition_state
    return (
        <div className="table-scroll energy-profile-scroll" tabIndex={0} role="region" aria-label="Points of the selected energy profile">
            <table className="data-table energy-profile-table">
                <caption className="energy-profile-caption-table">Every point of the selected profile and the calculation it comes from</caption>
                <thead>
                    <tr>
                        <th scope="col">Point</th>
                        <th scope="col">Electronic energy</th>
                        <th scope="col">Kind of energy</th>
                        <th scope="col">Program</th>
                        <th scope="col">Review</th>
                        <th scope="col">Relative to reactants</th>
                    </tr>
                </thead>
                <tbody>
                    <GroupRows title="Reactants" points={profile.reactants} sum={profile.energy_hartree.reactants} kj={profile.relative_kj_mol.reactants} labels={labels} />
                    <tr>
                        <th scope="row">
                            {single
                                ? "This transition state"
                                : <RecordLink kind="transition-state" to={`/transition-state-entries/${ts.transition_state_entry_ref}`} label={tsWords(ts.transition_state_entry_ref)} />}
                        </th>
                        <PointCells point={ts} />
                        <RelativeCell kj={profile.relative_kj_mol.transition_state} />
                    </tr>
                    <GroupRows title="Products" points={profile.products} sum={profile.energy_hartree.products} kj={profile.relative_kj_mol.products} labels={labels} />
                </tbody>
            </table>
        </div>
    )
}

function ProfileView({ profile, labels, several, markPeak, tsWords, showLevel }: { profile: EnergyProfileRecord; labels: ReadonlyMap<string, string>; several: boolean; markPeak: boolean; tsWords: (ref: string) => string; showLevel: boolean }) {
    const captionId = useId()
    const boxRef = useRef<HTMLDivElement | null>(null)
    const overflows = useOverflows(boxRef)
    const levels = levelsOf(profile)
    const kinds = profile.energy_types.map(energyTypeWord).join(" and ")
    const versions = profile.versions.map((version) => version ?? "version not recorded").join(", ")
    return (
        <div className="energy-profile-view">
            <dl className="kv-list energy-profile-facts">
                {!markPeak && <div><dt>Transition-state entry</dt><dd><RecordLink kind="transition-state" to={`/transition-state-entries/${profile.transition_state_entry_ref}`} label={tsWords(profile.transition_state_entry_ref)} /> <ReviewChip status={profile.transition_state_entry_review_status} explain={false} /></dd></div>}
                {showLevel && <div><dt>Level of theory</dt><dd><LevelLabel levelOfTheory={profile.level_of_theory} program={profile.program} /></dd></div>}
            </dl>
            <figure className="energy-profile-figure">
                {overflows && <p className="energy-profile-hint">Scroll sideways inside the box to see the whole picture. The table below has the same numbers.</p>}
                <div
                    className="energy-profile-box"
                    ref={boxRef}
                    {...(overflows ? { tabIndex: 0, role: "region", "aria-label": "Energy profile picture, scrolls sideways" } : {})}
                >
                    <ProfileFigure profile={profile} captionId={captionId} markPeak={markPeak} />
                </div>
                <figcaption id={captionId} className="energy-profile-caption">
                    Energy relative to the reactants, in kJ/mol
                    {profile.energy_kind !== "electronic" && <> (kind of energy: <code>{profile.energy_kind}</code>)</>}:{" "}
                    {levels.map((level, index) => (
                        <Fragment key={level.key}>{index > 0 && ", "}{level.label.toLowerCase()} <span className="t-preserve-case">{relativeKjText(level.kj)}</span></Fragment>
                    ))}.
                    {several && " Each profile has its own axis."}
                    <GuideLink to="energy-profile" />
                </figcaption>
            </figure>
            <ProfileDefinition definition={profile.definition} />
            {markPeak && <p className="note">{HILL_NOTE}</p>}
            {isMixed(profile) && (
                <p className="note energy-profile-mixed">
                    This profile mixes kinds of energy or program versions. Kinds of energy: {kinds}. Program versions: {versions}.
                </p>
            )}
            <ProfileTable profile={profile} labels={labels} single={markPeak} tsWords={tsWords} />
        </div>
    )
}

function Unavailable({ read, single }: { read: EnergyProfileRead; single: boolean }) {
    if (read.unavailable.length === 0) return null
    const tsWords = transitionStateNamer(read)
    const none = read.profiles.length === 0
    return (
        <div className="energy-profile-unavailable">
            {single
                ? <h3 className="t-heading-2">{none ? "Why there is no profile" : "Levels and programs with no profile"}</h3>
                : <h3 className="t-heading-2">No profile for these transition states</h3>}
            <ul>
                {read.unavailable.map((item, index) => (
                    <li key={`${item.transition_state_entry_ref}-${item.code}-${index}`}>
                        {!single && (
                            <>
                                <RecordLink kind="transition-state" to={`/transition-state-entries/${item.transition_state_entry_ref}`} label={tsWords(item.transition_state_entry_ref)} />
                                {" "}<ReviewChip status={item.transition_state_entry_review_status} explain={false} />
                            </>
                        )}
                        {item.level_of_theory && <>{!single && " · "}<LevelLabel levelOfTheory={item.level_of_theory} program={item.program} /></>}
                        {item.program && !item.level_of_theory && <>{!single && " · "}<span className="t-preserve-case">{item.program}</span></>}
                        {(!single || item.level_of_theory || item.program) && ": "}
                        {Object.hasOwn(UNAVAILABLE_WORDS, item.code) ? UNAVAILABLE_WORDS[item.code] : <code>{item.code}</code>}
                    </li>
                ))}
            </ul>
        </div>
    )
}

/** Where the kinetics records of the entry are listed, when that is not this page. */
export type RecordsAt = { to: string; version: string | null }

function CitedRow({ keyRef, position, calculation, labels, recordsAt }: { keyRef: string; position: number | undefined; calculation: EnergyProfileCitedCalculation; labels: ReadonlyMap<string, string>; recordsAt?: RecordsAt }) {
    const subject = citedSubjectRef(calculation)
    const subjectLabel = calculation.species_entry_ref ? labels.get(calculation.species_entry_ref) : undefined
    return (
        <tr>
            {recordsAt
                ? (
                    <th scope="row" className={position ? "energy-profile-record" : undefined}>
                        <Link to={`${recordsAt.to}#kinetics-${keyRef}`}>{position ? recordWords(position) : `A record of ${recordsAt.version ?? "this version"}`}</Link>
                    </th>
                )
                : <th scope="row" className="energy-profile-record"><a href={`#kinetics-${keyRef}`}>{recordWords(position ?? 1)}</a></th>}
            <td data-label="What it cites">{roleWord(calculation.role)}{subject && <>{": "}<span className="t-preserve-case">{subjectLabel ?? (calculation.species_entry_ref ? "species" : "transition state")}</span></>}</td>
            <td data-label="Electronic energy">
                {calculation.electronic_energy_hartree == null
                    ? <span className="energy-profile-absent">not recorded</span>
                    : <ValueLink value={hartreeText(calculation.electronic_energy_hartree)} unit="hartree" to={`/calculations/${calculation.calculation_ref}`} />}
            </td>
            <td data-label="Level of theory">
                {calculation.level_of_theory
                    ? <span className="level-label-cell"><LevelLabel levelOfTheory={calculation.level_of_theory} program={calculation.software ? programText(calculation.software) : null} /></span>
                    : "not recorded"}
            </td>
            <td data-label="Kind of energy">{energyTypeWord(calculation.energy_type)}</td>
            <td data-label="Program">{calculation.software ? programText(calculation.software) : "not recorded"}</td>
            <td data-label="Review"><ReviewChip status={calculation.review_status} explain={false} /></td>
        </tr>
    )
}

function CitedEnergies({ read, labels, recordNumbers, recordsAt }: { read: EnergyProfileRead; labels: ReadonlyMap<string, string>; recordNumbers?: ReadonlyMap<string, number>; recordsAt?: RecordsAt }) {
    // Away from the page that lists the records, a number is claimed only when the list gave one.
    const rows = read.cited_by_kinetics.flatMap((record, position) => record.calculations.map((calculation) => ({ ref: record.kinetics_ref, position: recordsAt ? recordNumbers?.get(record.kinetics_ref) : (recordNumbers?.get(record.kinetics_ref) ?? position + 1), calculation })))
    if (rows.length === 0) return null
    return (
        <div className="energy-profile-cited">
            <h3 className="t-heading-2">Energies a record cites</h3>
            <p className="note">Named by the records; not combined with the profiles.</p>
            <div className="table-scroll energy-profile-scroll" tabIndex={0} role="region" aria-label="Energies cited by records">
                <table className="data-table energy-profile-table">
                    <caption className="energy-profile-caption-table">Calculations cited by each record on this entry</caption>
                    <thead>
                        <tr>
                            <th scope="col">Record</th>
                            <th scope="col">What it cites</th>
                            <th scope="col">Electronic energy</th>
                            <th scope="col">Level of theory</th>
                            <th scope="col">Kind of energy</th>
                            <th scope="col">Program</th>
                            <th scope="col">Review</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map(({ ref, position, calculation }) => <CitedRow key={`${ref}-${calculation.role}-${calculation.calculation_ref}`} keyRef={ref} position={position} calculation={calculation} labels={labels} recordsAt={recordsAt} />)}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

/** The picker and the chosen profile; `read` needs only its profiles. */
export function Profiles({ read, labels, markPeak = false, showLevel = true }: {
    read: Pick<EnergyProfileRead, "profiles"> & Partial<Pick<EnergyProfileRead, "unavailable">>
    labels: ReadonlyMap<string, string>
    markPeak?: boolean
    /** False under a heading that already names the level and program. */
    showLevel?: boolean
}) {
    const pickerId = useId()
    const [index, setIndex] = useState(0)
    const optionLabels = profileLabels(read.profiles, read)
    const tsWords = transitionStateNamer(read)
    const chosen = read.profiles[Math.min(index, read.profiles.length - 1)]
    return (
        <>
            {read.profiles.length > 1 && (
                <div className="energy-profile-picker">
                    <label htmlFor={pickerId}>Profile</label>
                    <select id={pickerId} value={index} onChange={(event) => setIndex(Number(event.target.value))}>
                        {read.profiles.map((profile, position) => (
                            <option key={`${profile.transition_state_entry_ref}-${position}`} value={position}>{optionLabels[position]}</option>
                        ))}
                    </select>
                    <p className="note">
                        {read.profiles.length} profiles. The first is shown until you pick another.
                    </p>
                    <OrderNote order={markPeak
                        ? "By level, then program."
                        : "By TS entry, then level and program."} />
                </div>
            )}
            {chosen && <ProfileView profile={chosen} labels={labels} several={read.profiles.length > 1} markPeak={markPeak} tsWords={tsWords} showLevel={showLevel} />}
        </>
    )
}

/**
 * The energy profile of one reaction entry: reactants, transition state and products at one level of theory and one
 * program, as a picture and a table of the same points. Reads its own data; the cited energies of the rate records
 * stay a separate table. With `onlyTransitionState` the read is narrowed to that transition-state entry's own profiles
 * and reasons, its peak is marked, and the rate records' cited energies are kept only where they cite this transition state.
 */
export function EnergyProfile({ entryRef, speciesLabels, onlyTransitionState, recordNumbers, recordsAt }: {
    entryRef: string
    speciesLabels: ReadonlyMap<string, string>
    /** The page's one "Record N" numbering by kinetics ref; the cited order when absent. */
    recordNumbers?: ReadonlyMap<string, number>
    /** Set on a page that does not list the entry's records: cited records link to where they are listed. */
    recordsAt?: RecordsAt
    onlyTransitionState?: string
}) {
    const state = useScientificRecord(entryRef, loadReactionEnergyProfile)
    const served = state.status === "ready" ? state.record : null
    const read = served && onlyTransitionState
        ? {
            ...served,
            profiles: served.profiles.filter((profile) => profile.transition_state_entry_ref === onlyTransitionState),
            unavailable: served.unavailable.filter((item) => item.transition_state_entry_ref === onlyTransitionState),
            cited_by_kinetics: served.cited_by_kinetics
                .map((record) => ({ ...record, calculations: record.calculations.filter((calculation) => calculation.transition_state_entry_ref === onlyTransitionState) }))
                .filter((record) => record.calculations.length > 0),
        }
        : served
    const noun = onlyTransitionState ? "transition state" : "reaction entry"
    return (
        <section className="energy-profile" aria-labelledby="energy-heading">
            <SectionHeading id="energy-heading" intro={INTRO}>{onlyTransitionState ? "Energy hill" : "Energy profile"}</SectionHeading>
            {(state.status === "loading" || state.status === "retrying") && <p className="note" role="status" aria-busy="true">Loading the energy profile.</p>}
            {state.status !== "ready" && state.status !== "loading" && state.status !== "retrying" && (
                <p className="note" role="alert">The energy profile could not be loaded.</p>
            )}
            {read && (
                <>
                    {read.profiles.length === 0 && <p className="empty-projection">No complete energy profile is available for this {noun}.</p>}
                    <Profiles key={`${entryRef}-${onlyTransitionState ?? ""}`} read={read} labels={speciesLabels} markPeak={Boolean(onlyTransitionState)} />
                    <Unavailable read={read} single={Boolean(onlyTransitionState)} />
                    {onlyTransitionState && read.profiles.length === 0 && read.unavailable.length === 0 && (
                        <p className="note">The energy profile read gives no reason for this transition state: it was not among the entries it listed.</p>
                    )}
                    <CitedEnergies read={read} labels={speciesLabels} recordNumbers={recordNumbers} recordsAt={recordsAt} />
                    {hiddenNotes(read).map((note) => <p className="note" role="note" key={note}>{onlyTransitionState ? `For this reaction entry: ${note}` : note}</p>)}
                </>
            )}
        </section>
    )
}
