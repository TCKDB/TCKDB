import { useRef } from "react"
import { loadGeometry } from "../api/geometryApi"
import { useLoaded } from "../hooks/useLoaded"
import { useNearViewport } from "../hooks/useNeighbours"
import { MolPoster } from "./MolPoster"

/** A shape's picture, read only once the card is near the viewport so a long list does not read every geometry at once. */
export function ShapePicture({ geometryRef, formula }: { geometryRef: string | null; formula: string }) {
    const ref = useRef<HTMLSpanElement | null>(null)
    const near = useNearViewport(ref)
    const state = useLoaded(loadGeometry, near ? geometryRef : null)
    const atoms = state.status === "ready" ? state.data.atoms ?? [] : []
    const text = geometryRef === null || state.status === "failed" || (state.status === "ready" && atoms.length === 0)
        ? "No picture"
        : state.status === "rate-limited" ? "Picture not loaded yet" : "Loading picture"
    return (
        <span className="shape-picture" ref={ref} aria-hidden="true">
            {atoms.length > 0 ? <MolPoster atoms={atoms} formula={formula} /> : <span className="shape-picture-empty">{text}</span>}
        </span>
    )
}
