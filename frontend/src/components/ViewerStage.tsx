import { useCallback, useEffect, useId, useRef, useState } from "react"
import "../viewer-stage.css"
import { GeometryViewer, type ViewerStatus } from "./GeometryViewer"
import { MolPoster } from "./MolPoster"
import { useAnnounce } from "../hooks/useAnnounce"
import { useLiveViewerSlot } from "../hooks/useLiveViewerSlot"
import type { GeometryAtom } from "../api/geometryApi"
import type { CoordinateUnitMode } from "../domain/geometryXyz"

type StageMode = "poster" | "live" | "failed"

const INFERRED_BONDS_NOTE =
    "Bonds shown are inferred from interatomic distance for legibility only; they are not part of the deposited record."

/** Starts the 3Dmol download early; the viewer's own import then resolves from the module cache. */
function preloadViewerLibrary() {
    void import("3dmol").catch(() => {})
}

/**
 * A picture first, the interactive viewer on request. Only one stage per page holds a live viewer; opening another
 * returns the first to its picture. If WebGL is missing the picture stays and one sentence says why.
 */
export function ViewerStage({ atoms, formula, xyzText, coordinateUnitMode = "angstrom" }: {
    atoms: GeometryAtom[]
    formula: string
    xyzText: string | null
    coordinateUnitMode?: CoordinateUnitMode
}) {
    const id = useId()
    const slot = useLiveViewerSlot()
    const announce = useAnnounce()
    const [mode, setMode] = useState<StageMode>("poster")
    const stageRef = useRef<HTMLDivElement | null>(null)
    const wantsFocusRef = useRef(false)
    const name = formula || "this geometry"

    useEffect(() => () => slot?.release(id), [slot, id])

    const open = () => {
        wantsFocusRef.current = true
        slot?.claim(id, () => {
            wantsFocusRef.current = false
            setMode("poster")
            announce(`The 3D view of ${name} was closed because another opened. Its picture is shown.`)
        })
        setMode("live")
    }

    const close = () => {
        slot?.release(id)
        wantsFocusRef.current = false
        setMode("poster")
    }

    const onStatusChange = useCallback((status: ViewerStatus) => {
        if (status === "unavailable") {
            slot?.release(id)
            wantsFocusRef.current = false
            setMode("failed")
        } else if (status === "ready" && wantsFocusRef.current) {
            wantsFocusRef.current = false
            stageRef.current?.querySelector<HTMLElement>(".viewer-controls")?.focus()
        }
    }, [slot, id])

    return (
        <div className="viewer-poster-stage" ref={stageRef}>
            {mode === "live" ? (
                <>
                    <GeometryViewer
                        atoms={atoms}
                        formula={formula}
                        xyzText={xyzText}
                        coordinateUnitMode={coordinateUnitMode}
                        onStatusChange={onStatusChange}
                    />
                    <button type="button" className="viewer-poster-action" onClick={close}>Show picture</button>
                </>
            ) : (
                <>
                    <div className="viewer-poster-frame">
                        <MolPoster atoms={atoms} formula={formula} />
                    </div>
                    <p className="section-note viewer-poster-note">{INFERRED_BONDS_NOTE}</p>
                    {mode === "failed" ? (
                        <p className="empty-projection" role="status">
                            The 3D view could not start in this browser, which may not support WebGL. The picture above
                            and the coordinate table below are unaffected.
                        </p>
                    ) : (
                        <button
                            type="button"
                            className="viewer-poster-action"
                            onClick={open}
                            onMouseEnter={preloadViewerLibrary}
                            onFocus={preloadViewerLibrary}
                        >
                            Show in 3D
                        </button>
                    )}
                </>
            )}
        </div>
    )
}
