import { createContext, useContext, useEffect, useId, useMemo, useState, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { levelOfTheoryPath } from "../domain/methodsLinks"
import { inlineQualifiers, levelKey, levelLine, qualifiersOf, type LevelFields, type LevelKey, type Qualifier } from "../domain/levelLabel"
import "../level-label.css"

type Scope = { register: (level: LevelFields) => () => void; qualifiers: ReadonlyMap<LevelKey, Qualifier[]> }
const ScopeContext = createContext<Scope | null>(null)

/**
 * One page's worth of levels. Every `LevelLabel` below registers its level
 * here; two levels that share a short label but differ in a qualifier then
 * print that qualifier inline, so no two different levels read alike.
 */
export function LevelLabelScope({ children }: { children: ReactNode }) {
    const [levels, setLevels] = useState<ReadonlyMap<LevelKey, { level: LevelFields; count: number }>>(new Map())
    const register = useMemo(() => (level: LevelFields) => {
        const key = levelKey(level)
        setLevels((previous) => {
            const next = new Map(previous)
            next.set(key, { level, count: (previous.get(key)?.count ?? 0) + 1 })
            return next
        })
        return () => setLevels((previous) => {
            const next = new Map(previous)
            const count = (previous.get(key)?.count ?? 1) - 1
            if (count <= 0) next.delete(key)
            else next.set(key, { level, count })
            return next
        })
    }, [])
    const qualifiers = useMemo(() => inlineQualifiers([...levels.values()].map((entry) => entry.level)), [levels])
    const value = useMemo(() => ({ register, qualifiers }), [register, qualifiers])
    return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>
}

export type LevelLabelProps = {
    levelOfTheory: LevelFields
    /** The program (and version) the level was run with; printed after the method and basis. */
    program?: string | null
    /** Links the short label to the level's page when the level has a ref. Default true. */
    link?: boolean
    /** No details disclosure, for a spot that already lists the qualifiers beside it. */
    plain?: boolean
}

/**
 * A level of theory as one run of inline text: method/basis and program, then a small "details" toggle when the level
 * states qualifiers. The toggle is a button; its panel is positioned under the line, so opening it never moves the
 * label or changes the line box, and the whole thing is phrasing content that sits in a sentence, a list line or a
 * flex row. Built from the structured fields alone; qualifiers that tell this level from another on the page are
 * printed inline. `plain` leaves the toggle out where `LevelDetails` follows.
 */
export function LevelLabel({ levelOfTheory, program, link = true, plain = false }: LevelLabelProps) {
    const scope = useContext(ScopeContext)
    const register = scope?.register
    const [open, setOpen] = useState(false)
    const panelId = useId()
    const { method, basis, dispersion, solvent, solvent_model, aux_basis, cabs_basis, core_treatment, spin_treatment, relativistic_hamiltonian, pno_truncation, ri_approximation, level_of_theory_ref, display } = levelOfTheory
    useEffect(() => {
        if (!register) return undefined
        return register({ method, basis, dispersion, solvent, solvent_model, aux_basis, cabs_basis, core_treatment, spin_treatment, relativistic_hamiltonian, pno_truncation, ri_approximation, level_of_theory_ref, display })
    }, [register, method, basis, dispersion, solvent, solvent_model, aux_basis, cabs_basis, core_treatment, spin_treatment, relativistic_hamiltonian, pno_truncation, ri_approximation, level_of_theory_ref, display])
    const line = levelLine(levelOfTheory, scope?.qualifiers ?? new Map(), program)
    const head = link && level_of_theory_ref
        ? <Link className="level-label-text t-preserve-case" to={levelOfTheoryPath(level_of_theory_ref)}>{line}</Link>
        : <span className="level-label-text t-preserve-case">{line}</span>
    const pieces = plain ? [] : qualifiersOf(levelOfTheory)
    if (pieces.length === 0) return <span className="level-label">{head}</span>
    return (
        <span className="level-label">
            {head}
            <button
                type="button"
                className="level-label-toggle"
                aria-expanded={open}
                aria-controls={open ? panelId : undefined}
                aria-label={`Details of ${line}`}
                onClick={() => setOpen(!open)}
                onKeyDown={(event) => { if (event.key === "Escape") setOpen(false) }}
            >details</button>
            {open && (
                <span id={panelId} role="group" aria-label={`Qualifiers of ${line}`} className="level-label-panel">
                    {pieces.map((piece) => (
                        <span key={piece.key} className="level-label-piece">
                            <span className="level-label-piece-name">{piece.name}</span>
                            <span className="t-preserve-case">{piece.value}</span>
                        </span>
                    ))}
                </span>
            )}
        </span>
    )
}

/**
 * The qualifiers of a level behind a native "details" disclosure; nothing when it states none. Block content, so it
 * goes beside a sentence or in a cell, never inside a paragraph: a sentence uses `<LevelLabel plain>` and puts this
 * after it. Its name carries the level's inline qualifiers and program, so two disclosures on a page never share one.
 */
export function LevelDetails({ levelOfTheory, program }: { levelOfTheory: LevelFields; program?: string | null }) {
    const scope = useContext(ScopeContext)
    const pieces = qualifiersOf(levelOfTheory)
    if (pieces.length === 0) return null
    const line = levelLine(levelOfTheory, scope?.qualifiers ?? new Map(), program)
    return (
        <details className="level-label-details">
            <summary aria-label={`Details of ${line}`}>details</summary>
            <dl className="level-label-pieces">
                {pieces.map((piece) => (
                    <div key={piece.key}><dt>{piece.name}</dt><dd className="t-preserve-case">{piece.value}</dd></div>
                ))}
            </dl>
        </details>
    )
}
