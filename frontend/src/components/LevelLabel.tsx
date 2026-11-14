import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { levelOfTheoryPath } from "../domain/methodsLinks"
import { baseLabel, inlineQualifiers, inlineText, levelKey, qualifiersOf, type LevelFields, type LevelKey, type Qualifier } from "../domain/levelLabel"
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
 * A level of theory as method/basis and program on one line, with the
 * qualifiers folded behind "details". Built from the structured fields;
 * a view that holds only a label string shows that string and no details.
 */
export function LevelLabel({ levelOfTheory, program, link = true, plain = false }: LevelLabelProps) {
    const scope = useContext(ScopeContext)
    const register = scope?.register
    const { method, basis, dispersion, solvent, solvent_model, aux_basis, cabs_basis, core_treatment, spin_treatment, level_of_theory_ref, label, display } = levelOfTheory
    const key = levelKey(levelOfTheory)
    useEffect(() => {
        if (!register) return undefined
        return register({ method, basis, dispersion, solvent, solvent_model, aux_basis, cabs_basis, core_treatment, spin_treatment, level_of_theory_ref, label, display })
    }, [register, method, basis, dispersion, solvent, solvent_model, aux_basis, cabs_basis, core_treatment, spin_treatment, level_of_theory_ref, label, display])
    const pieces = qualifiersOf(levelOfTheory)
    const short = baseLabel(levelOfTheory)
    const parts = [short, ...(scope?.qualifiers.get(key) ?? []).map(inlineText), ...(program ? [program] : [])]
    const line = parts.join(" · ")
    const head = link && level_of_theory_ref
        ? <Link className="level-label-text t-preserve-case" to={levelOfTheoryPath(level_of_theory_ref)}>{line}</Link>
        : <span className="level-label-text t-preserve-case">{line}</span>
    if (plain || pieces.length === 0) return <span className="level-label">{head}</span>
    return (
        <span className="level-label">
            {head}
            <details className="level-label-details">
                <summary aria-label={`Details of ${short}`}>details</summary>
                <dl className="level-label-pieces">
                    {pieces.map((piece) => (
                        <div key={piece.key}><dt>{piece.name}</dt><dd className="t-preserve-case">{piece.value}</dd></div>
                    ))}
                </dl>
            </details>
        </span>
    )
}
