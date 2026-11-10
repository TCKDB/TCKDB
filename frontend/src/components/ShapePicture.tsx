import { useRef, useState } from "react"
import { loadGeometryAtoms } from "../api/geometryApi"
import { useLoaded } from "../hooks/useLoaded"
import { useNearViewport } from "../hooks/useNeighbours"
import { MolPoster } from "./MolPoster"

/**
 * The selectable top of a shape card: picture, label and counts in one button. `auto` cards read their geometry once
 * they are near the viewport; the others wait for the reader to ask, so a long list does not spend the read budget on
 * pictures nobody scrolled to. After a rate-limit wait the reader can ask again. The ask is its own button beside the
 * select button, never inside it; asking returns focus to the select button.
 */
export function ShapeCardHead({ geometryRef, formula, auto, selected, label, counts, onSelect }: {
    geometryRef: string | null
    formula: string
    auto: boolean
    selected: boolean
    label: string
    counts: string
    onSelect: () => void
}) {
    const ref = useRef<HTMLSpanElement | null>(null)
    const selectRef = useRef<HTMLButtonElement | null>(null)
    const near = useNearViewport(ref)
    const [asked, setAsked] = useState(0)
    const wanted = geometryRef !== null && ((auto && near) || asked > 0)
    const state = useLoaded(loadGeometryAtoms, wanted ? geometryRef : null, asked)
    const atoms = state.status === "ready" ? state.data.atoms ?? [] : []
    const missing = geometryRef === null || state.status === "failed" || (state.status === "ready" && atoms.length === 0)
    const canAsk = geometryRef !== null && atoms.length === 0 && ((state.status === "idle" && !auto) || state.status === "rate-limited")
    return (
        <>
            <button type="button" className="conformer-card-select" ref={selectRef} aria-pressed={selected} onClick={onSelect}>
                <span className="shape-picture" ref={ref} aria-hidden="true">
                    {atoms.length > 0
                        ? <MolPoster atoms={atoms} formula={formula} />
                        : <span className="shape-picture-empty">{missing ? "No picture" : state.status === "rate-limited" ? "Picture not loaded yet" : state.status === "loading" || auto ? "Loading picture" : "Picture not shown yet"}</span>}
                </span>
                <span className="conformer-card-label">{label}</span>
                <span className="conformer-card-meta">{counts}</span>
            </button>
            {canAsk && (
                <p className="shape-picture-ask">
                    {state.status === "rate-limited" && "The archive is busy. "}
                    <button type="button" onClick={() => { setAsked(asked + 1); selectRef.current?.focus() }}>{state.status === "rate-limited" ? "Try the picture again" : "Show picture"}</button>
                </p>
            )}
        </>
    )
}
